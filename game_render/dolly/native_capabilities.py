"""Validated optional native feature admission, separate from camera telemetry.

The block records reviewed startup prerequisites, not rendered-output quality.
Older verified helpers leave it zero and retain their original startup contract.
"""
from __future__ import annotations

import struct

OFFSET = 2 * 1024 * 1024 + 23144
WIRE = struct.Struct('<8s10I')
MAGIC = b'DLYCAP01'
ABI = 1
FEATURES = ('camera_core', 'camera_effects', 'follow_anchor')
STATES = ('unchecked', 'ready', 'not_required', 'unavailable')
REASONS = ('none', 'core_admission', 'signature_mismatch', 'hook_installation', 'initialization_exception')


def unpack(data, game_pid):
    if len(data) != WIRE.size:
        raise ValueError('Native capability report is truncated')
    if not any(data):
        return None
    magic, sequence, abi, pid, count, *values = WIRE.unpack(data)
    if magic != MAGIC or abi != ABI or sequence & 1 or pid != game_pid or count != len(FEATURES):
        raise ValueError('Native capability report has an invalid protocol or process identity')
    result = {}
    for name, state, reason in zip(FEATURES, values[::2], values[1::2]):
        if state >= len(STATES) or reason >= len(REASONS):
            raise ValueError('Native capability report has an unknown result')
        if (state == 3) != (reason != 0) or (name == 'camera_core' and state == 2):
            raise ValueError('Native capability report has an inconsistent result')
        result[name] = {'state': STATES[state], 'reason': REASONS[reason]}
    return result


def available(report, feature):
    if feature not in ('camera', 'follow'):
        raise ValueError('Unknown native capability: ' + str(feature))
    if report is None:
        return True  # Older verified helpers refused startup on these failures.
    if 'error' in report:
        return False
    core = report['camera_core']['state'] == 'ready'
    effects = report['camera_effects']['state'] in ('ready', 'not_required')
    follow = report['follow_anchor']['state'] in ('ready', 'not_required')
    return core and effects and (feature == 'camera' or follow)
