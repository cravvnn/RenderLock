"""Build-6774 static diffuse lightmap evaluation, excluding direct/specular light.

Derived from the installed deferred_lighting Vulkan compute shader, baked-lightmap
combo. Shader identity is pinned below; research lighting-20261011-03 retains GLSL.
Normals are normalized world-space Source axes. Undo any scene world yaw before
evaluation; uncorrected glTF/Blender-import axes are not Source axes.
Textures must be linearly filtered as data BEFORE coefficient reconstruction.
This is one lighting term, not a complete Source material/lighting evaluator.
"""
import numpy as np

SHADER_SHA256 = '629423a56c2ff637d6b14b1b74762efba0eb8eb3828ccefe2339c22b5c4567cc'
SHADER_RESOURCE = 'shaders/vfx/deferred_lighting_vulkan_50_cs.vcs'
SHADER_COMBO = 'D_BAKED_LIGHTING_FROM_LIGHTMAP=1'


def evaluate_diffuse(dc, directional, normal):
    """Evaluate RGB DC + encoded RGB-channel SH vectors for unit world normals.

    Shapes: dc (...,3), directional (...,3,3) [color channel, coefficient],
    normal (...,3). Leading shapes broadcast; invalid inputs fail explicitly.
    Returns linear RGB diffuse lighting before material response and occlusion.
    """
    dc, directional, normal = [np.asarray(v, dtype=np.float32) for v in (dc, directional, normal)]
    if dc.shape[-1:] != (3,) or directional.shape[-2:] != (3,3) or normal.shape[-1:] != (3,):
        raise ValueError('Expected RGB DC, 3x3 directional coefficients and XYZ normal')
    if not all(np.isfinite(v).all() for v in (dc, directional, normal)):
        raise ValueError('Lightmap inputs must be finite')
    if np.any(dc < 0) or np.any(directional < 0) or np.any(directional > 1):
        raise ValueError('Lightmap DC must be nonnegative and encoded coefficients in [0,1]')
    if np.any(abs(np.linalg.norm(normal, axis=-1)-1) > 1e-5):
        raise ValueError('Source lighting requires normalized world-space normals')
    coeff = ((directional * np.float32(2) - np.float32(1)) * dc[..., :, None]) * np.float32(1.7320507764816284)
    vectors = coeff[..., [2,0,1]] * np.asarray([-1,-1,1], dtype=np.float32)
    luminance_direction = np.sum(vectors * np.asarray([.2125999927520752,.7152000069618225,.0722000002861023], dtype=np.float32)[:,None], axis=-2)
    direction = luminance_direction / np.maximum(np.float32(1e-5), np.linalg.norm(luminance_direction, axis=-1, keepdims=True))
    ratio = abs(np.sum(vectors * direction[...,None,:], axis=-1) / np.maximum(np.float32(1e-5), dc))
    basis = normal[..., [1,2,0]] * np.asarray([-1,1,-1], dtype=np.float32) * np.float32(1.0233277082443237)
    linear = (dc * np.float32(.886227548122406) + np.sum(coeff * basis[...,None,:], axis=-1)) * np.float32(.31830987334251404)
    nd = np.sum(direction * normal, axis=-1)
    correction = (((dc * ratio) * (np.float32(.07999999821186066) + ratio * np.float32(.6000000238418579))) * np.float32(.25)) * (np.float32(.31539157032966614) * (np.float32(3)*nd*nd - np.float32(1)))[...,None]
    return np.maximum(np.float32(0), linear + correction)
