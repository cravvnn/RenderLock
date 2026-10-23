"""Resolve editable desktop paths before a worker changes directory.

The GUI validates paths in its launch directory. Child jobs run from the
application directory so their imports work; pass absolute paths to preserve
what the user selected, including paths typed with ``~``.
"""
from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping


PATH_FIELDS = frozenset({
    "input", "assets", "blender", "output", "game_install", "source2viewer",
    "asset_cache", "particle_probe", "particle_dotnet", "world_region_probe",
    "lighting_dotnet", "probe_atlas_decoder", "cubemap_decoder",
    "character_shader", "external_map",
})


def resolve_gui_path(value: str, *, base: Path | None = None) -> Path:
    """Resolve one nonempty field using the GUI's launch directory."""
    if "\x00" in value:
        raise ValueError("A file path cannot contain a NUL character.")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() if base is None else Path(base)) / path
    return path.resolve()


def normalize_gui_paths(values: Mapping[str, str], *, base: Path | None = None) -> dict[str, str]:
    """Return values whose nonempty path fields survive a child cwd change.

    Keep empty optional tools empty, and leave resource names and editable
    numbers untouched. Callers can also persist the result so reopening the
    app from another directory retains the same selected files.
    """
    result = dict(values)
    for key in PATH_FIELDS & result.keys():
        if not isinstance(result[key], str):
            # Settings readers decide which JSON values are valid preferences.
            # Leave malformed non-string values for that existing filter.
            continue
        value = result[key].strip()
        try:
            # Already absolute selections survive the worker cwd unchanged.
            # Retain their spelling in saved preferences and cache receipts.
            result[key] = value if value and Path(value).is_absolute() else (
                str(resolve_gui_path(value, base=base)) if value else "")
        except (OSError, RuntimeError, ValueError):
            # An inactive optional field can contain an old invalid path.
            # Preserve it for the active field's validation rather than making
            # an unrelated export or settings save fail during normalization.
            result[key] = value
    return result
