"""Versioned native-editor messages, separate from the camera path payload.

The editor owns configuration and acknowledgements. The game owns its status
and bounded event ring. No console command strings cross this interface.
"""
from __future__ import annotations

import math
import struct
from .editor_dof import ACTIONS as DOF_ACTIONS

CONFIG_OFFSET = 576
STATUS_OFFSET = 2 * 1024 * 1024 + 2048
EDITOR_ABI = 2
CONFIG_MAGIC = b"DLYEDIT1"
STATUS_MAGIC = b"DLYEDS01"
CONFIG = struct.Struct("<8s10I2d52H96s128s2diIdI2H2H2Bf4sBB")
VIDEO_FPS = (30, 60, 120, 300, 600)
VIDEO_BITRATE_MBPS = (10, 20, 40)
VIDEO_CODEC_MAX = 10
HEADER = struct.Struct("<8s8Id7d128sdiIQ")
EVENT = struct.Struct("<IId7diI")
EVENT_COUNT = 16
STATUS_BYTES = HEADER.size + EVENT.size * EVENT_COUNT
OWNERS = ("disabled", "flight", "panel", "game_ui", "console", "unfocused", "reshade")
EXTRA_ACTIONS = ("console", "set_speed", "select_view", "set_playback_speed", "set_playback_rate",
                 "reshade", "start_video", "stop_video", "set_video_fps", "set_video_bitrate",
                 "set_video_encoder", "set_video_fixed_step", "set_video_speed", "destroy_ragdolls",
                 "set_framing") + DOF_ACTIONS + ("set_video_depth", "set_video_depth_exr",
                 "set_video_layer_world", "set_video_layer_players", "set_video_layer_effects",
                 "toggle_citadel_glow", "toggle_healthbars", "near_player_opacity_fix",
                 "set_citadel_dof_enabled", "set_citadel_dof_sensor_size",
                 "set_citadel_dof_focus_distance",
                 "set_attach_target", "set_attach_point", "set_attach_offsets",
                 "set_attach_smoothing", "set_attach_hide", "attach_cycle_target",
                 "attach_cycle_point", "attach_reset",
                 "attach_preview", "attach_snap", "set_attach_bone", "set_source_blend", "seek_shot", "step_replay_ticks",
                 "reset_camera_path", "set_video_source", "set_pov_duration")
EXTRA_ACTIONS += ("set_confetti_enabled", "set_confetti_spawn_height",
                  "set_confetti_despawn_on_ground")
EXTRA_ACTIONS += ("open_bone_picker", "cancel_bone_picker", "finish_bone_picker")
EXTRA_ACTIONS += ("start_game_follow", "stop_game_follow")
EXTRA_ACTIONS += ("set_replay_hud",)
EXTRA_ACTIONS += ("select_camera", "view_camera", "delete_camera", "undo_shot", "redo_shot", "camera_page")
EXTRA_ACTIONS += ("set_camera_time", "set_camera_roll")
EXTRA_ACTIONS += ("framing_grid",)
EXTRA_ACTIONS += ("take_screenshot",)
# Object Picker actions are appended after every existing action, so the stable
# action-ID ABI is unchanged. Ids 99+ are reserved for this feature.
EXTRA_ACTIONS += ("object_picker_open", "object_picker_cancel", "object_place",
                  "object_select", "object_delete", "object_transform")
EXTRA_ACTIONS += ("save_shot", "seek_replay_tick", "clear_camera_path")
EXTRA_ACTIONS += ("focus_replay_player",)
EXTRA_ACTIONS += ("prepare_cinematic",)
EXTRA_ACTIONS += ("set_pause_after_capture",)
EXTRA_ACTIONS += ("set_camera_position",)
EXTRA_ACTIONS += ("set_camera_curve_value", "select_curve_camera")
# Action 114: value = camera * 12 + XYZ axis * 4 + Auto/Flat/Linked/Broken.
# pose = revision, incoming, outgoing, key time, session low/high uint32, zero.
# One completed gesture carries both derivatives, so Undo remains atomic.
EXTRA_ACTIONS += ("set_camera_position_tangents",)
EXTRA_ACTIONS += ("preview_camera_curve_value", "preview_camera_position_tangents",
                  "clear_camera_curve_preview")
