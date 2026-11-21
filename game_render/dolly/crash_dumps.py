"""Bounded, read-only analysis of Deadlock's Breakpad minidumps for support.

The diagnostics ZIP already carries the newest dumps. Naming the faulting
module and offset in ``diagnostics.json`` turns "the game crashed" into a
usable report without asking a user to install a debugger.
"""
from __future__ import annotations

import struct
from pathlib import Path

MAX_ANALYSIS_BYTES = 64 * 1024 * 1024
MAX_STREAMS = 4096
MAX_MODULES = 4096
_EXCEPTION_STREAM = 6
_MODULE_LIST_STREAM = 4


def _streams(data: bytes) -> dict[int, tuple[int, int]]:
    if len(data) < 32 or data[:4] != b"MDMP":
        raise ValueError("not a minidump")
    _signature, _version, count, directory = struct.unpack_from("<IIII", data, 0)
    if count > MAX_STREAMS or directory + count * 12 > len(data):
        raise ValueError("invalid stream table")
    streams = {}
    for index in range(count):
        kind, size, offset = struct.unpack_from("<III", data, directory + index * 12)
        if offset + size > len(data):
            raise ValueError("invalid stream bounds")
        streams.setdefault(kind, (size, offset))
    return streams


def _modules(data: bytes, streams: dict[int, tuple[int, int]]) -> list[tuple[int, int, str]]:
    if _MODULE_LIST_STREAM not in streams:
        return []
    _size, offset = streams[_MODULE_LIST_STREAM]
    if _size < 4 or offset + 4 > len(data):
        raise ValueError("truncated module list")
    count = struct.unpack_from("<I", data, offset)[0]
    if count > MAX_MODULES or 4 + count * 108 > _size or offset + 4 + count * 108 > len(data):
        raise ValueError("invalid module count")
    modules = []
    for index in range(count):
        base, image, _checksum, _timestamp, name_rva = struct.unpack_from(
            "<QIIII", data, offset + 4 + index * 108)
        if name_rva + 4 > len(data):
            continue
        length = struct.unpack_from("<I", data, name_rva)[0]
        start = name_rva + 4
        if length > 2048 or start + length > len(data):
            continue
        try:
            name = data[start:start + length].decode("utf-16-le")
        except UnicodeDecodeError:
            continue
        modules.append((base, image, name.replace("\\", "/").rsplit("/", 1)[-1]))
    return modules


def analyse(path: Path) -> dict:
    """Faulting module, offset, access type and address for one minidump."""
    target = Path(path)
    size = target.stat().st_size
    if size > MAX_ANALYSIS_BYTES:
        raise ValueError("minidump is too large to analyse")
    with target.open("rb") as stream:
        data = stream.read(MAX_ANALYSIS_BYTES + 1)
    if len(data) > MAX_ANALYSIS_BYTES:
        raise ValueError("minidump is too large to analyse")
    streams = _streams(data)
    if _EXCEPTION_STREAM not in streams:
        raise ValueError("minidump has no exception stream")
    _size, offset = streams[_EXCEPTION_STREAM]
    if _size < 40 or offset + 40 > len(data):
        raise ValueError("truncated exception stream")
    thread, _alignment = struct.unpack_from("<II", data, offset)
    base = offset + 8
    code, flags, _record, address, count = struct.unpack_from("<IIQQI", data, base)
    if count > 15 or 40 + count * 8 > _size or base + 32 + count * 8 > len(data):
        raise ValueError("invalid exception parameters")
    info = struct.unpack_from("<%dQ" % count, data, base + 32) if count else ()
    report = {"exception_code": "0x%08X" % code, "exception_flags": "0x%X" % flags,
              "address": "0x%X" % address, "thread": thread, "access": None,
              "access_address": None, "faulting_module": None, "module_offset": None}
    if count >= 2:
        kind = {0: "read", 1: "write", 8: "execute"}.get(info[0])
        if kind is not None:
            report["access"] = kind
            report["access_address"] = "0x%X" % info[1]
    for module_base, image, name in _modules(data, streams):
        if module_base <= address < module_base + image:
            report["faulting_module"] = name
            report["module_offset"] = "0x%X" % (address - module_base)
            break
    return report
