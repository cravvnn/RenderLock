"""Baked visibility term recovered from the qualified deferred lighting shader.

The stored shadow channel is occlusion: visibility is 1 minus that channel.
This term alone does not reconstruct direct lights, attenuation or material BRDF.
Callers must qualify the shader and non-gamma-encoded world shadow layout.
"""
import numpy as np


def baked_visibility(shadow_rgba, channel):
    if isinstance(channel, (bool, np.bool_)) or not isinstance(channel, (int, np.integer)) or not -1 <= channel <= 3:
        raise ValueError('Baked shadow channel must be -1 (no baked mask) or 0..3')
    values = np.asarray(shadow_rgba, dtype=np.float32)
    if values.ndim < 1 or values.shape[-1] != 4:
        raise ValueError('Baked shadow samples must have four channels')
    if not np.isfinite(values).all() or np.any((values < 0) | (values > 1)):
        raise ValueError('Baked shadow samples must be finite data in [0, 1]')
    if channel == -1:
        return np.ones(values.shape[:-1], dtype=np.float32)
    return np.float32(1) - values[..., channel]


def barn_baked_transmission(shadow_rgba, channel):
    """The barn branch multiplies light colour by visibility twice (not the sun)."""
    visibility = baked_visibility(shadow_rgba, channel)
    return visibility * visibility


def sample_linear_rgba(pixels, uv):
    """Sample normalized data with linear filtering and clamp-to-edge.

    pixels[y,x] and uv must use the same vertical origin. For Blender's bottom-up
    image pixels, use the imported UVs (Source2Viewer glTF V is flipped on import).
    Filter encoded shadow data before visibility inversion and barn squaring.
    """
    image = np.asarray(pixels)
    coordinates = np.asarray(uv, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 4 or min(image.shape[:2]) < 1:
        raise ValueError('Shadow image must be a nonempty height/width/RGBA array')
    if coordinates.ndim < 1 or coordinates.shape[-1] != 2 or not np.isfinite(coordinates).all():
        raise ValueError('Shadow UVs must have two finite components')
    if image.dtype != np.float32:
        raise ValueError('Shadow pixels must be float32 normalized data')
    height,width=image.shape[:2]
    # Clip before multiplying, avoiding overflow for finite out-of-range UVs.
    xy=np.clip(coordinates,0,1)*np.array([width,height],dtype=np.float32)-np.float32(.5)
    xy=np.maximum(xy,0);xy=np.minimum(xy,[width-1,height-1])
    lo=np.floor(xy).astype(np.int64);hi=np.minimum(lo+1,[width-1,height-1])
    weight=(xy-lo).astype(np.float32)
    a=image[lo[...,1],lo[...,0]];b=image[lo[...,1],hi[...,0]]
    c=image[hi[...,1],lo[...,0]];d=image[hi[...,1],hi[...,0]]
    samples=np.stack([a,b,c,d])
    if not np.isfinite(samples).all() or np.any((samples<0)|(samples>1)):
        raise ValueError('Sampled shadow texels must be finite data in [0, 1]')
    wx=weight[...,0,None];wy=weight[...,1,None]
    return (a*(1-wx)+b*wx)*(1-wy)+(c*(1-wx)+d*wx)*wy
