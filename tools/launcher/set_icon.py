"""Embed the RenderLock icon into a built launcher executable.

The launcher is a plain Rust executable; this tool adds the standard Windows
icon resources (RT_ICON + RT_GROUP_ICON) with the resource update API, so no
resource compiler is required. Run it after `cargo build --release`:

    python tools/launcher/set_icon.py
    python tools/launcher/set_icon.py --exe <path> --ico <path>

The icon file is produced from the project artwork by tools/make_app_icon.py.
"""

from __future__ import annotations

import argparse
import ctypes
import struct
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXE = ROOT / "tools" / "launcher" / "target" / "release" / "RenderLock.exe"
DEFAULT_ICO = ROOT / "artwork" / "RenderLock.ico"

RT_ICON = 3
RT_GROUP_ICON = 14


def read_ico(path: Path) -> list[tuple[int, int, int, int, int, bytes]]:
    data = path.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or kind != 1 or count == 0:
        raise SystemExit(f"{path} is not a Windows icon file")
    entries = []
    for index in range(count):
        width, height, _colors, _reserved, planes, bpp, size, offset = struct.unpack(
            "<BBBBHHII", data[6 + 16 * index:22 + 16 * index])
        blob = data[offset:offset + size]
        if len(blob) != size:
            raise SystemExit(f"{path}: entry {index} is truncated")
        entries.append((width, height, planes, bpp, size, blob))
    return entries


def inject(exe: Path, ico: Path) -> None:
    entries = read_ico(ico)
    kernel32 = ctypes.windll.kernel32
    kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
    kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    kernel32.UpdateResourceW.restype = wintypes.BOOL
    kernel32.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.WORD, ctypes.c_void_p, wintypes.DWORD]
    kernel32.EndUpdateResourceW.restype = wintypes.BOOL
    kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]

    handle = kernel32.BeginUpdateResourceW(str(exe), False)
    if not handle:
        raise SystemExit(f"cannot open {exe} for resource update (error {ctypes.get_last_error()})")
    try:
        for resource_id, (_w, _h, _planes, _bpp, size, blob) in enumerate(entries, start=1):
            buffer = ctypes.create_string_buffer(blob, size)
            if not kernel32.UpdateResourceW(handle, ctypes.c_void_p(RT_ICON),
                                            ctypes.c_void_p(resource_id), 0, buffer, size):
                raise SystemExit(f"RT_ICON {resource_id} update failed")
        group = struct.pack("<HHH", 0, 1, len(entries))
        for resource_id, (width, height, planes, bpp, size, _blob) in enumerate(entries, start=1):
            group += struct.pack("<BBBBHHIH", width, height, 0, 0, planes, bpp, size, resource_id)
        buffer = ctypes.create_string_buffer(group, len(group))
        if not kernel32.UpdateResourceW(handle, ctypes.c_void_p(RT_GROUP_ICON),
                                        ctypes.c_void_p(1), 0, buffer, len(group)):
            raise SystemExit("RT_GROUP_ICON update failed")
    except BaseException:
        kernel32.EndUpdateResourceW(handle, True)  # discard
        raise
    if not kernel32.EndUpdateResourceW(handle, False):
        raise SystemExit("EndUpdateResource failed; the executable was not modified")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    parser.add_argument("--ico", type=Path, default=DEFAULT_ICO)
    args = parser.parse_args()
    if not args.exe.is_file():
        raise SystemExit(f"executable not found: {args.exe}")
    if not args.ico.is_file():
        raise SystemExit(f"icon not found: {args.ico}")
    inject(args.exe, args.ico)
    print(f"icon embedded: {args.ico.name} -> {args.exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
