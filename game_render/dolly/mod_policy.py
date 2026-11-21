"""Decide which installed mod mounts may join a Dolly editing session.

Content mods (skins, models, maps, audio) are carried into the temporary
session file; render, visual-interface and startup-config overrides are not.
Those change what the camera capture sees, or fight Dolly's own HUD hiding and
UI override, which is exactly why the isolated editing session exists.

The decision is made from the mount's own content, never from a mod name:
loose files and the directory trees of VPK packs are inspected without reading
payloads. Unreadable or linked content is refused rather than guessed at;
non-asset replay storage is ignored without traversing it.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .file_ops import _plain_path
from .panorama_assets import _iter_vpk_tree, _read_vpk_tree

LOG = logging.getLogger(__name__)

# Content roots a Dolly session may load.
# Deadlock ships audio under sounds/ and character animation under animgraphs/
# (checked against its pak01); sound/ and animations/ are kept for older packs.
ALLOWED_ROOTS = frozenset({"models", "maps", "sound", "sounds", "soundevents", "scenes", "animations",
                           "animgraphs", "materials"})
# Material overrides are only content when they belong to a model or map. Any
# other material path is a visual/render override (environment, effects, ...).
ALLOWED_MATERIAL_ROOTS = frozenset({"models", "maps"})
# Named only for a clearer message; any other unrecognised directory is refused
# by the same rule.
DENIED_ROOTS = frozenset({"panorama", "shaders", "cfg", "scripts", "particles", "effects", "resource",
                          "tools", "dev"})
# Mod managers put each mod in a folder below a shared mount. One level below the
# mount root is a mod folder; deeper is content.
MAX_CONTAINER_DEPTH = 1
MAX_VPK_TREE_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE = 4
# Replay storage is not an asset search root. Mod managers can put a junction
# here; do not inspect its target or let it classify the surrounding skin pack.
NON_ASSET_FOLDERS = frozenset({"replays"})


def _not_loaded(child: Path, depth: int) -> bool:
    """True for entries the game never loads from this mount.

    The engine mounts packs only at the mount root, so a pack in a subfolder
    (DMM's other profiles below citadel/addons) is not loaded; hidden folders
    (Grimoire keeps disabled mods in citadel/addons/.disabled) are skipped.
    """
    name = child.name.casefold()
    if name in NON_ASSET_FOLDERS or name.startswith("."):
        return True
    return depth > 0 and name.endswith("_dir.vpk")


class MountInspectionError(ValueError):
    """Content could not be inspected; an override is not a successful scan."""


def _holds_compiled_files(folder: Path) -> bool:
    """True when compiled game files (``*.v…_c``) sit directly in ``folder``.

    The game loads those from an asset folder such as ``postprocessing/``; a
    mod-manager container holds packs or content folders instead.
    """
    try:
        return any(entry.is_file() and entry.suffix.casefold().endswith("_c") for entry in folder.iterdir())
    except OSError as exc:
        raise MountInspectionError(f"{folder.name} could not be read ({exc.__class__.__name__})") from exc


def _content(parts: list[str]) -> bool:
    """True when this virtual path is reviewed skin/map/audio content."""
    root = parts[0].casefold()
    if root in DENIED_ROOTS:
        return False
    if root == "materials":
        return len(parts) > 1 and parts[1].casefold() in ALLOWED_MATERIAL_ROOTS
    return root in ALLOWED_ROOTS


def _reason(parts: list[str]) -> str | None:
    """Why one virtual path is not content-only, or None when it is allowed."""
    if _content(parts):
        return None
    root = parts[0].casefold()
    if root in DENIED_ROOTS:
        return f"{parts[0]}/ changes rendering, interface or startup content"
    if root == "materials":
        sub = parts[1].casefold() if len(parts) > 1 else ""
        return f"{'/'.join(parts[:2]) or parts[0]} overrides a visual material"
    return f"{parts[0]}/ is not skin or map content"


def _scan_materials(root: Path, evidence: list[str]) -> None:
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        raise MountInspectionError(f"materials could not be read ({exc.__class__.__name__})") from exc
    for child in children:
        if not _plain_path(child):
            raise MountInspectionError(f"materials/{child.name} is a link")
        if child.is_dir() and child.name.casefold() in ALLOWED_MATERIAL_ROOTS:
            continue
        evidence.append(f"materials/{child.name} overrides a visual material")


def _scan_vpk(pack: Path, evidence: list[str]) -> None:
    """Validate the complete directory tree, retaining bounded policy evidence."""
    try:
        _header, _tree_size, tree = _read_vpk_tree(pack, MAX_VPK_TREE_BYTES)
        for key, _crc, _preload, _archive, _offset, _length in _iter_vpk_tree(tree):
            parts = [part for part in key.replace("\\", "/").split("/") if part]
            if len(parts) < 2:
                continue  # A root-level readme or licence is not content.
            reason = _reason(parts)
            if reason:
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(f"{pack.name}: {reason} ({key})")
    except (OSError, ValueError) as exc:
        raise MountInspectionError(f"{pack.name} could not be checked ({exc.__class__.__name__})") from exc


def _vpk_has_content(pack: Path) -> bool:
    try:
        _header, _tree_size, tree = _read_vpk_tree(pack, MAX_VPK_TREE_BYTES)
        for key, _crc, _preload, _archive, _offset, _length in _iter_vpk_tree(tree):
            parts = [part for part in key.replace("\\", "/").split("/") if part]
            if len(parts) >= 2 and _content(parts):
                return True
    except (OSError, ValueError):
        pass
    return False


def _has_content(root: Path, depth: int = 0) -> bool:
    """Find reviewed skin/map/audio content even when the mount also has UI files."""
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return False
    for child in children:
        if _not_loaded(child, depth):
            continue
        try:
            plain = _plain_path(child)
        except OSError:
            continue
        if not plain:
            continue
        name = child.name.casefold()
        if child.is_file():
            if name.endswith("_dir.vpk") and _vpk_has_content(child):
                return True
            continue
        if name == "materials":
            try:
                if any(entry.is_dir() and entry.name.casefold() in ALLOWED_MATERIAL_ROOTS
                       for entry in child.iterdir()):
                    return True
            except OSError:
                pass
            continue
        if name in DENIED_ROOTS:
            continue
        if name in ALLOWED_ROOTS:
            return True
        if depth < MAX_CONTAINER_DEPTH and _has_content(child, depth + 1):
            return True
    return False


def _scan_loose(root: Path, depth: int, evidence: list[str]) -> None:
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        raise MountInspectionError(f"{root.name or 'mount'} could not be read ({exc.__class__.__name__})") from exc
    for child in children:
        if _not_loaded(child, depth):
            continue
        plain = _plain_path(child)
        if not plain:
            raise MountInspectionError(f"{child.name} is a link")
        name = child.name.casefold()
        if child.is_file():
            # Source 2 mounts a pack through its *_dir.vpk index; numbered
            # shards beside it carry payload only and have no tree of their own.
            if name.endswith("_dir.vpk"):
                _scan_vpk(child, evidence)
            continue
        if name in DENIED_ROOTS:
            evidence.append(f"{child.name}/ changes rendering, interface or startup content")
            continue
        if name == "materials":
            _scan_materials(child, evidence)
            continue
        if name in ALLOWED_ROOTS:
            continue
        if depth < MAX_CONTAINER_DEPTH and not _holds_compiled_files(child):
            _scan_loose(child, depth + 1, evidence)
            continue
        evidence.append(f"{child.name}/ is not skin or map content")


def classify_mount(root: Path, *, allow_overrides: bool = False) -> dict:
    """Return ``{"allowed", "reason", "evidence"}`` for one installed mount.

    ``allow_overrides`` is the explicit user override for mixed skin packs.
    The mount must still be a plain folder inside the game; only its content
    classification is skipped.
    """
    path = Path(root)
    try:
        if not _plain_path(path):
            return {"allowed": False, "reason": "the mount is a link", "evidence": []}
        if not path.is_dir():
            return {"allowed": False, "reason": "the mount folder is missing", "evidence": []}
    except FileNotFoundError:
        return {"allowed": False, "reason": "the mount folder is missing", "evidence": []}
    except OSError as exc:
        return {"allowed": False, "reason": f"the mount could not be read ({exc.__class__.__name__})",
                "evidence": []}
    if allow_overrides:
        return {"allowed": True, "reason": None, "evidence": [],
                "allow_overrides": True}
    evidence: list[str] = []
    try:
        _scan_loose(path, 0, evidence)
    except (OSError, MountInspectionError) as exc:
        return {"allowed": False, "reason": f"a mount entry could not be checked ({exc.__class__.__name__}): {exc}",
                "evidence": evidence[:MAX_EVIDENCE]}
    if evidence:
        if _has_content(path):
            # A skin pack may also ship its own HUD, post-processing or shader
            # content in the same VPK mount. Source 2 cannot mount only part of
            # that pack, so carry the mixed skin pack automatically; the
            # evidence remains visible in diagnostics. Pure UI/render packs
            # without models/maps/audio are still refused.
            return {"allowed": True, "reason": None,
                    "evidence": evidence[:MAX_EVIDENCE], "mixed_skin": True}
        return {"allowed": False, "reason": evidence[0], "evidence": evidence[:MAX_EVIDENCE]}
    return {"allowed": True, "reason": None, "evidence": []}


def _mount_path(game_dir: Path, value: str, game_root: Path) -> Path | None:
    """Resolve a SearchPaths value inside the game folder, or None."""
    text = value.replace("\\", "/").strip("/")
    parts = text.split("/")
    if not text or "|" in text or ":" in text or text.startswith("/") or ".." in parts:
        return None
    try:
        resolved = (game_dir / text).resolve()
        resolved.relative_to(game_root)
    except (OSError, ValueError):
        return None
    return resolved


CONTENT_MOUNT_KEYS = frozenset({"game", "addonroot", "officialaddonroot"})


def split_mounts(mounts, game_dir, *, log: logging.Logger | None = None,
                 allow_overrides: bool = False) -> tuple[list[dict], list[dict]]:
    """Split ``(key, path)`` mounts into carried and excluded records.

    Content keys mount files and are classified; ``Mod``/``Write`` only describe
    lookup and write paths, so they travel with a carried content mount.
    """
    game_dir = Path(game_dir)
    try:
        game_root = game_dir.resolve()
    except OSError:
        game_root = game_dir
    carried: list[dict] = []
    excluded: list[dict] = []
    content_carried = False
    for key, value in mounts:
        if key.casefold() not in CONTENT_MOUNT_KEYS:
            carried.append({"key": key, "path": value})
            continue
        root = _mount_path(game_dir, value, game_root)
        verdict = (classify_mount(root, allow_overrides=allow_overrides)
                   if root is not None else
                   {"allowed": False, "reason": "the mount is not a plain folder inside the game",
                    "evidence": []})
        record = {"key": key, "path": value}
        if verdict.get("allow_overrides"):
            record["allow_overrides"] = True
        if verdict.get("mixed_skin"):
            record["mixed_skin"] = True
        missing_addon_root = (key.casefold() != "game"
                              and verdict["reason"] == "the mount folder is missing")
        if verdict["allowed"] or missing_addon_root:
            # An addon root that does not exist yet loads nothing; its entry is
            # still carried so an installed official-addon folder keeps working.
            carried.append(record)
            content_carried = content_carried or bool(verdict["allowed"])
            if verdict.get("allow_overrides"):
                (log or LOG).warning(
                    "Loading mod mount %s with UI/render/startup overrides allowed by preference.",
                    value)
            elif verdict.get("mixed_skin"):
                (log or LOG).warning(
                    "Loading mixed skin mount %s with its included UI/render files; "
                    "captures may include modded interface content.",
                    value)
            continue
        excluded.append({**record, "reason": verdict["reason"], "evidence": verdict["evidence"]})
    if not content_carried:
        # Metadata without a content mount would still change addon lookup.
        carried = []
    for record in excluded:
        (log or LOG).warning("Not loading mod mount %s: %s", record["path"], record["reason"])
    return carried, excluded
