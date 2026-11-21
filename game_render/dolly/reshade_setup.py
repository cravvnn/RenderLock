"""Point the private ReShade config at Dolly's bundled shader library.

Dolly ships shaders and a preset but never the ReShade runtime. This module
only edits the per-user config Dolly already hands to the user's own runtime
(``settings.reshade_config_path``), so the bundled library is discoverable
without touching a global ReShade installation. Every function is a no-op when
no library was packaged.
"""
from __future__ import annotations

from pathlib import Path
import ntpath
import os
import secrets
import tempfile

PRESET_NAME = "Deadlock-Dolly.ini"
_SHADER_RELATIVE = Path("third_party") / "reshade_shaders"
_PRESET_RELATIVE = Path("assets") / "reshade" / PRESET_NAME
_GENERAL_SECTION = "[general]"


def _candidate_roots() -> list[Path]:
    from .runtime import resource_root

    try:
        root = resource_root()
    except (ImportError, OSError, ValueError):
        return []
    # Source builds keep the files at the repository root. A frozen onedir
    # bundle stores collected data under _internal, so consider both layouts.
    return [root, root / "_internal"]


def bundled_shader_paths() -> tuple[Path | None, Path | None]:
    """Return (Shaders, Textures) for the packaged library, or (None, None)."""
    for root in _candidate_roots():
        shaders = root / _SHADER_RELATIVE / "Shaders"
        if shaders.is_dir():
            textures = root / _SHADER_RELATIVE / "Textures"
            return shaders, (textures if textures.is_dir() else None)
    return None, None


def bundled_preset() -> Path | None:
    for root in _candidate_roots():
        preset = root / _PRESET_RELATIVE
        if preset.is_file():
            return preset
    return None


def _has_effects(folder: Path) -> bool:
    try:
        return folder.is_dir() and next(folder.glob("*.fx"), None) is not None
    except OSError:
        return False


def library_paths(root) -> tuple[Path | None, Path | None]:
    """Return (Shaders, Textures) inside a user-chosen FX library folder.

    The folder may be the shader folder itself or its parent. Textures are
    looked up beside the shaders, where the bundled and common ReShade packs
    keep them.
    """
    base = Path(str(root)).expanduser()
    shaders = base if _has_effects(base) else base / "Shaders"
    shaders = shaders if _has_effects(shaders) else None
    for textures in (base / "Textures", base.parent / "Textures"):
        if textures.is_dir():
            return shaders, textures
    return shaders, None


def discover_library(runtime_path) -> Path | None:
    """Find a ReShade shader folder near the selected runtime DLL, or None.

    Portable setups commonly keep ``reshade-shaders`` (or ``Shaders``) beside
    the DLL or one level up. Only folders that actually contain .fx files
    count, so this never registers an empty or unrelated directory.
    """
    try:
        base = Path(str(runtime_path)).expanduser().parent
    except (OSError, ValueError):
        return None
    seen: set[str] = set()
    for root in (base, base.parent):
        for candidate in (root / "reshade-shaders", root / "reshade_shaders",
                          root / "Shaders", root):
            key = _normalize_path(str(candidate))
            if key in seen:
                continue
            seen.add(key)
            shaders, _ = library_paths(candidate)
            if shaders is not None:
                return candidate
    return None


def library_issue(selected) -> str | None:
    """Explain why a chosen ReShade FX library folder cannot be used, or None."""
    candidate = Path(str(selected)).expanduser()
    if not candidate.is_dir():
        return (f"ReShade FX library folder missing: {candidate}. Choose the folder that "
                "contains the .fx shaders.")
    if not _has_effects(candidate) and not _has_effects(candidate / "Shaders"):
        return (f"No .fx shader files found in {candidate}. Choose the folder that contains "
                "the ReShade effects, for example the bundled third_party\\reshade_shaders\\Shaders.")
    return None


def _normalize_path(value: str) -> str:
    text = value.strip().strip('"').strip()
    return ntpath.normcase(ntpath.normpath(text))


def runtime_issue(selected, *, application_root: Path | None = None) -> str | None:
    """Explain why a selected ReShade runtime cannot be used yet, or None.

    Dolly never bundles, copies or deletes the user's ReShade runtime: it stays
    wherever the user put it. The two failures reported after an update are a
    path that no longer exists (moved, cleaned or replaced) and a DLL kept
    inside the Dolly folder, which a portable update can replace wholesale.
    """
    candidate = Path(str(selected)).expanduser()
    if not candidate.is_file():
        return (f"ReShade runtime missing: {candidate}. Choose the DLL again; Dolly never "
                "moves or deletes it.")
    if application_root is not None:
        try:
            inside = candidate.resolve().is_relative_to(Path(application_root).resolve())
        except (OSError, ValueError):
            inside = False
        if inside:
            return ("ReShade runtime is inside the Dolly folder, which an update replaces. "
                    "Move the DLL to its own folder (for example Documents\\Dolly-ReShade) "
                    "and select it again.")
    return None


def _bundled_family(value: str):
    """Name a bundled library folder (Shaders/Textures), or None."""
    text = value.strip().strip('"')
    parts = [part for part in ntpath.normpath(text).split("\\") if part and part != "."]
    if len(parts) >= 2 and parts[-2].lower() == "reshade_shaders":
        name = parts[-1].lower()
        if name in ("shaders", "textures"):
            return name
    return None


