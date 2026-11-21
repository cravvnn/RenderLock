"""Make an optional, separate copy of a replay with a specific manifest workaround.

This recognizes one specific Source 2 recording pattern. It does not claim to
repair arbitrary demo corruption and never changes the selected recording.
"""
from __future__ import annotations

import os
import errno
import secrets
import hashlib
from pathlib import Path
import struct

from .demo_packets import packet_index

_MAX_PACKET = 8 * 1024 * 1024
_MAX_FILE = (1 << 32) - 1
_COPY_CHUNK = 1024 * 1024


class ReplayRepairUnavailable(ValueError):
    """The recording does not contain the narrowly supported repair pattern."""


def _read_varint(data: bytes, position: int, limit: int = 5) -> tuple[int, int]:
    value = 0
    for shift in range(limit):
        if position >= len(data):
            raise ReplayRepairUnavailable("The replay contains a truncated value.")
        byte = data[position]
        position += 1
        if shift == limit - 1 and byte > (15 if limit == 5 else 1):
            raise ReplayRepairUnavailable("The replay contains an oversized value.")
        value |= (byte & 127) << (shift * 7)
        if not byte & 128:
            return value, position
    raise ReplayRepairUnavailable("The replay contains an unterminated value.")


def _write_varint(value: int) -> bytes:
    output = bytearray()
    while value >= 128:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def _uncompress_snappy(data: bytes) -> bytes:
    """Decode one bounded raw Snappy block from a demo record."""
    expected, pos = _read_varint(data, 0)
    if expected > _MAX_PACKET:
        raise ReplayRepairUnavailable("The replay's first packet is too large to repair safely.")
    output = bytearray()
    while pos < len(data) and len(output) < expected:
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:
            length = tag >> 2
            if length < 60:
                length += 1
            else:
                extra = length - 59
                if pos + extra > len(data):
                    raise ReplayRepairUnavailable("The replay's compressed packet is truncated.")
                length = int.from_bytes(data[pos:pos + extra], "little") + 1
                pos += extra
            if pos + length > len(data) or len(output) + length > expected:
                raise ReplayRepairUnavailable("The replay's compressed packet is invalid.")
            output.extend(data[pos:pos + length])
            pos += length
            continue
        if kind == 1:
            length = 4 + ((tag >> 2) & 7)
            if pos >= len(data):
                raise ReplayRepairUnavailable("The replay's compressed packet is truncated.")
            offset = ((tag & 0xe0) << 3) | data[pos]
            pos += 1
        else:
            length = 1 + (tag >> 2)
            width = 2 if kind == 2 else 4
            if pos + width > len(data):
                raise ReplayRepairUnavailable("The replay's compressed packet is truncated.")
            offset = int.from_bytes(data[pos:pos + width], "little")
            pos += width
        if not offset or offset > len(output) or len(output) + length > expected:
            raise ReplayRepairUnavailable("The replay's compressed packet has an invalid copy.")
        start = len(output) - offset
        for index in range(length):
            output.append(output[start + index])
    if len(output) != expected or pos != len(data):
        raise ReplayRepairUnavailable("The replay's compressed packet has an invalid length.")
    return bytes(output)


