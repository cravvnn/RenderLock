"""Read Source 2 particle messages and preserve recorded instance lifetimes.

Protocol field numbers follow the recovered usermessages.proto/networkbasetypes.proto
checkpoint. Only wire metadata is included; no game resources or generated protobuf
runtime are bundled. Simulation and resource interpretation are separate stages.
"""
from __future__ import annotations

import collections
import json
import math
from pathlib import Path
import struct
from typing import Any, Callable, Iterable, Mapping

from .vendor.resource_hash import resource_id

ENTRY_BITS = 14
ENTRY_MASK = (1 << ENTRY_BITS) - 1
INVALID_HANDLES = {0xFFFFFF, 0xFFFFFFFF}
_EVENT_PREFIX = "GAME_PARTICLE_MANAGER_EVENT_"

# (field name, protobuf scalar/message type, referenced message, repeated).
_SCHEMA = {'CUserMsg_ParticleManager': {1: ('type', 14, 'PARTICLE_MESSAGE', False),
                              2: ('index', 13, '', False),
                              3: ('release_particle_index',
                                  11,
                                  'CUserMsg_ParticleManager.ReleaseParticleIndex',
                                  False),
                              4: ('create_particle', 11, 'CUserMsg_ParticleManager.CreateParticle', False),
                              5: ('destroy_particle', 11, 'CUserMsg_ParticleManager.DestroyParticle', False),
                              6: ('destroy_particle_involving',
                                  11,
                                  'CUserMsg_ParticleManager.DestroyParticleInvolving',
                                  False),
                              7: ('update_particle',
                                  11,
                                  'CUserMsg_ParticleManager.UpdateParticle_OBSOLETE',
                                  False),
                              8: ('update_particle_fwd',
                                  11,
                                  'CUserMsg_ParticleManager.UpdateParticleFwd_OBSOLETE',
                                  False),
                              9: ('update_particle_orient',
                                  11,
                                  'CUserMsg_ParticleManager.UpdateParticleOrient_OBSOLETE',
                                  False),
                              10: ('update_particle_fallback',
                                   11,
                                   'CUserMsg_ParticleManager.UpdateParticleFallback',
                                   False),
                              11: ('update_particle_offset',
                                   11,
                                   'CUserMsg_ParticleManager.UpdateParticleOffset',
                                   False),
                              12: ('update_particle_ent',
                                   11,
                                   'CUserMsg_ParticleManager.UpdateParticleEnt',
                                   False),
                              14: ('update_particle_should_draw',
                                   11,
                                   'CUserMsg_ParticleManager.UpdateParticleShouldDraw',
                                   False),
                              15: ('update_particle_set_frozen',
                                   11,
                                   'CUserMsg_ParticleManager.UpdateParticleSetFrozen',
                                   False),
                              16: ('change_control_point_attachment',
                                   11,
                                   'CUserMsg_ParticleManager.ChangeControlPointAttachment',
                                   False),
                              17: ('update_entity_position',
                                   11,
                                   'CUserMsg_ParticleManager.UpdateEntityPosition',
                                   False),
                              18: ('set_particle_fow_properties',
                                   11,
                                   'CUserMsg_ParticleManager.SetParticleFoWProperties',
                                   False),
                              19: ('set_particle_text', 11, 'CUserMsg_ParticleManager.SetParticleText', False),
                              20: ('set_particle_should_check_fow',
                                   11,
                                   'CUserMsg_ParticleManager.SetParticleShouldCheckFoW',
                                   False),
                              21: ('set_control_point_model',
                                   11,
                                   'CUserMsg_ParticleManager.SetControlPointModel',
                                   False),
                              22: ('set_control_point_snapshot',
                                   11,
                                   'CUserMsg_ParticleManager.SetControlPointSnapshot',
                                   False),
                              23: ('set_texture_attribute',
                                   11,
                                   'CUserMsg_ParticleManager.SetTextureAttribute',
                                   False),
                              24: ('set_scene_object_generic_flag',
                                   11,
                                   'CUserMsg_ParticleManager.SetSceneObjectGenericFlag',
                                   False),
                              25: ('set_scene_object_tint_and_desat',
                                   11,
                                   'CUserMsg_ParticleManager.SetSceneObjectTintAndDesat',
                                   False),
                              26: ('destroy_particle_named',
                                   11,
                                   'CUserMsg_ParticleManager.DestroyParticleNamed',
                                   False),
                              27: ('particle_skip_to_time',
                                   11,
                                   'CUserMsg_ParticleManager.ParticleSkipToTime',
                                   False),
                              28: ('particle_can_freeze',
                                   11,
                                   'CUserMsg_ParticleManager.ParticleCanFreeze',
                                   False),
                              29: ('set_named_value_context',
                                   11,
                                   'CUserMsg_ParticleManager.SetParticleNamedValueContext',
                                   False),
                              30: ('update_particle_transform',
                                   11,
                                   'CUserMsg_ParticleManager.UpdateParticleTransform',
                                   False),
                              31: ('particle_freeze_transition_override',
                                   11,
                                   'CUserMsg_ParticleManager.ParticleFreezeTransitionOverride',
                                   False),
                              32: ('freeze_particle_involving',
                                   11,
                                   'CUserMsg_ParticleManager.FreezeParticleInvolving',
                                   False),
                              33: ('add_modellist_override_element',
                                   11,
                                   'CUserMsg_ParticleManager.AddModellistOverrideElement',
                                   False),
                              34: ('clear_modellist_override',
                                   11,
                                   'CUserMsg_ParticleManager.ClearModellistOverride',
                                   False),
                              35: ('create_physics_sim',
                                   11,
                                   'CUserMsg_ParticleManager.CreatePhysicsSim',
                                   False),
                              36: ('destroy_physics_sim',
                                   11,
                                   'CUserMsg_ParticleManager.DestroyPhysicsSim',
                                   False),
                              37: ('set_vdata', 11, 'CUserMsg_ParticleManager.SetVData', False),
                              38: ('set_material_override',
                                   11,
                                   'CUserMsg_ParticleManager.SetMaterialOverride',
                                   False),
                              39: ('add_fan', 11, 'CUserMsg_ParticleManager.AddFan', False),
                              40: ('update_fan', 11, 'CUserMsg_ParticleManager.UpdateFan', False),
                              41: ('set_particle_cluster_growth',
                                   11,
                                   'CUserMsg_ParticleManager.SetParticleClusterGrowth',
                                   False),
                              42: ('remove_fan', 11, 'CUserMsg_ParticleManager.RemoveFan', False),
                              43: ('create_smoke_grid', 11, 'CUserMsg_ParticleManager.CreateSmokeGrid', False)},
 'CUserMsg_ParticleManager.ReleaseParticleIndex': {},
 'CUserMsg_ParticleManager.CreateParticle': {1: ('particle_name_index', 6, '', False),
                                             2: ('attach_type', 5, '', False),
                                             3: ('entity_handle', 13, '', False),
                                             4: ('entity_handle_for_modifiers', 13, '', False),
                                             5: ('apply_voice_ban_rules', 8, '', False),
                                             6: ('team_behavior', 5, '', False),
                                             7: ('control_point_configuration', 9, '', False),
                                             8: ('cluster', 8, '', False),
                                             9: ('endcap_time', 2, '', False),
                                             10: ('aggregation_position', 11, 'CMsgVector', False)},
 'CUserMsg_ParticleManager.DestroyParticle': {1: ('destroy_immediately', 8, '', False)},
 'CUserMsg_ParticleManager.DestroyParticleInvolving': {1: ('destroy_immediately', 8, '', False),
                                                       3: ('entity_handle', 13, '', False)},
 'CUserMsg_ParticleManager.DestroyParticleNamed': {1: ('particle_name_index', 6, '', False),
                                                   2: ('entity_handle', 13, '', False),
                                                   3: ('destroy_immediately', 8, '', False),
                                                   4: ('play_endcap', 8, '', False)},
 'CUserMsg_ParticleManager.UpdateParticle_OBSOLETE': {1: ('control_point', 5, '', False),
                                                      2: ('position', 11, 'CMsgVector', False)},
 'CUserMsg_ParticleManager.UpdateParticleFwd_OBSOLETE': {1: ('control_point', 5, '', False),
                                                         2: ('forward', 11, 'CMsgVector', False)},
 'CUserMsg_ParticleManager.UpdateParticleOrient_OBSOLETE': {1: ('control_point', 5, '', False),
                                                            2: ('forward', 11, 'CMsgVector', False),
                                                            3: ('deprecated_right', 11, 'CMsgVector', False),
                                                            4: ('up', 11, 'CMsgVector', False),
                                                            5: ('left', 11, 'CMsgVector', False)},
 'CUserMsg_ParticleManager.UpdateParticleTransform': {1: ('control_point', 5, '', False),
                                                      2: ('position', 11, 'CMsgVector', False),
                                                      3: ('orientation', 11, 'CMsgQuaternion', False),
                                                      4: ('interpolation_interval', 2, '', False)},
 'CUserMsg_ParticleManager.UpdateParticleFallback': {1: ('control_point', 5, '', False),
                                                     2: ('position', 11, 'CMsgVector', False)},
 'CUserMsg_ParticleManager.UpdateParticleOffset': {1: ('control_point', 5, '', False),
                                                   2: ('origin_offset', 11, 'CMsgVector', False),
                                                   3: ('angle_offset', 11, 'CMsgQAngle', False)},
 'CUserMsg_ParticleManager.UpdateParticleEnt': {1: ('control_point', 5, '', False),
                                                2: ('entity_handle', 13, '', False),
                                                3: ('attach_type', 5, '', False),
                                                4: ('attachment', 5, '', False),
                                                5: ('fallback_position', 11, 'CMsgVector', False),
                                                6: ('include_wearables', 8, '', False),
                                                7: ('offset_position', 11, 'CMsgVector', False),
                                                8: ('offset_angles', 11, 'CMsgQAngle', False)},
 'CUserMsg_ParticleManager.UpdateParticleSetFrozen': {1: ('set_frozen', 8, '', False),
                                                      2: ('transition_duration', 2, '', False)},
 'CUserMsg_ParticleManager.UpdateParticleShouldDraw': {1: ('should_draw', 8, '', False)},
 'CUserMsg_ParticleManager.ChangeControlPointAttachment': {1: ('attachment_old', 5, '', False),
                                                           2: ('attachment_new', 5, '', False),
                                                           3: ('entity_handle', 13, '', False)},
 'CUserMsg_ParticleManager.UpdateEntityPosition': {1: ('entity_handle', 13, '', False),
                                                   2: ('position', 11, 'CMsgVector', False)},
 'CUserMsg_ParticleManager.SetParticleFoWProperties': {1: ('fow_control_point', 5, '', False),
                                                       2: ('fow_control_point2', 5, '', False),
                                                       3: ('fow_radius', 2, '', False)},
 'CUserMsg_ParticleManager.SetParticleShouldCheckFoW': {1: ('check_fow', 8, '', False)},
 'CUserMsg_ParticleManager.SetControlPointModel': {1: ('control_point', 5, '', False),
                                                   2: ('model_name', 9, '', False)},
 'CUserMsg_ParticleManager.SetControlPointSnapshot': {1: ('control_point', 5, '', False),
                                                      2: ('snapshot_name', 9, '', False)},
 'CUserMsg_ParticleManager.SetParticleText': {1: ('text', 9, '', False), 2: ('localize', 8, '', False)},
 'CUserMsg_ParticleManager.SetTextureAttribute': {1: ('attribute_name', 9, '', False),
                                                  2: ('texture_name', 9, '', False)},
 'CUserMsg_ParticleManager.SetSceneObjectGenericFlag': {1: ('flag_value', 8, '', False)},
 'CUserMsg_ParticleManager.SetSceneObjectTintAndDesat': {1: ('tint', 7, '', False), 2: ('desat', 2, '', False)},
 'CUserMsg_ParticleManager.ParticleSkipToTime': {1: ('skip_to_time', 2, '', False)},
 'CUserMsg_ParticleManager.ParticleCanFreeze': {1: ('can_freeze', 8, '', False)},
 'CUserMsg_ParticleManager.ParticleFreezeTransitionOverride': {1: ('freeze_transition_override', 2, '', False)},
 'CUserMsg_ParticleManager.FreezeParticleInvolving': {1: ('set_frozen', 8, '', False),
                                                      2: ('transition_duration', 2, '', False),
                                                      3: ('entity_handle', 13, '', False)},
 'CUserMsg_ParticleManager.AddModellistOverrideElement': {1: ('model_name', 9, '', False),
                                                          2: ('spawn_probability', 2, '', False),
                                                          3: ('groupid', 13, '', False)},
 'CUserMsg_ParticleManager.ClearModellistOverride': {1: ('groupid', 13, '', False)},
 'CUserMsg_ParticleManager.SetParticleNamedValueContext': {1: ('float_values',
                                                               11,
                                                               'CUserMsg_ParticleManager.SetParticleNamedValueContext.FloatContextValue',
                                                               True),
                                                           2: ('vector_values',
                                                               11,
                                                               'CUserMsg_ParticleManager.SetParticleNamedValueContext.VectorContextValue',
                                                               True),
                                                           3: ('transform_values',
                                                               11,
                                                               'CUserMsg_ParticleManager.SetParticleNamedValueContext.TransformContextValue',
                                                               True),
                                                           4: ('ehandle_values',
                                                               11,
                                                               'CUserMsg_ParticleManager.SetParticleNamedValueContext.EHandleContext',
                                                               True)},
 'CUserMsg_ParticleManager.SetParticleNamedValueContext.FloatContextValue': {1: ('value_name_hash',
                                                                                 13,
                                                                                 '',
                                                                                 False),
                                                                             2: ('value', 2, '', False)},
 'CUserMsg_ParticleManager.SetParticleNamedValueContext.VectorContextValue': {1: ('value_name_hash',
                                                                                  13,
                                                                                  '',
                                                                                  False),
                                                                              2: ('value',
                                                                                  11,
                                                                                  'CMsgVector',
                                                                                  False)},
 'CUserMsg_ParticleManager.SetParticleNamedValueContext.TransformContextValue': {1: ('value_name_hash',
                                                                                     13,
                                                                                     '',
                                                                                     False),
                                                                                 2: ('angles',
                                                                                     11,
                                                                                     'CMsgQAngle',
                                                                                     False),
                                                                                 3: ('translation',
                                                                                     11,
                                                                                     'CMsgVector',
                                                                                     False)},
 'CUserMsg_ParticleManager.SetParticleNamedValueContext.EHandleContext': {1: ('value_name_hash', 13, '', False),
                                                                          2: ('ent_index', 13, '', False)},
 'CUserMsg_ParticleManager.CreatePhysicsSim': {1: ('prop_group_name', 9, '', False),
                                               2: ('use_high_quality_simulation', 8, '', False),
                                               3: ('max_particle_count', 13, '', False)},
 'CUserMsg_ParticleManager.DestroyPhysicsSim': {},
 'CUserMsg_ParticleManager.CreateSmokeGrid': {1: ('vdata_name', 9, '', False)},
 'CUserMsg_ParticleManager.SetVData': {1: ('vdata_name', 9, '', False)},
 'CUserMsg_ParticleManager.SetMaterialOverride': {1: ('material_name', 9, '', False),
                                                  2: ('include_children', 8, '', False)},
 'CUserMsg_ParticleManager.AddFan': {1: ('active', 8, '', False),
                                     2: ('bounds_mins', 11, 'CMsgVector', False),
                                     3: ('bounds_maxs', 11, 'CMsgVector', False),
                                     4: ('fan_origin', 11, 'CMsgVector', False),
                                     5: ('fan_origin_offset', 11, 'CMsgVector', False),
                                     6: ('fan_direction', 11, 'CMsgVector', False),
                                     7: ('force', 2, '', False),
                                     8: ('fan_force_curve', 9, '', False),
                                     9: ('falloff', 8, '', False),
                                     10: ('pull_towards_point', 8, '', False),
                                     11: ('curve_min_dist', 2, '', False),
                                     12: ('curve_max_dist', 2, '', False),
                                     13: ('fan_type', 13, '', False),
                                     14: ('cone_start_radius', 2, '', False),
                                     15: ('cone_end_radius', 2, '', False),
                                     16: ('cone_length', 2, '', False),
                                     17: ('entity_handle', 13, '', False),
                                     18: ('attachment_name', 9, '', False)},
 'CUserMsg_ParticleManager.UpdateFan': {1: ('active', 8, '', False),
                                        2: ('fan_origin', 11, 'CMsgVector', False),
                                        3: ('fan_origin_offset', 11, 'CMsgVector', False),
                                        4: ('fan_direction', 11, 'CMsgVector', False),
                                        7: ('fan_ramp_ratio', 2, '', False),
                                        5: ('bounds_mins', 11, 'CMsgVector', False),
                                        6: ('bounds_maxs', 11, 'CMsgVector', False)},
 'CUserMsg_ParticleManager.RemoveFan': {},
 'CUserMsg_ParticleManager.SetParticleClusterGrowth': {1: ('duration', 2, '', False),
                                                       2: ('origin', 11, 'CMsgVector', False)},
 'CMsgVector': {1: ('x', 2, '', False), 2: ('y', 2, '', False), 3: ('z', 2, '', False), 4: ('w', 2, '', False)},
 'CMsgQAngle': {1: ('x', 2, '', False), 2: ('y', 2, '', False), 3: ('z', 2, '', False)},
 'CMsgQuaternion': {1: ('x', 2, '', False),
                    2: ('y', 2, '', False),
                    3: ('z', 2, '', False),
                    4: ('w', 2, '', False)}}

