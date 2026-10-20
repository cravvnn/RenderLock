"""Explicitly partial NPR indirect stage for the normal scene builder."""
import json


def validate_npr_runtime(data, *, allow_partial):
    runtime=data.get('npr_runtime')
    if runtime is None:return None
    expected={'mode':'partial_indirect_reference','assume_camera_forward':True,
              'assume_white_instance_color':True}
    if (allow_partial is not True or not isinstance(runtime,dict)
            or set(runtime)!=set(expected) or runtime.get('mode')!=expected['mode']
            or runtime.get('assume_camera_forward') is not True
            or runtime.get('assume_white_instance_color') is not True):
        raise ValueError('Explicit partial NPR indirect runtime assumptions required')
    if data.get('hero_lighting') is None or data.get('npr_materials') is None:
        raise ValueError('NPR runtime requires hero lighting and selected material packages')
    return runtime


def apply_scene_npr(scene, roots, data, *, manifest_dir, coordinates, allow_partial=False):
    runtime=validate_npr_runtime(data,allow_partial=allow_partial)
    if runtime is None:raise ValueError('NPR runtime not selected')
    from .blender_npr_selection import select_npr_actor_materials,actor_material_rows
    from .source_material_routes import material_route,NPR_ROUTE
    from .blender_npr_primary import camera_primary_track,bind_primary_track
    from .blender_prepare_npr_package import prepare_material_package
    from .blender_hero_lighting import verify_surface_coverage
    actors=select_npr_actor_materials(data['npr_materials'],data['entities'],roots,manifest_dir=manifest_dir)
    materials=[m for actor in actors for m in actor['materials']]
    objects=[o for actor in actors for o in actor['objects']]
    if any(m.node_tree.animation_data is not None for m in materials):
        raise ValueError('NPR preparation requires unanimated converted material trees')
    track=camera_primary_track(scene,coordinates,assume_camera_forward=True)
    coverage=verify_surface_coverage(scene,roots,data['hero_lighting'],coordinates)
    from .source_probe_blending import contributing_actor_probes
    subsets=contributing_actor_probes(data['hero_lighting']['plan']['probes'],coverage)
    prepared=[]
    for actor in actors:
        identity=actor['entity']['id']
        row=prepare_material_package(data['hero_lighting'],actor['package'],track['rows'][0]['primary'],
            materials=actor['materials'],objects=actor['objects'],world_sha256=data.get('map',{}).get('source_world_sha256'),
            manifest_dir=manifest_dir,allow_partial=True,assume_white_instance_color=True,
            actor_id=identity,probe_ids=[str(p['hammeruniqueid']) for p in subsets[identity]])
        prepared.append(dict(entity_id=identity,**row))
    copies=sorted({slot.material for obj in objects for slot in obj.material_slots
                   if slot.material.get('agr_source_material_route')==NPR_ROUTE},key=lambda m:m.name)
    animation=bind_primary_track(copies,track) if copies else dict(status='not_applicable',materials=0,reason='No NPR material draws selected')
    scene.frame_set(scene.frame_current,subframe=scene.frame_subframe)
    report=dict(status='partial',mode=runtime['mode'],preparation=dict(actors=prepared,materials=[m for row in prepared for m in row['materials']]),
                npr_material_count=len(copies),preserved_material_count=len(materials)-len(copies),
                primary_track=track,primary_animation=animation,coverage=coverage,
                rim={'status':'not_applied','reason':'Frame-specific depth/rim lifecycle required'},
                limits=['NPR indirect only; direct lighting and depth-dependent rim are incomplete.',
                        'Reference white instance/camera/exposure/DFAO/screen assumptions.',
                        'Integer frame camera bake; rebake after camera edits; no engine parity claim.'])
    scene['agr_npr_lighting']=json.dumps(report)
    return report


