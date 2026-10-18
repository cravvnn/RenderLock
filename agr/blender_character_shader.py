"""Partial character look using the qualified user-supplied Elagrimm library.

The supplied library stays external to AGR. A copied group accepts recovered
Source lighting references; this is an artistic adapter, not engine shader parity.
"""
import hashlib
import json
from pathlib import Path

LIBRARY_SHA256='018bb08347dcd9cf48da317f5b4c2bcee9e0dcc4fecd4f32c3be3a3225ec1891'
LIBRARY_COMMIT='e18af8b1c9c1d34b4af72002fb1f4136aa159515'
LIBRARY_URL='https://github.com/Elagrimm/BLENDER-Deadlock-Shader'
MATERIAL_NAME='Deadlock'
NODE_NAME='D2B character shader'


def validate_character_shader_library(path):
    path=Path(path).expanduser().resolve()
    if path.suffix.lower()!='.blend' or not path.is_file() or not 0<path.stat().st_size<=16*1024*1024:
        raise ValueError('Elagrimm character shader requires the qualified local .blend library')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=LIBRARY_SHA256:
        raise ValueError('Character shader library changed or is not the supported Elagrimm library; '
                         'choose BLENDER-Deadlock-Shader.blend from the documented revision')
    return path


def _probe_regions(package):
    """Pack the seven unchanged directional/shadow stacks for each probe."""
    import bpy
    import numpy as np
    rows={r['name']:r for r in package['atlas']}
    if len(rows)!=len(package['atlas']):raise ValueError('Duplicate probe atlas identity')
    images={name:bpy.data.images.get('AGR probe '+name) for name in rows}
    if any(image is None for image in images.values()):
        raise ValueError('Expected builder-owned Source probe images')
    families={}
    for name,image in images.items():
        w,h,d=rows[name]['size']
        if tuple(image.size)!=(w,h*d) or image.colorspace_settings.name!='Non-Color' or image.alpha_mode!='CHANNEL_PACKED':
            raise ValueError('Probe image layout/sampling differs')
        key=name.rsplit('-irradiance-',1)[0] if '-irradiance-' in name else name.removesuffix('-shadow')
        families.setdefault(key,[]).append((name,image))
    # Check all families before allocating any packed images.
    for key,entries in families.items():
        expected={key+'-irradiance-'+str(i) for i in range(6)}|{key+'-shadow'}
        if {name for name,_ in entries}!=expected or len({tuple(i.size) for _,i in entries})!=1:
            raise ValueError('Incomplete or unequal probe image family')
    regions={}
    for key,entries in sorted(families.items()):
        entries.sort();w,h=entries[0][1].size
        if max(w*7,h)>16384:raise ValueError('Combined probe image exceeds dimension bound')
        values=np.empty((h,w*7,4),dtype=np.float32)
        for index,(name,image) in enumerate(entries):
            pixels=np.empty(w*h*4,dtype=np.float32);image.pixels.foreach_get(pixels)
            if not np.isfinite(pixels).all():raise ValueError('Nonfinite probe image')
            values[:,index*w:(index+1)*w]=pixels.reshape(h,w,4)
        packed=bpy.data.images.new('AGR combined probe '+key,width=w*7,height=h,
                                  alpha=True,float_buffer=True)
        packed.colorspace_settings.name='Non-Color';packed.alpha_mode='CHANNEL_PACKED'
        packed.pixels.foreach_set(values.reshape(-1));packed.pack()
        for index,(name,image) in enumerate(entries):
            regions[image]=(packed,(index*w,0),rows[name]['size'])
    return regions


def _copy_groups(tree, memo, regions):
    """Copy material dependencies; preserve every original graph and binding."""
    from .blender_cube_sampling import create_cube_group
    from .blender_probe_volume import create_volume_group
    if tree in memo:return memo[tree]
    if tree.name.startswith('AGR seamless cube'):
        images=[n.image for n in tree.nodes if n.type=='TEX_IMAGE']
        if len(images)!=7:raise ValueError('Expected unmodified seven-mip cube group')
        result=create_cube_group(images,pack_mips=True)
    elif tree.name.startswith('AGR volume '):
        images=[n.image for n in tree.nodes if n.type=='TEX_IMAGE']
        if len(images)!=2 or images[0]!=images[1] or images[0] not in regions:
            raise ValueError('Expected builder-owned probe volume group')
        image,origin,size=regions[images[0]]
        result=create_volume_group(image,size,atlas_origin=origin)
    else:
        result=tree.copy();memo[tree]=result
        for node in result.nodes:
            if node.type=='GROUP':node.node_tree=_copy_groups(node.node_tree,memo,regions)
    memo[tree]=result
    return result