# Appended actions preserve all earlier IDs and existing shared-memory layouts.
EXTRA_ACTIONS += ("level_view",)
# Selected authored camera: value = camera index; pose[0] = list revision (119).
EXTRA_ACTIONS += ("seek_camera",)
EXTRA_ACTIONS += ("set_video_ae_camera",)
EXTRA_ACTIONS += ("set_video_usd_camera",)
EXTRA_ACTIONS += ("set_attach_vertical_aim_lock",)
EXTRA_ACTIONS += ("export_renderlock", "cancel_renderlock")  # IDs 123, 124

# Optional completion signal in the unused mapping tail; existing offsets stay fixed.
CINEMATIC_OFFSET = 2 * 1024 * 1024 + 60000
CINEMATIC_STATUS = struct.Struct("<8s5I")

CAMERA_LIST_OFFSET = 2 * 1024 * 1024 + 23360
CAMERA_LIST_COUNT = 32
CAMERA_LIST_HEADER = struct.Struct("<8s8I")
CAMERA_LIST_ROW = struct.Struct("<3d2I")
CAMERA_LIST_BYTES = CAMERA_LIST_HEADER.size + CAMERA_LIST_COUNT * CAMERA_LIST_ROW.size

# Optional framing-guide block after the camera list; enabled state only.
FRAMING_GRID_OFFSET = 2 * 1024 * 1024 + 24424
FRAMING_GRID_ABI = 1
FRAMING_GRID_MAGIC = b"DLYGRID1"
FRAMING_GRID = struct.Struct("<8s4I")

# Optional live level-view block after the framing guide. Carries the binding
# plus the project's normal framing, so the native side can level the live
# paused camera without a round trip through Python.
LEVEL_VIEW_OFFSET = 2 * 1024 * 1024 + 24448
LEVEL_VIEW_ABI = 1
LEVEL_VIEW_MAGIC = b"DLYLEVL1"
LEVEL_VIEW = struct.Struct("<8s4Id")

# Experimental prop-persistence probe, appended after the object status block in
# the object page. Offsets must match dolly_editor.hpp exactly.
OBJECT_CONFIG_BYTES = 32 + 8 + 64 * 40          # EditorObjectConfig
OBJECT_STATUS_BYTES = 32                          # EditorObjectStatus
PROP_PROBE_OFFSET = 2 * 1024 * 1024 + 24576 + OBJECT_CONFIG_BYTES + OBJECT_STATUS_BYTES
PROP_PROBE_ABI = 1
PROP_PROBE_MAGIC = b"DLYPRP01"
PROP_PROBE_STATUS_MAGIC = b"DLYPRS01"
PROP_PROBE_CONFIG = struct.Struct("<8s4I")
PROP_PROBE_STATUS = struct.Struct("<8s4I" + "I" * 5 + "I" * 3 + "Q" + "Q" + "32s" + "I" * 6)
# request values
PROP_PROBE_CREATE = 1
PROP_PROBE_RECOUNT = 2


def pack_prop_probe(sequence, request):
    if request not in (PROP_PROBE_CREATE, PROP_PROBE_RECOUNT):
        raise ValueError("Prop probe request must be create or recount")
    return PROP_PROBE_CONFIG.pack(PROP_PROBE_MAGIC, _uint(sequence, "sequence"),
                                  PROP_PROBE_ABI, int(request), 0)


def unpack_prop_probe_status(data):
    if len(data) != PROP_PROBE_STATUS.size:
        raise ValueError("Prop probe status returned the wrong size")
    if not any(data):
        return None
    fields = PROP_PROBE_STATUS.unpack(data)
    magic, sequence, abi, state, resolved = fields[:5]
    if magic != PROP_PROBE_STATUS_MAGIC or abi != PROP_PROBE_ABI or sequence & 1:
        raise ValueError("Prop probe status does not match this build")
    return {"sequence": sequence, "state": state, "resolved": resolved,
            "before_count": fields[5], "after_count": fields[6],
            "entity_handle": fields[7], "calls": fields[8], "error": fields[9],
            "alive_after_create": fields[10], "alive_after_seek": fields[11],
            "entity_index": fields[12],
            "entity_ptr": fields[13], "identity_ptr": fields[14],
            "entity_class": fields[15].split(b"\0", 1)[0].decode("ascii", "replace")}