def merge_path_list(existing: str, additions: list[Path]) -> str:
    """Comma-join paths, dropping duplicates and stale bundled folders.

    Duplicate spellings already in the list are collapsed, and a library path
    from an older Dolly extraction is replaced by the current one instead of
    accumulating on every update. That keeps ReShade from listing each bundled
    effect several times.
    """
    tokens = existing.split(",") if existing else []
    additions_text = [str(path) for path in additions if str(path)]
    families = {_bundled_family(text) for text in additions_text}
    families.discard(None)
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        text = token.strip()
        normalized = _normalize_path(text)
        if not normalized or normalized in seen:
            continue
        family = _bundled_family(text)
        if family is not None and family in families:
            continue  # Stale copy of Dolly's own library; the current path wins.
        seen.add(normalized)
        result.append(text)
    for text in additions_text:
        normalized = _normalize_path(text)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(text)
    return ",".join(result)


def _install_presets(config: Path, preset: Path | None) -> tuple[Path | None, list[Path]]:
    if preset is None:
        return None, []
    folder = config.parent / "presets"
    folder.mkdir(parents=True, exist_ok=True)
    sources = [preset]
    optional = preset.parent / "Deadlock-AO.ini"
    if optional != preset and optional.is_file():
        sources.append(optional)
    created = []
    for source in sources:
        target = folder / source.name
        data = source.read_bytes()
        if target.exists() or target.is_symlink():
            continue  # Existing user presets need no temporary-file allocation.
        temporary = folder / (".dolly-preset-" + secrets.token_hex(16) + ".tmp")
        stream = temporary.open("xb")
        try:
            with stream as output:
                output.write(data)
            try:
                if os.name == "nt":
                    os.rename(temporary, target)  # Windows refuses an existing target.
                else:
                    os.link(temporary, target)  # Same-filesystem no-overwrite publication.
            except FileExistsError:
                continue  # Preserve the user's edits, including a competing install.
            created.append(target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # A private leftover must not hide the install result.
    return folder / preset.name, created


def _write_config(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        os.replace(name, path)
    finally:
        try:
            Path(name).unlink(missing_ok=True)
        except OSError:
            pass


def prepare_config(config_path, library_root=None) -> dict:
    """Merge bundled and chosen shader paths and preset into a ReShade config.

    The existing file is preserved; only the ``[GENERAL]`` search paths are
    extended and ``PresetPath`` is filled in when it is still empty. A chosen
    ``library_root`` lets the user point ReShade at their own effect library
    when the bundled one is missing. Returns a summary and never raises when
    neither library is present.
    """
    shaders, textures = bundled_shader_paths()
    preset = bundled_preset()
    library_shaders = library_textures = None
    if library_root is not None:
        library_shaders, library_textures = library_paths(library_root)
    shader_paths = [path for path in (shaders, library_shaders) if path is not None]
    texture_paths = [path for path in (textures, library_textures) if path is not None]
    summary = {"shaders": shaders, "textures": textures, "preset": preset,
               "library": Path(str(library_root)).expanduser() if library_root is not None else None,
               "library_shaders": library_shaders, "library_textures": library_textures,
               "changed": False}
    if not shader_paths and not texture_paths and preset is None:
        return summary

    path = Path(config_path)
    try:
        original_bytes = path.read_bytes()
    except FileNotFoundError:
        original_bytes = b""
    original = original_bytes.decode("utf-8-sig")
    preset, installed = _install_presets(path, preset)
    summary["preset"] = preset
    summary["installed_presets"] = installed
    newline = "\r\n" if "\r\n" in original else ("\n" if original else "\r\n")
    lines = original.splitlines()

    header = next((i for i, line in enumerate(lines)
                   if line.strip().lower() == _GENERAL_SECTION), None)
    if header is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[GENERAL]")
        header = len(lines) - 1
    end = len(lines)
    for index in range(header + 1, len(lines)):
        if lines[index].lstrip().startswith("["):
            end = index
            break

    def find_field(name: str) -> tuple[int | None, str]:
        for index in range(header + 1, end):
            text = lines[index].strip()
            if "=" in text and text.split("=", 1)[0].strip().lower() == name.lower():
                return index, text.split("=", 1)[1]
        return None, ""

    desired: list[tuple[str, list[Path]]] = []
    if shader_paths:
        desired.append(("EffectSearchPaths", shader_paths))
    if texture_paths:
        desired.append(("TextureSearchPaths", texture_paths))
    for name, additions in desired:
        index, existing = find_field(name)
        value = merge_path_list(existing, additions)
        if index is None:
            lines.insert(end, f"{name}={value}")
            end += 1
        else:
            lines[index] = f"{name}={value}"

    if preset is not None:
        index, existing = find_field("PresetPath")
        if index is None:
            lines.insert(end, f"PresetPath={preset}")
        elif not existing.strip():
            lines[index] = f"PresetPath={preset}"

    result = newline.join(lines) + newline
    if result != original:
        bom = b"\xef\xbb\xbf" if original_bytes.startswith(b"\xef\xbb\xbf") else b""
        _write_config(path, bom + result.encode("utf-8"))
        summary["changed"] = True
    return summary
