"""Pinned legacy captured-slide profile and pure numeric/schema helpers.

This does not enable the old ambient/model-driver acquisition backends.
Native pins identify the retained capture contract, not the current installed ABI.
"""
import math
import numpy as np
NATIVE_DRIVER_DEFAULTS={'client_sha256':'2007fddaf9f48211e1ed188aff3c31ad47ba667cbaace16f5dd30e9376b884c7','particles_sha256':'5d5e29757162d86a4bb940c0f0b0189fbf12598d4238a7d27ec4082e9740ff7b'}

def require(value, message):
    if not value:
        raise ValueError('Observed sprite arrays: '+message)


def keys(value, expected, label):
    require(isinstance(value, dict) and set(value) == set(expected), label+' fields differ')


def digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def finite(value, bound=1e8):
    return type(value) in (float, int) and math.isfinite(value) and abs(value) <= bound


def native_color_bytes(rgb, alpha, alpha2):
    """Installed gather's float32 UNORM packing, before renderer alpha scale.

    Addition of 2**23 rounds to integer using native nearest-even float32. Its
    low byte becomes the channel. The admitted RGB range excludes wraparound;
    alpha is the native clamped product of attributes7 and16.
    """
    require(isinstance(rgb, list) and len(rgb) == 3
            and all(finite(v) and 0 <= v <= 1 for v in rgb)
            and finite(alpha) and finite(alpha2), 'unqualified native color range')
    f = np.float32
    values = [f(v) for v in rgb]+[f(min(f(1), max(f(0), f(f(alpha)*f(alpha2)))))]
    return [int(np.asarray(f(f(v*f(255))+f(8388608)), dtype='<f4').view('<u4')) & 255
            for v in values]
