"""Keep saved portable tool choices attached to their installation after a move."""
from __future__ import annotations

import json
from pathlib import Path
import re


PROGRAM_ROOT_SETTING = "tool_program_root"
_BUNDLED_TOOLS = {
    "source2viewer": ("tools/source2viewer/Source2Viewer-CLI.exe",),
    "particle_probe": ("tools/particle_simulation/ParticleSimulationProbe.exe",
                       "tools/particle_simulation/ParticleSimulationProbe.dll",
                       "tools/particles/ParticleProbe.exe", "tools/particles/ParticleProbe.dll"),
    "particle_dotnet": ("tools/dotnet/dotnet.exe",),
    "lighting_dotnet": ("tools/dotnet/dotnet.exe",),
    "world_region_probe": ("tools/lighting/world/WorldRegionProbe.dll",),
    "probe_atlas_decoder": ("tools/lighting/atlas/ProbeAtlasDecode.dll",),
    "cubemap_decoder": ("tools/lighting/cube/CubemapDecode.dll",),
}
_TOOL_LABELS = {
    "blender": ("Blender", "Setup"),
    "source2viewer": ("Source 2 Viewer CLI", "Setup"),
    "particle_probe": ("particle simulator", "Advanced"),
    "particle_dotnet": ("particle .NET runtime", "Advanced"),
    "lighting_dotnet": ("lighting .NET runtime", "Lighting lab"),
    "world_region_probe": ("world lighting helper", "Lighting lab"),
    "probe_atlas_decoder": ("probe atlas decoder", "Lighting lab"),
    "cubemap_decoder": ("cubemap decoder", "Lighting lab"),
}


def bundled_particle_probes(program_root):
    """Prefer the isolated simulator while retaining legacy installations."""
    return tuple(Path(program_root)/relative for relative in _BUNDLED_TOOLS['particle_probe'])


def _portable_root(path: Path) -> bool:
    # Old releases saved absolute paths without provenance. Their standard ZIP
    # root remains recognizable after the old folder has been removed.
    if re.fullmatch(r"RenderLock-\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)-Windows-x64", path.name,
                    flags=re.IGNORECASE):
        return True
    manifest = path / "release_manifest.json"
    try:
        if manifest.stat().st_size > 4 * 1024 * 1024:
            return False
        return json.loads(manifest.read_text(encoding="utf-8")).get("kind") == "Windows-x64-portable"
    except (OSError, ValueError, AttributeError):
        return False


def reconcile_saved_tools(saved: dict, program_root: Path) -> tuple[dict, list[str]]:
    """Rebind identified bundle paths; retain explicit external choices.

    The saved program root handles arbitrary renamed portable folders. For
    older settings, an existing portable manifest or the standard release root
    identifies package paths. A missing unidentifiable override stays visible
    with guidance instead of silently replacing a user's choice.
    """
    values = dict(saved)
    current_root = Path(program_root).resolve()
    previous_root = values.get(PROGRAM_ROOT_SETTING)
    try:
        previous_root = Path(previous_root).expanduser().resolve() if isinstance(previous_root, str) and previous_root else None
    except (OSError, RuntimeError, ValueError):
        previous_root = None
    notices = []
    for key, relatives in _BUNDLED_TOOLS.items():
        value = values.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            selected = Path(value).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        for relative in relatives:
            parts = Path(relative).parts
            old_root = selected.parents[len(parts) - 1] if len(selected.parts) > len(parts) else None
            if old_root is None or selected != old_root / relative:
                continue
            legacy_particle = key=='particle_probe'and relative.startswith('tools/particles/')
            if old_root == current_root and not legacy_particle:
                break
            if old_root == current_root and legacy_particle and not any(
                    (current_root/p).is_file()for p in relatives if p.startswith('tools/particle_simulation/')):
                break
            if old_root != current_root and old_root != previous_root and not _portable_root(old_root):
                continue
            candidates=relatives
            if key=='particle_probe'and not legacy_particle:
                # A missing new simulator must stay visible, not silently
                # downgrade to the separately retained legacy material helper.
                candidates=tuple(p for p in relatives if p.startswith('tools/particle_simulation/'))
            replacement = next((current_root / candidate for candidate in candidates
                                if (current_root / candidate).is_file()), None)
            if replacement is not None and replacement!=selected:
                values[key] = str(replacement)
                label, _ = _TOOL_LABELS[key]
                notices.append(f"Updated saved {label} path to this RenderLock folder.")
            break
    for key, (label, page) in _TOOL_LABELS.items():
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            try:
                exists = Path(value).expanduser().is_file()
            except (OSError, RuntimeError, ValueError):
                exists = False
            if not exists:
                notices.append(f"Saved {label} was not found: {value}. Choose its current executable in {page}.")
    return values, notices
