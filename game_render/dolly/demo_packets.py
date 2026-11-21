"""Read-only packet boundaries for completed Source 2 replay files.

Only record framing is inspected; packet payloads are skipped, not decoded or
executed. An unavailable/unsupported index leaves normal exact seeking intact.
"""
from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from dataclasses import dataclass
import math
import os
from pathlib import Path
import stat
import struct
import threading
from typing import Callable

_MAGIC = b"PBDEMS2\0"
SOURCETV_CLIENT_NAME = "SourceTV Demo"
_HEADER_FIELDS = {
    2: "patch_version", 3: "server_name", 4: "client_name", 5: "map_name",
    6: "game_directory", 7: "fullpackets_version", 11: "demo_version_name",
    12: "demo_version_guid", 13: "build_num", 14: "game_build_num",
}
_MAX_BYTES = (1 << 32) - 1  # This header stores 32-bit file offsets.
_MAX_RECORDS = 2_000_000
_MAX_PAYLOAD = 128 * 1024 * 1024
_MAX_HEADER = 64 * 1024
_MAX_METADATA = 1024 * 1024
_CACHE_ENTRIES = 2
_CACHE: OrderedDict[tuple, PacketIndex | None] = OrderedDict()
_CACHE_LOCK = threading.Lock()


class _InvalidIndex(ValueError):
    pass


@dataclass(frozen=True)
class PacketIndex:
    """Sorted packet ticks, with file identity checked by the public reader."""
    ticks: array
    total_ticks: int
    playback_time: float | None = None

    @property
    def tick_rate(self) -> float | None:
        """Replay ticks per second from ``CDemoFileInfo``, or None.

        Replay files are recorded at different rates (32 and 64 are both live
        today), so this is the only trustworthy conversion between a replay
        tick and an authored shot second. A missing or malformed duration
        leaves the rate unknown instead of guessing one.
        """
        if not self.playback_time or self.playback_time <= 0 or self.total_ticks <= 0:
            return None
        return self.total_ticks / self.playback_time

    def following(self, tick: int) -> int | None:
        # Tick zero / signon and the first snapshot floor have a separate,
        # explicitly reported engine policy in Controller._seek_tick.
        if not self.ticks or tick <= 0 or tick < self.ticks[0]:
            return None
        position = bisect_left(self.ticks, tick)
        return int(self.ticks[position]) if position < len(self.ticks) else None

    def preceding(self, tick: int) -> int | None:
        """Recorded packet at or before tick, without reversing seek direction."""
        if not self.ticks or tick <= 0:
            return None
        position = bisect_right(self.ticks, tick) - 1
        return int(self.ticks[position]) if position >= 0 else None


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _core(info):
    # Replacement, append and truncation all change the inode or the size. The
    # nanosecond timestamps are deliberately excluded: Windows updates a
    # freshly written file's times asynchronously, so a new stat() can report a
    # later value than an already-open handle and would otherwise invalidate a
    # perfectly valid scan under load (the long-standing CI flakiness).
    return (info.st_dev, info.st_ino, info.st_size)


def _varint(stream):
    value = 0
    for index in range(5):
        byte = stream.read(1)
        if not byte:
            raise _InvalidIndex("Truncated varint")
        number = byte[0]
        if index == 4 and number > 15:
            raise _InvalidIndex("Oversized uint32 varint")
        value |= (number & 127) << (7 * index)
        if number < 128:
            return value
    raise _InvalidIndex("Unterminated uint32 varint")


def _fields(data):
    """Parse bounded metadata fields without a protobuf/runtime dependency."""
    from io import BytesIO
    stream = BytesIO(data)
    values = {}
    while stream.tell() < len(data):
        key = _varint(stream)
        field, wire = key >> 3, key & 7
        if not field or field in values:
            raise _InvalidIndex("Unsupported metadata fields")
        if wire == 0:
            value = _varint(stream)
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value = stream.read(size)
            if len(value) != size:
                raise _InvalidIndex("Truncated fixed metadata")
        elif wire == 2:
            size = _varint(stream)
            if size > len(data) - stream.tell():
                raise _InvalidIndex("Truncated metadata string")
            value = stream.read(size)
        else:
            raise _InvalidIndex("Unsupported metadata wire type")
        values[field] = (wire, value)
    return values


