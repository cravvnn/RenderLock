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
    from .blender_npr_selection import select_npr_materials
    from .blender_npr_primary import camera_primary_track,bind_primary_track
    from .blender_prepare_npr_package import prepare_material_package
    from .blender_hero_lighting import verify_surface_coverage
    package,materials,objects=select_npr_materials(data['npr_materials'],data['entities'],
                                                   roots,manifest_dir=manifest_dir)
    if any(m.node_tree.animation_data is not None for m in materials):
        raise ValueError('NPR preparation requires unanimated converted material trees')
    track=camera_primary_track(scene,coordinates,assume_camera_forward=True)
    coverage=verify_surface_coverage(scene,roots,data['hero_lighting'],coordinates)
    prepared=prepare_material_package(data['hero_lighting'],package,track['rows'][0]['primary'],
        materials=materials,objects=objects,world_sha256=data.get('map',{}).get('source_world_sha256'),
        manifest_dir=manifest_dir,allow_partial=True,assume_white_instance_color=True)
    copies=sorted({slot.material for obj in objects for slot in obj.material_slots},key=lambda m:m.name)
    animation=bind_primary_track(copies,track)
    scene.frame_set(scene.frame_current,subframe=scene.frame_subframe)
    report=dict(status='partial',mode=runtime['mode'],preparation=prepared,
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
    from .blender_npr_selection import select_npr_materials
    from .blender_npr_primary import camera_primary_track
    from .blender_npr_package import validate_npr_pixels
    from .blender_hero_lighting import verify_surface_coverage
    if validate_npr_runtime(data,allow_partial=allow_partial) is None:
        raise ValueError('Explicit NPR runtime required')
    saved=json.loads(scene.get('agr_npr_lighting','null'))
    if not isinstance(saved,dict) or saved.get('status')!='partial' or saved.get('mode')!='partial_indirect_reference' or saved.get('rim',{}).get('status')!='not_applied':
        raise ValueError('Expected saved partial NPR indirect builder state')
    package,materials,objects=select_npr_materials(data['npr_materials'],data['entities'],roots,manifest_dir=manifest_dir)
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
    pixels=validate_npr_pixels(package)
    coverage=verify_surface_coverage(scene,roots,data['hero_lighting'],coordinates)
    return dict(status='partial',mode=saved['mode'],preparation=dict(pixels=pixels),
                coverage=coverage,primary_track=track,source='validated_saved_indirect',
                limits='Builder-owned saved graphs; arbitrary shader edits and engine runtime fidelity unqualified.')
