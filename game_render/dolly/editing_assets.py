"""Load reviewed cfg assets and deploy them only inside Dolly's owned mount."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .file_ops import _atomic_write, _plain_ancestors
from .game_installation import validate_game
from .launch_errors import LaunchError
from .runtime import resource_root
from .settings import _unique_object


EDITING_ROOT = resource_root(Path(__file__).resolve().parent.parent) / "assets" / "editing"
MAX_FILES = 64
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
REQUIRED = frozenset({
    "autoexec.cfg", "config.cfg", "machine_convars_default.vcfg",
    "user_keys_default.vcfg", "user_keys_dev_default.vcfg", "configschema.vcfg",
    "configdefaults.pc.vcfg",
})
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _names(files: dict) -> None:
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES or not REQUIRED <= files.keys():
        raise LaunchError("Dolly's editing cfg list is incomplete or exceeds its file limit.")
    for name in files:
        if (not isinstance(name, str) or re.fullmatch(r"[a-z0-9_][a-z0-9_.-]{0,119}\.(?:cfg|vcfg|vdf)", name) is None
                or name.split(".", 1)[0] in _RESERVED):
            raise LaunchError("Dolly's editing cfg list contains an unsafe filename.")


def _read(path: Path) -> bytes:
    if not _plain_ancestors(path) or not path.is_file():
        raise LaunchError(f"Dolly's editing assets must be ordinary local files: {path}")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise LaunchError("Dolly's editing asset exceeds its file size limit.")
    return data


def _json(data: bytes):
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)


def _payloads(files: dict[str, bytes]) -> None:
    _names(files)
    if any(not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES for data in files.values()):
        raise LaunchError("Dolly's editing cfg data is invalid or exceeds its file size limit.")
    if sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise LaunchError("Dolly's editing cfg assets exceed their total size limit.")


def load(editing_root: Path | None = None) -> dict[str, bytes]:
    """Verify every asset before the caller creates or changes a deployment."""
    try:
        root = EDITING_ROOT if editing_root is None else Path(editing_root)
        profile = _json(_read(root / "profile.json"))
        if not isinstance(profile, dict) or type(profile.get("format")) is not int or profile["format"] != 1:
            raise LaunchError("Dolly's editing profile has an unsupported format.")
        digest = profile.get("cfg_manifest_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise LaunchError("Dolly's editing cfg manifest has no reviewed fingerprint.")
        data = _read(root / "cfg-manifest.json")
        if hashlib.sha256(data).hexdigest() != digest:
            raise LaunchError("Dolly's editing cfg manifest failed its SHA-256 check.")
        manifest = _json(data)
        if not isinstance(manifest, dict) or type(manifest.get("format")) is not int or manifest["format"] != 1:
            raise LaunchError("Dolly's editing cfg manifest has an unsupported format.")
        entries = manifest.get("files")
        _names(entries)
        result = {}
        total = 0
        for name, entry in entries.items():
            digest = entry.get("sha256") if isinstance(entry, dict) else None
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise LaunchError(f"Dolly's editing cfg asset has an invalid fingerprint: {name}")
            payload = _read(root / "cfg" / name)
            if hashlib.sha256(payload).hexdigest() != digest:
                raise LaunchError(f"Dolly's editing cfg asset failed its SHA-256 check: {name}")
            total += len(payload)
            if total > MAX_TOTAL_BYTES:
                raise LaunchError("Dolly's editing cfg assets exceed their total size limit.")
            result[name] = payload
        return result
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise LaunchError(f"Could not verify Dolly's editing cfg assets: {exc}") from exc


def deploy(overlay: Path, files: dict[str, bytes]) -> None:
    """Copy a result from load() into a fresh cfg directory in an owned mount.

    Partial private writes remain available to normal owned-overlay cleanup.
    No installed or Steam-profile cfg directory is changed.
    """
    from .session_cleanup import _marker
    try:
        _payloads(files)
        overlay = Path(overlay)
        paths = validate_game(overlay.parent)
        _marker(overlay, paths)
        parent = overlay / "cvar_unlocker"
        target = parent / "cfg"
        if not parent.is_dir() or not _plain_ancestors(parent):
            raise LaunchError("Dolly's editing cfg deployment parent must be an ordinary owned directory.")
        if target.exists() or target.is_symlink():
            raise LaunchError("Dolly's editing cfg deployment already exists; existing files were left untouched.")
        target.mkdir(exist_ok=False)
        if not _plain_ancestors(target):
            raise LaunchError("Dolly's editing cfg deployment became linked during preparation.")
        for name, data in files.items():
            path = target / name
            _atomic_write(path, data)
            if _read(path) != data:
                raise LaunchError(f"Dolly's editing cfg deployment did not read back correctly: {name}")
    except (OSError, ValueError, TypeError) as exc:
        raise LaunchError(f"Could not deploy Dolly's editing cfg assets: {exc}") from exc