_EVENT_NAMES = {0: 'GAME_PARTICLE_MANAGER_EVENT_CREATE',
 1: 'GAME_PARTICLE_MANAGER_EVENT_UPDATE',
 2: 'GAME_PARTICLE_MANAGER_EVENT_UPDATE_FORWARD',
 3: 'GAME_PARTICLE_MANAGER_EVENT_UPDATE_ORIENTATION',
 4: 'GAME_PARTICLE_MANAGER_EVENT_UPDATE_FALLBACK',
 5: 'GAME_PARTICLE_MANAGER_EVENT_UPDATE_ENT',
 6: 'GAME_PARTICLE_MANAGER_EVENT_UPDATE_OFFSET',
 7: 'GAME_PARTICLE_MANAGER_EVENT_DESTROY',
 8: 'GAME_PARTICLE_MANAGER_EVENT_DESTROY_INVOLVING',
 9: 'GAME_PARTICLE_MANAGER_EVENT_RELEASE',
 10: 'GAME_PARTICLE_MANAGER_EVENT_LATENCY',
 11: 'GAME_PARTICLE_MANAGER_EVENT_SHOULD_DRAW',
 12: 'GAME_PARTICLE_MANAGER_EVENT_FROZEN',
 13: 'GAME_PARTICLE_MANAGER_EVENT_CHANGE_CONTROL_POINT_ATTACHMENT',
 14: 'GAME_PARTICLE_MANAGER_EVENT_UPDATE_ENTITY_POSITION',
 15: 'GAME_PARTICLE_MANAGER_EVENT_SET_FOW_PROPERTIES',
 16: 'GAME_PARTICLE_MANAGER_EVENT_SET_TEXT',
 17: 'GAME_PARTICLE_MANAGER_EVENT_SET_SHOULD_CHECK_FOW',
 18: 'GAME_PARTICLE_MANAGER_EVENT_SET_CONTROL_POINT_MODEL',
 19: 'GAME_PARTICLE_MANAGER_EVENT_SET_CONTROL_POINT_SNAPSHOT',
 20: 'GAME_PARTICLE_MANAGER_EVENT_SET_TEXTURE_ATTRIBUTE',
 21: 'GAME_PARTICLE_MANAGER_EVENT_SET_SCENE_OBJECT_GENERIC_FLAG',
 22: 'GAME_PARTICLE_MANAGER_EVENT_SET_SCENE_OBJECT_TINT_AND_DESAT',
 23: 'GAME_PARTICLE_MANAGER_EVENT_DESTROY_NAMED',
 24: 'GAME_PARTICLE_MANAGER_EVENT_SKIP_TO_TIME',
 25: 'GAME_PARTICLE_MANAGER_EVENT_CAN_FREEZE',
 26: 'GAME_PARTICLE_MANAGER_EVENT_SET_NAMED_VALUE_CONTEXT',
 27: 'GAME_PARTICLE_MANAGER_EVENT_UPDATE_TRANSFORM',
 28: 'GAME_PARTICLE_MANAGER_EVENT_FREEZE_TRANSITION_OVERRIDE',
 29: 'GAME_PARTICLE_MANAGER_EVENT_FREEZE_INVOLVING',
 30: 'GAME_PARTICLE_MANAGER_EVENT_ADD_MODELLIST_OVERRIDE_ELEMENT',
 31: 'GAME_PARTICLE_MANAGER_EVENT_CLEAR_MODELLIST_OVERRIDE',
 32: 'GAME_PARTICLE_MANAGER_EVENT_CREATE_PHYSICS_SIM',
 33: 'GAME_PARTICLE_MANAGER_EVENT_DESTROY_PHYSICS_SIM',
 34: 'GAME_PARTICLE_MANAGER_EVENT_SET_VDATA',
 35: 'GAME_PARTICLE_MANAGER_EVENT_SET_MATERIAL_OVERRIDE',
 36: 'GAME_PARTICLE_MANAGER_EVENT_ADD_FAN',
 37: 'GAME_PARTICLE_MANAGER_EVENT_UPDATE_FAN',
 38: 'GAME_PARTICLE_MANAGER_EVENT_SET_CLUSTER_GROWTH',
 39: 'GAME_PARTICLE_MANAGER_EVENT_REMOVE_FAN',
 40: 'GAME_PARTICLE_MANAGER_EVENT_CREATE_SMOKE_GRID'}


