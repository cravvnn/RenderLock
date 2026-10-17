"""Capture a fresh saved-baseline Cycles depth frame with bounded disk output."""
import json
import shutil
from pathlib import Path
import numpy as np
from .blender_rim_depth import current_identity
from .rim_depth_manifest import make_manifest,load_verified


def capture_rim_depth(scene_path,coordinates,output):
    """Caller opens baseline, sets frame/viewport, and makes no geometry edits.

    Retains only EXR, axial NPY and identity manifest. No scene copy or beauty
    PNG. Arbitrary unsaved geometry/visibility mutations are not authenticated.
    """
    import bpy
    scene=bpy.context.scene
    contract=current_identity(scene_path,coordinates)
    output=Path(output).resolve()
    if output.drive.upper()!='C:':raise ValueError('Generated rim depth must stay on C:')
    width,height=contract['width'],contract['height']
    if max(width,height)>4096:raise ValueError('Depth viewport exceeds bounded capture size')
    parent=output.parent
    if not parent.is_dir() or output.exists():raise ValueError('Existing parent and new depth directory required')
    required=64*1024*1024+width*height*32
    if shutil.disk_usage(parent).free<required:raise ValueError('Insufficient free space for bounded depth capture')
    if any(m.use_nodes and m.node_tree.nodes.get('AGR frame rim') is not None for m in bpy.data.materials):
        raise ValueError('Capture baseline before applying frame-specific rim')
    old_tree=scene.compositing_node_group
    old_samples=scene.cycles.samples;old_denoising=scene.cycles.use_denoising
    layer=scene.view_layers[0];old_z=layer.use_pass_z
    old_compositing=scene.render.use_compositing
    output.mkdir()
    group=None;image=None
    try:
        scene.cycles.samples=1;scene.cycles.use_denoising=False;layer.use_pass_z=True
        scene.render.use_compositing=True
        group=bpy.data.node_groups.new('AGR capture frame depth','CompositorNodeTree')
        scene.compositing_node_group=group
        render=group.nodes.new('CompositorNodeRLayers');render.layer=layer.name
        dest=group.nodes.new('CompositorNodeOutputFile');dest.directory=str(output)
        dest.file_name='depth';dest.format.media_type='IMAGE';dest.format.file_format='OPEN_EXR';dest.format.color_depth='32'
        dest.file_output_items.new('FLOAT','Z');group.links.new(render.outputs['Depth'],dest.inputs['Z'])
        bpy.ops.render.render(write_still=False)
        files=list(output.glob('*.exr'))
        if len(files)!=1:raise ValueError('Expected exactly one captured depth EXR')
        image=bpy.data.images.load(str(files[0]),check_existing=False)
        if list(image.size)!=[width,height]:raise ValueError('Captured depth dimensions differ')
        pixels=np.empty(len(image.pixels),dtype=np.float32);image.pixels.foreach_get(pixels)
        depth=pixels.reshape(height,width,4)[::-1,:,0].astype(np.float64)/coordinates['unit_scale']
        if current_identity(scene_path,coordinates)!=contract:raise ValueError('Depth capture identity changed during render')
        path=output/'axial-source-depth.npy';np.save(path,depth)
        manifest=make_manifest(path,contract)
        load_verified(path,manifest,contract)
        (output/'depth-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        return dict(status='captured_verified_identity',depth_path=str(path),
                    manifest_path=str(output/'depth-manifest.json'),identity=contract,
                    limits='Cycles axial reference; no engine depth or arbitrary unsaved geometry authentication.')
    finally:
        scene.compositing_node_group=old_tree
        scene.cycles.samples=old_samples;scene.cycles.use_denoising=old_denoising
        layer.use_pass_z=old_z;scene.render.use_compositing=old_compositing
        if image is not None:bpy.data.images.remove(image)
        if group is not None:bpy.data.node_groups.remove(group)