def validate_prepared_scene_npr(scene,roots,data,*,manifest_dir,coordinates,allow_partial=False):
    """Validate saved builder state without applying the indirect stage twice.

    This checks selection, package pixels, material interface, coverage and camera
    bake. It is not authentication of arbitrary user-edited shader internals.
    """
    import numpy as np
    from .blender_npr_selection import select_npr_actor_materials,actor_material_rows
    from .source_material_routes import material_route,NPR_ROUTE
    from .blender_npr_primary import camera_primary_track
    from .blender_npr_package import validate_npr_pixels
    from .blender_hero_lighting import verify_surface_coverage
    if validate_npr_runtime(data,allow_partial=allow_partial) is None:
        raise ValueError('Explicit NPR runtime required')
    saved=json.loads(scene.get('agr_npr_lighting','null'))
    if not isinstance(saved,dict) or saved.get('status')!='partial' or saved.get('mode')!='partial_indirect_reference' or saved.get('rim',{}).get('status')!='not_applied':
        raise ValueError('Expected saved partial NPR indirect builder state')
    actors=select_npr_actor_materials(data['npr_materials'],data['entities'],roots,manifest_dir=manifest_dir)
    materials=[m for actor in actors for m,row in actor_material_rows(actor) if material_route(row)==NPR_ROUTE]
    coverage=verify_surface_coverage(scene,roots,data['hero_lighting'],coordinates)
    from .source_probe_blending import contributing_actor_probes
    subsets=contributing_actor_probes(data['hero_lighting']['plan']['probes'],coverage)
    for actor in actors:
        identity=actor['entity']['id'];probe_ids=[str(p['hammeruniqueid']) for p in subsets[identity]]
        for material,row in actor_material_rows(actor):
            if (material.get('agr_hero_actor_id')!=identity
                    or json.loads(material.get('agr_hero_probe_ids','null'))!=probe_ids):
                raise ValueError('Actor changed since probe selection; rebuild required')
            if material_route(row)!=NPR_ROUTE:
                if (material.get('agr_source_material_route')!=material_route(row)
                        or material.node_tree.nodes.get('AGR NPR indirect preview') is not None):
                    raise ValueError('Saved generic material route differs')
                continue
            node=material.node_tree.nodes.get('AGR NPR actual probes')
            if node is None or node.node_tree is None or json.loads(node.node_tree.get('agr_hero_probe_ids','null'))!=probe_ids:
                raise ValueError('Saved NPR probe graph selection differs')
            expected=bool(material['vmat'].get('IntParams',{}).get('F_NO_SPECULAR_AT_FULL_ROUGHNESS',0))
            tree=material.node_tree.nodes.get('AGR NPR indirect preview')
            if tree is None or tree.node_tree.get('agr_no_specular_at_full_roughness',False) is not expected:
                raise ValueError('Saved NPR material roughness switch differs')
    sockets=[]
    for material in materials:
        nodes=material.node_tree.nodes;npr=nodes.get('AGR NPR indirect preview')
        albedo=nodes.get('AGR NPR source albedo')
        if npr is None or albedo is None or nodes.get('AGR frame rim') is not None:
            raise ValueError('Saved NPR material stages differ')
        if 'Radiance' not in npr.inputs or 'Denominator' not in npr.inputs or npr.inputs['Primary'].is_linked:
            raise ValueError('Saved NPR input interface differs')
        if len(npr.inputs['Albedo'].links)!=1 or npr.inputs['Albedo'].links[0].from_node!=albedo:
            raise ValueError('Saved authored albedo binding differs')
        sockets.append(npr.inputs['Primary'])
    track=camera_primary_track(scene,coordinates,assume_camera_forward=True)
    prior=saved.get('primary_track',{}).get('rows',[])
    if len(prior)!=len(track['rows']):raise ValueError('Saved primary frame coverage differs')
    frame,subframe=scene.frame_current,scene.frame_subframe
    try:
        for row,old in zip(track['rows'],prior):
            if row['frame']!=old.get('frame') or row['camera']!=old.get('camera') or not np.allclose(row['primary'],old.get('primary'),rtol=0,atol=1e-6):
                raise ValueError('Camera changed since primary bake; rebuild required')
            scene.frame_set(row['frame'])
            if any(not np.allclose(socket.default_value,row['primary'],rtol=0,atol=1e-6) for socket in sockets):
                raise ValueError('Saved primary socket animation differs')
    finally:scene.frame_set(frame,subframe=subframe)
    pixels=[dict(entity_id=actor['entity']['id'],**validate_npr_pixels(actor['package'])) for actor in actors]
    return dict(status='partial',mode=saved['mode'],preparation=dict(pixels=pixels),
                coverage=coverage,primary_track=track,source='validated_saved_indirect',
                limits='Builder-owned saved graphs; arbitrary shader edits and engine runtime fidelity unqualified.')
