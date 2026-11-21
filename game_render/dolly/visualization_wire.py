"""Bounded, optional path guides for the native editor's existing DX11 overlay.

The separate ``<native mapping name>.viewer`` mapping is at most 2 MiB. A
64-byte little-endian header is followed by authored key times, bounded
per-key authoring metadata, and an unchanged native camera path. The publishing transport writes an odd sequence at offset
8 before replacing a packet, then commits its even sequence. This module only
builds immutable packets; it never accesses the game or evaluates per frame.
"""

from __future__ import annotations

import math
import struct

from .native_path import MAX_CAMERA_KEYS, compile_project
from .path import POSITION_CHANNELS, TANGENT_MODES, Project, channel_value

MAGIC = b"DLYVIS01"
ABI = 4
MAPPING_BYTES = 2 * 1024 * 1024
HEADER = struct.Struct("<8s9IQ3I")
# ABI 4 retains the authored values/source prefix and appends XYZ tangent
# intent. Auto carries the evaluator's effective slopes; authored modes carry
# saved slopes even while interpolation keeps them dormant. Flag bit 1 says
# whether Smooth position authoring is active; bit 0 remains unwrapped angles.
KEY_METADATA = struct.Struct("<7dII" + "II2d" * 3)
HEADER_BYTES = HEADER.size
SEQUENCE_OFFSET = 8
MAX_SAMPLES = 4096
MAX_MARKERS = 128
DEFAULT_SAMPLES = 2048
DEFAULT_MARKERS = 64


def _integer(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}")
    return value


def build_visualization(project: Project | None, *, sequence=2, enabled=True,
                        selected_camera=0, sample_budget=DEFAULT_SAMPLES,
                        marker_budget=DEFAULT_MARKERS, revision=0, session_id=0) -> bytes:
    """Build a viewer packet without modifying a shot or its playback curves.

    Empty shots produce a clearing packet. Large shots keep every camera's
    position in the sampled line; glyphs are evenly spaced and always include
    the selected camera. This budget changes only drawing, never playback.
    """
    revision = _integer(revision, "Shot revision", 0, 0xFFFFFFFF)
    session_id = _integer(session_id, "Shot session", 0, 0xFFFFFFFFFFFFFFFF)
    sequence = _integer(sequence, "Viewer sequence", 2, 0xFFFFFFFE)
    if sequence & 1:
        raise ValueError("Viewer sequence must be even")
    if not isinstance(enabled, bool):
        raise ValueError("Viewer enabled must be a boolean")
    selected_camera = _integer(selected_camera, "Selected camera", 0, MAX_CAMERA_KEYS - 1)
    sample_budget = _integer(sample_budget, "Viewer sample budget", 2, MAX_SAMPLES)
    marker_budget = _integer(marker_budget, "Viewer marker budget", 1, MAX_MARKERS)
    if project is not None and not isinstance(project, Project):
        raise ValueError("Viewer compilation requires a Project")
    keys = project.keyframes if project is not None else []
    if not enabled or not keys:
        return HEADER.pack(MAGIC, sequence, ABI, 0, 0, 0, 0, sample_budget, marker_budget, revision, session_id, 0, 0, 0)
    payload = compile_project(project)
    if selected_camera >= len(keys):
        raise ValueError("Selected camera is outside this shot")
    budget = max(sample_budget, len(keys))
    result = bytearray(HEADER.pack(MAGIC, sequence, ABI, 1, selected_camera,
                         len(keys), len(payload), budget, marker_budget, revision, session_id,
                         len(keys) * KEY_METADATA.size,
                         int(project.rotation_mode == "unwrapped") | int(project.interpolation == "smooth") << 1, 0))
    result += struct.pack(f"<{len(keys)}d", *(float(key.time) for key in keys))
    from .native_path import CHANNELS
    # Prepare each channel once rather than evaluating/validating a whole shot
    # for every key. The compiler above has already validated these inputs.
    samples = {name: project._camera_channel_samples(name) for name in POSITION_CHANNELS}
    for index, key in enumerate(keys):
        tangents = []
        for name in POSITION_CHANNELS:
            tangent = project.camera_tangent(index, name)
            if tangent.mode == "auto":
                channel = samples[name]
                if channel.incoming_tangents is not None:
                    incoming, outgoing = channel.incoming_tangents[index], channel.outgoing_tangents[index]
                elif channel.tangents is not None:
                    incoming = outgoing = channel.tangents[index]
                elif len(keys) == 1:
                    incoming = outgoing = 0.0
                else:
                    # Match camera_tangent_slopes' finite secant fallback.
                    def slope(left):
                        value = ((channel.values[left + 1] - channel.values[left])
                                 / (channel.times[left + 1] - channel.times[left]))
                        return value if math.isfinite(value) else 0.0
                    incoming, outgoing = slope(max(0, index - 1)), slope(min(index, len(keys) - 2))
            elif tangent.mode == "flat":
                incoming = outgoing = 0.0
            else:
                incoming, outgoing = tangent.incoming, tangent.outgoing
            tangents.extend((TANGENT_MODES.index(tangent.mode), 0, incoming, outgoing))
        result += KEY_METADATA.pack(*(float(channel_value(key, name)) for name in CHANNELS),
                                    int(key.source == "attach"), 0, *tangents)
    result += payload
    if len(result) > MAPPING_BYTES:
        raise ValueError("Viewer packet exceeds its fixed mapping size")
    return bytes(result)
