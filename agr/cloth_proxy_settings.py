"""Explicit approximation settings for decompiled cloth proxies."""
from __future__ import annotations

import math


def proxy_gravity_settings(proxy, unit_scale, scene_gravity, use_gravity=True):
    """Calculate a candidate gravity weight for research, not export use.

    Blender exposes one gravity weight per cloth object. Nonuniform source
    paint needs another solver; averaging it would discard authored behavior.
    This is a nominal unit conversion only: native cloth timing and internal
    force scaling do not currently reproduce this acceleration in scene time.
    See research/verify_cloth_gravity.py. Do not enable on character exports
    until an absolute acceleration and moving-anchor check passes.
    """
    values = proxy.get('paint', {}).get('cloth_gravity$0')
    if not values or len(values) != len(proxy['vertices']):
        raise ValueError('Missing or mismatched authored cloth gravity paint')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('Authored cloth gravity must be finite')
    if any(v != values[0] for v in values):
        raise ValueError('Nonuniform authored cloth gravity requires per-node forces')
    if type(unit_scale) not in (int, float) or not math.isfinite(unit_scale) or unit_scale <= 0:
        raise ValueError('Invalid cloth unit scale')
    if len(scene_gravity) != 3 or not all(math.isfinite(v) for v in scene_gravity):
        raise ValueError('Invalid scene gravity')
    acceleration = values[0] * unit_scale
    if not use_gravity or abs(scene_gravity[0]) > 1e-8 or abs(scene_gravity[1]) > 1e-8 or scene_gravity[2] >= 0:
        raise ValueError('Authored cloth gravity requires enabled downward scene gravity')
    return {'mode': 'authored_uniform', 'source_acceleration': values[0],
            'target_acceleration_m_s2': acceleration, 'multiplier': acceleration / -scene_gravity[2],
            'validated_for_export': False}


def proxy_pin_weights(proxy: dict, mode: str = 'static_only') -> list[float]:
    """Map authored strength to Blender pins only when explicitly requested.

    Source goal forces and Blender pin springs are different solvers. This
    mapping is an artistic approximation, not a unit conversion between them.
    """
    if mode not in ('static_only', 'authored_strength'):
        raise ValueError(f'Unsupported proxy goal pin mapping: {mode}')
    count=len(proxy['vertices'])
    paint=proxy['paint']
    enable=paint['cloth_enable$0']
    strength=paint.get('cloth_goal_strength_v2$0') if mode=='authored_strength' else [0.0]*count
    if strength is None or len(enable)!=count or len(strength)!=count:
        raise ValueError('Missing or mismatched proxy pin paint')
    for values in (enable,strength):
        if any(type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<=1 for v in values):
            raise ValueError('Proxy pin paint must be finite values in [0, 1]')
    return [1.0 if enabled<=0 else float(goal) for enabled,goal in zip(enable,strength)]


def proxy_clock_settings(proxy, unit_scale, scene_gravity, fps, use_gravity=True):
    """Bounded clock correction for legacy Blender cloth, opt-in studies only.

    Native free-fall and animated static pins are verified in Blender 5.2.1.
    Power-of-two timing/quality avoids the extra floating-point substep seen
    with other combinations. This does not establish Source spring or damping
    equivalence. Do not generalize to arbitrary FPS without a native check.
    """
    if type(fps) not in (int, float) or fps not in (32, 64, 128):
        raise ValueError('Authored cloth clock is verified only at 32, 64 or 128 FPS')
    nominal = proxy_gravity_settings(proxy, unit_scale, scene_gravity, use_gravity)
    return {'mode': 'authored_clock_v1', 'fps': fps, 'quality': 32,
            'time_scale': 32 / fps, 'gravity_multiplier': nominal['multiplier'] * 1000 / 32**2,
            'target_acceleration_m_s2': nominal['target_acceleration_m_s2'],
            'source_acceleration': nominal['source_acceleration'],
            'validation_scope': 'Uniform free-fall and moving static pins; Source springs/damping/collision response unverified'}
