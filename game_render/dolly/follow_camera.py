"""Validated settings for the stock spectator rig; no implicit game access.

The controller must establish replay ownership and typed capabilities before
creating a transaction. This module does not grant a camera owner or write
through the native effect allowlist.
"""
from dataclasses import dataclass
import math
import struct

PREFIX = "citadel_camera_override_"
ENABLED = "citadel_camera_overrides_enabled"
FOLLOW_AIM = "citadel_camera_spectator_auto_target_view"
# Reviewed build-6712 registration limits, not restore values.
BOUNDS = {
    "ads_fov": (20, 120), "fov": (50, 150),
    "ads_x_offset": (-400, 100), "x_offset": (-400, 100),
    "ads_y_offset": (-150, 150), "y_offset": (-150, 150),
    "ads_z_offset": (-150, 150), "z_offset": (-150, 150),
    "pivot_x_offset": (-100, 100), "pivot_y_offset": (-100, 100),
    "pivot_z_offset": (0, 200),
    "pivot_x_offset_crouching": (-100, 100),
    "pivot_y_offset_crouching": (-100, 100),
    "pivot_z_offset_crouching": (0, 200),
    "x_worst_case_offset": (-100, 100),
    "y_worst_case_offset": (-100, 100),
    "z_worst_case_offset": (-100, 100),
}
NAMES = tuple(PREFIX + suffix for suffix in BOUNDS) + (FOLLOW_AIM, ENABLED)


def validate_value(name, value):
    if name not in NAMES:
        raise ValueError("Unreviewed Game Follow setting")
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("Game Follow settings must be finite numbers")
    low, high = (0, 1) if name in (ENABLED, FOLLOW_AIM) else BOUNDS[name[len(PREFIX):]]
    if not low <= value <= high or (name in (ENABLED, FOLLOW_AIM) and value not in (0, 1)):
        raise ValueError("Game Follow setting is outside its reviewed range: " + name)
    return float(value)


@dataclass(frozen=True)
class FollowSettings:
    """Camera-space height, distance behind the pivot, and shoulder to the right."""
    distance: float = 135
    shoulder: float = 34
    height: float = 0

    def values(self):
        if isinstance(self.distance, bool) or not isinstance(self.distance, (int, float)) or self.distance < 0:
            raise ValueError("Game Follow distance must be nonnegative")
        values = {
            PREFIX + "x_offset": -self.distance,
            PREFIX + "y_offset": -self.shoulder,
            PREFIX + "z_offset": self.height,
            FOLLOW_AIM: 1,
            ENABLED: 1,
        }
        # Validate UI inputs before negation can turn Boolean values into ints.
        for value in (self.shoulder, self.height):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Game Follow offsets must be numbers")
        return {name: validate_value(name, value) for name, value in values.items()}


class FollowTransaction:
    """Save once, verify every write, and retain originals if restoration fails.

    read/write are caller-supplied typed, ownership-checked operations. Capability
    discovery belongs to the controller and must precede this transaction.
    Standing and crouching share the current standing pivot while Follow owns
    the rig. ADS offsets and wall settings stay as found. The aimed FOV starts
    from the normal lens so the weapon's own zoom is applied once, not a second time.
    """
    def __init__(self, read, write):
        self.read = read
        self.write = write
        self.originals = {}
        self.restoring = False

    def _write_verified(self, name, value):
        self.write(name, value)
        actual = validate_value(name, self.read(name))
        if struct.pack('<f', actual) != struct.pack('<f', value):
            raise RuntimeError("Game Follow readback mismatch: " + name)

    def apply(self, settings):
        values = settings.values()
        snapshot = self.originals
        if not self.originals:
            # Finish all reads and validation before the first mutation.
            snapshot = {name: validate_value(name, self.read(name)) for name in NAMES}
        normal_fov = (validate_value(PREFIX + "fov", self.read(PREFIX + "fov"))
                      if self.originals else snapshot[PREFIX + "fov"])
        try:
            values[PREFIX + "ads_fov"] = validate_value(PREFIX + "ads_fov", normal_fov)
        except ValueError as exc:
            raise ValueError("Game Follow needs a normal FOV between 50 and 120 to preserve weapon zoom.") from exc
        # The stock crouch blend can briefly pulse at slide boundaries. Keep
        # Follow's pivot fixed through it, without changing the hero's world
        # movement, aiming, collision or the native anchor correction.
        for axis in "xyz":
            standing = PREFIX + "pivot_" + axis + "_offset"
            crouching = standing + "_crouching"
            pivot = (validate_value(standing, self.read(standing))
                     if self.originals else snapshot[standing])
            values[crouching] = validate_value(crouching, pivot)
        if not self.originals:
            self.originals = snapshot
        try:
            for name, value in values.items():
                if name != ENABLED:
                    self._write_verified(name, value)
            self._write_verified(ENABLED, values[ENABLED])
        except Exception as failure:
            try:
                self.restore()
            except Exception as restoration:
                raise RuntimeError("Game Follow failed; original settings still need restoration") from restoration
            raise failure

    def restore(self):
        if not self.originals:
            return
        # Suspend overrides while restoring the complete descriptor; original
        # enable state is restored only after every other value verifies.
        self.restoring = True
        try:
            self._write_verified(ENABLED, 0)
            for name, value in self.originals.items():
                if name != ENABLED:
                    self._write_verified(name, value)
            self._write_verified(ENABLED, self.originals[ENABLED])
            self.originals.clear()
        finally:
            self.restoring = False