def _compress_literal(data: bytes) -> bytes:
    """Write a valid raw Snappy block without changing the packet's bytes."""
    if not data:
        return b"\x00"
    size = len(data) - 1
    if size < 60:
        tag = bytes([size << 2])
    else:
        width = max(1, (size.bit_length() + 7) // 8)
        tag = bytes([(59 + width) << 2]) + size.to_bytes(width, "little")
    return _write_varint(len(data)) + tag + data


def _byte_field_range(packet: bytes, field: int, *, required=True) -> tuple[int, int, int] | None:
    """Find one protobuf bytes field without changing any other field."""
    pos = 0
    found = None
    while pos < len(packet):
        key, pos = _read_varint(packet, pos)
        number, wire = key >> 3, key & 7
        if not number:
            raise ReplayRepairUnavailable("The replay's first packet is invalid.")
        if wire == 0:
            _, pos = _read_varint(packet, pos, 10)
        elif wire in (1, 5):
            pos += 8 if wire == 1 else 4
        elif wire == 2:
            length_start = pos
            length, pos = _read_varint(packet, pos)
            end = pos + length
            if end > len(packet):
                raise ReplayRepairUnavailable("The replay's first packet is truncated.")
            if number == field:
                if found is not None:
                    raise ReplayRepairUnavailable("The replay has an ambiguous first packet.")
                found = (length_start, pos, end)
            pos = end
        else:
            raise ReplayRepairUnavailable("The replay uses an unsupported packet format.")
        if pos > len(packet):
            raise ReplayRepairUnavailable("The replay's first packet is truncated.")
    if found is None and required:
        raise ReplayRepairUnavailable("The replay has no first packet data to repair.")
    return found


def _packet_data_range(packet: bytes) -> tuple[int, int, int]:
    return _byte_field_range(packet, 3)


class _Bits:
    def __init__(self, data: bytes):
        self.data = data
        self.position = 0

    def read(self, count: int) -> int:
        start = self.position
        end = start + count
        if end > len(self.data) * 8:
            raise ReplayRepairUnavailable("The replay's message stream is truncated.")
        self.position = end
        value = int.from_bytes(self.data[start // 8:(end + 7) // 8], "little")
        return (value >> (start % 8)) & ((1 << count) - 1)

    def uint(self) -> int:
        first = self.read(6)
        selector = first & 48
        return ((first & 15) | (self.read({16: 4, 32: 8, 48: 28}[selector]) << 4)
                if selector else first)

    def varint(self) -> int:
        value = 0
        for index in range(5):
            byte = self.read(8)
            if index == 4 and byte > 15:
                raise ReplayRepairUnavailable("The replay's message length is invalid.")
            value |= (byte & 127) << (7 * index)
            if not byte & 128:
                return value
        raise ReplayRepairUnavailable("The replay's message length is invalid.")


def _proto_fields(data: bytes) -> list[tuple[int, int, object, int, int]]:
    """Read the boundaries of protobuf fields, including unknown fields."""
    rows = []
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field, wire = key >> 3, key & 7
        if not field:
            raise ReplayRepairUnavailable("The replay has an invalid spawn-group message.")
        if wire == 0:
            start = pos
            value, pos = _read_varint(data, pos, 10)
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            start = pos
            pos += length
            value = data[start:pos]
        elif wire in (1, 5):
            start = pos
            pos += 8 if wire == 1 else 4
            value = data[start:pos]
        else:
            raise ReplayRepairUnavailable("The replay uses an unsupported spawn-group message.")
        if pos > len(data):
            raise ReplayRepairUnavailable("The replay has a truncated spawn-group message.")
        rows.append((field, wire, value, start, pos))
    return rows


def _one_field(rows, number: int, wire: int):
    matches = [row for row in rows if row[0] == number]
    if len(matches) > 1 or (matches and matches[0][1] != wire):
        raise ReplayRepairUnavailable("The replay has an ambiguous spawn-group message.")
    return matches[0] if matches else None


def _network_range(packet: bytes, kind: int) -> tuple[int, int] | None:
    # A full-packet snapshot wraps CDemoPacket inside its own protobuf field.
    base = 0
    if kind == 13:
        wrapper = _byte_field_range(packet, 2, required=False)
        if wrapper is None:
            return None
        _, base, end = wrapper
        nested = packet[base:end]
    else:
        nested = packet
    _, start, end = _packet_data_range(nested)
    return base + start, base + end


def _scan_packet(packet: bytes, kind: int, record_offset: int, loads: dict, updates: dict) -> None:
    bounds = _network_range(packet, kind)
    if bounds is None:
        return
    start, end = bounds
    network = packet[start:end]
    bits = _Bits(network)
    ordinal = 0
    while len(network) * 8 - bits.position > 8:
        message, length = bits.uint(), bits.varint()
        body = bits.position
        end_bit = body + length * 8
        if end_bit > len(network) * 8:
            raise ReplayRepairUnavailable("The replay's message stream is truncated.")
        if message in (8, 9):
            payload = bytes(bits.read(8) for _ in range(length))
            rows = _proto_fields(payload)
            if message == 8:
                group = _one_field(rows, 4, 0)
                manifest = _one_field(rows, 8, 2)
                flags = _one_field(rows, 9, 0)
                incomplete = _one_field(rows, 11, 0)
                # Only the observed non-initial, explicitly incomplete group
                # with a real manifest is a candidate. Initial map loads and
                # unmatched/unknown cases remain untouched.
                if (group and 0 < group[2] <= 0xffffffff and manifest and manifest[2]
                        and flags and not (flags[2] & 8) and incomplete and incomplete[2] == 1):
                    if payload[incomplete[3]:incomplete[4]] != b"\x01":
                        raise ReplayRepairUnavailable("The replay has a noncanonical manifest flag.")
                    key = (record_offset, group[2], hashlib.sha256(payload).digest(), len(payload))
                    position = start * 8 + body + incomplete[3] * 8
                    loads.setdefault(key, []).append((record_offset, ordinal, position))
                    if len(loads) > 256 or len(loads[key]) > 16:
                        raise ReplayRepairUnavailable("The replay has too many incomplete groups to repair safely.")
            else:
                group = _one_field(rows, 1, 0)
                manifest = _one_field(rows, 2, 2)
                incomplete = _one_field(rows, 3, 0)
                if (group and 0 < group[2] <= 0xffffffff and manifest and manifest[2]
                        and incomplete and incomplete[2] == 0):
                    updates.setdefault(group[2], []).append((record_offset, ordinal))
                    if len(updates) > 256 or len(updates[group[2]]) > 4096:
                        raise ReplayRepairUnavailable("The replay has too many manifest updates to repair safely.")
        else:
            bits.position = end_bit
        ordinal += 1
        if ordinal > 100000:
            raise ReplayRepairUnavailable("The replay's packet has too many messages to repair safely.")
    # Remaining padding is left byte-for-byte intact; only selected flag bits
    # are changed, so its value has no bearing on the message boundaries.


def _manifest_patches(loads: dict, updates: dict) -> dict[int, list[int]]:
    """Require two identical loads and a later complete update for that group."""
    patches: dict[int, list[int]] = {}
    for (record, group, _digest, _length), occurrences in loads.items():
        if len(occurrences) != 2:
            continue
        last = max((event[0], event[1]) for event in occurrences)
        if not any(position > last for position in updates.get(group, ())):
            continue
        patches.setdefault(record, []).extend(event[2] for event in occurrences)
    return patches


def _stream_varint(stream) -> int:
    value = 0
    for index in range(5):
        byte = stream.read(1)
        if not byte or (index == 4 and byte[0] > 15):
            raise ReplayRepairUnavailable("The replay's record header is invalid.")
        value |= (byte[0] & 127) << (7 * index)
        if not byte[0] & 128:
            return value
    raise ReplayRepairUnavailable("The replay's record header is invalid.")


def _copy_exact(source, target, count: int) -> None:
    while count:
        chunk = source.read(min(_COPY_CHUNK, count))
        if not chunk:
            raise ReplayRepairUnavailable("The original replay changed while copying.")
        target.write(chunk)
        count -= len(chunk)


def repair_replay(source: Path, output: Path) -> Path:
    """Create a new .dem with the narrow manifest workaround; keep the original."""
    source = Path(source).resolve(strict=True)
    output = Path(output).resolve()
    if source == output or source.suffix.lower() != ".dem" or output.suffix.lower() != ".dem":
        raise ValueError("Choose a new .dem filename for the repaired copy.")
    indexed_stat = source.stat()
    index = packet_index(source)
    if index is None:
        raise ReplayRepairUnavailable("This is not a completed, supported replay file.")
    staging: Path | None = None
    try:
        with source.open("rb") as original:
            before = os.fstat(original.fileno())
            if (indexed_stat.st_dev, indexed_stat.st_ino, indexed_stat.st_size) != (
                    before.st_dev, before.st_ino, before.st_size):
                raise ReplayRepairUnavailable("The original replay was replaced while it was being checked.")
            header = original.read(16)
            if len(header) != 16 or header[:8] != b"PBDEMS2\0" or before.st_size > _MAX_FILE:
                raise ReplayRepairUnavailable("This is not a supported replay file.")
            info, spawn = struct.unpack_from("<II", header, 8)
            loads, updates, locations = {}, {}, {}
            while original.tell() < before.st_size:
                record_start = original.tell()
                command = _stream_varint(original)
                tick = _stream_varint(original)
                size_pos = original.tell()
                length = _stream_varint(original)
                body = original.tell()
                record_end = body + length
                if record_end > before.st_size:
                    raise ReplayRepairUnavailable("The replay has a truncated record.")
                kind = command & ~64
                if kind in (7, 8, 13):
                    if length > _MAX_PACKET:
                        raise ReplayRepairUnavailable("A replay packet is too large to repair safely.")
                    payload = original.read(length)
                    if len(payload) != length:
                        raise ReplayRepairUnavailable("The original replay changed while it was read.")
                    decoded = _uncompress_snappy(payload) if command & 64 else payload
                    if len(decoded) > _MAX_PACKET:
                        raise ReplayRepairUnavailable("A replay packet is too large to repair safely.")
                    _scan_packet(decoded, kind, record_start, loads, updates)
                    locations[record_start] = (command, tick, size_pos, body, record_end)
                else:
                    original.seek(record_end)
            patches = _manifest_patches(loads, updates)
            if not patches:
                raise ReplayRepairUnavailable(
                    "This replay does not contain the incomplete-manifest pattern this repair recognizes.")
            replacements = []
            for record_start, positions in sorted(patches.items()):
                command, _tick, size_pos, body, record_end = locations[record_start]
                if len(positions) != len(set(positions)):
                    raise ReplayRepairUnavailable("The replay has overlapping manifest flags.")
                original.seek(body)
                payload = original.read(record_end - body)
                decoded = _uncompress_snappy(payload) if command & 64 else payload
                fixed = bytearray(decoded)
                for bit in positions:
                    if bit >= len(fixed) * 8 or not (fixed[bit // 8] & (1 << (bit % 8))):
                        raise ReplayRepairUnavailable("A replay manifest changed during repair.")
                    fixed[bit // 8] &= ~(1 << (bit % 8))
                encoded = _compress_literal(fixed) if command & 64 else bytes(fixed)
                original.seek(record_start)
                prefix = original.read(size_pos - record_start)
                replacement = prefix + _write_varint(len(encoded)) + encoded
                replacements.append((record_start, record_end, replacement))
            after_scan = os.fstat(original.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after_scan.st_dev, after_scan.st_ino, after_scan.st_size, after_scan.st_mtime_ns):
                raise ReplayRepairUnavailable("The original replay changed while it was being checked.")
            delta = sum(len(data) - (end - start) for start, end, data in replacements)
            last_end = replacements[-1][1]
            if not (info >= last_end and spawn >= last_end
                    and 16 <= info + delta <= _MAX_FILE and 16 <= spawn + delta <= _MAX_FILE
                    and before.st_size + delta <= _MAX_FILE):
                raise ReplayRepairUnavailable("The replay's footer cannot be adjusted safely.")
            fixed_header = header[:8] + struct.pack("<II", info + delta, spawn + delta)
            if output.exists():
                raise FileExistsError(errno.EEXIST, "File exists", str(output))
            candidate_path = output.with_name(f".dolly-repair-{secrets.token_hex(16)}.tmp")
            with candidate_path.open("xb") as target:
                staging = candidate_path
                target.write(fixed_header)
                previous = 16
                for start, end, replacement in replacements:
                    original.seek(previous)
                    _copy_exact(original, target, start - previous)
                    target.write(replacement)
                    previous = end
                original.seek(previous)
                _copy_exact(original, target, before.st_size - previous)
                target.flush()
                os.fsync(target.fileno())
            after = os.fstat(original.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ReplayRepairUnavailable("The original replay changed while the copy was made.")
            current_source = source.stat()
            if (before.st_dev, before.st_ino, before.st_size) != (
                    current_source.st_dev, current_source.st_ino, current_source.st_size):
                raise ReplayRepairUnavailable("The original replay was replaced while the copy was made.")
        verified = packet_index(staging)
        if (verified is None or verified.ticks != index.ticks or verified.total_ticks != index.total_ticks
                or verified.playback_time != index.playback_time):
            raise ReplayRepairUnavailable("The repaired copy did not pass its replay checks.")
        if os.name == "nt":
            os.rename(staging, output)  # Windows refuses an existing destination.
        else:
            os.link(staging, output)  # Same-filesystem no-replace publication.
        return output
    finally:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass  # A private leftover must not mask the repair result.