def pack_framing_grid(sequence, enabled, binding=None):
    """Guide switch plus its binding in the reserved field (vk | mods << 16)."""
    if type(enabled) is not bool:
        raise ValueError("Framing guide switch must be a boolean")
    if binding is None:
        reserved = 0
    else:
        from .editor_actions import EditorBinding
        if not isinstance(binding, EditorBinding):
            raise ValueError("Framing guide binding must be an EditorBinding or None")
        reserved = binding.vk | binding.modifiers << 16
    return FRAMING_GRID.pack(FRAMING_GRID_MAGIC, _uint(sequence, "sequence"), FRAMING_GRID_ABI,
                             int(enabled), reserved)


def pack_level_view(sequence, binding=None, standard_aspect=None):
    """Level-view binding plus the project's normal framing.

    ``standard_aspect`` is the project's normal aspect ratio; native level view
    returns the live paused camera to it. ``binding`` travels in the reserved
    field (vk | mods << 16) exactly like the framing guide.
    """
    if binding is None:
        reserved = 0
    else:
        from .editor_actions import EditorBinding
        if not isinstance(binding, EditorBinding):
            raise ValueError("Level view binding must be an EditorBinding or None")
        reserved = binding.vk | binding.modifiers << 16
    aspect = 0.0 if standard_aspect is None else float(standard_aspect)
    if aspect and (not math.isfinite(aspect) or not .5 <= aspect <= 4):
        raise ValueError("Level view framing must be between 0.5 and 4")
    return LEVEL_VIEW.pack(LEVEL_VIEW_MAGIC, _uint(sequence, "sequence"), LEVEL_VIEW_ABI,
                           int(binding is not None), reserved, aspect)


def pack_camera_list(sequence, project, revision, first=0, *, can_undo=False, can_redo=False):
    revision = _uint(revision, 'camera list revision')
    first = _uint(first, 'camera list page')
    total = len(project.keyframes)
    if (not revision or first > max(0, total - 1)
            or first % CAMERA_LIST_COUNT and first != max(0, total - CAMERA_LIST_COUNT)):
        raise ValueError('Invalid camera list page or revision')
    keys = project.keyframes[first:first + CAMERA_LIST_COUNT]
    flags = int(bool(can_undo)) | int(bool(can_redo)) << 1
    data = bytearray(CAMERA_LIST_BYTES)
    CAMERA_LIST_HEADER.pack_into(data, 0, b'DLYCAMS1', _uint(sequence, 'sequence'),
                                 1, revision, total, first, len(keys), flags, 0)
    from .path import channel_value
    for index, key in enumerate(keys):
        roll = channel_value(key, 'roll')
        if (not all(math.isfinite(v) for v in (key.time, key.aspect_ratio, roll))
                or key.time < 0 or not .5 <= key.aspect_ratio <= 4
                or key.source not in ('free', 'attach')
                or index and key.time <= keys[index - 1].time):
            raise ValueError('Invalid camera list row')
        CAMERA_LIST_ROW.pack_into(data, CAMERA_LIST_HEADER.size + index * CAMERA_LIST_ROW.size,
                                  key.time, key.aspect_ratio, roll, int(key.source == 'attach'), 0)
    return bytes(data)

