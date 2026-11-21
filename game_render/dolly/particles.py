"""Particle preset registry: editor names for the native preset ids.

Ids are protocol-stable: append only, never reorder, because the native bridge
reads the index from control flags. The native engine owns each preset's effect
paths and tuning (``native/src/dolly_confetti.cpp``); this module mirrors the
order so the editor can present names, and tests assert the two stay in step.

Every preset follows the camera as a moving volume; spawn height selects the
close/far regime, and intensity scales the emission rate.
"""
from __future__ import annotations

PARTICLES = (
    ("confetti", "Confetti", "Six-colour confetti with optional ground despawn."),
    ("snow_flakes", "Snow - light flakes", "The game's Christmas hideout flakes."),
    ("snow_long_fall", "Snow - long fall", "Slower, longer-falling flakes."),
    ("snow_heavy", "Snow - heavy", "Denser exterior snowfall."),
    ("snow_machine", "Snow machine", "The winter shop ambient snow machine."),
    ("frozen_flakes", "Frozen flakes", "Rejuvenation frozen snowflakes."),
    ("rain", "Rain", "Custom falling rain streaks."),
    ("frozen_classic", "Frozen - classic crystals", "The game's original rejuvenation crystals."),
)
PARTICLE_IDS = tuple(item[0] for item in PARTICLES)
PARTICLE_LABELS = {item[0]: item[1] for item in PARTICLES}
LABEL_TO_ID = {item[1]: item[0] for item in PARTICLES}
PARTICLE_DEFAULT = PARTICLE_IDS[0]
PARTICLE_INTENSITY_DEFAULT = 1.0
PARTICLE_INTENSITY_MIN = 0.25
PARTICLE_INTENSITY_MAX = 3.0


def preset_index(name: str) -> int:
    """Protocol index for a preset id; the native engine validates the range."""
    if name not in PARTICLE_IDS:
        raise ValueError("Choose a particle preset from the library")
    return PARTICLE_IDS.index(name)


def preset_label(name: str) -> str:
    return PARTICLE_LABELS.get(name, name)
