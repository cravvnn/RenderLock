"""Conservative creation-relative expiry proofs for recorded particle trees.

Source: ValveResourceFormat Renderer/ParticleRenderer/{Particle.cs,
Emitters/InstantaneousEmitter.cs, Emitters/ContinuousEmitter.cs,
Initializers/InitFloat.cs, Initializers/RandomLifeTime.cs,
Operators/Decay.cs, Operators/FadeAndKill.cs, ParticleFunction.cs}.
A particle's Lifetime does not itself kill it. A normal, always-active decay
operator must be present. Release and destruction messages are deliberately not
inputs: release is not death, and endcap timing has no creation-relative bound.
"""
from __future__ import annotations

import math
import struct


# These initializers write spatial, appearance or velocity attributes only.
_SPATIAL_INITIALIZERS = {
    'C_INIT_CreateWithinSphereTransform', 'C_INIT_PositionOffset',
    'C_INIT_RandomSequence', 'C_INIT_RandomYawFlip', 'C_INIT_InitialVelocityNoise',
    'C_INIT_VelocityRandom',
    'C_INIT_NormalAlignToCP',
    'C_INIT_RingWave',
}
_SPATIAL_OPERATORS = {'C_OP_BasicMovement', 'C_OP_InterpolateRadius', 'C_OP_SpinUpdate'}
# Scalar outputs whose verified meanings cannot change age or life duration.
_APPEARANCE_FIELDS = {3, 4, 5, 7, 10, 12, 16}


class _Unknown(ValueError):
    pass


def _float32(value):
    try:
        result = struct.unpack('<f', struct.pack('<f', value))[0]
    except (OverflowError, struct.error) as exc:
        raise _Unknown('Overflowed single-precision lifetime') from exc
    if not math.isfinite(result):
        raise _Unknown('Non-finite single-precision lifetime')
    return result


def _adjacent32(value, upward):
    if value == 0:
        return 2**-149 if upward else -2**-149
    bits = struct.unpack('<I', struct.pack('<f', value))[0]
    bits += 1 if (value > 0) == upward else -1
    result = struct.unpack('<f', struct.pack('<I', bits))[0]
    if not math.isfinite(result):
        raise _Unknown('Unbounded single-precision interval')
    return result


def _outward32(low, high):
    a, b = _float32(low), _float32(high)
    return (_adjacent32(a, False) if a > low else a,
            _adjacent32(b, True) if b < high else b)


def _product32(left, right):
    values = [a*b for a in left for b in right]
    return _outward32(min(values), max(values))


def _simulation_range(value, default):
    low, high = _range(value, default)
    low, high = _float32(low), _float32(high)
    if isinstance(value, dict) and value.get('m_nType') in ('PF_TYPE_RANDOM_UNIFORM', 'PF_TYPE_RANDOM_BIASED'):
        # The qualified provider interpolates float endpoints with a [0,1]
        # draw. Enclose its subtraction/product/final rounding, including FMA.
        magnitude = max(abs(low), abs(high))
        padding = 4 * max(2**-149, 2.0**(math.frexp(magnitude)[1]-24))
        low, high = _outward32(max(0, low-padding) if low >= 0 else low-padding, high+padding)
    return low, high


def _blend_lifespan(old, target, strength):
    # Qualified .NET Single.Lerp IL: MultiplyAddEstimate(old, 1-strength,
    # target*strength). Outward intervals enclose fused and unfused execution.
    weight = _outward32(1-strength[1], 1-strength[0])
    left, right = _product32(old, weight), _product32(target, strength)
    return _outward32(left[0]+right[0], left[1]+right[1])


def _number(value):
    if type(value) not in (int, float):
        raise _Unknown('Invalid numeric field')
    value = float(value)
    if not math.isfinite(value):
        raise _Unknown('Non-finite numeric field')
    return value


def _range(value, default=0.0):
    if value is None:
        return (default, default)
    if not isinstance(value, dict):
        v = _number(value)
        return (v, v)
    if value.get('m_nMapType', 'PF_MAP_TYPE_DIRECT') != 'PF_MAP_TYPE_DIRECT':
        raise _Unknown('Dynamic scalar mapping')
    kind = value.get('m_nType', 'PF_TYPE_LITERAL')
    if kind == 'PF_TYPE_LITERAL':
        low = high = _number(value.get('m_flLiteralValue', default))
    elif kind in ('PF_TYPE_RANDOM_UNIFORM', 'PF_TYPE_RANDOM_BIASED'):
        low = _number(value.get('m_flRandomMin', 0))
        high = _number(value.get('m_flRandomMax', 1))
        if low > high:
            raise _Unknown('Inverted random interval')
    else:
        raise _Unknown('Dynamic scalar provider')
    if value.get('m_bHasRandomSignFlip'):
        high = max(abs(low), abs(high))
        low = -high
    return low, high