class ParticleEventError(RuntimeError):
    """Invalid replay packet framing or unsupported particle wire data."""


def _wire_functions():
    # The vendor reader already provides validated Source 2 packet framing.
    # Its snappy dependency is required only when extracting/decoding, not when
    # consuming a saved timeline in Blender.
    from .vendor.extract_animation_tables import Bits, one, varint, wire
    return Bits, one, varint, wire


def _decode_message(raw: bytes, name: str) -> dict[str, Any]:
    _, one, _, wire = _wire_functions()
    fields = wire(raw)
    result: dict[str, Any] = {}
    unknown = {}
    for number, values in fields.items():
        spec = _SCHEMA[name].get(number)
        if spec is None:
            unknown[str(number)] = [v.hex() if isinstance(v, bytes) else v for v in values]
            continue
        field_name, scalar_type, nested, repeated = spec
        if not repeated and len(values) != 1:
            raise ValueError(f"Duplicate singular particle field {name}.{field_name}")
        decoded = []
        for value in values:
            if scalar_type == 11:
                if not isinstance(value, bytes):
                    raise ValueError(f"Expected nested particle message {field_name}")
                value = _decode_message(value, nested)
            elif scalar_type == 9:
                if not isinstance(value, bytes):
                    raise ValueError(f"Expected particle string {field_name}")
                value = value.decode("utf-8")
            elif scalar_type == 2:
                if not isinstance(value, bytes) or len(value) != 4:
                    raise ValueError(f"Expected particle float32 {field_name}")
                value = struct.unpack("<f", value)[0]
                if not math.isfinite(value):
                    raise ValueError(f"Nonfinite particle float {field_name}")
            elif scalar_type in (6, 7):
                expected = 8 if scalar_type == 6 else 4
                if not isinstance(value, bytes) or len(value) != expected:
                    raise ValueError(f"Expected fixed-width particle integer {field_name}")
                value = int.from_bytes(value, "little")
                # Resource IDs remain decimal strings across JSON round trips.
                if scalar_type == 6:
                    value = str(value)
            else:
                if not isinstance(value, int):
                    raise ValueError(f"Expected particle varint {field_name}")
                if scalar_type == 8:
                    value = bool(value)
                elif scalar_type == 5:
                    value &= 0xFFFFFFFF
                    if value & 0x80000000:
                        value -= 0x100000000
                elif scalar_type == 14:
                    value = _EVENT_NAMES.get(value, value)
                elif scalar_type != 13:
                    raise ValueError(f"Unsupported particle scalar type {scalar_type}")
            decoded.append(value)
        result[field_name] = decoded if repeated else decoded[0]
    if unknown:
        result["_unknown_fields"] = unknown
    return result