def apply_character_shader(scene, roots, data, library, *, manifest_dir, allow_partial=False):
    """All-material preflight and deferred mesh assignment; discard failed scenes."""
    import bpy
    from .blender_npr_selection import select_npr_materials
    from .blender_npr_package import validate_npr_pixels
    from .blender_material_emission import add_converted_emission
    from .npr_material_identity import material_resource
    if allow_partial is not True:raise ValueError('Character shader requires explicit partial export')
    from .blender_scene_npr import validate_npr_runtime
    if validate_npr_runtime(data,allow_partial=allow_partial) is None:
        raise ValueError('Character shader requires explicit NPR runtime')
    path=validate_character_shader_library(library)
    if 'd2b_character_shader' in scene:raise ValueError('Character shader is already applied')
    package,materials,objects=select_npr_materials(data['npr_materials'],data['entities'],roots,
                                                manifest_dir=manifest_dir)
    plans=[]
    for material in materials:
        nodes=material.node_tree.nodes;npr=nodes.get('AGR NPR indirect preview')
        bsdfs=[n for n in nodes if n.type=='BSDF_PRINCIPLED']
        normals=[n for n in nodes if n.type=='NORMAL_MAP']
        trans=nodes.get('AGR NPR transmissive')
        if (npr is None or len(bsdfs)!=1 or len(normals)!=1 or trans is None
                or trans.image is None or nodes.get(NODE_NAME) is not None
                or nodes.get('AGR frame rim') is not None):
            raise ValueError('Expected prepared opaque NPR character material')
        bsdf=bsdfs[0];normal=normals[0]
        if bsdf.inputs['Alpha'].is_linked or bsdf.inputs['Alpha'].default_value!=1:
            raise ValueError('Character shader transparency needs separate qualification')
        if (normal.inputs['Strength'].is_linked or normal.inputs['Strength'].default_value!=1
                or not normal.inputs['Color'].is_linked or not npr.inputs['Albedo'].is_linked):
            raise ValueError('Character shader requires qualified albedo and unit-strength tangent normal')
        if 'Irradiance' not in npr.outputs or 'Specular' not in npr.outputs:
            raise ValueError('Source-reference shader outputs missing')
        plans.append((material,material_resource(material)))
    pixels=validate_npr_pixels(package)
    masks={r['material']:r['textures']['g_tTintMaskRimLightMask']['linear_rim_mask']
           for r in pixels['materials']}
    regions=_probe_regions(data['hero_lighting'])
    with bpy.data.libraries.load(str(path),link=False) as (source,destination):
        if MATERIAL_NAME not in source.materials:raise ValueError('Qualified author material absent')
        destination.materials=[MATERIAL_NAME]
    template=destination.materials[0]
    author_nodes=[n for n in template.node_tree.nodes if n.type=='GROUP'
                  and n.node_tree.name == 'Deadlock']
    if len(author_nodes)!=1:raise ValueError('Qualified author shader group absent')
    author=author_nodes[0];bridge=author.node_tree.copy()
    bridge.name='D2B Elagrimm - Source reference bridge'
    for name in ('Source Diffuse Reference','Source Specular Reference'):
        bridge.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketColor')
    direct=bridge.interface.new_socket(name='Additional Light Influence',in_out='INPUT',socket_type='NodeSocketFloat')
    direct.default_value=1;direct.min_value=0;direct.max_value=2
    inputs=next(n for n in bridge.nodes if n.type=='GROUP_INPUT')
    for name,socket in [('Shader to RGB','Source Diffuse Reference'),
                        ('Shader to RGB.001','Source Specular Reference')]:
        node=bridge.nodes[name]
        if node.bl_idname!='ShaderNodeShaderToRGB':raise ValueError('Author lighting graph differs')
        selected=[l for l in bridge.links if l.from_node==node and l.from_socket.name=='Color']
        if not selected:raise ValueError('Author lighting output is disconnected')
        native=bridge.nodes.new('ShaderNodeVectorMath');native.operation='SCALE'
        bridge.links.new(node.outputs['Color'],native.inputs[0])
        bridge.links.new(inputs.outputs['Additional Light Influence'],native.inputs['Scale'])
        total=bridge.nodes.new('ShaderNodeVectorMath');total.operation='ADD'
        bridge.links.new(inputs.outputs[socket],total.inputs[0]);bridge.links.new(native.outputs['Vector'],total.inputs[1])
        for link in selected:
            target=link.to_socket;bridge.links.remove(link);bridge.links.new(total.outputs['Vector'],target)
    replacements={};rows=[];memo={}
    for old,resource in plans:
        material=old.copy();material.name=old.name+' | Elagrimm Source reference'
        nodes,links=material.node_tree.nodes,material.node_tree.links
        for node in nodes:
            if node.type=='GROUP':node.node_tree=_copy_groups(node.node_tree,memo,regions)
        npr=nodes['AGR NPR indirect preview']
        bsdf=next(n for n in nodes if n.type=='BSDF_PRINCIPLED')
        normal=next(n for n in nodes if n.type=='NORMAL_MAP')
        shader=nodes.new('ShaderNodeGroup');shader.node_tree=bridge;shader.name=NODE_NAME
        for socket in author.inputs:
            if hasattr(socket,'default_value'):
                value=socket.default_value
                try:value=value[:]
                except TypeError:pass
                shader.inputs[socket.name].default_value=value
        links.new(npr.inputs['Albedo'].links[0].from_socket,shader.inputs['Diffuse Color'])
        links.new(normal.inputs['Color'].links[0].from_socket,shader.inputs['Normal'])
        packed=nodes.new('ShaderNodeCombineColor');packed.name='D2B AO roughness metallic'
        for dst,name in [('Red','Material AO'),('Green','Roughness'),('Blue','Metallic')]:
            socket=npr.inputs[name]
            if socket.is_linked:links.new(socket.links[0].from_socket,packed.inputs[dst])
            else:packed.inputs[dst].default_value=socket.default_value
        links.new(packed.outputs['Color'],shader.inputs['ORM'])
        mask=masks[resource]
        # Elagrimm exposes strength rather than a mask socket. Apply the
        # qualified material's constant Source green mask to the author's value.
        shader.inputs['Rimlight Strength'].default_value *= mask
        # Preserve converted emission once, outside the author's optional
        # emission branch. Otherwise authored/default emission could double it.
        shader.inputs['Use Emission'].default_value=False
        links.new(npr.outputs['Irradiance'],shader.inputs['Source Diffuse Reference'])
        links.new(npr.outputs['Specular'],shader.inputs['Source Specular Reference'])
        author_rgb=nodes.new('ShaderNodeShaderToRGB')
        author_rgb.name='AGR Elagrimm shader response'
        links.new(shader.outputs['Output'],author_rgb.inputs['Shader'])
        final=add_converted_emission(nodes,links,bsdf,author_rgb.outputs['Color'])
        for node in nodes:
            if node.type=='OUTPUT_MATERIAL':node.is_active_output=False
        output=nodes.new('ShaderNodeOutputMaterial');output.is_active_output=True
        emission=nodes.new('ShaderNodeEmission');emission.inputs['Strength'].default_value=1
        links.new(final,emission.inputs['Color']);links.new(emission.outputs['Emission'],output.inputs['Surface'])
        material['d2b_character_shader']=LIBRARY_SHA256
        replacements[old]=material
        rows.append(dict(material=resource,rim_mask=mask,rim_factor=shader.inputs['Rimlight Strength'].default_value,
                         converted_emission_preserved=True))
    # Every selected material is fully constructed before changing any mesh slot.
    for obj in objects:
        for slot in obj.material_slots:slot.material=replacements[slot.material]
    scene.render.engine='BLENDER_EEVEE'
    disabled=[]
    for obj in scene.objects:
        if obj.type=='LIGHT' and obj.name in ('AGR | Key','AGR | Fill'):
            obj.data.energy=0;disabled.append(obj.name)
    report=dict(status='partial',author='Elagrimm',library_sha256=LIBRARY_SHA256,
                library_commit=LIBRARY_COMMIT,library_url=LIBRARY_URL,
                mode='source_reference_rgb_bridge',materials=rows,renderer=scene.render.engine,
                additional_lights='Native Eevee diffuse/specular added with editable Additional Light Influence',
                disabled_review_lights=disabled,
                rim='Elagrimm artist rim multiplied by constant green Source mask; no Source rim parity',
                limits=['Author group copied; diffuse/specular ShaderToRGB inputs replaced by Source NPR references.',
                        'Source specular is already shaded before author shaping; artistic approximation.',
                        'Author shadow tint and artistic controls retain library defaults; Source transmission is not reconstructed.',
                        'Converted emission preserved; general Source emissive/status/outline binding incomplete.',
                        'Packed lookup bilinear references qualified; Eevee implicit minification unqualified.',
                        'Additional Blender lights use artist shader response; Source light units/shadows are not certified.',
                        'Opaque NPR characters only; integer camera bake requires rebaking after edits.'])
    scene['d2b_character_shader']=json.dumps(report)
    return report
