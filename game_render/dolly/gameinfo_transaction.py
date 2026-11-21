"""Gameinfo journal validation and exact original-byte restoration.

Lower-level defaults serve recovery callers; the launcher can supply its existing
installation-validation and read/save/write boundaries. This module owns no process checks, locks or deployment removal.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Protocol

from .launch_errors import LaunchError
from .file_ops import _atomic_write
from .game_installation import validate_game as _validate_game


class GameinfoConflict(LaunchError):
    """gameinfo.gi changed outside Dolly while Dolly's version was mounted.

    Dolly never overwrites those bytes on its own; resolve_conflict applies
    the user's explicit choice.
    """

    def __init__(self, message: str, *, session_dir: Path, target: Path, backup: Path):
        super().__init__(message)
        self.session_dir = Path(session_dir)
        self.target = Path(target)
        self.backup = Path(backup)


class GameinfoPaths(Protocol):
    """Only the validated installation fields consumed by a journal."""

    @property
    def game_dir(self) -> Path: ...

    @property
    def gameinfo(self) -> Path: ...


class AtomicWriter(Protocol):
    def __call__(self, path: Path, data: bytes, mode: int | None = None) -> None: ...


def save_record(session_dir: Path, record: dict[str, Any], *, atomic_write: AtomicWriter = _atomic_write) -> None:
    atomic_write(session_dir / "session.json", (json.dumps(record, indent=2) + "\n").encode("utf-8"))


def _same_session(recorded: str, session_dir: Path) -> bool:
    """The journal's own folder, or that same logs/<id> folder after the whole
    Dolly folder was moved or renamed. The stored path is never rewritten; the
    backup's hash and the game-side checks still decide what may be restored."""
    recorded = Path(recorded)
    if recorded.resolve() == session_dir.resolve():
        return True
    return (recorded.is_absolute() and recorded.name == session_dir.name
            and recorded.parent.name == "logs" and session_dir.resolve().parent.name == "logs")


def load_record(session_dir: Path, *, validate_game: Callable[[Path], GameinfoPaths] = _validate_game) -> dict[str, Any]:
    try:
        from .file_ops import _plain_path, _plain_ancestors
        if not _plain_ancestors(session_dir) or not _plain_path(session_dir / "session.json"):
            raise ValueError("linked session directory or journal")
        record = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("owner") != "Deadlock Dolly" or not _same_session(record["session_dir"], session_dir):
            raise ValueError("unrecognized session owner or directory")
        paths = validate_game(Path(record["original_gameinfo"]).parent)
        if paths.gameinfo.resolve() != Path(record["original_gameinfo"]).resolve():
            raise ValueError("unexpected gameinfo target")
        overlay = Path(record["overlay_dir"])
        if overlay.parent.resolve() != paths.game_dir.resolve() or not re.fullmatch(r"citadel_dolly_[a-z0-9_]+", overlay.name):
            raise ValueError("unexpected plugin mount directory")
        if record["backup_name"] != "original.gameinfo.gi":
            raise ValueError("unexpected backup name")
        if not all(re.fullmatch(r"[0-9a-f]{64}", record[key]) for key in ("original_sha256", "patched_sha256")):
            raise ValueError("invalid content hashes")
        return record
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise LaunchError(f"Could not read Dolly's recovery journal at {session_dir}: {exc}") from exc


def restore_record(
    session_dir: Path, *, load_record: Callable[[Path], dict[str, Any]] = load_record,
    save_record: Callable[[Path, dict[str, Any]], None] = save_record, atomic_write: AtomicWriter = _atomic_write,
) -> bool:
    record = load_record(session_dir)
    if record.get("config_state") == "restored":
        return True
    target = Path(record["original_gameinfo"])
    backup = session_dir / record["backup_name"]
    try:
        from .file_ops import _plain_path
        if not _plain_path(backup) or not _plain_path(target):
            raise LaunchError("Linked game configuration or backup left untouched during recovery.")
        original = backup.read_bytes()
        if hashlib.sha256(original).hexdigest() != record["original_sha256"]:
            raise LaunchError(f"Dolly's original gameinfo backup failed its hash check. Nothing was overwritten. Backup: {backup}")
        current = target.read_bytes()
        digest = hashlib.sha256(current).hexdigest()
        if digest == record["original_sha256"]:
            record["config_state"] = "restored"
        elif digest == record["patched_sha256"]:
            atomic_write(target, original, record.get("original_mode"))
            record["config_state"] = "restored"
        else:
            record["config_state"] = "conflict"
            save_record(session_dir, record)
            raise GameinfoConflict(
                f"Deadlock gameinfo.gi changed after Dolly mounted its plugin. Dolly left those newer bytes untouched. Original backup: {backup}\nCompare the current file with this backup before another Dolly launch; recovery remains pending.",
                session_dir=session_dir, target=target, backup=backup)
        record["restored_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_record(session_dir, record)
        return True
    except OSError as exc:
        raise LaunchError(f"Could not restore Deadlock gameinfo.gi: {exc}\nOriginal backup: {backup}. Exit Deadlock, then use File > Recover game configuration in Dolly.") from exc

def resolve_conflict(
    session_dir: Path, *, keep_current: bool, load_record: Callable[[Path], dict[str, Any]] = load_record,
    save_record: Callable[[Path, dict[str, Any]], None] = save_record, atomic_write: AtomicWriter = _atomic_write,
) -> str:
    """Apply the user's choice for a gameinfo.gi changed outside Dolly.

    keep_current keeps the newer file and retires Dolly's backup obligation,
    unless that file still mounts Dolly's temporary folder (it could not load
    once the folder is removed). Otherwise the verified original is written
    back. Callers must ensure Deadlock is not running.
    """
    record = load_record(session_dir)
    if record.get("config_state") == "restored":
        return "restored"
    target = Path(record["original_gameinfo"])
    backup = session_dir / record["backup_name"]
    try:
        from .file_ops import _plain_path
        if not _plain_path(backup) or not _plain_path(target):
            raise LaunchError("Linked game configuration or backup left untouched during recovery.")
        original = backup.read_bytes()
        if hashlib.sha256(original).hexdigest() != record["original_sha256"]:
            raise LaunchError(f"Dolly's original gameinfo backup failed its hash check. Nothing was overwritten. Backup: {backup}")
        if keep_current:
            overlay = Path(record["overlay_dir"]).name
            if overlay.encode("utf-8").lower() in target.read_bytes().lower():
                raise LaunchError(
                    "The current gameinfo.gi still loads Dolly's temporary folder, so it cannot be kept as is. "
                    "Choose to put your original back instead.")
            record["conflict_resolution"] = "kept_current"
        else:
            atomic_write(target, original, record.get("original_mode"))
            record["conflict_resolution"] = "restored_original"
        record["config_state"] = "restored"
        record["restored_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_record(session_dir, record)
        return record["conflict_resolution"]
    except OSError as exc:
        raise LaunchError(f"Could not update Deadlock gameinfo.gi: {exc}\nOriginal backup: {backup}") from exc