def _positive_max(value, default=0.0):
    low, high = _simulation_range(value, default)
    if low < 0:
        raise _Unknown('Negative time')
    return high


def _always(op):
    if op.get('m_bDisableOperator') or op.get('m_bDisable'):
        return False
    if op.get('m_nOpEndCapState', 'PARTICLE_ENDCAP_ALWAYS_ON') != 'PARTICLE_ENDCAP_ALWAYS_ON':
        return False
    strength = op.get('m_flOpStrength')
    if (isinstance(strength, dict) and strength.get('m_nType', 'PF_TYPE_LITERAL') != 'PF_TYPE_LITERAL'
            or _range(strength, 1) != (1, 1)):
        return False
    for key in ('m_flOpStartFadeInTime', 'm_flOpEndFadeInTime', 'm_flOpStartFadeOutTime',
                'm_flOpEndFadeOutTime', 'm_flOpFadeOscillatePeriod', 'm_flOpTimeOffsetMin',
                'm_flOpTimeOffsetMax'):
        if _number(op.get(key, 0)) != 0:
            return False
    for key in ('m_flOpTimeScaleMin', 'm_flOpTimeScaleMax'):
        if _number(op.get(key, 1)) != 1:
            return False
    return True


def _local_age(definition):
    if definition.get('_class') != 'CParticleSystemDefinition':
        raise _Unknown('Not a particle definition')
    # These settings can freeze or defer simulation while live particles remain
    # renderable. A lifetime bound on the simulation clock cannot cull them by
    # elapsed replay time. Qualify the ordinary, uninterrupted clock only.
    for key in ('m_flMinimumTimeStep', 'm_flMinimumSimTime', 'm_flMaximumSimTime',
                'm_flPreSimulationTime', 'm_flStopSimulationAfterTime'):
        if _number(definition.get(key, 0)) != 0:
            raise _Unknown('Deferred or frozen system simulation clock')
    if _number(definition.get('m_flMaximumTimeStep', .1)) not in (0, .1):
        raise _Unknown('Custom simulation step budget')
    if _number(definition.get('m_flMaxDrawDistance', -1)) > 0:
        raise _Unknown('Distance-gated child simulation')
    behavior = definition.get('m_nBehaviorVersion', 0)
    override = definition.get('m_nFirstMultipleOverride_BackwardCompat', -1)
    initial = definition.get('m_nInitialParticles', 0)
    if (any(type(v) is not int for v in (behavior, override, initial))
            or behavior < 0 or override < -1 or initial < 0):
        raise _Unknown('Invalid initializer scheduling metadata')
    # A control-point orientation change cannot alter literal/random time bounds.
    # Other pre-emission operations may alter simulation timing or emission.
    if any(op.get('_class') != 'C_OP_SetCPOrientationToPointAtCP'
           for op in definition.get('m_PreEmissionOperators', [])):
        raise _Unknown('Unknown pre-emission operation')
    if definition.get('m_ForceGenerators') or definition.get('m_Constraints'):
        raise _Unknown('Unverified external simulation operation')
    for key in ('m_flSimulationTimeScale', 'm_flConstantTimeScale'):
        if _range(definition.get(key), 1) != (1, 1):
            raise _Unknown('Changed simulation timescale')
    emitters = definition.get('m_Emitters', [])
    if not emitters:
        # Container roots have no own particles; the entire child tree is still
        # proven below, including children of otherwise empty roots.
        if initial or definition.get('m_Initializers') or definition.get('m_Operators'):
            raise _Unknown('Unverified emitterless simulation')
        return 0.0
    last_emission = 0.0
    for emitter in emitters:
        if not _always(emitter):
            raise _Unknown('Phase-dependent emitter')
        for key, value in emitter.items():
            if 'snapshot' in key.lower() and value not in (None, '', -1):
                raise _Unknown('Snapshot-driven emission')
        if _range(emitter.get('m_flInitFromKilledParentParticles'), 0) != (0, 0):
            raise _Unknown('Recurring parent-death emitter')
        if emitter.get('m_nMaxEmittedPerFrame', -1) != -1:
            raise _Unknown('Delayed per-frame burst budget')
        start = _positive_max(emitter.get('m_flStartTime'))
        kind = emitter.get('_class')
        if kind == 'C_OP_InstantaneousEmitter':
            end = start
        elif kind == 'C_OP_ContinuousEmitter':
            low, duration = _simulation_range(emitter.get('m_flEmissionDuration'), 0)
            if low <= 0:
                raise _Unknown('Continuous emission without finite duration')
            end = _outward32(start + duration, start + duration)[1]
        else:
            raise _Unknown('Unknown emitter')
        last_emission = max(last_emission, end)
    life_low, life = _simulation_range(definition.get('m_flConstantLifespan'), 1.0)
    if life_low < 0:
        raise _Unknown('Negative initial lifespan')
    life_written = False
    for index, init in enumerate(definition.get('m_Initializers', [])):
        if not _always(init):
            raise _Unknown('Phase-dependent initialization')
        kind = init.get('_class')
        if kind == 'C_INIT_InitFloat':
            field = init.get('m_nOutputField', 3)
            if type(field) is not int:
                raise _Unknown('Invalid scalar output attribute')
            if field in _APPEARANCE_FIELDS:
                continue
            if field != 1 or init.get('m_nSetMethod', 'PARTICLE_SET_REPLACE_VALUE') != 'PARTICLE_SET_REPLACE_VALUE':
                raise _Unknown('Unverified scalar initialization')
            if life_written and behavior < 6 and (override < 0 or index < override):
                continue
            lo, hi = _simulation_range(init.get('m_InputValue'), 0)
            slo, shi = _simulation_range(init.get('m_InputStrength'), 1)
            # InitFloat blends the current attribute toward the set-method
            # target. Strength is not an input multiplier. Keep both ends of
            # the old range and enclose actual single-precision operations.
            life_low, life = _blend_lifespan((life_low, life), (lo, hi), (slo, shi))
            if life_low < 0:
                raise _Unknown('Negative initialized lifespan')
            life_written = True
        elif kind == 'C_INIT_RandomLifeTime':
            if life_written and behavior < 6 and (override < 0 or index < override):
                continue
            # m_fLifetimeMax is the source spelling; unknown aliases are not
            # substituted for it; this initializer replaces the lifespan.
            minimum = _number(init.get('m_fLifetimeMin', 0))
            maximum = _number(init.get('m_fLifetimeMax', 0))
            if minimum < 0 or maximum < minimum:
                raise _Unknown('Inverted random lifetime')
            life_low, life = _simulation_range({'m_nType': 'PF_TYPE_RANDOM_UNIFORM',
                'm_flRandomMin': minimum, 'm_flRandomMax': maximum}, 0)
            life_written = True
        elif kind == 'C_INIT_RandomColor':
            if type(init.get('m_nFieldOutput', 6)) is not int or init.get('m_nFieldOutput', 6) != 6:
                raise _Unknown('Color initializer targets another attribute')
        elif kind not in _SPATIAL_INITIALIZERS:
            raise _Unknown('Unknown initializer')
    kill_bounds = []
    for op in definition.get('m_Operators', []):
        kind = op.get('_class')
        if kind == 'C_OP_Decay':
            if _always(op):
                kill_bounds.append(life)
        elif kind == 'C_OP_FadeAndKill':
            if _always(op):
                # Completing the alpha fade does not end the particle's life.
                # Never prune before its lifetime even when fade-out ends early.
                kill_bounds.append(max(life, max(life, 0.0001) * _positive_max(op.get('m_flEndFadeOutTime'), 1)))
        elif kind in _SPATIAL_OPERATORS:
            continue
        elif kind == 'C_OP_PositionLock':
            # The reference implementation can redirect both vector outputs.
            # Qualify the normal position/previous-position configuration only.
            if (type(op.get('m_nFieldOutput', 0)) is not int or op.get('m_nFieldOutput', 0) != 0
                    or type(op.get('m_nFieldOutputPrev', 2)) is not int or op.get('m_nFieldOutputPrev', 2) != 2):
                raise _Unknown('Unverified position-lock output attributes')
        elif kind == 'C_OP_ColorInterpolate':
            if type(op.get('m_nFieldOutput', 6)) is not int or op.get('m_nFieldOutput', 6) != 6:
                raise _Unknown('Color interpolation targets another attribute')
        elif kind in ('C_OP_SetFloat', 'C_OP_LerpScalar', 'C_OP_FadeOutSimple', 'C_OP_RemapSpeed', 'C_OP_RampScalarSpline'):
            field = (op.get('m_nOutputField', 3) if kind == 'C_OP_SetFloat' else
                     op.get('m_nField', 3) if kind == 'C_OP_RampScalarSpline' else
                     op.get('m_nFieldOutput', 3 if kind == 'C_OP_RemapSpeed' else 7))
            if type(field) is not int or field not in _APPEARANCE_FIELDS:
                raise _Unknown('Runtime scalar may change lifetime or age')
        else:
            raise _Unknown('Unknown runtime operation')
    if not kill_bounds:
        raise _Unknown('No always-active lifetime killer')
    bound = _outward32(last_emission + min(kill_bounds), last_emission + min(kill_bounds))[1]
    if not math.isfinite(bound):
        raise _Unknown('Overflowed lifetime')
    return bound


