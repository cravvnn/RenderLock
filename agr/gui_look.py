"""Migrate saved character looks and resolve effective command settings.

The saved shader library and helper paths remain preferences. A preset can
leave them dormant for a job without losing the user's selections.
"""
from __future__ import annotations

from copy import deepcopy


LOOK_STANDARD = "standard"
LOOK_SOURCE = "source"
LOOK_ELAGRIMM = "elagrimm_source"
LOOK_CUSTOM = "custom"

_LOOKS = (LOOK_STANDARD, LOOK_SOURCE, LOOK_ELAGRIMM, LOOK_CUSTOM)
_LIGHTING_FLAGS = ("source_lighting", "source_hero_lighting", "source_npr_lighting")


def _has_shader(values: dict) -> bool:
    path = values.get("character_shader", "")
    return isinstance(path, str) and bool(path.strip())


def migrate_look_settings(values: dict) -> dict:
    """Return independent preferences with a validated ``character_look``.

    Only an absent or empty look is inferred from legacy settings. A nonempty
    shader used to enable Elagrimm regardless of the three lighting checkboxes.
    Keep all raw flags and paths, including unusual combinations, for Custom.
    GUI flags use the strings ``"0"`` and ``"1"``; do not use their truthiness.
    """
    migrated = deepcopy(values)
    mode = migrated.get("character_look")
    if mode is None or mode == "":
        enabled = tuple(migrated.get(key, "0") == "1" for key in _LIGHTING_FLAGS)
        if _has_shader(migrated):
            mode = LOOK_ELAGRIMM
        elif enabled == (True, True, False):
            mode = LOOK_SOURCE
        elif any(enabled):
            mode = LOOK_CUSTOM
        else:
            mode = LOOK_STANDARD
    if mode not in _LOOKS:
        raise ValueError(
            f"Unknown character look {mode!r}. Choose Standard, Source, "
            "Elagrimm Source, or Custom under Character look."
        )
    migrated["character_look"] = mode
    return migrated


def resolve_look_settings(values: dict, *, character_only: bool = False) -> dict:
    """Return effective job settings without changing stored preferences.

    Source presets request map preparation for lighting data even when map
    geometry is excluded. Standard prunes unused preparation in character-only
    jobs. Custom retains raw values for the command builder's existing guards.
    No shader file or helper is opened or validated here.
    """
    effective = migrate_look_settings(values)
    mode = effective["character_look"]
    if mode == LOOK_CUSTOM:
        return effective
    if mode == LOOK_ELAGRIMM and not _has_shader(effective):
        raise ValueError(
            "Elagrimm Source requires a shader library. Choose the supported "
            "Elagrimm shader .blend under Character look, or select Standard "
            "or Source."
        )
    enabled = {
        LOOK_STANDARD: ("0", "0", "0"),
        LOOK_SOURCE: ("1", "1", "0"),
        LOOK_ELAGRIMM: ("1", "1", "1"),
    }[mode]
    effective.update(zip(_LIGHTING_FLAGS, enabled))
    if mode in (LOOK_STANDARD, LOOK_SOURCE):
        effective["character_shader"] = ""
    if mode in (LOOK_SOURCE, LOOK_ELAGRIMM):
        effective["map_enabled"] = "1"
    elif character_only:
        effective["map_enabled"] = "0"
    return effective