def _scan(stream, size, check_cancelled):
    header = stream.read(16)
    if len(header) != 16 or header[:8] != _MAGIC:
        raise _InvalidIndex("Unsupported replay header")
    info_offset, spawn_offset = struct.unpack_from("<II", header, 8)
    if not 16 <= info_offset < size or not 16 <= spawn_offset < size:
        raise _InvalidIndex("Incomplete replay header")
    ticks = array("I")
    stop_tick = None
    info_tick = None
    playback_time = None
    spawn_seen = False
    seen_header = False
    last_record_tick = -1
    records = 0
    while stream.tell() < size:
        if records >= _MAX_RECORDS:
            raise _InvalidIndex("Replay record limit exceeded")
        if records % 1024 == 0 and check_cancelled is not None:
            check_cancelled()
        records += 1
        offset = stream.tell()
        command, tick, payload_size = _varint(stream), _varint(stream), _varint(stream)
        # Known Source 2 demo commands plus the compression bit. Future command
        # formats deliberately fall back to exact seeking instead of guessing.
        kind = command & ~64
        if not 0 <= kind <= 18:
            raise _InvalidIndex("Unsupported replay command")
        compressed = bool(command & 64)
        payload_end = stream.tell() + payload_size
        if payload_size > _MAX_PAYLOAD or payload_end > size:
            raise _InvalidIndex("Replay payload outside file")
        if records == 1:
            if command != 1 or tick != 0xffffffff or payload_size > _MAX_HEADER:
                raise _InvalidIndex("Missing initial file header")
            fields = _fields(stream.read(payload_size))
            if fields.get(1) != (2, _MAGIC):
                raise _InvalidIndex("Mismatched file stamp")
            seen_header = True
        elif kind == 1:
            raise _InvalidIndex("Repeated file header")
        if tick != 0xffffffff:
            if tick > 0x7fffffff or tick < last_record_tick:
                raise _InvalidIndex("Nonmonotonic replay ticks")
            last_record_tick = tick
        elif ticks:
            raise _InvalidIndex("Signon record after playback")
        if stop_tick is not None and kind not in (2, 15):
            raise _InvalidIndex("Playback data after Stop")
        if kind == 7:
            if tick == 0xffffffff or stop_tick is not None or not payload_size:
                raise _InvalidIndex("Invalid packet record")
            if not ticks or tick > ticks[-1]:
                ticks.append(tick)
        elif kind == 0:
            if compressed or payload_size or tick == 0xffffffff or stop_tick is not None:
                raise _InvalidIndex("Invalid Stop record")
            stop_tick = tick
        if offset == spawn_offset:
            if kind != 15:
                raise _InvalidIndex("Mismatched spawn-groups offset")
            spawn_seen = True
        if offset == info_offset:
            if command != 2 or stop_tick is None or payload_size > _MAX_METADATA:
                raise _InvalidIndex("Invalid completed FileInfo")
            fields = _fields(stream.read(payload_size))
            if fields.get(2) != (0, tick):
                raise _InvalidIndex("Mismatched playback tick count")
            info_tick = tick
            recorded = fields.get(1)
            if recorded is not None and recorded[0] == 5 and len(recorded[1]) == 4:
                seconds = struct.unpack("<f", recorded[1])[0]
                if math.isfinite(seconds) and seconds > 0:
                    playback_time = float(seconds)
        elif kind == 2:
            raise _InvalidIndex("Mismatched FileInfo offset")
        stream.seek(payload_end)
    if (not seen_header or not spawn_seen or not ticks or info_tick is None or
            stop_tick != info_tick or ticks[-1] > info_tick):
        raise _InvalidIndex("Incomplete replay packet index")
    return PacketIndex(ticks, info_tick, playback_time)


