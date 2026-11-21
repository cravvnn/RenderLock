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


@pytest.mark.parametrize("field", ["sha256"])
def test_identity_fields_required(field):
    data = example()
    del data["replay"][field]
    with pytest.raises(ShotCollectionError, match=field):
        validate_shot_collection(data)


@pytest.mark.parametrize("case", ["scene", "bounds"])
def test_invalid_contracts_fail_closed(case):
    data = example()
    if case == "scene": data["scene"] = {"path": "scene.rlscene"}
    elif case == "bounds": data["replay"]["total_ticks"] = 66
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


def _scene_for_example():
    replay = example()["replay"]
    return {"tick_rate": replay["tick_rate"], "start_tick": 0, "end_tick": 100,
            "metadata": {"demo_sha256": replay["sha256"], "build": replay["build"]}}


@pytest.mark.parametrize("plan", [{"status": "cropped", "manifest_sha256": "b" * 64}])
def test_reconstruction_import_requires_verified_full_map_plan(plan):
    with pytest.raises(ShotPackageImportError, match="verified full prepared map"):
        validate_against_scene(example(), _scene_for_example(), fps=30.0, reconstruction_map=plan)


def test_reconstruction_import_with_full_map_plan_still_checks_scene_identity():
    plan = {"status": "full prepared map", "manifest_sha256": "b" * 64,
            "package_id": "midtown-fixture-v2", "game_version": "6861"}
    assert isinstance(validate_against_scene(example(), _scene_for_example(), fps=30.0,
                                             reconstruction_map=plan), list)
    wrong = _scene_for_example()
    wrong["metadata"]["demo_sha256"] = "c" * 64
    with pytest.raises(ShotPackageImportError, match="SHA-256"):
        validate_against_scene(example(), wrong, fps=30.0, reconstruction_map=plan)
    short = _scene_for_example()
    short["end_tick"] = 65
    with pytest.raises(ShotPackageImportError, match="range"):
        validate_against_scene(example(), short, fps=30.0, reconstruction_map=plan)


def test_convert_refuses_reconstruction_crav_without_full_prepared_map(tmp_path, capsys):
    from agr.cli import main
    package = tmp_path / "shot.crav"
    package.write_text(json.dumps(example()), encoding="utf-8")
    assert main(["convert", "--demo", str(tmp_path / "missing.dem"), "--output",
                 str(tmp_path / "scene.blend"), "--shot-package", str(package)]) == 1
    assert "complete prepared map package" in capsys.readouterr().err


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