def decode_particle_message(payload: bytes) -> dict[str, Any]:
    """Decode all known particle manager fields without generated protobuf code.

    Missing optional fields remain missing. Unknown event/field numbers are
    retained so a newer demo cannot silently appear fully supported.
    """
    try:
        result = _decode_message(payload, "CUserMsg_ParticleManager")
        result.setdefault("type", _EVENT_NAMES[0])
        result.setdefault("index", 0)
        return result
    except (ValueError, EOFError, UnicodeDecodeError, struct.error) as exc:
        raise ParticleEventError(f"Invalid particle message: {exc}") from exc


def _validate_range(start_tick: int, end_tick: int) -> None:
    if (isinstance(start_tick, bool) or isinstance(end_tick, bool)
            or not isinstance(start_tick, int) or not isinstance(end_tick, int)
            or not 0 <= start_tick <= end_tick < 0x7FFFFFFF):
        raise ParticleEventError("Particle tick range requires 0 <= start <= end < 2147483647.")


def iter_particle_events(demo: Path, end_tick: int,
                         progress: Callable[[str], None] | None = None):
    """Yield sequential particle events from signon through an inclusive end.

    Full packets are seek snapshots and deliberately not replayed a second time.
    Global order counts particle messages; same-tick ordering is never sorted.
    """
    _validate_range(0, end_tick)
    import snappy
    Bits, one, varint, wire = _wire_functions()
    demo = Path(demo)
    size = demo.stat().st_size
    order = packets = 0
    try:
        with demo.open("rb") as stream:
            header = stream.read(16)
            if len(header) != 16 or header[:8] != b"PBDEMS2\0":
                raise ParticleEventError("Expected a decompressed Source 2 PBDEMS2 demo.")
            while stream.tell() < size:
                offset = stream.tell()
                command, tick, length = varint(stream), varint(stream), varint(stream)
                if tick & 0x80000000:
                    tick -= 0x100000000
                if tick > end_tick:
                    break
                if length > size - stream.tell():
                    raise ParticleEventError(f"Truncated demo command at byte {offset}")
                kind, compressed = command & ~64, bool(command & 64)
                if kind not in (7, 8):
                    stream.seek(length, 1)
                    continue
                raw = stream.read(length)
                if compressed:
                    raw = snappy.decompress(raw)
                packet = wire(raw)
                bits = Bits(one(packet, 3, b""))
                packets += 1
                while bits.size - bits.p >= 14:
                    message_kind, message_size = bits.ubitvar(), bits.varint()
                    payload = bits.bytes(message_size)
                    if message_kind != 145:
                        continue
                    record = {"demo_tick": tick, "order": order,
                              "data": decode_particle_message(payload),
                              "protobuf_hex": payload.hex()}
                    order += 1
                    yield record
                if packets % 20000 == 0 and progress:
                    progress(f"Reading particle events: tick {tick:,}; {order:,} messages")
    except ParticleEventError:
        raise
    except Exception as exc:
        raise ParticleEventError(f"Particle extraction failed: {type(exc).__name__}: {exc}") from exc
    if progress:
        progress(f"Read {order:,} particle messages from {packets:,} sequential packets")