def replay_header(path) -> dict | None:
    """Bounded ``CDemoFileHeader`` metadata for diagnostics, or None.

    A replay this installed build cannot reconstruct can fatal Deadlock while
    it loads, so the recorded map, game build and demo version are what a
    support report needs. Only the first record is read; packet payloads are
    never decoded or executed, and every malformed shape returns None.
    """
    try:
        selected = Path(path).resolve(strict=True)
        size = selected.stat().st_size
        if not 16 <= size <= _MAX_BYTES:
            return None
        with selected.open("rb") as stream:
            header = stream.read(16)
            if len(header) != 16 or header[:8] != _MAGIC:
                return None
            info_offset, spawn_offset = struct.unpack_from("<II", header, 8)
            if not 16 <= info_offset < size or not 16 <= spawn_offset < size:
                return None
            command, tick, payload_size = _varint(stream), _varint(stream), _varint(stream)
            # SourceTV stamps this record with the signon tick; a player's own
            # ``record`` demo stamps it with tick 0. Both carry the same header.
            if command != 1 or tick not in (0, 0xffffffff) or not 0 < payload_size <= _MAX_HEADER:
                return None
            fields = _fields(stream.read(payload_size))
    except (OSError, TypeError, ValueError):
        return None
    if fields.get(1) != (2, _MAGIC):
        return None
    result = {"name": selected.name}
    for field, label in _HEADER_FIELDS.items():
        entry = fields.get(field)
        if entry is None:
            continue
        wire, value = entry
        if wire == 2:
            text = value.decode("utf-8", "replace")
            if text:
                result[label] = text
        elif wire == 0:
            result[label] = value
    return result


def recorded_by_player(path) -> str | None:
    """Name of the player whose ``record`` command wrote this demo, or None.

    SourceTV demos (match replays and ``tv_record``) name "SourceTV Demo" as
    their client and carry full packets. A demo written by the ``record``
    command names the recording player instead and has none, so the game can
    neither seek in it nor, on current builds, apply its snapshots: every one
    is refused as a delta from a tick the client does not have, and the scene
    stays frozen. Anything unreadable or unnamed is left to the game.
    """
    header = replay_header(path)
    if not header:
        return None
    client = header.get("client_name")
    if not isinstance(client, str) or not client or client == SOURCETV_CLIENT_NAME:
        return None
    return client


def packet_index(path, *, check_cancelled: Callable[[], None] | None = None) -> PacketIndex | None:
    """Return a bounded index or None; a changing file never supplies boundaries.

    Only two stat-keyed entries are retained. Cache hits still compare the open
    file and path identity; replacement, append, truncation and normal timestamp
    changes invalidate cached results. Cancellation propagates to the caller.
    """
    try:
        selected = Path(path).resolve(strict=True)
        if not stat.S_ISREG(selected.stat().st_mode):
            return None
        with selected.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 16 <= before.st_size <= _MAX_BYTES:
                return None
            identity = _identity(before)
            key = (str(selected), *identity)
            with _CACHE_LOCK:
                present = key in _CACHE
                cached = _CACHE.get(key)
                if present:
                    _CACHE.move_to_end(key)
            if check_cancelled is not None:
                check_cancelled()
            if present:
                result = cached
            else:
                try:
                    result = _scan(stream, before.st_size, check_cancelled)
                except _InvalidIndex:
                    result = None
            if (identity != _identity(os.fstat(stream.fileno())) or
                    _core(before) != _core(selected.stat())):
                return None
            if not present:
                with _CACHE_LOCK:
                    _CACHE[key] = result
                    _CACHE.move_to_end(key)
                    while len(_CACHE) > _CACHE_ENTRIES:
                        _CACHE.popitem(last=False)
            return result
    except (OSError, TypeError, ValueError):
        return None
