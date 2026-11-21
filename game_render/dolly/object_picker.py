"""Pure project edits for placed objects, driven by the Object Picker UI.

The Object Picker is its own feature: the desktop toggle only decides whether the
mode is open; the in-game right-side window and scene canvas send placement,
selection and deletion as editor events. This module turns those events into
validated project mutations. It is a leaf: no engine access, no Tk, no wire.

The placed list lives on the project (``Project.objects``) so it persists with
the shot. Every edit deep-copies the list so a rejected edit never mutates the
live project.
"""
from __future__ import annotations

from copy import deepcopy

from .object_library import OBJECT_DEFAULT, OBJECT_IDS
from .object_model import PlacedObject

MAX_OBJECTS = 64
DISTANCE_MIN = 10.0
DISTANCE_MAX = 20000.0
DISTANCE_DEFAULT = 600.0


def objects(project) -> list:
    """The project's placed list, or an empty list when it has none."""
    return list(getattr(project, "objects", None) or [])


def place(project, library_id, position, *, angles=None, scale=1.0):
    """Return a new project with one object appended. Rejects unknown ids."""
    if library_id not in OBJECT_IDS:
        raise ValueError("Choose an object from the library")
    if len(objects(project)) >= MAX_OBJECTS:
        raise ValueError(f"Place at most {MAX_OBJECTS} objects in a shot")
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise ValueError("Placement needs a world position")
    candidate = deepcopy(project)
    item = PlacedObject(library_id=library_id, model="",
                        position=[float(v) for v in position],
                        angles=[float(v) for v in (angles or (0.0, 0.0, 0.0))],
                        scale=float(scale))
    candidate.objects = objects(project) + [item]
    candidate.validate()
    return candidate


def remove(project, index):
    """Return a new project without the object at ``index``."""
    current = objects(project)
    if not 0 <= index < len(current):
        raise ValueError("Choose a placed object first")
    candidate = deepcopy(project)
    candidate.objects = current[:index] + current[index + 1:]
    candidate.validate()
    return candidate


def select(project, index):
    """Clamp a selection to the placed list; -1 means nothing selected."""
    count = len(objects(project))
    if index < 0 or index >= count:
        return -1
    return int(index)


def set_transform(project, index, *, position=None, angles=None, scale=None):
    """Return a new project with one object's transform edited."""
    current = objects(project)
    if not 0 <= index < len(current):
        raise ValueError("Choose a placed object first")
    candidate = deepcopy(project)
    item = candidate.objects[index]
    if position is not None:
        item.position = [float(v) for v in position]
    if angles is not None:
        item.angles = [float(v) for v in angles]
    if scale is not None:
        item.scale = float(scale)
    candidate.validate()
    return candidate


def clamp_distance(value) -> float:
    """Bound the fixed aim distance used when no ground plane is available."""
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Aim distance must be a number") from exc
    if not DISTANCE_MIN <= value <= DISTANCE_MAX:
        raise ValueError(f"Aim distance must be between {DISTANCE_MIN:g} and {DISTANCE_MAX:g}")
    return value
