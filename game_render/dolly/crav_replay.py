"""Bounded, cancellable replay identity pinning for a new capture.

PacketIndex reads completed FileInfo framing; its playback tick count and rate
match RenderLock's pinned Boon reader. No runtime tick or filename is identity.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import stat

from .demo_packets import packet_index, replay_header


def _identity(info):
    # Windows/Python can expose pathname ctime as creation time while fstat
    # exposes change time. Compare ctime only between two open-handle reads.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def pin_replay(path, *, check_cancelled=lambda: None) -> dict:
    """Read one stable regular file; mutations/replacement during the pin fail.

    Ordinary filesystem identity/timestamp checks are fences, not protection
    against a hostile writer restoring timestamps. The content SHA is rechecked
    after capture and by the receiving computer before reconstruction.
    """
    selected = Path(path).resolve(strict=True)
    check_cancelled()
    with selected.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 16 <= before.st_size < 2**32:
            raise ValueError("RenderLock export requires a bounded completed replay file")
        header = replay_header(selected)
        index = packet_index(selected, check_cancelled=check_cancelled)
        if (not header or index is None or index.tick_rate is None
                or not math.isfinite(index.tick_rate) or index.tick_rate <= 0
                or type(header.get("build_num")) is not int or header["build_num"] <= 0
                or not isinstance(header.get("map_name"), str) or not header["map_name"]):
            raise ValueError("Replay metadata is incomplete or unsupported for RenderLock export")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            check_cancelled()
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError("Replay was truncated while pinning capture identity")
            digest.update(block)
            remaining -= len(block)
        check_cancelled()
        after = os.fstat(stream.fileno())
        if (stream.read(1) or _identity(before) != _identity(after)
                or before.st_ctime_ns != after.st_ctime_ns
                or _identity(before) != _identity(selected.stat())):
            raise ValueError("Replay changed while pinning capture identity")
    return dict(path=selected.name, sha256=digest.hexdigest(), build=header["build_num"],
                map=header["map_name"], tick_rate=index.tick_rate, total_ticks=index.total_ticks)