def _full_entity_ids(entities: Iterable[dict[str, Any]] | None) -> dict[int, str] | None:
    if entities is None:
        return None
    entities = list(entities)
    if any(e.get('model_track') is not None for e in entities):
        from .model_tracks import logical_entities
        try:
            entities = logical_entities(entities).values()
        except ValueError as error:
            raise ParticleEventError(str(error)) from error
    result = {}
    for entity in entities:
        entry, serial = entity.get("entity_index"), entity.get("entity_serial")
        if (isinstance(entry, bool) or isinstance(serial, bool)
                or not isinstance(entry, int) or not isinstance(serial, int)
                or not 0 <= entry <= ENTRY_MASK or not 0 <= serial < (1 << (32 - ENTRY_BITS))):
            raise ParticleEventError("Particle attachment mapping requires full entity index and serial.")
        handle = (serial << ENTRY_BITS) | entry
        identity = str(entity.get("id", f"{entry}:{serial}"))
        if handle in result and result[handle] != identity:
            raise ParticleEventError(f"Conflicting pawn identity for entity handle {handle}")
        if handle not in INVALID_HANDLES:
            result[handle] = identity
    return result


def _handles(value: Any) -> set[int]:
    """Find actual full handles, never entry-only aliases or resource hashes."""
    found: set[int] = set()
    if isinstance(value, dict):
        for name, child in value.items():
            if (name in ("entity_handle", "entity_handle_for_modifiers")
                    and isinstance(child, int) and not isinstance(child, bool)
                    and 0 <= child <= 0xFFFFFFFF and child not in INVALID_HANDLES):
                found.add(child)
            elif isinstance(child, (dict, list)):
                found.update(_handles(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_handles(child))
    return found


def extract_particle_events(demo: Path, start_tick: int, end_tick: int,
                            progress: Callable[[str], None] | None = None,
                            *, entities: Iterable[dict[str, Any]] | None = None,
                            output_path: Path | None = None) -> dict[str, Any]:
    """Extract raw messages, optionally retaining only selected pawn lifetimes.

    When entities are provided, keep a generation's preceding messages until an
    owner/control point identifies a selected full handle. This includes active
    creations from before the selected range and authored tails after release or
    non-immediate destruction. No arbitrary pre-roll horizon is imposed.
    """
    _validate_range(start_tick, end_tick)
    identities = _full_entity_ids(entities)
    records = []
    pending: dict[int, list[dict[str, Any]]] = {}
    selected: set[int] = set()
    counts: collections.Counter = collections.Counter()
    total = segment_count = 0
    for record in iter_particle_events(demo, end_tick, progress):
        total += 1
        data, tick = record["data"], record["demo_tick"]
        if start_tick <= tick <= end_tick:
            segment_count += 1
            counts[str(data["type"])] += 1
        if identities is None:
            records.append(record)
            continue
        index, kind = int(data.get("index", 0)), _event_kind(data)
        involving = kind in ("DESTROY_INVOLVING", "DESTROY_NAMED", "FREEZE_INVOLVING")
        if involving:
            if _handles(data) & identities.keys():
                records.append(record)
            continue
        if kind == "CREATE":
            pending[index] = []
            selected.discard(index)
        if index in selected:
            records.append(record)
        else:
            queue = pending.setdefault(index, [])
            queue.append(record)
            if _handles(data) & identities.keys():
                selected.add(index)
                records.extend(queue)
                pending.pop(index, None)
        if kind in ("DESTROY", "RELEASE"):
            pending.pop(index, None)
            # A RELEASE surrenders the control index; a later CREATE can reuse
            # it. Existing emitted particles remain in the retained generation.
            if kind == "DESTROY":
                selected.discard(index)
    records.sort(key=lambda row: row["order"])
    result = {"schema_version": 1, "start_tick": start_tick, "end_tick": end_tick,
              "scope": "Sequential network particle events; authored simulation is separate.",
              "selection": "full_entity_handles" if identities is not None else "all_network_particles",
              "particle_messages_through_end": total, "segment_messages": segment_count,
              "segment_event_counts": dict(counts), "retained_messages": len(records),
              "records": records}
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(result, separators=(",", ":"), allow_nan=False) + "\n",
                             encoding="utf-8")
        temporary.replace(path)
    return result