DOF_OFFSET = 2 * 1024 * 1024 + 3712
DOF_CONFIG = struct.Struct("<8s4I11d")
CITADEL_DOF_OFFSET = DOF_OFFSET + DOF_CONFIG.size
CITADEL_DOF = struct.Struct("<8s4I2d")
ATTACH_OFFSET = CITADEL_DOF_OFFSET + CITADEL_DOF.size
ATTACH_CONFIG = struct.Struct("<8s4IQ8I6I7d3IQ64s")
from ._runtime_generated import ATTACH_WIRE_FIELDS as ATTACH_FIELDS
ATTACH_NO_TARGET = 0xFFFFFFFF
ATTACH_OFFSET_LIMIT = 10000.0
ROSTER_OFFSET = 2 * 1024 * 1024 + 4096
ROSTER_ABI = 1
ROSTER_PLAYERS = 16
ROSTER_HEADER = struct.Struct("<8s4I")
ROSTER_ENTRY = struct.Struct("<2IQ96s")
ROSTER_BYTES = ROSTER_HEADER.size + ROSTER_ENTRY.size * ROSTER_PLAYERS
ATTACH_RESULT_OFFSET = ROSTER_OFFSET + ROSTER_BYTES
ATTACH_RESULT_ABI = 1
ATTACH_RESULT = struct.Struct("<8s4I4IQ6dd")
BONES_OFFSET = ATTACH_RESULT_OFFSET + ATTACH_RESULT.size
BONES_HEADER = struct.Struct("<8s4IQ2I")
BONES_COUNT = 256
BONES_BYTES = BONES_HEADER.size + BONES_COUNT * 64
PICKER_OFFSET = 2 * 1024 * 1024 + 22528
PICKER_RESULT = struct.Struct("<8s6IQiI64s7d")
FOLLOW_OFFSET = 2 * 1024 * 1024 + 23040
FOLLOW_CONFIG = struct.Struct("<8s4I3d")


def pack_follow(sequence, settings, *, available=False, active=False, pending=False, show_hud=False):
    from .follow_camera import FollowSettings
    if not isinstance(settings, FollowSettings):
        raise ValueError("Invalid Game Follow settings")
    settings.values()
    if any(type(value) is not bool for value in (available, active, pending, show_hud)):
        raise ValueError("Game Follow switches must be booleans")
    flags = int(available) | int(active) << 1 | int(pending) << 2 | int(show_hud) << 3
    return FOLLOW_CONFIG.pack(b'DLYFOLL1', _uint(sequence, 'sequence'), 1, flags, 0,
                              settings.distance, settings.shoulder, settings.height)


def unpack_picker(data):
    if len(data) != PICKER_RESULT.size:
        raise ValueError("Invalid Bone Picker result size")
    if not any(data):
        return None
    (magic, sequence, abi, flags, request, handle, entity, model, selected, total,
     raw, *original) = PICKER_RESULT.unpack(data)
    import re
    name = raw.split(b"\0", 1)[0].decode("ascii")
    if (magic != b"DLYPICK1" or abi != 1 or sequence & 1 or flags & ~15 or
            not -1 <= selected < 4096 or not 0 <= total <= 4096 or
            any(not math.isfinite(value) for value in original) or
            (name and not re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_.]{0,63}|\$cloth_m[0-9]+p[0-9]+)", name))):
        raise ValueError("Invalid Bone Picker result")
    return dict(sequence=sequence, request=request, active=bool(flags & 1),
                ready=bool(flags & 2), preview=bool(flags & 4), finishing=bool(flags & 8),
                handle=handle, entity_id=entity, model=model, selected=selected,
                total=total, name=name, original_pose=tuple(original))


def unpack_bones(data):
    import re
    if len(data) != BONES_BYTES:
        raise ValueError("Native bone picker returned the wrong size")
    magic, sequence, abi, handle, entity_id, model, count, total = BONES_HEADER.unpack_from(data)
    if magic != b"DLYBONE1" or abi != 1 or sequence & 1 or not count <= BONES_COUNT or not count <= total <= 4096:
        raise ValueError("Native bone picker protocol does not match this build")
    names = []
    for index in range(count):
        raw = data[BONES_HEADER.size + index * 64:BONES_HEADER.size + (index + 1) * 64]
        name = raw.split(b"\0", 1)[0].decode("ascii")
        if not re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_.]{0,63}|\$cloth_m[0-9]+p[0-9]+)", name):
            raise ValueError("Native bone picker returned an invalid name")
        names.append(name)
    return {"sequence": sequence, "handle": handle, "entity_id": entity_id,
            "model": model, "names": names, "total": total}


