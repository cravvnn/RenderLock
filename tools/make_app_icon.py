"""Regenerate artwork/DeadBlend.ico from the project artwork.

Run inside Blender (it decodes WebP and scales with the render pipeline):

    blender -b --factory-startup --python tools/make_app_icon.py

Source: artwork/deadblendmain.png (WebP data, 1280 x 1280)
Output: artwork/DeadBlend.ico (16-64 BMP entries, 128/256 PNG entries)

The .ico is then embedded into the launcher with tools/launcher/set_icon.py.
"""

import struct
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artwork" / "deadblendmain.png"
OUTPUT = ROOT / "artwork" / "DeadBlend.ico"
BMP_SIZES = (16, 24, 32, 48, 64)
PNG_SIZES = (128, 256)


def bmp_entry(size: int, rgba: bytes) -> bytes:
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         size * size * 4, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(size):
        row = rgba[y * size * 4:(y + 1) * size * 4]
        for x in range(size):
            r, g, b, a = row[x * 4:x * 4 + 4]
            xor += bytes((b, g, r, a))
    mask_row = ((size + 31) // 32) * 4
    mask = bytearray()
    for y in range(size):
        row = rgba[y * size * 4:(y + 1) * size * 4]
        bits = bytearray(mask_row)
        for x in range(size):
            if row[x * 4 + 3] < 128:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    return bytes(header) + bytes(xor) + bytes(mask)


def main() -> None:
    image = bpy.data.images.load(str(SOURCE))
    if image.size[0] != image.size[1]:
        raise SystemExit("icon source must be square")
    print("source:", image.size[0], "x", image.size[1])

    entries = []
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        for size in BMP_SIZES + PNG_SIZES:
            copy = image.copy()
            copy.scale(size, size)
            if size in BMP_SIZES:
                pixels = list(copy.pixels)  # bottom-up RGBA floats
                rgba = bytes(max(0, min(255, round(value * 255))) for value in pixels)
                entries.append((size, bmp_entry(size, rgba)))
            else:
                png = temp_path / f"icon-{size}.png"
                copy.filepath_raw = str(png)
                copy.file_format = "PNG"
                copy.save()
                entries.append((size, png.read_bytes()))
            print("scaled", size)

    count = len(entries)
    offset = 6 + 16 * count
    directory = struct.pack("<HHH", 0, 1, count)
    blobs = b""
    for size, data in entries:
        dim = 0 if size == 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    OUTPUT.write_bytes(directory + blobs)
    print("wrote", OUTPUT, len(directory + blobs), "bytes")


main()
