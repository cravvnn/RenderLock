"""Renderer inputs from actual per-system simulator snapshots.

Control points can contain scalar switches, not just world positions. Keep the
raw values and child overrides; a missing snapshot never means a zero input.
"""
from __future__ import annotations

import math
from .installed_effects import UnsupportedEffect, hermite
from .particle_control_provenance import confirmed_materialized_points

SNAPSHOT_POLICY = 'agr_visible_control_points_v1'


def finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise UnsupportedEffect('Non-finite or invalid particle renderer scalar')
    return float(value)


def render_contexts(simulation):
    """Index validated effective values by exact instance, child path and tick.

    Legacy packages can still render literal settings. Dynamic settings require
    the new helper's snapshots and the existing external/authored input proof.
    """
    policy = simulation.get('control_point_snapshot_policy')
    if policy is None:
        return {}
    if policy != SNAPSHOT_POLICY:
        raise UnsupportedEffect('Unknown particle render control-point snapshot policy')
    result = {}
    for instance in simulation['instances']:
        supplied = instance.get('control_point_indices')
        if (not isinstance(supplied, list) or not supplied
                or any(type(cp) is not int or not 0 <= cp <= 63 for cp in supplied)
                or supplied != sorted(set(supplied))
                or not confirmed_materialized_points(instance, supplied)):
            raise UnsupportedEffect('Renderer control points lack qualified input provenance')
        allowed = set(instance['materialized_control_point_indices'])
        for frame in instance['frames']:
            for system in frame['systems']:
                path = system.get('system_path')
                if not isinstance(path, str):
                    raise UnsupportedEffect('Renderer control points require exact child system paths')
                key = instance['id'], path, frame['tick']
                if key in result:
                    raise UnsupportedEffect('Duplicate particle renderer control-point snapshot')
                rows = system.get('control_points')
                if not isinstance(rows, list):
                    raise UnsupportedEffect('Missing particle renderer control-point snapshot; rebuild ParticleProbe')
                points = {}
                for row in rows:
                    if not isinstance(row, dict):
                        raise UnsupportedEffect('Invalid particle renderer control-point snapshot')
                    cp, position = row.get('index'), row.get('position')
                    if (type(cp) is not int or cp not in allowed or cp in points
                            or not isinstance(position, list) or len(position) != 3):
                        raise UnsupportedEffect('Invalid or unqualified particle renderer control point')
                    points[cp] = [finite(value) for value in position]
                age = finite(system.get('system_age'))
                if age < 0:
                    raise UnsupportedEffect('Invalid particle renderer system age')
                result[key] = dict(points=points, age=age)
    return result


def render_scalar(value, *, context=None, default=0, age=None):
    if value is None:
        return finite(default)
    if not isinstance(value, dict):
        return finite(value)
    kind = value.get('m_nType', 'PF_TYPE_LITERAL')
    if kind == 'PF_TYPE_LITERAL':
        result = finite(value.get('m_flLiteralValue', default))
    elif kind == 'PF_TYPE_COLLECTION_AGE':
        result = finite(context['age'] if context is not None else age)
    elif kind == 'PF_TYPE_CONTROL_POINT_COMPONENT':
        cp, component = value.get('m_nControlPoint', 0), value.get('m_nVectorComponent', 0)
        if type(cp) is not int or not 0 <= cp <= 63 or type(component) is not int or not 0 <= component <= 2:
            raise UnsupportedEffect('Invalid particle renderer control-point component')
        if context is None or cp not in context['points']:
            raise UnsupportedEffect('Missing qualified renderer control-point snapshot; rebuild ParticleProbe')
        result = finite(context['points'][cp][component])
    else:
        raise UnsupportedEffect('Scalar provider ' + str(kind))
    mode = value.get('m_nMapType', 'PF_MAP_TYPE_DIRECT')
    if mode == 'PF_MAP_TYPE_MULT':
        result *= finite(value.get('m_flMultFactor', 0))
    elif mode == 'PF_MAP_TYPE_REMAP':
        low, high, out_low, out_high = [finite(value.get(k, 0)) for k in
            ('m_flInput0', 'm_flInput1', 'm_flOutput0', 'm_flOutput1')]
        if low > high:
            low, high, out_low, out_high = high, low, out_high, out_low
        input_mode = value.get('m_nInputMode', 'PF_INPUT_MODE_CLAMPED')
        if input_mode == 'PF_INPUT_MODE_LOOPED':
            if high != 0:
                result = high * (result / high - math.floor(result / high))
        elif input_mode != 'PF_INPUT_MODE_CLAMPED':
            raise UnsupportedEffect('Unsupported particle renderer remap input mode')
        if low == high:
            result = out_high if result >= high else out_low
        else:
            amount = max(0., min(1., (result-low)/(high-low)))
            result = out_low + amount*(out_high-out_low)
    elif mode == 'PF_MAP_TYPE_CURVE':
        result = hermite(value.get('m_Curve', {}).get('m_spline', []), result)
    elif mode != 'PF_MAP_TYPE_DIRECT':
        raise UnsupportedEffect('Scalar mapping ' + str(mode))
    return finite(result)
