"""Pure native-editor value mapping; protocol IDs and defaults are unchanged."""
from dataclasses import dataclass
import math
from .editor_wire import ATTACH_NO_TARGET
from .native_effects import model_token
from .path import AttachKey
from .video_export import BITRATE_PRESETS, CODEC_BY_KEY, CODEC_CHOICES, CODEC_LABEL_TO_KEY, DEFAULT_CODEC_KEY

VIDEO_FPS = (30, 60, 120, 300, 600)
VIDEO_BITRATE_MBPS = (10, 20, 40)
CODEC_MAX = 10
MISSING = object()


@dataclass(frozen=True)
class ReadError:
    """A captured field-read failure, replayed at the original validation point."""
    error: Exception


@dataclass(frozen=True)
class EditorControls:
    speed: object = MISSING
    rate: object = MISSING
    video_fps: object = MISSING
    video_bitrate: object = MISSING
    video_codec: object = MISSING
    video_fixed_step: object = MISSING
    video_depth: object = MISSING
    video_depth_exr: object = MISSING
    video_export_speed: object = MISSING
    video_pov_duration: object = MISSING

    def read(self, name, default=None):
        value = getattr(self, name)
        if isinstance(value, ReadError):
            raise value.error
        return default if value is MISSING else value


def playback_values(controls: EditorControls, previous=None):
    # The editable desktop speed can briefly be empty, '-' or out of range
    # while typing. Keep publishing the last valid setting until it is valid;
    # an unfinished edit must never block input-owner/camera-count refreshes.
    previous = previous or {}
    speed = previous.get("playback_speed", 1.0)
    rate = previous.get("playback_rate", 60)
    try:
        candidate = float(controls.read("speed", 1.0))
        if math.isfinite(candidate) and .05 <= candidate <= 4:
            speed = candidate
    except (ValueError, TypeError):
        pass
    try:
        candidate = float(controls.read("rate", 60))
        if candidate in (30, 60, 120):
            rate = int(candidate)
    except (ValueError, TypeError):
        pass
    return speed, rate


def video_values(controls: EditorControls, previous=None):
    # The in-game Export page mirrors the desktop Export tab. Keep the last
    # valid values while the desktop fields are mid-edit, exactly like playback.
    previous = previous or {}
    fps = previous.get("video_fps", 60)
    bitrate = previous.get("video_bitrate_mbps", 20)
    codec = previous.get("video_encoder", 0)
    fixed = bool(previous.get("video_fixed_step", False))
    depth = bool(previous.get("video_depth", False))
    depth_exr = bool(previous.get("video_depth_exr", False))
    speed = previous.get("video_speed", 1.0)
    try:
        candidate = int(controls.read("video_fps", 60))
        if candidate in VIDEO_FPS:
            fps = candidate
    except (ValueError, TypeError):
        pass
    try:
        candidate = BITRATE_PRESETS.get(controls.read("video_bitrate", "20 Mbps"))
        if candidate in (10_000_000, 20_000_000, 40_000_000):
            bitrate = candidate // 1_000_000
    except (ValueError, TypeError, AttributeError):
        pass
    try:
        key = CODEC_LABEL_TO_KEY.get(controls.read("video_codec", ""), DEFAULT_CODEC_KEY)
        candidate = CODEC_BY_KEY[key][2]
        if 0 <= candidate <= CODEC_MAX:
            codec = candidate
    except (ValueError, TypeError, KeyError, IndexError):
        pass
    fixed = bool(controls.read("video_fixed_step", fixed))
    depth = bool(controls.read("video_depth", depth))
    depth_exr = bool(controls.read("video_depth_exr", depth_exr)) and depth
    try:
        candidate = float(controls.read("video_export_speed", 1.0))
        if math.isfinite(candidate) and .05 <= candidate <= 4:
            speed = candidate
    except (ValueError, TypeError):
        pass
    return fps, bitrate, codec, fixed, speed, depth, depth_exr


