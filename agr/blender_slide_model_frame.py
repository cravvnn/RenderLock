"""One prepared slide-model mesh, called inside an allocation transaction.

This component is intentionally not in normal dispatch until dense capture and
instance-color provenance qualify. The caller owns rollback of newly allocated
Blender data. Its immutable numeric input comes from particle_slide_mesh.
"""
import hashlib
from pathlib import Path
import numpy as np
from .particle_slide_parameters import require
from .particle_slide_mesh import UV_NAMES

MATERIAL_BUNDLE_SHA256 = '86e4af946c96de7283d4c84beaa99026814003a93fc53f7da9c70c34a59daee6'


def import_slide_material(bundle):
    """Import the pinned VRF PBR conversion; no invented packed-normal decoder."""
    import bpy
    path = Path(bundle)
    require(hashlib.sha256(path.read_bytes()).hexdigest() == MATERIAL_BUNDLE_SHA256,
            'slide material bundle differs')
    before = set(bpy.data.materials)
    objects = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path),import_unused_materials=True,import_pack_images=True)
    added = set(bpy.data.materials)-before
    require(set(bpy.data.objects) == objects and len(added) == 1,
            'slide material-only import coverage differs')
    material = added.pop()
    require(material.use_nodes and len([n for n in material.node_tree.nodes
            if n.type == 'BSDF_PRINCIPLED']) == 1, 'converted slide PBR graph differs')
    return material


