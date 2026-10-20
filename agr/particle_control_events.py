"""Recorded literal CP updates and lifecycle, separate from entity-provider proof."""
from __future__ import annotations

import math
import struct


def require_owned_event_provider(instance, controls, *, always=False):
    """New lifecycle support is qualified only for the exact owned-entity branch.

    Native world/unowned effects route immediate destruction differently. Never
    apply this owned stop policy to a request that merely supplies a position.
    """
    if not always and not controls['literal_transforms'] and controls['destroy'] is None:
        return
    index, generation, handle = (instance.get(field) for field in ('index', 'generation', 'entity_handle'))
    if (type(index) is not int or not 0 <= index < 2**31
            or type(generation) is not int or generation < 1
            or instance.get('id') != f'{index}:{generation}'
            or type(handle) is not int or not 0 <= handle < 0xffffffff
            or instance.get('entity_id') != f'{handle & 0x3fff}:{handle >> 14}'):
        raise ValueError('Recorded control/lifecycle requires exact particle generation and full entity identity')
    creation = instance['events'][0].get('data', {}).get('create_particle')
    if (not isinstance(creation, dict) or type(creation.get('entity_handle')) is not int
            or creation['entity_handle'] != handle or type(creation.get('attach_type')) is not int
            or creation['attach_type'] not in (2, 5) or creation.get('cluster', False) is not False):
        raise ValueError('Recorded control/lifecycle requires a qualified entity-owned creation provider')
    if ('resource_hash' in instance and creation.get('particle_name_index') != instance['resource_hash']):
        raise ValueError('Particle creation resource identity differs from its instance')


def _components(value, fields, *, default):
    if value is None:
        return list(default)
    if (not isinstance(value, dict) or set(value) - set(fields)
            or any(type(item) not in (int, float) or not math.isfinite(item) for item in value.values())):
        raise ValueError('Invalid literal particle transform components')
    try:
        return [struct.unpack('<f', struct.pack('<f', value.get(field, 0)))[0] for field in fields]
    except (OverflowError, struct.error) as exc:
        raise ValueError('Literal particle transform exceeds finite float32 components') from exc


def compile_control_events(instance, start, end):
    """Validate a complete event history without qualifying entity attachments.

    Literal UPDATE_TRANSFORM defaults/copies match the current offline native
    handler oracle. Interpolated updates remain rejected until their setter is
    separately qualified. RELEASE is ownership provenance and never death.
    Returned UPDATE_ENT events still require exact root/attachment/wearable
    providers before any simulation may be reported as rendered.
    """
    birth = instance.get('created_tick')
    if any(type(value) is not int for value in (birth, start, end)) or not 0 <= start <= birth <= end:
        raise ValueError('Particle controls require complete creation-time history')
    events = instance.get('events')
    if not isinstance(events, list) or not events:
        raise ValueError('Particle events are missing')
    allowed = {'CREATE', 'UPDATE_ENT', 'UPDATE_TRANSFORM', 'RELEASE', 'DESTROY'}
    previous_tick, previous_order = birth, None
    creates = 0
    literals, bindings, releases = [], [], []
    destroy = None
    for index, event in enumerate(events):
        if (not isinstance(event, dict) or event.get('type') not in allowed
                or type(event.get('tick')) is not int or not previous_tick <= event['tick'] <= end):
            raise ValueError('Unsupported or unordered particle lifecycle/control event')
        tick = event['tick']
        order = event.get('order')
        if order is not None:
            if type(order) is not int or (previous_order is not None and order <= previous_order):
                raise ValueError('Particle event order must increase strictly')
            previous_order = order
        previous_tick = tick
        kind = event['type']
        if kind == 'CREATE':
            creates += 1
            if index != 0 or tick != birth or creates != 1:
                raise ValueError('Particle requires one first creation event')
        elif not creates:
            raise ValueError('Particle control precedes creation')
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Invalid particle event payload')
        if 'index' in data and (type(data['index']) is not int or data['index'] != instance.get('index')):
            raise ValueError('Particle event index differs from its instance')
        if kind == 'UPDATE_TRANSFORM':
            if destroy is not None:
                raise ValueError('Particle transform changes after destruction require separate qualification')
            value = data.get('update_particle_transform')
            if not isinstance(value, dict) or set(value) - {'control_point', 'position', 'orientation', 'interpolation_interval'}:
                raise ValueError('Unsupported literal particle transform fields')
            cp = value.get('control_point')
            if type(cp) is not int or not 0 <= cp <= 63:
                raise ValueError('Literal particle control-point index is out of range')
            interval = value.get('interpolation_interval', 0)
            if type(interval) not in (int, float) or not math.isfinite(interval) or interval != 0:
                raise ValueError('Interpolated particle transforms require setter qualification')
            if any(field in value and value[field] is None for field in ('position', 'orientation')):
                raise ValueError('Invalid literal particle transform components')
            position = _components(value.get('position'), 'xyz', default=(0, 0, 0))
            rotation = _components(value.get('orientation'), 'xyzw', default=(0, 0, 0, 1))
            if abs(sum(item * item for item in rotation) - 1) > 2e-5:
                raise ValueError('Literal particle orientation must be a finite unit quaternion')
            literals.append(dict(tick=tick, control_point=cp, position=position, rotation=rotation,
                                 interpolation_interval=0))
        elif kind == 'UPDATE_ENT':
            if destroy is not None:
                raise ValueError('Particle attachment changes after destruction require separate qualification')
            bindings.append(event)
        elif kind == 'RELEASE':
            releases.append(tick)
        elif kind == 'DESTROY':
            value = data.get('destroy_particle')
            if (destroy is not None or not isinstance(value, dict)
                    or set(value) - {'destroy_immediately'}
                    or type(value.get('destroy_immediately', False)) is not bool):
                raise ValueError('Invalid or repeated particle destruction')
            destroy = dict(tick=tick, destroy_immediately=value.get('destroy_immediately', False))
    if creates != 1:
        raise ValueError('Particle requires one creation event')
    if instance.get('destroy_tick') != (destroy['tick'] if destroy else None):
        raise ValueError('Particle destroy summary differs from recorded events')
    if destroy and instance.get('destroy_immediately') is not destroy['destroy_immediately']:
        raise ValueError('Particle destroy mode differs from recorded events')
    if 'release_ticks' in instance and instance['release_ticks'] != releases:
        raise ValueError('Particle release summary differs from recorded events')
    return dict(schema_version=1, literal_transforms=literals, entity_binding_events=bindings,
                destroy=destroy, release_ticks=releases,
                provider_scope='Entity/root/custom-origin/wearable providers require separate qualification')