def unpack_attach_result(data):
    """Parse the native attach result (snap / attached-fly offsets)."""
    if len(data) != ATTACH_RESULT.size:
        raise ValueError("Native attach result returned the wrong size")
    (magic, sequence, abi, flags, _reserved, handle, entity_index, point, _reserved2, model,
     *numbers) = ATTACH_RESULT.unpack(data)
    if magic != b"DLYATR01" or abi != ATTACH_RESULT_ABI or flags & ~1:
        raise ValueError("Native attach result protocol does not match this build")
    if point > 2 or any(not math.isfinite(value) for value in numbers) or any(abs(value) > ATTACH_OFFSET_LIMIT for value in numbers[:6]) or not 0 <= numbers[6] <= 5:
        raise ValueError("Native attach result contains an invalid pose")
    return {"sequence": sequence, "valid": bool(flags & 1), "handle": handle,
            "config_sequence": _reserved,
            "entity_index": entity_index, "point": point, "model": model,
            "offset": tuple(numbers[:6]), "smoothing": numbers[6]}


def pack_attach(sequence, offsets, attach=None, preview=False, snap_request=0, picker=False):
    """Attach-camera block: schema offsets plus the selected key's attach state.

    Every offset comes from the live schema query, so the provider never
    hardcodes a layout. ``attach`` is the selected camera key's AttachKey data
    (or None for a free key) and drives the in-game card display.
    """
    values = []
    for name in ATTACH_FIELDS:
        if name not in offsets:
            raise ValueError("Missing attach camera field: " + name)
        value = _uint(offsets[name], "attach " + name)
        if not 8 <= value <= 0x8000:
            raise ValueError("Attach camera field is outside the reviewed range: " + name)
        values.append(value)
    flags = 1
    model = 0
    bone_hash = 0
    bone_name = b""
    handle = entity_id = target_index = point = hide = 0
    attached_keys = key_count = blend_ms = 0
    numbers = [0.0] * 7
    if attach is not None:
        if attach.get("selected", True):
            flags |= 2
        if preview and flags & 2:
            flags |= 4
        if picker and flags & 2:
            flags |= 8
        blend_ms = round(_finite(attach.get("source_blend", 0), 0, 10, "source blend") * 1000)
        handle = _uint(attach.get("handle", 0), "attach handle")
        entity_id = _uint(attach.get("entity_id", 0), "attach entity id")
        target_index = _uint(attach.get("target_index", ATTACH_NO_TARGET), "attach target index")
        if target_index != ATTACH_NO_TARGET and target_index >= ROSTER_PLAYERS:
            raise ValueError("Attach target index is out of range")
        point = _uint(attach.get("point", 0), "attach point")
        if point > 2:
            raise ValueError("Unknown attach point")
        bone = attach.get("bone", "")
        if point == 2:
            import re
            from .native_effects import model_token
            if not isinstance(bone, str) or len(bone) > 64 or not re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_.]{0,63}|\$cloth_m[0-9]+p[0-9]+)", bone):
                raise ValueError("Choose a valid bone name")
            bone_name = bone.encode("ascii")
            bone_hash = model_token(bone)
        elif bone:
            raise ValueError("A bone name requires the Bone attach point")
        hide = 1 if attach.get("hide_body", True) else 0
        mode = attach.get("clearance_mode", "exact")
        if mode not in ("exact", "auto"):
            raise ValueError("Unknown attach clearance mode")
        hide |= 2 if mode == "auto" else 0
        orientation_mode = attach.get("orientation_mode", "legacy")
        if orientation_mode not in ("legacy", "rigid"):
            raise ValueError("Unknown attach orientation mode")
        hide |= 4 if orientation_mode == "rigid" else 0
        lock_vertical_aim = attach.get("lock_vertical_aim", False)
        if type(lock_vertical_aim) is not bool:
            raise ValueError("Vertical aim lock must be true or false")
        hide |= 8 if lock_vertical_aim else 0
        model = attach.get("model", 0)
        if (isinstance(model, bool) or not isinstance(model, int)
                or not 0 <= model <= 0xFFFFFFFFFFFFFFFF):
            raise ValueError("Invalid native editor attach model")
        offsets_value = attach.get("offset") or (0.0,) * 6
        if len(offsets_value) != 6:
            raise ValueError("Attach offsets need six numbers")
        for index, value in enumerate(offsets_value):
            numbers[index] = _finite(value, -ATTACH_OFFSET_LIMIT, ATTACH_OFFSET_LIMIT,
                                     "attach offset")
        numbers[6] = _finite(attach.get("smoothing", 0.0), 0, 5, "attach smoothing")
        attached_keys = _uint(attach.get("attached_keys", 0), "attach key count")
        key_count = _uint(attach.get("key_count", 0), "shot key count")
        if attached_keys > key_count or key_count > 100000:
            raise ValueError("Attach key counts are inconsistent")
    return ATTACH_CONFIG.pack(b"DLYATTC1", _uint(sequence, "sequence"), 6 if hide & 12 else 5 if hide & 2 else 4 if picker else (3 if blend_ms else 2), flags, 0, model,
                              *values, handle, entity_id, target_index, point, hide, blend_ms,
                              *numbers, attached_keys, key_count,
                              _uint(snap_request, "snap request"), bone_hash, bone_name)


