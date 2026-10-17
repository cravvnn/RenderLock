"""Bounded authored brightness case qualified by C107 installed emulation."""
import numpy as np


def neutral_tint_controls(vmat, *, assume_white_instance_color):
    if assume_white_instance_color is not True:
        raise ValueError('Explicit white instance-color assumption required')
    vectors=vmat.get('VectorParams',{});ints=vmat.get('IntParams',{})
    csb=np.asarray(vectors.get('g_vAlbedoContrastSaturationBrightness1'),dtype=float)
    tint=np.asarray(vectors.get('g_vColorTint1'),dtype=float)
    if csb.shape!=(4,) or not np.isfinite(csb).all() or not np.array_equal(csb[:2],[1,1]) or not 0<=csb[2]<=2:
        raise ValueError('Only qualified unit contrast/saturation brightness supported')
    if tint.shape!=(4,) or not np.array_equal(tint[:3],[1,1,1]) or ints.get('g_nTextureColorTintMode1')!=0:
        raise ValueError('Only qualified white multiply tint supported')
    strength=vmat.get('FloatParams',{}).get('g_fVertexColorStrength1')
    if not isinstance(strength,(float,int)) or not np.isfinite(strength) or not 0<=strength<=1:
        raise ValueError('Explicit vertex strength required')
    flags=[]
    for key in ['g_bApplyTintToVertexColors','g_bMaskVertexColorTint1','g_bMaskColorTint1']:
        value=ints.get(key)
        # With white instance color, vertex multiplication is identity on either
        # side of the matrices. Choose a canonical equivalent order when omitted;
        # this is not an inference about the engine's default flag.
        if key=='g_bApplyTintToVertexColors' and key not in ints:value=0
        if type(value) is not int or value not in (0,1):raise ValueError('Explicit authored tint flags required')
        flags.append(bool(value))
    correction=np.diag([csb[2],csb[2],csb[2],1])
    return dict(correction_matrix=correction,tint_matrix=np.eye(4),
        instance_color=[1,1,1],tint_mask=1,vertex_strength=float(strength),
        apply_tint_to_vertex_colors=flags[0],mask_vertex_tint=flags[1],mask_color_tint=flags[2])


def apply_to_materials(materials, *, assume_white_instance_color):
    """Caller has authenticated saved scene; image identity is checkpoint-bound."""
    import bpy
    from agr.blender_npr_albedo_inputs import bind_albedo
    plans=[]
    for mat in materials:
        controls=neutral_tint_controls(mat['vmat'],assume_white_instance_color=assume_white_instance_color)
        users=[o for o in bpy.data.objects if o.type=='MESH' and any(s.material==mat for s in o.material_slots)]
        names=set()
        for obj in users:
            layers=[u.name for u in obj.data.uv_layers if u.active_render]
            if len(layers)!=1:raise ValueError('Expected one render UV')
            names.add(layers[0])
        if len(names)!=1:raise ValueError('Expected consistent material render UV')
        bsdf=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
        source=bsdf.inputs['Base Color'].links[0].from_node
        if source.type!='TEX_IMAGE':raise ValueError('Expected direct checkpoint color image')
        plans.append((mat,controls,names.pop(),source.image))
    rows=[]
    for mat,controls,uv,image in plans:
        row=bind_albedo(mat,mat.node_tree.nodes['AGR NPR indirect preview'],expected_image=image,
            color_resource=mat['vmat']['TextureParams']['g_tColor'],uv_name=uv,**controls)
        rows.append(dict(material=mat.name,brightness=float(controls['correction_matrix'][0,0]),**row))
    return dict(materials=rows,assumption='White instance color; network-to-GPU binding and runtime overrides unproven',
                limits='Analytical unit-contrast/saturation matrix within C107 emulation tolerance; tint mask immaterial with white tint and white instance color')