def build_slide_model_frame(name, prepared, prototype, textures, collection):
    """Allocate a mesh/material from complete prepared arrays. No publication.

No clock/lifetime inference, persistence or resource cleanup owner is added.
The existing package transaction (ModifierMaskRender) is the intended owner;
private component checks use that same snapshot/batch_remove boundary.
"""
    import bpy
    require(type(prepared) is dict and set(prepared.get('uv_layers',{})) == set(UV_NAMES),
            'complete slide UV layers required')
    vertices = np.asarray(prepared['vertices']);normals = np.asarray(prepared['normals'])
    require(vertices.ndim == 2 and vertices.shape[1] == 3 and 1 <= len(vertices) <= 1000000
            and normals.shape == vertices.shape and np.isfinite(vertices).all()
            and np.isfinite(normals).all(), 'prepared slide mesh arrays differ')
    for layer in prepared['uv_layers'].values():
        require(np.asarray(layer).shape == (len(vertices),2) and np.isfinite(layer).all(),
                'prepared slide UV array incomplete')
    require(prepared['self_illum_scale'] == 0, 'nonzero slide emission not admitted by this component')
    require(prototype.use_nodes and len([n for n in prototype.node_tree.nodes
            if n.type == 'BSDF_PRINCIPLED']) == 1, 'slide prototype unavailable')
    paths = {}
    for key in ('g_tColor','g_tAltTranslucency'):
        row = textures[key];descriptor=row['descriptor']
        require(descriptor['status'] == 'supported' and descriptor['is_sprite_sheet'] is False,
                'slide texture is unavailable or animated')
        frames=descriptor['sequences']['0']['frames']
        require(len(frames) == 1, 'slide texture frame coverage differs')
        filename=frames[0]['image'];relative=Path(filename)
        require(not relative.is_absolute() and '..' not in relative.parts, 'slide texture path escapes asset directory')
        path=Path(row['directory'])/relative
        require(hashlib.sha256(path.read_bytes()).hexdigest() == frames[0]['sha256'],
                'slide texture image identity differs')
        paths[key]=path
    mesh=bpy.data.meshes.new(name)
    mesh.from_pydata(vertices.tolist(),[],prepared['indices'].tolist());mesh.update()
    for poly in mesh.polygons:poly.use_smooth=True
    for name_uv,values in prepared['uv_layers'].items():
        layer=mesh.uv_layers.new(name='AGR Slide '+name_uv)
        for loop in mesh.loops:layer.data[loop.index].uv=values[loop.vertex_index]
    obj=bpy.data.objects.new(name,mesh);collection.objects.link(obj);obj.visible_shadow=False
    # Repeated-index padding is removed in the pure projection. Refuse any other
    # topology repair before Blender's native corner-normal builder; its two
    # setters crashed on the original padding in retained component checks.
    require(not mesh.validate(clean_customdata=False), 'slide mesh required topology repair')
    mesh.update(calc_edges=True)
    mesh.calc_loop_triangles()
    require(len(mesh.corner_normals) == len(mesh.loops), 'slide corner normal cache incomplete')
    mesh.normals_split_custom_set([normals[loop.vertex_index].tolist() for loop in mesh.loops])
    material=prototype.copy();material.name=name+' | captured masks';mesh.materials.append(material)
    material['agr_slide_material_scope']='Selected UV/mask inputs; VRF Blender PBR approximation, not Source lighting parity'
    material['agr_slide_input_frame_sha256']=prepared['input_frame_sha256']
    nodes,links=material.node_tree.nodes,material.node_tree.links
    principled=next(n for n in nodes if n.type=='BSDF_PRINCIPLED')
    principled.inputs['Emission Strength'].default_value=0
    # The fixed authored normal/self-illum transforms equal albedo. Preserve
    # converted PBR packed-map decoding and only supply the baked coordinates.
    uv=nodes.new('ShaderNodeUVMap');uv.uv_map='AGR Slide albedo'
    for node in list(nodes):
        if node.type=='TEX_IMAGE':links.new(uv.outputs['UV'],node.inputs['Vector'])
    def image(key,layer,raw=False):
        node=nodes.new('ShaderNodeTexImage');node.label=key
        node.image=bpy.data.images.load(str(paths[key]),check_existing=False)
        expected=textures[key]['descriptor']['sequences']['0']['frames'][0]
        require(tuple(node.image.size) == (expected['width'],expected['height'])
                and len(node.image.pixels) == expected['width']*expected['height']*4,
                'slide image decoded dimensions/data differ')
        node.image.colorspace_settings.name='Non-Color' if raw else 'sRGB'
        node.image.pack();node.extension='REPEAT';node.interpolation='Linear'
        coords=nodes.new('ShaderNodeUVMap');coords.uv_map='AGR Slide '+layer
        links.new(coords.outputs['UV'],node.inputs['Vector']);return node
    color=image('g_tColor','albedo')
    mask=image('g_tColor','translucency',True)
    alt=image('g_tAltTranslucency','alt_translucency',True)
    def scalar(op,a,b):
        node=nodes.new('ShaderNodeMath');node.operation=op
        for value,target in zip((a,b),node.inputs):
            if isinstance(value,(int,float)):target.default_value=float(value)
            else:links.new(value,target)
        return node.outputs[0]
    source=nodes.new('ShaderNodeSeparateXYZ');links.new(color.outputs['Color'],source.inputs[0])
    result=nodes.new('ShaderNodeCombineXYZ');matrix=prepared['tint_matrix']
    # Row-vector affine tint matrix, followed by explicit scene-instance RGB.
    for axis in range(3):
        value=scalar('MULTIPLY',source.outputs[0],float(matrix[0,axis]))
        for channel in (1,2):value=scalar('ADD',value,scalar('MULTIPLY',source.outputs[channel],float(matrix[channel,axis])))
        value=scalar('ADD',value,float(matrix[3,axis]))
        value=scalar('MULTIPLY',value,prepared['instance_rgba'][axis]);links.new(value,result.inputs[axis])
    links.new(result.outputs[0],principled.inputs['Base Color'])
    red=nodes.new('ShaderNodeSeparateXYZ');links.new(alt.outputs['Color'],red.inputs[0])
    alpha=scalar('MULTIPLY',mask.outputs['Alpha'],red.outputs['X'])
    alpha=scalar('MULTIPLY',alpha,prepared['instance_rgba'][3]*prepared['opacity_scale'])
    links.new(alpha,principled.inputs['Alpha'])
    material.use_backface_culling=False
    if hasattr(material,'surface_render_method'):material.surface_render_method='DITHERED'
    obj['agr_slide_input_frame_sha256']=prepared['input_frame_sha256']
    obj['agr_slide_game_time']=prepared['game_time']
    source=prepared.get('instance_color_source')
    if source:
        obj['agr_slide_instance_color_policy']=source['policy']
        obj['agr_slide_instance_color_raw']=source['raw_hex']
    obj['agr_slide_scope']='Frame component; dense/common-scene timing, final GPU packing and Source lighting remain separate'
    return obj
