"""Bounded Players conversion, preserving the scalar display/alpha transform."""
import math
import struct

CHUNK_PIXELS = 65536


def convert_rgba(pixels, *, cancelled=lambda: False):
    from .player_layer import _preview_channel
    if len(pixels) % 8:
        raise ValueError('Truncated Players pixels.')
    try:
        import numpy as np
    except ImportError:
        np = None  # Source-only installs retain the dependency-free path.
    output = bytearray(len(pixels) // 2)
    for start in range(0, len(pixels), CHUNK_PIXELS * 8):
        if cancelled():
            raise InterruptedError('Players processing cancelled.')
        data = memoryview(pixels)[start:start + CHUNK_PIXELS * 8]
        offset = start // 2
        if np is None:
            for i, rgba in enumerate(struct.iter_unpack('<4e', data)):
                a = rgba[3]
                if not math.isfinite(a) or not 0 <= a <= 1:
                    raise ValueError('Invalid Players alpha.')
                if a:
                    if not all(math.isfinite(x) for x in rgba[:3]):
                        raise ValueError('Invalid Players color.')
                    output[offset+i*4:offset+i*4+4] = bytes(
                        [*(_preview_channel(x/a) for x in rgba[:3]), round(a*255)])
            continue
        rgba = np.frombuffer(data, dtype='<f2').reshape(-1, 4).astype(np.float64)
        alpha = rgba[:, 3]
        if not np.all(np.isfinite(alpha) & (alpha >= 0) & (alpha <= 1)):
            raise ValueError('Invalid Players alpha.')
        visible = alpha != 0
        result = np.zeros((len(rgba), 4), dtype=np.uint8)
        rgb = rgba[visible, :3]
        if not np.all(np.isfinite(rgb)):
            raise ValueError('Invalid Players color.')
        original = rgb / alpha[visible, None]
        mapped = np.maximum(original, 0)
        mapped /= 1 + mapped
        low = mapped <= .0031308
        mapped[low] *= 12.92
        mapped[~low] = 1.055 * np.power(mapped[~low], 1 / 2.4) - .055
        mapped = np.clip(mapped, 0, 1) * 255
        rounded = np.rint(mapped).astype(np.uint8)
        # SIMD pow and scalar pow can differ by a few ulps. Resolve rounding
        # boundaries with the original scalar expression, including ties-to-even.
        near = np.abs(mapped - (np.floor(mapped) + .5)) < 1e-10
        for row, channel in zip(*np.nonzero(near)):
            rounded[row, channel] = _preview_channel(float(original[row, channel]))
        result[visible, :3] = rounded
        result[visible, 3] = np.rint(alpha[visible] * 255).astype(np.uint8)
        output[offset:offset + result.size] = result.tobytes()
    return output
