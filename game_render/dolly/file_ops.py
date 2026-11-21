"""Atomic file replacement and ordinary-path guards for local configuration IO.

Callers own file bounds, transaction policy, ownership and recovery decisions.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile


def _atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Replace one file atomically after flushing its new contents to disk.

    Steam validation and modding guides can leave gameinfo.gi read-only, and
    Windows refuses to replace a read-only destination. Clear exactly that
    attribute for the swap. Prepare the recorded mode on the completed temporary
    first, so replacement publishes the new bytes and mode together.
    """
    descriptor, name = tempfile.mkstemp(prefix=".dolly-write-", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    writable = stat.S_IWRITE | stat.S_IREAD
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        try:
            os.replace(temp, path)
        except PermissionError:
            if os.name != "nt" or not path.exists():
                raise
            original_mode = stat.S_IMODE(path.stat().st_mode)
            os.chmod(path, writable)
            try:
                os.replace(temp, path)
            except BaseException:
                try:
                    os.chmod(path, original_mode)
                except OSError:
                    pass  # Preserve the replacement error if restoration is denied too.
                raise
    finally:
        try:
            if temp.exists():
                os.chmod(temp, writable)
                temp.unlink()
        except OSError:
            pass  # A locked leftover must not hide the real write result.


def _plain_path(path: Path) -> bool:
    """Do not traverse symbolic links, Windows junctions or other reparse points.

    A reparse point that resolves to itself redirects nothing and stays plain.
    Wine reports every Unix mount point that way, including the root behind
    the Z: drive, so Proton paths would otherwise all look linked.
    """
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return False
    if not getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return True
    return os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.abspath(path))


def _plain_ancestors(path: Path) -> bool:
    return all(_plain_path(parent) for parent in (path, *path.parents))
