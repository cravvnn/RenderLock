"""Placed-object record and its validated JSON form.

A placed object is one entry in the shot's object list: which library entry it
came from, where it sits in Source world space, how it is rotated and scaled,
and an optional model path (empty while the Object Picker draws proxy geometry).
This is a leaf module: no I/O, no engine access, no dependency on the controller
or the GUI. ``dolly/object_library.py`` owns the catalog; this owns one record.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .object_library import OBJECT_IDS, SCALE_MAX, SCALE_MIN

POSITION_LIMIT = 1_000_000.0
ANGLE_LIMIT = 100_000.0


@dataclass
class PlacedObject:
    library_id: str = "marker"
    model: str = ""
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    angles: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: float = 1.0

    def validate(self, label: str = "Object") -> None:
        if not isinstance(self.library_id, str) or self.library_id not in OBJECT_IDS:
            raise ValueError(f"{label} must name an object from the library")
        if not isinstance(self.model, str) or len(self.model) > 256:
            raise ValueError(f"{label} model path must be text no longer than 256 characters")
        _vec(self.position, label + ".position")
        _vec(self.angles, label + ".angles")
        if any(abs(value) > POSITION_LIMIT for value in self.position):
            raise ValueError(f"{label} position is outside the reviewed range")
        if any(abs(value) > ANGLE_LIMIT for value in self.angles):
            raise ValueError(f"{label} angles are outside the reviewed range")
        if isinstance(self.scale, bool) or not isinstance(self.scale, (int, float)) \
                or not math.isfinite(self.scale) or not SCALE_MIN <= self.scale <= SCALE_MAX:
            raise ValueError(f"{label} scale must be between {SCALE_MIN:g} and {SCALE_MAX:g}")

    def to_dict(self) -> dict:
        self.validate()
        return {"library_id": self.library_id, "model": self.model,
                "position": [float(v) for v in self.position],
                "angles": [float(v) for v in self.angles],
                "scale": float(self.scale)}

    @classmethod
    def from_dict(cls, value) -> "PlacedObject":
        if not isinstance(value, dict):
            raise ValueError("Object entry must be a JSON object")
        allowed = {"library_id", "model", "position", "angles", "scale"}
        extra = set(value) - allowed
        if extra:
            raise ValueError("Unknown object field: " + ", ".join(sorted(str(k) for k in extra)))
        missing = allowed - set(value)
        if missing:
            raise ValueError("Object is missing field: " + ", ".join(sorted(missing)))
        try:
            obj = cls(library_id=value["library_id"], model=value["model"],
                      position=list(value["position"]), angles=list(value["angles"]),
                      scale=value["scale"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid object entry: {exc}") from exc
        obj.validate()
        return obj


def _vec(value, label: str) -> None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must be three numbers")
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValueError(f"{label} must be three finite numbers")


def validate_objects(objects, label: str = "Objects") -> None:
    if not isinstance(objects, list):
        raise ValueError(f"{label} must be a list")
    for index, obj in enumerate(objects):
        if not isinstance(obj, PlacedObject):
            raise ValueError(f"{label} entry {index} must be a PlacedObject")
        obj.validate(f"{label} entry {index}")
