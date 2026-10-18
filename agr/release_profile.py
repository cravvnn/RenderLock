"""Feature boundaries for staged distributions; development keeps its tools."""
from __future__ import annotations

import json
from pathlib import Path


def character_beta() -> bool:
    path = Path(__file__).resolve().parents[1] / "release_profile.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    if data != {"profile": "character-beta"}:
        raise ValueError("Unrecognized release profile")
    return True


def validate_conversion(args) -> None:
    """Keep map geometry out of the character beta; the look tools are enabled."""
    if not character_beta():
        return
    # The experimental character look (partial Source lighting, probe
    # reflections and the Elagrimm shader) is part of this beta.
    # Map geometry stays omitted: scenes remain character-only and maps are
    # added separately in Blender.
    args.character_only = True


def validate_scene(data) -> None:
    """Saved packages may carry the experimental Source lighting inputs."""
