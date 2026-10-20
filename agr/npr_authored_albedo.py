"""Authored static color expressions, checked against installed built-ins."""
import numpy as np
from .npr_color_matrices import saturation_brightness,color_tint,color_correction

# Read from the qualified compiled pbr pixel shader's variable descriptions.
# These are static metadata defaults (no dynamic expression/layer override),
# not inferred values for arbitrary materials or shader revisions.
DEFAULT_SHADER_SHA256 = '13cc4576f69be8411f1b1400ea5c0bcedcb947c9406e24102cc6ccd7a33e41ef'
STATIC_DEFAULTS = {
    'VectorParams': {'g_vAlbedoContrastSaturationBrightness1': [1, 1, 1, 0],
                     'g_vColorTint1': [1, 1, 1, 0]},
    'IntParams': {'g_nTextureColorTintMode1': 0, 'g_bApplyTintToVertexColors': 0,
                  'g_bMaskVertexColorTint1': 1, 'g_bMaskColorTint1': 1},
    'FloatParams': {'g_fVertexColorStrength1': 1},
}


def neutral_tint_controls(vmat, *, assume_white_instance_color, shader_sha256=None,texture_average=None):
    if assume_white_instance_color is not True:
        raise ValueError('Explicit white instance-color assumption required')
    if shader_sha256 is not None:
        if shader_sha256 != DEFAULT_SHADER_SHA256:
            raise ValueError('NPR albedo shader defaults require requalification')
        # Preserve explicit authored values, including invalid/null values which
        # the normal checks below must reject. Never mutate retained metadata.
        vmat = {section: dict(values, **vmat.get(section, {}))
                for section, values in STATIC_DEFAULTS.items()}
    vectors=vmat.get('VectorParams',{});ints=vmat.get('IntParams',{})
    csb=np.asarray(vectors.get('g_vAlbedoContrastSaturationBrightness1'),dtype=float)
    tint=np.asarray(vectors.get('g_vColorTint1'),dtype=float)
    if (csb.shape!=(4,) or not np.isfinite(csb).all()
            or np.any(csb[:3]<0) or np.any(csb[:3]>4)):
        raise ValueError('Qualified authored contrast/saturation/brightness in0..4 required')
    if csb[0]!=1 and texture_average is None:
        raise ValueError('Authored contrast requires authenticated original texture average')
    mode=ints.get('g_nTextureColorTintMode1')
    if (tint.shape!=(4,) or not np.isfinite(tint).all()
            or np.any(tint[:3]<0) or np.any(tint[:3]>1)
            or type(mode) is not int or mode not in (0,1,2)):
        raise ValueError('Only qualified finite tint modes0/1/2 supported')
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
    correction=(saturation_brightness(float(csb[1]),float(csb[2])) if csb[0]==1 else
                color_correction(csb[:3],texture_average))
    tint_matrix=color_tint(tint[:3],mode)
    return dict(correction_matrix=correction,tint_matrix=tint_matrix,
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
