"""Recovered shader albedo ordering with explicit row-vector color matrices.

C45 NPR GLSL 294..317. Matrix construction and runtime vertex tint are caller
inputs, not inferred from authored scalar controls or viewer approximations.
"""
import numpy as np


def albedo_color(sampled, vertex_color, tint_mask, *, correction_matrix,
                 tint_matrix, vertex_strength, apply_tint_to_vertex_colors,
                 mask_vertex_tint, mask_color_tint):
    color=np.asarray(sampled,dtype=float)
    vertex=np.asarray(vertex_color,dtype=float)
    mask=np.asarray(tint_mask,dtype=float)
    correction=np.asarray(correction_matrix,dtype=float)
    tint=np.asarray(tint_matrix,dtype=float)
    if color.ndim!=2 or color.shape[1]!=3 or vertex.shape!=color.shape or mask.shape!=(len(color),):
        raise ValueError('Expected Nx3 colors and N tint mask')
    if correction.shape!=(4,4) or tint.shape!=(4,4):
        raise ValueError('Explicit 4x4 row-vector matrices required')
    if any(type(x) is not bool for x in (apply_tint_to_vertex_colors,mask_vertex_tint,mask_color_tint)):
        raise ValueError('Explicit tint mode booleans required')
    if np.ndim(vertex_strength) or not np.isfinite(vertex_strength) or not 0<=vertex_strength<=1:
        raise ValueError('Invalid vertex tint strength')
    if not all(np.isfinite(x).all() for x in (color,vertex,mask,correction,tint)) or np.any((mask<0)|(mask>1)):
        raise ValueError('Invalid albedo inputs')
    vertex_weight=(mask if mask_vertex_tint else np.ones(len(mask)))*vertex_strength
    def vertex_mix(c):
        return c*(1+(vertex-1)*vertex_weight[:,None])
    def transform(c,m):
        return np.column_stack((c,np.ones(len(c))))@m[:,:3]
    before=vertex_mix(color) if apply_tint_to_vertex_colors else color
    corrected=transform(before,correction)
    tinted=transform(corrected,tint)
    weight=(mask if mask_color_tint else np.ones(len(mask)))[:,None]
    result=corrected*(1-weight)+tinted*weight
    if not apply_tint_to_vertex_colors:result=vertex_mix(result)
    if not np.isfinite(result).all():raise ValueError('Albedo output exceeds finite domain')
    return result