def _event_kind(data: dict[str, Any]) -> str:
    kind = data.get("type", _EVENT_NAMES[0])
    return kind.removeprefix(_EVENT_PREFIX) if isinstance(kind, str) else f"UNKNOWN_{kind}"


def particle_resource_lookup(resource_paths: Iterable[str] | Mapping[Any, Any]) -> dict[str, str]:
    """Resolve Source name hashes from an installed inventory or explicit hash map.

    GameAssetProvider.resources maps resource paths to VPKEntry objects. Its keys
    must be hashed just like an iterable of resource paths; those entries are not
    an already-resolved hash table. Explicit uint64-to-path maps are also accepted
    after checking each supplied hash against its normalized particle path.
    """
    lookup: dict[str, str] = {}
    items = (resource_paths.items() if isinstance(resource_paths, Mapping)
             else ((value, None) for value in resource_paths))
    for value, mapped_value in items:
        supplied_hash = None
        if (isinstance(resource_paths, Mapping) and not isinstance(value, bool)
                and str(value).isascii() and str(value).isdecimal()):
            supplied_hash = str(int(value))
            if not isinstance(mapped_value, str):
                raise ParticleEventError("Explicit particle resource hashes must map to .vpcf paths.")
            value = mapped_value
        path = str(value).replace("\\", "/").lower()
        if path.endswith(".vpcf_c"):
            path = path[:-2]
        if not path.endswith(".vpcf"):
            if supplied_hash is not None:
                raise ParticleEventError("Explicit particle resource hashes must map to .vpcf paths.")
            continue
        key = str(resource_id(path))
        if supplied_hash is not None and supplied_hash != key:
            raise ParticleEventError(f"Particle resource hash does not match its path: {path}")
        if key in lookup and lookup[key] != path:
            raise ParticleEventError(f"Particle resource hash collision: {lookup[key]} and {path}")
        lookup[key] = path
    return lookup