def pov_duration(controls: EditorControls, previous=None):
    try:
        value = float(controls.read("video_pov_duration", 10))
        if math.isfinite(value) and .1 <= value <= 120:
            return value
    except (ValueError, TypeError):
        pass
    return (previous or {}).get("pov_duration", 10)


def codec_label(codec_id):
    for _key, label, _encoder, codec, _needs in CODEC_CHOICES:
        if codec == codec_id:
            return label
    return CODEC_CHOICES[0][1]


def attach_state(project, roster, selected, count):
    """Selected key's attach data for the in-game card, or None for free keys."""
    if not count or selected is None or not 0 <= selected < count:
        return None
    key = project.keyframes[selected]
    attach = getattr(key, "attach", None)
    if getattr(key, "source", "free") != "attach" or not isinstance(attach, AttachKey):
        if key.source_blend:
            return {"selected": False, "source_blend": key.source_blend,
                    "attached_keys": sum(item.source == "attach" for item in project.keyframes),
                    "key_count": count}
        return None
    players = roster.get("players", []) if isinstance(roster, dict) else []
    target_index = ATTACH_NO_TARGET
    for position, player in enumerate(players):
        if (isinstance(player, dict) and player.get("handle") == attach.handle
                and str(player.get("model_path") or "") == attach.model):
            target_index = position
            break
    return {"source_blend": key.source_blend, "handle": attach.handle, "entity_id": attach.entity_id,
            "target_index": target_index, "model": model_token(attach.model),
            "point": ("eyes", "weapon", "bone").index(attach.point),
            "bone": attach.bone,
            "hide_body": bool(attach.hide_body), "clearance_mode": attach.clearance_mode,
            "orientation_mode": attach.orientation_mode,
            "lock_vertical_aim": attach.lock_vertical_aim,
            "offset": tuple(attach.offset),
            "smoothing": float(attach.smoothing),
            "attached_keys": sum(1 for item in project.keyframes
                                 if getattr(item, "source", "free") == "attach"),
            "key_count": len(project.keyframes)}


def editor_values(*, active, settings, project, selected, count, playhead, status,
                  busy, playback, video, message, video_pov, pov_duration, layers,
                  video_ae_camera=False, video_usd_camera=False, renderlock_exporting=False):
    playback_speed, playback_rate = playback
    video_fps, video_bitrate, video_encoder, video_fixed_step, video_speed, video_depth, video_depth_exr = video
    return dict(enabled=active, bindings=settings.action_bindings,
                  reshade_binding=settings.reshade_binding,
                  speed=settings.movement_speed, sensitivity=settings.mouse_sensitivity,
                  selected_camera=selected, camera_count=count,
                  shot_name=project.name, message=message,
                  duration=float(project.duration), playhead=max(0.0, playhead),
                  replay_tick=int(status.get("tick") or 0),
                  playing=bool(status.get("playing")), busy=bool(busy),
                  pause_after_capture=settings.pause_after_capture,
                  scene_appearance_flags=status.get("scene_appearance_flags", 0),
                  playback_speed=playback_speed, playback_rate=playback_rate,
                  video_fps=video_fps, video_bitrate_mbps=video_bitrate,
                  video_encoder=video_encoder, video_fixed_step=video_fixed_step,
                  video_depth=video_depth,
                  video_depth_exr=video_depth_exr,
                  video_speed=video_speed,
                  video_pov=video_pov,
                  video_ae_camera=bool(video_ae_camera),
                  video_usd_camera=bool(video_usd_camera),
                  renderlock_exporting=bool(renderlock_exporting),
                  pov_duration=pov_duration,
                  confetti_enabled=bool(project.confetti_enabled),
                  confetti_spawn_height=float(project.confetti_spawn_height),
                  confetti_despawn_on_ground=bool(project.confetti_despawn_on_ground),
                  **{"video_layer_" + layer: bool(layers[layer])
                     for layer in ("world", "players", "effects")})