def unpack_roster(data):
    """Parse the native live player roster for the attach target picker."""
    if len(data) != ROSTER_BYTES:
        raise ValueError("Native attach roster returned the wrong size")
    magic, _sequence, abi, count, flags = ROSTER_HEADER.unpack_from(data)
    if magic != b"DLYROS01" or abi != ROSTER_ABI or count > ROSTER_PLAYERS or flags & ~1:
        raise ValueError("Native attach roster protocol does not match this build")
    players = []
    for index in range(count):
        handle, entity_index, model, path = ROSTER_ENTRY.unpack_from(
            data, ROSTER_HEADER.size + index * ROSTER_ENTRY.size)
        players.append({"handle": handle, "entity_index": entity_index, "model": model,
                        "model_path": path.split(b"\0", 1)[0].decode("utf-8", errors="replace")})
    return {"available": bool(flags & 1), "players": players}


def pack_dof(sequence, enabled, values):
    if len(values) != 11 or not isinstance(enabled, bool):
        raise ValueError("Invalid native DOF settings")
    checked = [_finite(value, -3.4028234663852886e38, 3.4028234663852886e38, "DOF value")
               for value in values]
    if any(value not in (0, 1) for value in checked[:2]):
        raise ValueError("Invalid native DOF switch")
    return DOF_CONFIG.pack(b"DLYDOF01", _uint(sequence, "sequence"), 1, int(enabled), 0, *checked)


def pack_citadel_dof(sequence, available, enabled, sensor_size, focus_distance):
    """Optional Citadel DOF block; appended after the native DOF block."""
    if not isinstance(available, bool) or not isinstance(enabled, bool):
        raise ValueError("Invalid Citadel DOF switch")
    return CITADEL_DOF.pack(b"DLYCDOF1", _uint(sequence, "sequence"), 1, int(available),
                            int(enabled), _finite(sensor_size, .5, 3, "sensor size"),
                            _finite(focus_distance, 0, 10000, "focus distance"))


def _text(value, capacity):
    return str(value).replace("\0", "").encode("utf-8")[:capacity - 1].decode("utf-8", errors="ignore").encode("utf-8")


