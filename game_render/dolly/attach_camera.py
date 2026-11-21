"""Attach camera (POV / bone) field contract.

The attach camera reads a live player's world transform every rendered view and
places the shot camera at a chosen point: the eyes for a POV, the weapon node
for a gun camera, and named bones later. Every offset it needs is a schema
field, so the game itself reports the layout at runtime the same way the
players layer resolves ``CGameSceneNode.m_pOwner`` and
``C_BaseEntity.m_pGameSceneNode`` (see :mod:`dolly.player_layer`). Nothing here
is hardcoded beyond field names, expected types and plausibility ranges: a
missing, retyped or implausible field refuses the attach camera instead of
guessing.

The required fields were live-verified 2026-09-19 on client ``d1ee16fc``
against replay 104550184 at tick 22473: the game reported each field at the
expected offset and type, and the decoded eye point matched the head/eye bone
on all twelve players. Optional entries remain candidates for Phase 5 bone
work. See ``docs/internal/ATTACH_CAMERA_PLAN.md``.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from ._runtime_generated import ATTACH_FIELD_SPECS, VIEW_OFFSET_VALUE_OFFSETS

# The same reviewed range the players layer accepts for schema offsets.
_MIN_OFFSET, _MAX_OFFSET = 8, 0x8000

# One schema row: optional "+" inline marker, outer offset, offset, class,
# field and the rest of the line as the type text.
_ROW = re.compile(r"^\s*(\+?)\s*(\d+)\s+(\d+)\s+(\S+)\s+(m_\S+)\s+(.+?)\s*$", re.M)


@dataclass(frozen=True)
class AttachField:
    """One required or optional schema field for the attach camera."""

    purpose: str
    layout_class: str
    field: str
    expected_type: str
    required: bool = True


# v1 needs the target's world pose, the eye offset and angles, and the scene
# graph links used to find the weapon node. The rest are captured for the bone
# and roster work but stay optional so a missing optional field cannot disable
# the eyes/weapon paths.
ATTACH_FIELDS: tuple[AttachField, ...] = tuple(AttachField(*spec) for spec in ATTACH_FIELD_SPECS)

# Live-verified 2026-09-19 on client d1ee16fc (replay 104550184, tick 22473):
# every required field above was reported at the expected offset with the
# expected type. The eye offset stores one float per component at +16, +24 and
# +32 from the field start (stride 8; the following dword is network
# quantization metadata). Standing poses read (0, 0, eye height), and the pose
# transform bone nearest that point matched the head/eye bone within 1.4 to 8.7
# units on all twelve players. The optional entries stay candidates.


def decode_view_offset(raw: bytes) -> tuple[float, float, float] | None:
    """Decode CNetworkViewOffsetVector bytes into a local XYZ view offset.

    Returns None when the buffer is short or a component is not finite and
    plausible; the caller then refuses the eye point instead of guessing.
    """
    if len(raw) < VIEW_OFFSET_VALUE_OFFSETS[-1] + 4:
        return None
    values = tuple(struct.unpack_from("<f", raw, base)[0] for base in VIEW_OFFSET_VALUE_OFFSETS)
    if any(not math.isfinite(value) or abs(value) > 200 for value in values):
        return None
    return values


# Verified model file stems from the 2026-09-16 roster run (the folder and file
# names differ for some heroes, so the stem is authoritative). Unknown stems
# fall back to a readable stem; a hero name is never guessed from a partial
# match.
HERO_NAMES_BY_MODEL_STEM = {
    "astro": "Holliday",
    "abrams": "Abrams",
    "baba": "Baba",
    "chessmaster": "Solomon",
    "familiar_wip": "Rem",
    "yamato": "Yamato",
    "digger": "Mo and Krill",
    "drifter": "Drifter",
    "lash": "Lash",
    "necro": "Graves",
    "ratking": "Rat King",
    "wraith": "Wraith",
    "nano": "Calico",
    "hornet": "Vindicta",
    "warden": "Warden",
}


def _find_field(layout_text: str, field: str) -> tuple[int, str] | None:
    """Return (offset, type text) for a top-level field in one class layout."""
    for plus, outer, offset, _kind, name, type_text in _ROW.findall(layout_text):
        if plus or int(outer) != 0 or name != field:
            continue
        return int(offset), type_text.strip()
    return None


def parse_field(layout_text: str, field: str) -> tuple[int, str]:
    """Return (offset, type text) for a field, refusing plausible-looking junk."""
    found = _find_field(layout_text, field)
    if found is None:
        raise RuntimeError("The game did not report %s." % field)
    offset, type_text = found
    if not _MIN_OFFSET <= offset <= _MAX_OFFSET:
        raise RuntimeError("%s is outside the reviewed offset range." % field)
    return offset, type_text


def field_report(layouts: Mapping[str, str]) -> list[dict]:
    """Per-field status for diagnostics and the derivation tool.

    ``layouts`` maps a class name to the console layout text for that class.
    Status is one of ``ok``, ``layout`` (class text absent), ``field`` (field
    absent), ``type`` (retyped), or ``range``.
    """
    report = []
    for spec in ATTACH_FIELDS:
        text = layouts.get(spec.layout_class)
        entry = {
            "purpose": spec.purpose,
            "class": spec.layout_class,
            "field": spec.field,
            "expected_type": spec.expected_type,
            "actual_type": "",
            "offset": None,
            "required": spec.required,
            "status": "layout",
        }
        found = _find_field(text, spec.field) if text else None
        if found is not None:
            offset, type_text = found
            entry["actual_type"] = type_text
            if spec.expected_type not in type_text:
                entry["status"] = "type"
            elif not _MIN_OFFSET <= offset <= _MAX_OFFSET:
                entry["status"] = "range"
            else:
                entry["offset"] = offset
                entry["status"] = "ok"
        elif text:
            entry["status"] = "field"
        report.append(entry)
    return report


def parse_fields(layouts: Mapping[str, str],
                 specs: tuple[AttachField, ...] = ATTACH_FIELDS) -> dict[str, int]:
    """Return purpose -> offset, refusing any unusable required field.

    Optional fields drop out silently when missing, retyped or implausible;
    their absence must only disable the feature that needs them (bone cams,
    roster names), never the eyes/weapon paths.
    """
    offsets: dict[str, int] = {}
    problems: list[str] = []
    for spec in specs:
        text = layouts.get(spec.layout_class)
        found = _find_field(text, spec.field) if text else None
        if found is None:
            if spec.required:
                problems.append("%s.%s is not reported" % (spec.layout_class, spec.field))
            continue
        offset, type_text = found
        if spec.expected_type not in type_text:
            if spec.required:
                problems.append("%s.%s is %r, expected %s"
                                % (spec.layout_class, spec.field, type_text, spec.expected_type))
            continue
        if not _MIN_OFFSET <= offset <= _MAX_OFFSET:
            if spec.required:
                problems.append("%s.%s is outside the reviewed range"
                                % (spec.layout_class, spec.field))
            continue
        offsets[spec.purpose] = offset
    if problems:
        raise RuntimeError("Attach camera fields are unavailable: " + "; ".join(problems))
    return offsets


def hero_name(model_name: str) -> str:
    """Display name for a model path; unknown stems are shown, never guessed."""
    stem = Path(str(model_name).replace("\\", "/")).stem.lower().strip()
    if not stem:
        return "Unknown"
    return HERO_NAMES_BY_MODEL_STEM.get(stem, stem.replace("_", " ").title())


def camera_summary(project) -> str:
    """Describe the saved camera sources, independently of preview overrides."""
    sources = []
    for key in project.keyframes:
        if key.source == "attach" and key.attach:
            attach = key.attach
            point = "Bone " + attach.bone if attach.point == "bone" else attach.point.title()
            label = "Attach — %s, %s" % (hero_name(attach.model), point)
        else:
            label = "Free path"
        if label not in sources:
            sources.append(label)
    if not sources:
        return "Free path"
    if len(sources) <= 2:
        return " / ".join(sources)
    return "%d camera sources · %d keys" % (len(sources), len(project.keyframes))


# Runtime order of the native attach offset block; the native parser and
# dolly.editor_wire.pack_attach share it.
RUNTIME_FIELD_ORDER = ("scene_node", "owner", "player_origin", "player_angles",
                       "eye_offset", "eye_angles", "scene_child", "scene_sibling")
RUNTIME_LAYOUT_CLASSES = ("CGameSceneNode", "C_BaseModelEntity", "C_CitadelPlayerPawn",
                          "C_BaseEntity")


def query_field_offsets(controller) -> dict[str, int]:
    """Query the live schema once and return the runtime attach offset block.

    Uses the same console commands the players layer uses, so no offset is
    hardcoded: a missing or retyped field raises instead of guessing. Call from
    a worker, never the UI thread, and store the result for republication.
    """
    from . import player_layer
    texts = {}
    for layout_class in RUNTIME_LAYOUT_CLASSES:
        # The detailed pawn layout recursively expands inline structures. Build
        # 6712 contains an inline entry that crashes the game's schema printer.
        # Binding output uses the same typed top-level rows without expansion;
        # m_angEyeAngles needs only that top-level contract.
        command = ("schema_dump_binding" if layout_class == "C_CitadelPlayerPawn"
                   else "schema_detailed_class_layout")
        texts[layout_class] = str(controller._request(
            command + " " + layout_class, timeout=5))
    offsets = parse_fields(texts)
    offsets["scene_node"] = player_layer.parse_scene_node_offset(texts["C_BaseEntity"])
    offsets["owner"] = player_layer.parse_owner_offset(texts["CGameSceneNode"])
    missing = [name for name in RUNTIME_FIELD_ORDER if name not in offsets]
    if missing:
        raise RuntimeError("The live schema did not report: " + ", ".join(missing))
    return {name: offsets[name] for name in RUNTIME_FIELD_ORDER}