def resolve_particle_instance_resources(instances: Iterable[dict[str, Any]],
                                       resource_paths: Iterable[str] | Mapping[Any, Any]
                                       ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Repair saved unresolved names using the recorded uint64 without a rescan.

    The original instance/event mappings are never mutated. A name already
    resolved by an earlier installation remains available for coverage reporting
    even if that asset is absent from the currently installed inventory.
    """
    lookup = particle_resource_lookup(resource_paths)
    repaired = []
    newly_resolved = 0
    unresolved = collections.Counter()
    for instance in instances:
        item = dict(instance)
        if not item.get("resource"):
            name_hash = str(item.get("resource_hash", ""))
            path = lookup.get(name_hash)
            if path is not None:
                item["resource"] = path
                newly_resolved += 1
            else:
                unresolved[name_hash or "missing_recorded_hash"] += 1
        repaired.append(item)
    return repaired, {"instance_count": len(repaired), "newly_resolved": newly_resolved,
                      "resolved_after": len(repaired) - sum(unresolved.values()),
                      "unresolved_after": sum(unresolved.values()),
                      "unresolved_resource_ids": dict(sorted(unresolved.items()))}


def compile_particle_timeline(records: Iterable[dict[str, Any]],
                              resource_paths: Iterable[str] | Mapping[Any, Any],
                              start_tick: int, end_tick: int, *, tick_rate: float,
                              entities: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Normalize instances without assuming a match, hero list or simulation.

    RELEASE is not a destroy. Non-immediate pre-range destroys remain candidates
    because emitted particles may still live; the renderer must cull using their
    authored lifetimes. Every control-point vector retains original source units.
    """
    _validate_range(start_tick, end_tick)
    if isinstance(tick_rate, bool) or not isinstance(tick_rate, (int, float)) or not math.isfinite(tick_rate) or tick_rate <= 0:
        raise ParticleEventError("Particle timeline requires a positive finite replay tick rate.")
    lookup, identities = particle_resource_lookup(resource_paths), _full_entity_ids(entities)
    generations: collections.Counter = collections.Counter()
    active: dict[int, dict[str, Any]] = {}
    instances: list[dict[str, Any]] = []
    unresolved = []
    previous_order = -1
    previous_tick = -0x80000000
    parsed = 0
    for ordinal, record in enumerate(records):
        tick = int(record["demo_tick"])
        order = int(record.get("order", ordinal))
        if order <= previous_order or tick < previous_tick:
            raise ParticleEventError("Particle messages must retain sequential tick and event order.")
        previous_order, previous_tick = order, tick
        if tick > end_tick:
            continue
        parsed += 1
        data = record["data"]
        kind, index = _event_kind(data), int(data.get("index", 0))
        event = {"order": order, "tick": tick, "frame": tick - start_tick + 1,
                 "type": kind, "data": data}
        if kind == "CREATE":
            args = data.get("create_particle")
            if not isinstance(args, dict) or "particle_name_index" not in args:
                unresolved.append({"order": order, "tick": tick, "index": index,
                                   "type": kind, "reason": "create has no resource ID"})
                active.pop(index, None)
                continue
            if index in active:
                active[index]["index_reused_tick"] = tick
            generations[index] += 1
            handle = args.get("entity_handle")
            instance = {"id": f"{index}:{generations[index]}", "index": index,
                        "generation": generations[index], "resource": lookup.get(str(args["particle_name_index"])),
                        "resource_hash": str(args["particle_name_index"]),
                        "created_tick": tick, "created_frame": tick - start_tick + 1,
                        "entity_handle": handle, "entity_id": identities.get(handle) if identities is not None else None,
                        "create": args, "events": [], "destroy_tick": None,
                        "destroy_immediately": None, "release_ticks": [],
                        "_handles": _handles(args)}
            active[index] = instance
            instances.append(instance)
        elif kind in ("DESTROY_INVOLVING", "DESTROY_NAMED", "FREEZE_INVOLVING"):
            field = {"DESTROY_INVOLVING": "destroy_particle_involving",
                     "DESTROY_NAMED": "destroy_particle_named",
                     "FREEZE_INVOLVING": "freeze_particle_involving"}[kind]
            args = data.get(field, {})
            handle = args.get("entity_handle")
            for instance in list(active.values()):
                if handle not in instance["_handles"]:
                    continue
                if kind == "DESTROY_NAMED" and str(args.get("particle_name_index")) != instance["resource_hash"]:
                    continue
                instance["events"].append(event)
                if kind.startswith("DESTROY"):
                    instance["destroy_tick"] = tick
                    instance["destroy_immediately"] = bool(args.get("destroy_immediately", False))
                    active.pop(instance["index"], None)
            continue
        instance = active.get(index)
        if instance is None:
            unresolved.append({"order": order, "tick": tick, "index": index, "type": kind})
            continue
        instance["events"].append(event)
        instance["_handles"].update(_handles(data))
        if instance["entity_id"] is None and identities is not None and kind == "UPDATE_ENT":
            args = data.get("update_particle_ent", {})
            if args.get("control_point", 0) == 0:
                instance["entity_id"] = identities.get(args.get("entity_handle"))
        if kind == "RELEASE":
            instance["release_ticks"].append(tick)
        elif kind == "DESTROY":
            instance["destroy_tick"] = tick
            instance["destroy_immediately"] = bool(data.get("destroy_particle", {}).get("destroy_immediately", False))
            active.pop(index, None)
    relevant = []
    for instance in instances:
        handles = instance.pop("_handles")
        instance["involved_entity_handles"] = sorted(handles)
        instance["involved_entity_ids"] = sorted({identities[h] for h in handles if h in identities}) if identities is not None else []
        if identities is not None and not instance["involved_entity_ids"]:
            continue
        if instance["destroy_immediately"] and instance["destroy_tick"] < start_tick:
            continue
        instance["pre_range_tail_candidate"] = (instance["created_tick"] < start_tick
            and (instance["destroy_tick"] is not None or bool(instance["release_ticks"])))
        relevant.append(instance)
    return {"schema_version": 2, "start_tick": start_tick, "end_tick": end_tick,
            "tick_rate": tick_rate, "entity_entry_mask": ENTRY_MASK,
            "semantics": {"identity": "Full handles include entity serials; entry-only matches are rejected.",
                          "release": "Released control indices may be reused; emitted particles retain authored lifetimes.",
                          "destroy": "Non-immediate destroys stop emission and retain particle tails.",
                          "control_points": "Raw Source vectors; conversion is operator-specific.",
                          "pre_range_tails": "Conservative candidates; cull with actual authored lifetimes."},
            "instances": relevant, "unresolved_events": unresolved,
            "summary": {"parsed_messages": parsed, "all_created_instances": len(instances),
                        "selected_instances": len(relevant),
                        "selected_created_in_segment": sum(i["created_tick"] >= start_tick for i in relevant),
                        "resources": dict(collections.Counter(i["resource"] or "unresolved" for i in relevant))}}


def slice_particle_instances(instances, start_tick, end_tick, entities):
    """Keep pre-shot inputs, remove future events and rebase display frames.

    Instance/generation identities remain those from the original extraction.
    This does not infer whether an older emitted particle is still alive.
    """
    import copy
    _validate_range(start_tick, end_tick)
    identities = (_full_entity_ids(entities) if all('entity_index' in e and 'entity_serial' in e
                                                   for e in entities) else None)
    result = []
    for original in instances:
        if type(original.get('created_tick')) is not int:
            raise ParticleEventError('Particle birth must be an integer tick')
        if original['created_tick'] > end_tick:
            continue
        item = copy.deepcopy(original)
        item['created_frame'] = item['created_tick'] - start_tick + 1
        events = []
        for event in item.get('events', []):
            if type(event.get('tick')) is not int:
                raise ParticleEventError('Particle event must have an integer tick')
            if event['tick'] <= end_tick:
                event['frame'] = event['tick'] - start_tick + 1
                events.append(event)
        item['events'] = events
        for field in ('destroy_tick', 'index_reused_tick'):
            if item.get(field) is not None and item[field] > end_tick:
                item.pop(field, None)
                if field == 'destroy_tick':
                    item.update(destroy_tick=None, destroy_immediately=None)
        item['release_ticks'] = [tick for tick in item.get('release_ticks', []) if tick <= end_tick]
        item['pre_range_tail_candidate'] = (item['created_tick'] < start_tick
            and (item.get('destroy_tick') is not None or bool(item['release_ticks'])))
        handles = _handles(item.get('create', {}))
        for event in events:
            handles.update(_handles(event.get('data', {})))
        if identities is not None:
            item['involved_entity_handles'] = sorted(handles)
            item['involved_entity_ids'] = sorted({identities[h] for h in handles if h in identities})
            item['entity_id'] = identities.get(item.get('entity_handle'))
            if item['entity_id'] is None:
                for event in events:
                    binding = event.get('data', {}).get('update_particle_ent', {})
                    if event.get('type') == 'UPDATE_ENT' and binding.get('control_point', 0) == 0:
                        item['entity_id'] = identities.get(binding.get('entity_handle'))
                        if item['entity_id'] is not None:
                            break
        result.append(item)
    return result


def prune_expired_particle_instances(timeline: dict[str, Any],
                                     maximum_effect_age_seconds: Mapping[str, float]) -> dict[str, Any]:
    """Cull only effects with a caller-proven finite total duration from creation.

    Bounds must include emitter duration, all descendant emission delays and the
    longest authored particle/endcap lifetime. A particle's individual lifespan
    alone is NOT a valid bound for an emitter that can keep producing particles.
    Missing bounds preserve the instance; RELEASE never supplies a bound itself.
    The returned timeline is a new mapping and leaves its input unchanged.
    """
    bounds = {}
    for resource, duration in maximum_effect_age_seconds.items():
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration < 0):
            raise ParticleEventError(f"Invalid authored total-duration bound for {resource}")
        bounds[resource] = float(duration)
    kept = []
    culled: collections.Counter = collections.Counter()
    for instance in timeline["instances"]:
        duration = bounds.get(instance.get("resource"))
        if (duration is not None
                and instance["created_tick"] + duration * timeline["tick_rate"] < timeline["start_tick"]):
            culled[instance["resource"]] += 1
        else:
            kept.append(instance)
    result = dict(timeline)
    result["instances"] = kept
    result["summary"] = dict(timeline["summary"])
    result["summary"].update(selected_instances=len(kept),
                             resources=dict(collections.Counter(i["resource"] or "unresolved" for i in kept)),
                             authored_duration_culled_instances=sum(culled.values()),
                             authored_duration_culled_resources=dict(culled))
    return result
