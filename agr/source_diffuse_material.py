"""Diffuse material factor recovered from the qualified deferred shader.

The final light accumulation multiplies diffuse by albedo * (1 - metallic).
Specular, emissive and the indirect BRDF energy terms remain separate.
"""
import numpy as np


def diffuse_material(lighting, albedo, metallic):
    lighting=np.asarray(lighting,dtype=np.float32);albedo=np.asarray(albedo,dtype=np.float32)
    metallic=np.asarray(metallic,dtype=np.float32)
    if lighting.ndim<1 or lighting.shape[-1]!=3 or albedo.ndim<1 or albedo.shape[-1]!=3:
        raise ValueError('Diffuse lighting and albedo require RGB')
    for data in (lighting,albedo,metallic):
        if not np.isfinite(data).all() or np.any(data<0):raise ValueError('Invalid diffuse material inputs')
    if np.any(albedo>1) or np.any(metallic>1):raise ValueError('Albedo and metallic must be in [0,1]')
    return lighting*albedo*(np.float32(1)-metallic[...,None])
