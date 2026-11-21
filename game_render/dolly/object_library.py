"""Placeable object registry: the small starting catalog for the Object Picker.

Ids are protocol-stable: append only, never reorder, because the native bridge
reads the index from the wire block. Each entry names a proxy shape the native
object runtime knows how to draw, plus a default size in Source world units. The
``model`` field is reserved for a real engine model once that path exists; it is
empty while the object picker draws proxy geometry.

This module is import-only: no file I/O, no engine access. It mirrors
``dolly/particles.py`` so the two catalogs stay symmetrical.
"""
from __future__ import annotations

# (id, label, category, shape, size_x, size_y, size_z, model)
# shape is one of PROXY_SHAPES. model is "" until a real model path is reviewed.
OBJECTS = (
    ("marker", "Marker", "Guides", "box", 16.0, 16.0, 16.0, ""),
    ("arrow", "Arrow", "Guides", "arrow", 24.0, 8.0, 8.0, ""),
    ("sphere", "Sphere", "Guides", "sphere", 32.0, 32.0, 32.0, ""),
    ("crate", "Crate", "Props", "box", 48.0, 48.0, 48.0, ""),
    ("barrel", "Barrel", "Props", "cylinder", 32.0, 32.0, 56.0, ""),
    ("pillar", "Pillar", "Props", "cylinder", 24.0, 24.0, 160.0, ""),
    ("poster", "Poster", "Props", "box", 8.0, 64.0, 96.0, ""),
    ("flat_light", "Flat light", "Lights", "box", 64.0, 64.0, 4.0, ""),
    ("point_light", "Point light", "Lights", "sphere", 16.0, 16.0, 16.0, ""),
)
PROXY_SHAPES = ("box", "sphere", "cylinder", "arrow")
MAX_OBJECTS = 64
SIZE_MIN = 1.0
SIZE_MAX = 4096.0
SCALE_MIN = 0.05
SCALE_MAX = 20.0

OBJECT_IDS = tuple(item[0] for item in OBJECTS)
OBJECT_LABELS = {item[0]: item[1] for item in OBJECTS}
OBJECT_CATEGORIES = {item[0]: item[2] for item in OBJECTS}
OBJECT_SHAPES = {item[0]: item[3] for item in OBJECTS}
OBJECT_DEFAULT_MODEL = {item[0]: item[7] for item in OBJECTS}
OBJECT_DEFAULT_SIZE = {item[0]: (item[4], item[5], item[6]) for item in OBJECTS}
OBJECT_DEFAULT = OBJECT_IDS[0]
CATEGORY_ORDER = ("Guides", "Props", "Lights")


def object_index(name: str) -> int:
    """Protocol index for a library id; the native runtime validates the range."""
    if name not in OBJECT_IDS:
        raise ValueError("Choose an object from the library")
    return OBJECT_IDS.index(name)


def object_label(name: str) -> str:
    return OBJECT_LABELS.get(name, name)


def object_shape(name: str) -> str:
    return OBJECT_SHAPES.get(name, PROXY_SHAPES[0])


def object_size(name: str) -> tuple[float, float, float]:
    return OBJECT_DEFAULT_SIZE.get(name, (16.0, 16.0, 16.0))


def object_model(name: str) -> str:
    return OBJECT_DEFAULT_MODEL.get(name, "")


def catalog() -> list[dict]:
    """Portable catalog rows for the desktop UI (no persistent state)."""
    return [{"id": item[0], "label": item[1], "category": item[2], "shape": item[3],
             "size": [item[4], item[5], item[6]], "model": item[7]}
            for item in OBJECTS]
