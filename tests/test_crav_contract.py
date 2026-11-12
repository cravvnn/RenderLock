"""Portable lightweight contract checks; no game, media, assets or GUI needed."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from agr.shot_package import (
    ShotCollectionError, read_shot_collection, write_shot_collection,
    validate_shot_collection,
)
from agr.blender_shot_package import (
    ShotPackageImportError, validate_against_scene, camera_transform, scene_frame,
)

EXAMPLE = Path(__file__).parents[1] / "docs" / "interchange" / "minimal.crav"


def example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_portable_camera_round_trip_without_media_or_scene(tmp_path):
    data = example()
    original = copy.deepcopy(data)
    path = write_shot_collection(data, tmp_path / "shot.crav")
    assert read_shot_collection(path, resolve_paths=False) == original
    assert data == original
    shot = data["shots"][0]
    for index, sample in enumerate(shot["camera"]["samples"]):
        assert scene_frame(shot, sample, scene_start_tick=64, tick_rate=64, fps=30) == pytest.approx(index + 1)
        assert camera_transform(sample, {})["location"] == pytest.approx([index * .0254, 0, 0])
    assert path.stat().st_size < 4096


@pytest.mark.parametrize("field", ["sha256", "map", "build", "total_ticks"])
def test_identity_fields_required(field):
    data = example()
    del data["replay"][field]
    with pytest.raises(ShotCollectionError, match=field):
        validate_shot_collection(data)


@pytest.mark.parametrize("case", [
    "scene", "media", "profile", "no_profile", "whole_map", "lighting", "margin",
    "partial", "units", "aspect", "incomplete", "retime", "end", "bounds",
    "samples", "authored", "overrides", "projection", "fov",
])
def test_invalid_contracts_fail_closed(case):
    data = example()
    shot = data["shots"][0]
    camera = shot["camera"]
    selection = data["reconstruction"]["map_selection"]
    if case == "scene": data["scene"] = {"path": "scene.rlscene"}
    elif case == "media": shot["media"] = {}
    elif case == "profile": data["profile"] = "future"
    elif case == "no_profile": del data["profile"]
    elif case == "whole_map": selection["mode"] = "whole-map"
    elif case == "lighting": selection["include_lighting_dependencies"] = False
    elif case == "margin": selection["margin_source_units"] = -1
    elif case == "partial": data["reconstruction"]["missing_content"] = "ignore"
    elif case == "units": camera["unit_scale"] = 1
    elif case == "aspect": camera["samples"][0]["aspect"] = 1
    elif case == "incomplete": camera["samples"][0]["state"] = "projection-only"
    elif case == "retime": camera["samples"][1]["replay_time"] = 0
    elif case == "end": shot["range"].update(end_tick=70, end_tick_exact=70)
    elif case == "bounds": data["replay"]["total_ticks"] = 66
    elif case == "samples": del camera["samples"]
    elif case == "authored": camera["source"]["kind"] = "external"
    elif case == "overrides": shot["cvars"] = {"hide_players": True}
    elif case == "projection": camera["samples"][0]["projection"] = [0] * 16
    elif case == "fov": camera["samples"][0]["horizontal_fov"] = 179
    with pytest.raises(ShotCollectionError):
        validate_shot_collection(data)


def test_relocated_replay_verified_by_bytes_not_name(tmp_path):
    data = example()
    replay = tmp_path / "relocated.dem"
    replay.write_bytes(b"synthetic replay fixture")
    data["replay"].update(path="relocated.dem", sha256=hashlib.sha256(replay.read_bytes()).hexdigest())
    path = write_shot_collection(data, tmp_path / "shot.crav")
    read_shot_collection(path, check_files=True)
    replay.write_bytes(b"different replay fixture")
    with pytest.raises(ShotCollectionError, match="SHA-256"):
        read_shot_collection(path, check_files=True)


def test_old_camera_importer_cannot_silently_ignore_reconstruction_request():
    with pytest.raises(ShotPackageImportError, match="not implemented"):
        validate_against_scene(example(), {})


def test_overlapping_shots_are_valid_but_ids_must_be_unique():
    data = example()
    shot = copy.deepcopy(data["shots"][0])
    shot["id"] = "second"
    data["shots"].append(shot)
    validate_shot_collection(data)
    shot["id"] = data["shots"][0]["id"]
    with pytest.raises(ShotCollectionError, match="duplicates"):
        validate_shot_collection(data)


def test_sample_budget_applies_to_whole_collection(monkeypatch):
    from agr import shot_package
    monkeypatch.setattr(shot_package, "MAX_SAMPLES", 3)
    data = example()
    second = copy.deepcopy(data["shots"][0])
    second["id"] = "second"
    data["shots"].append(second)
    with pytest.raises(ShotCollectionError, match="collection-wide"):
        validate_shot_collection(data)


def test_size_refusal_preserves_existing_output(tmp_path, monkeypatch):
    from agr import shot_package
    output = tmp_path / "existing.crav"
    output.write_bytes(b"preserved original")
    monkeypatch.setattr(shot_package, "MAX_BYTES", 100)
    with pytest.raises(ShotCollectionError, match="byte limit"):
        write_shot_collection(example(), output)
    assert output.read_bytes() == b"preserved original"