def _finite(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Invalid native editor {name}")
    return float(value)


def _uint(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xffffffff:
        raise ValueError(f"Invalid native editor {name}")
    return value


def pack_config(sequence, owner_sequence, ack_event, values):
    from .editor_actions import ACTION_ORDER, default_action_bindings, validate_action_bindings
    from .settings import DEFAULT_RESHADE_BINDING, validate_reshade_binding
    enabled = values.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("Editor enabled must be a boolean")
    pause_after_capture = values.get("pause_after_capture", False)
    if type(pause_after_capture) is not bool:
        raise ValueError("Pause after capture must be a boolean")
    scene_appearance_flags = _uint(values.get("scene_appearance_flags", 0), "scene appearance flags")
    if scene_appearance_flags > 7:
        raise ValueError("Unknown scene appearance flags")
    owner = values.get("owner", "disabled")
    if owner not in OWNERS or owner == "unfocused":
        raise ValueError("Unknown native editor input owner")
    bindings = validate_action_bindings(values.get("bindings") or default_action_bindings())
    reshade = validate_reshade_binding(values.get("reshade_binding", DEFAULT_RESHADE_BINDING), bindings)
    wire = []
    for name in ACTION_ORDER:
        binding = bindings[name]
        wire.extend((0, 0) if binding is None else (binding.vk, int(binding.ctrl) | int(binding.alt) << 1 | int(binding.shift) << 2))
    if len(wire) != 52:
        raise ValueError("Editor action table does not match the native build")
    count = _uint(values.get("camera_count", 0), "camera count")
    selected = _uint(values.get("selected_camera", 0), "selected camera")
    if selected >= max(1, count):
        raise ValueError("Selected native camera is out of range")
    tick = values.get("replay_tick", 0)
    if isinstance(tick, bool) or not isinstance(tick, int) or not -0x80000000 <= tick <= 0x7fffffff:
        raise ValueError("Invalid replay tick")
    playback_rate = _uint(values.get("playback_rate", 60), "playback update rate")
    if playback_rate not in (30, 60, 120):
        raise ValueError("Native editor playback update rate must be 30, 60, or 120")
    video_fps = _uint(values.get("video_fps", 60), "video fps")
    if video_fps not in VIDEO_FPS:
        raise ValueError("Native editor video FPS must be 30, 60, 120, 300, or 600")
    video_bitrate = _uint(values.get("video_bitrate_mbps", 20), "video bitrate")
    if video_bitrate not in VIDEO_BITRATE_MBPS:
        raise ValueError("Native editor video bitrate must be 10, 20, or 40 Mbps")
    video_encoder = _uint(values.get("video_encoder", 0), "video encoder")
    if video_encoder > VIDEO_CODEC_MAX:
        raise ValueError("Unknown native editor video encoder")
    if type(values.get("video_depth", False)) is not bool:
        raise ValueError("Depth master must be a boolean")
    if type(values.get("video_depth_exr", False)) is not bool:
        raise ValueError("Depth EXR sequence must be a boolean")
    if values.get("video_depth_exr") and not values.get("video_depth"):
        raise ValueError("The depth EXR sequence requires the depth master")
    for layer in ("world", "players", "effects"):
        if type(values.get("video_layer_" + layer, False)) is not bool:
            raise ValueError("Video layer must be a boolean")
    if type(values.get("video_ae_camera", False)) is not bool:
        raise ValueError("After Effects camera export must be a boolean")
    if type(values.get("video_usd_camera", False)) is not bool:
        raise ValueError("USD camera export must be a boolean")
    # The video flags byte is full; further camera formats use their own byte.
    if type(values.get("renderlock_exporting", False)) is not bool:
        raise ValueError("RenderLock export state must be a boolean")
    camera_export_flags = ((1 if values.get("video_usd_camera") else 0)
                           | (2 if values.get("renderlock_exporting") else 0))
    video_flags = ((1 if values.get("video_fixed_step") else 0)
                   | (2 if values.get("video_depth") else 0)
                   | (4 if values.get("video_depth_exr") else 0)
                   | (8 if values.get("video_layer_world") else 0)
                   | (16 if values.get("video_layer_players") else 0)
                   | (32 if values.get("video_layer_effects") else 0)
                   | (64 if values.get("video_pov") else 0)
                   | (128 if values.get("video_ae_camera") else 0))
    video_speed = _finite(values.get("video_speed", 1.0), .05, 4, "video export speed")
    confetti_enabled = values.get("confetti_enabled", False)
    confetti_despawn = values.get("confetti_despawn_on_ground", False)
    if type(confetti_enabled) is not bool or type(confetti_despawn) is not bool:
        raise ValueError("Confetti switches must be booleans")
    confetti_height = round(_finite(values.get("confetti_spawn_height", 250.0),
                                    100, 1500, "confetti spawn height"))
    confetti = (confetti_height | int(confetti_enabled) << 16 |
                int(confetti_despawn) << 17)
    return CONFIG.pack(
        CONFIG_MAGIC, _uint(sequence, "sequence"), EDITOR_ABI, int(enabled), OWNERS.index(owner),
        _uint(owner_sequence, "owner sequence"), selected, count, _uint(ack_event, "event acknowledgement"),
        int(bool(values.get("invert_y", False))), confetti,
        _finite(values.get("speed", 320.0), 1, 10000, "movement speed"),
        _finite(values.get("sensitivity", .12), .001, 10, "mouse sensitivity"),
        *wire, _text(values.get("shot_name", "Untitled shot"), 96), _text(values.get("message", ""), 128),
        _finite(values.get("duration", 0), 0, 1e9, "duration"),
        _finite(values.get("playhead", 0), 0, 1e9, "playhead"), tick,
        int(bool(values.get("playing", False))) | int(bool(values.get("busy", False))) << 1
        | int(pause_after_capture) << 2,
        _finite(values.get("playback_speed", 1.0), .05, 4, "playback speed"),
        playback_rate, 0 if reshade is None else reshade.vk,
        0 if reshade is None else reshade.modifiers,
        video_fps, video_bitrate, video_encoder, video_flags, video_speed,
        struct.pack("<f", _finite(values.get("pov_duration", 10), .1, 120, "POV duration"))
        if values.get("video_pov") else b"\0" * 4, camera_export_flags,
        scene_appearance_flags)


def unpack_status(data, ack_event=0):
    from .editor_actions import ACTION_ORDER
    if len(data) != STATUS_BYTES:
        raise ValueError("Native editor returned the wrong status size")
    if data == b"\0" * STATUS_BYTES:
        return {"ready": False, "enabled": False, "input_mode": "disabled", "events": [], "last_event": 0}
    h = HEADER.unpack_from(data)
    magic, seq, abi, owner, flags, selected, count, latest, dropped = h[:9]
    if magic != STATUS_MAGIC or abi != EDITOR_ABI or seq & 1 or owner >= len(OWNERS) or flags & ~127:
        raise ValueError("Native editor protocol or input state does not match this build")
    speed, pose, message, phase, tick, reserved, frames = h[9], list(h[10:17]), h[17], *h[18:]
    if not all(math.isfinite(x) for x in [speed, phase, *pose]) or not 0 <= speed <= 10000:
        raise ValueError("Native editor returned invalid camera data")
    events = []
    action_names = ACTION_ORDER + EXTRA_ACTIONS
    for index in range(EVENT_COUNT):
        fields = EVENT.unpack_from(data, HEADER.size + EVENT.size * index)
        serial, action, value, *tail = fields
        if not serial or serial <= ack_event:
            continue
        event_pose, event_tick, paused = tail[:7], tail[7], tail[8]
        if serial > latest or action >= len(action_names) or paused not in (0, 1) or not all(math.isfinite(x) for x in [value, *event_pose]):
            raise ValueError("Native editor returned an invalid action")
        if action in (0, 1) and not .25 <= event_pose[6] <= 8:
            raise ValueError("Native capture returned an invalid framing value")
        events.append({"sequence": serial, "action": action_names[action], "action_id": action,
                       "value": value, "pose": event_pose, "tick": event_tick, "paused": bool(paused)})
    events.sort(key=lambda event: event["sequence"])
    serials = [e["sequence"] for e in events]
    if len(set(serials)) != len(serials) or (serials and serials != list(range(ack_event + 1, serials[-1] + 1))):
        raise ValueError("Native editor action queue lost synchronization; stop and reconnect the editor")
    return {"ready": bool(flags & 16), "enabled": bool(flags & 1), "focused": bool(flags & 2),
            "paused": bool(flags & 4), "flight_active": bool(flags & 8), "overlay_available": bool(flags & 32),
            "input_available": bool(flags & 64), "input_mode": OWNERS[owner],
            "console_open": owner == 4, "game_ui": owner == 3, "reshade_open": owner == 6,
            "selected_camera": selected, "camera_count": count, "last_event": latest, "dropped_events": dropped,
            "speed": speed, "applied_pose": pose, "message": message.split(b"\0", 1)[0].decode("utf-8", errors="replace"),
            "visualization_state": ({0: "disconnected", 1: "waiting", 2: "ready", 3: "disabled",
                                     4: "invalid", 5: "updating", 6: "unavailable"}.get(reserved, "unknown")),
            "phase": phase, "tick": tick, "frame_count": frames, "events": events}