def maximum_effect_age(resource, get_definition):
    """Return a proven maximum age in seconds, or None if any branch is unknown.

    ``get_definition(path)`` supplies decoded VPCF dictionaries. The function
    never approximates unsupported operators or uses a fixed preroll limit.
    Returned bounds should prune only when the elapsed age is strictly greater
    than the bound; equality may still contain the last simulation sample.
    """
    active, memo = set(), {}

    def visit(path):
        if path in memo:
            return memo[path]
        if path in active or len(active) >= 64 or len(memo) >= 1024:
            raise _Unknown('Cyclic or excessive child tree')
        active.add(path)
        definition = get_definition(path)
        bound = _local_age(definition)
        for child in definition.get('m_Children', []):
            if child.get('m_bDisableChild'):
                continue
            if (child.get('m_bEndCap') or child.get('m_nEndCapState') not in (None, 0, 'PARTICLE_ENDCAP_ALWAYS_ON')
                    or child.get('m_bInheritParticle') or child.get('m_bUseParentParticles')):
                raise _Unknown('Endcap or parent-particle child scheduling')
            ref = child.get('m_ChildRef')
            if not isinstance(ref, str) or not ref:
                raise _Unknown('Missing child reference')
            delay = _positive_max(child.get('m_flDelay'))
            child_bound = delay + visit(ref)
            bound = max(bound, _outward32(child_bound, child_bound)[1])
        active.remove(path)
        memo[path] = bound
        return bound

    try:
        return visit(resource)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError, OverflowError, RecursionError):
        return None


