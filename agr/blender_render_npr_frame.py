"""Fresh-baseline, audited partial NPR frame render with owned scratch cleanup."""
import json
from pathlib import Path
import shutil
from .asset_converter import sha


def render_frame(scene_path,data,output,frame,*,manifest_dir,width=960,height=540,
                 samples=24,allow_partial=False,retain_diagnostics=False):
    from .blender_scene_npr import validate_npr_runtime,apply_scene_npr,validate_prepared_scene_npr
    validate_npr_runtime(data,allow_partial=allow_partial)
    if data.get('npr_runtime') is None:raise ValueError('Explicit NPR runtime required')
    if any(type(v) is not int or v<=0 for v in (width,height,samples)) or type(frame) is not int:
        raise ValueError('Positive integer viewport/samples and integer frame required')
    if max(width,height)>4096 or samples>256:raise ValueError('Bounded render dimensions/samples required')
    output=Path(output).resolve()
    if output.exists() or not output.parent.is_dir():
        raise ValueError('New render directory with existing parent required')
    if shutil.disk_usage(output.parent).free<96*1024*1024+width*height*128:
        raise ValueError('Insufficient space for one audited frame')
    import bpy
    from .blender_capture_rim_depth import capture_rim_depth
    from .blender_frame_rim import apply_frame_rim,assert_rim_frame
    from . import blender_rim_audit as audit
    scene_path=Path(scene_path).resolve(strict=True)
    baseline_sha=sha(scene_path)
    output.mkdir();receipt={'status':'running','frame':frame,'scene_sha256':baseline_sha}
    def save():
        (output/'frame-report.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    save()
    try:
        bpy.ops.wm.open_mainfile(filepath=str(scene_path),load_ui=False,use_scripts=False)
        scene=bpy.context.scene
        if scene.get('d2b_character_shader'):
            raise ValueError('Artist character shader scenes use Eevee rendering; render-npr depth rim cannot be applied on top')
        if not scene.frame_start<=frame<=scene.frame_end:raise ValueError('Frame outside exported range')
        scene.frame_set(frame);scene.render.resolution_x=width;scene.render.resolution_y=height
        scene.render.resolution_percentage=100
        coords=data['metadata']['coordinate_system']
        roots=[o for o in scene.objects if o.parent is None and o.get('agr_entity_id')]
        # Loading a fresh saved baseline before capture excludes unsaved geometry
        # edits from this worker's supported workflow.
        depth=capture_rim_depth(scene_path,coords,output/'depth')
        apply=validate_prepared_scene_npr if scene.get('agr_npr_lighting') else apply_scene_npr
        prepared=apply(scene,roots,data,manifest_dir=manifest_dir,
                       coordinates=coords,allow_partial=True)
        from .blender_npr_selection import select_npr_actor_materials,actor_material_rows
        from .source_material_routes import material_route,NPR_ROUTE
        actors=select_npr_actor_materials(data['npr_materials'],data['entities'],roots,manifest_dir=manifest_dir)
        materials=[material for actor in actors for material,row in actor_material_rows(actor) if material_route(row)==NPR_ROUTE]
        if not materials:raise ValueError('NPR frame rim requires at least one selected NPR material draw')
        masks={}
        for actor in actors:
            for row in actor['package']['materials']:
                if material_route(row)!=NPR_ROUTE:continue
                name=row['material'];texture=row['textures']['g_tTintMaskRimLightMask']
                if name in masks and masks[name]!=texture:
                    raise ValueError('Conflicting shared frame-rim mask identities')
                masks[name]=texture
        contract,rows=apply_frame_rim(scene_path,coords,depth['depth_path'],depth['manifest_path'],materials,masks,allow_partial=True)
        audit.register()
        for row in rows:
            mat=row['material']
            if row['depth_policy']!=depth['depth_policy']:
                raise ValueError('Rim depth policy changed after capture')
            audit.attach(mat.node_tree.nodes,mat.node_tree.links,row['project'],row['depths'],row['rim'],row['npr'],row['total'],base_socket=row['base'],depth_policy=row['depth_policy'])
        diagnostics=output/'diagnostics';diagnostics.mkdir();audit.compositor(diagnostics)
        scene.cycles.samples=samples;scene.render.image_settings.file_format='PNG'
        image_path=output/'frame.png';scene.render.filepath=str(image_path)
        assert_rim_frame(scene_path,coords,contract)
        bpy.ops.render.render(write_still=True)
        checks=audit.verify(diagnostics,frame,width,height,depth_policy=depth['depth_policy'])
        if sha(scene_path)!=baseline_sha:raise ValueError('Baseline changed during frame render')
        scratch=list((output/'depth').iterdir())+list(diagnostics.glob('*.exr'))
        if len(scratch)!=8 or any(not p.is_file() or p.is_symlink() or p.stat().st_nlink!=1 for p in scratch):
            raise ValueError('Unexpected owned scratch inventory; preserving evidence')
        files=[dict(path=str(p.relative_to(output)),bytes=p.stat().st_size,sha256=sha(p)) for p in scratch]
        receipt.update(status='verified',image=dict(path='frame.png',sha256=sha(image_path)),
            audit=checks,depth_identity=contract,depth_policy=depth['depth_policy'],
            npr_material_draws=len(materials),npr_material_resources=len(masks),
            scratch_files=files,diagnostics_retained=True,
            scope='Partial NPR indirect/rim reference. Direct lighting/runtime fields/engine parity remain unqualified.')
        save()
        if not retain_diagnostics:
            # Only the exact eight files created by this invocation, after PASS.
            # No recursive cleanup, historical directories or shared payloads.
            for p,row in zip(scratch,files):
                resolved=p.resolve(strict=True)
                if resolved.parent not in (output/'depth',diagnostics) or sha(p)!=row['sha256']:
                    raise ValueError('Scratch changed before cleanup; preserving remaining files')
                p.unlink()
            (output/'depth').rmdir()
            receipt['diagnostics_retained']=False
        receipt['status']='PASS';save()
        return receipt
    except BaseException as exc:
        receipt.update(status='FAIL',error=str(exc),error_type=type(exc).__name__)
        save();raise
