"""Live game frames from Dolly's native DLL (the "<session>.frames" mapping).

Layout matches native/include/dolly_stream.hpp. The controller process creates
the mapping; the game's DLL opens it by the session token and publishes each
clean Present (before Dolly's own UI) into one of three slots. With depth
requested, each frame also carries the game's scene depth (linear camera-axis
distance in Source units, float32, inf for the sky) from the same Present.
"""
from __future__ import annotations

import mmap
import struct
import zlib

MAGIC = b"DLYFRM01"
VERSION = 2
SLOTS = 3
HEADER_BYTES = 4096
SLOT_HEADER_OFFSET = 256
SLOT_HEADER_BYTES = 256
HEADER = struct.Struct("<8sIIQIIQQQII160s")
SLOT = struct.Struct("<QIIIIQQiId7dIIqIIIIdd")
ENABLED_OFFSET = 24
DEPTH_OFFSET = 28
LATEST_OFFSET = 32
CAMERA_OFFSET = 1024

# DXGI formats the native side publishes (4 bytes per pixel).
BGRA8 = {87, 91}          # B8G8R8A8_UNORM(_SRGB)
RGBA8 = {28, 29}          # R8G8B8A8_UNORM(_SRGB)
RGB10A2 = {24}            # R10G10B10A2_UNORM

DEPTH_STATES = {0: "off", 1: "ok", 2: "missing", 3: "failed"}


def mapping_name(token: str) -> str:
    return "Local\\DeadlockDollyNative_" + token + ".frames"


class FrameStream:
    def __init__(self, token: str, max_width: int = 3840, max_height: int = 2160):
        self.slot_bytes = max_width * max_height * 4
        # Colour slots, then the same number of depth slots (float32 per pixel).
        size = HEADER_BYTES + 2 * SLOTS * self.slot_bytes
        self._map = mmap.mmap(-1, size, tagname=mapping_name(token), access=mmap.ACCESS_WRITE)
        self._map[:HEADER_BYTES] = b"\0" * HEADER_BYTES
        self._map[:HEADER.size] = HEADER.pack(MAGIC, VERSION, SLOTS, self.slot_bytes, 0, 0, 0, 0, 0,
                                              0, 0, b"")
        self._seen = 0

    def close(self):
        if self._map is not None:
            self.enable(False)
            self._map.close()
            self._map = None

    def enable(self, on: bool = True):
        struct.pack_into("<I", self._map, ENABLED_OFFSET, 1 if on else 0)

    def set_depth(self, on: bool = True):
        """Also stream each frame's scene depth (costs a readback per frame)."""
        struct.pack_into("<I", self._map, DEPTH_OFFSET, 1 if on else 0)

    def set_camera(self, pose=None):
        """Stream the game camera: (x, y, z, pitch, yaw, roll, hfov_deg, aspect) or None to release."""
        seq = struct.unpack_from("<Q", self._map, CAMERA_OFFSET)[0]
        struct.pack_into("<Q", self._map, CAMERA_OFFSET, seq + 1 if seq % 2 == 0 else seq + 2)
        if pose is None:
            struct.pack_into("<I", self._map, CAMERA_OFFSET + 8, 0)
        else:
            struct.pack_into("<II8d", self._map, CAMERA_OFFSET + 8, 1, 0, *map(float, pose))
        seq = struct.unpack_from("<Q", self._map, CAMERA_OFFSET)[0]
        struct.pack_into("<Q", self._map, CAMERA_OFFSET, seq + 1)

    def header(self) -> dict:
        (magic, version, slots, slot_bytes, enabled, _r0, latest, presents, dropped, error, _r1,
         message) = HEADER.unpack_from(self._map, 0)
        return dict(enabled=enabled, latest=latest, presents=presents, dropped=dropped,
                    error=error, message=message.split(b"\0", 1)[0].decode("utf-8", "replace"))

    def latest(self, newer_than: int | None = None):
        """(meta, bytes) of the newest complete frame, or None if none is new."""
        threshold = self._seen if newer_than is None else newer_than
        for _ in range(4):
            latest = struct.unpack_from("<Q", self._map, LATEST_OFFSET)[0]
            if not latest or latest <= threshold:
                return None
            index = latest % SLOTS
            base = SLOT_HEADER_OFFSET + index * SLOT_HEADER_BYTES
            values = SLOT.unpack_from(self._map, base)
            if values[0] != latest:
                continue
            width, height, row_bytes, fmt = values[1:5]
            start = HEADER_BYTES + index * self.slot_bytes
            pixels = self._map[start:start + row_bytes * height]
            depth_width, depth_height, depth_state = values[20:23]
            depth = None
            if depth_state == 1:
                start = HEADER_BYTES + (SLOTS + index) * self.slot_bytes
                depth = self._map[start:start + depth_width * depth_height * 4]
            if SLOT.unpack_from(self._map, base)[0] != latest:
                continue  # overwritten while copying
            meta = dict(seq=latest, width=width, height=height, row_bytes=row_bytes, format=fmt,
                        present=values[5], view_serial=values[6], tick=values[7],
                        paused=bool(values[8]), phase=values[9], pose=list(values[10:17]),
                        camera_ack=values[17], qpc=values[19],
                        depth_state=DEPTH_STATES.get(depth_state, "failed"),
                        depth_width=depth_width, depth_height=depth_height,
                        depth_near=values[24], depth_far=values[25], depth=depth)
            self._seen = latest
            return meta, pixels
        return None


def to_rgba8(meta: dict, pixels: bytes) -> bytes:
    """Top-down RGBA8 bytes (alpha forced opaque)."""
    fmt = meta["format"]
    data = bytearray(pixels)
    if fmt in BGRA8:
        data[0::4], data[2::4] = data[2::4], data[0::4]
    elif fmt in RGB10A2:
        import numpy as np
        words = np.frombuffer(pixels, dtype="<u4")
        out = np.empty((words.size, 4), dtype=np.uint8)
        for channel, shift in enumerate((0, 10, 20)):
            out[:, channel] = ((words >> shift) & 0x3FF) >> 2
        out[:, 3] = 255
        return out.tobytes()
    elif fmt not in RGBA8:
        raise ValueError(f"Unsupported stream format {fmt}")
    data[3::4] = b"\xff" * (len(data) // 4)
    return bytes(data)


def write_png(path, width: int, height: int, rgba: bytes):
    stride = width * 4
    raw = b"".join(b"\0" + rgba[row * stride:(row + 1) * stride] for row in range(height))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body +
                struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