def continuous_creation_clock(instance):
    """A freeze, seek or unknown event invalidates a creation-relative bound."""
    birth = instance.get('created_tick')
    events = instance.get('events')
    if type(birth) is not int or birth < 0 or not isinstance(events, list) or not events:
        return False
    safe = {'CREATE', 'UPDATE_ENT', 'UPDATE_TRANSFORM', 'RELEASE', 'DESTROY'}
    previous = birth
    creates = 0
    for event in events:
        if not isinstance(event, dict):
            return False
        tick = event.get('tick')
        if event.get('type') not in safe or type(tick) is not int or tick < previous:
            return False
        if event['type'] == 'CREATE':
            creates += 1
            if tick != birth:
                return False
        previous = tick
    return creates == 1 and events[0].get('type') == 'CREATE'


def partition_expired_history(instances, start_tick, tick_rate, get_definition):
    """Cull only proven old tails; preserve unknown history and its diagnostics.

    Release is never treated as death. Keep the boundary plus a full simulation
    tick for emission quantization, and never cull a clock with unhandled events.
    """
    if type(start_tick) is not int or start_tick < 0 or type(tick_rate) is not int or tick_rate <= 0:
        raise ValueError('Invalid particle expiry range or tick rate')
    retained, bounds, expired = [], {}, {}
    for instance in instances:
        birth = instance.get('created_tick')
        resource = instance.get('resource')
        if (type(birth) is int and birth < start_tick and isinstance(resource, str)
                and resource and continuous_creation_clock(instance)):
            if resource not in bounds:
                bounds[resource] = maximum_effect_age(resource, get_definition)
            bound = bounds[resource]
            if bound is not None and birth + bound * tick_rate + 1 < start_tick:
                expired[resource] = expired.get(resource, 0) + 1
                continue
        retained.append(instance)
    return retained, {
        'removed_instances': sum(expired.values()), 'retained_instances': len(retained),
        'expired_by_resource': expired,
        'proven_maximum_age_seconds': {name: age for name, age in bounds.items() if age is not None},
        'unknown_lifetimes': 'Retained; no guessed preroll horizon is applied',
        'clock_scope': 'Unmodified creation clock only; frozen, sought or unknown event histories are retained',
    }
