import copy
import json
import hashlib
from pathlib import Path

import pytest

from agr.crav_import import plan_reconstruction
from agr import crav_import
from agr.shot_package import ShotCollectionError


def inputs():
    collection = json.loads((Path(__file__).parents[1] / "docs/interchange/minimal.crav").read_text())
    metadata = dict(demo_sha256="a"*64, map_name="dl_midtown", build=6851, tick_rate=64, total_ticks=640)
    return collection, metadata


def test_fractional_end_uses_next_tick_and_plan_does_not_claim_reconstruction():
    data, metadata = inputs()
    original = copy.deepcopy(data)
    plan = plan_reconstruction(data, metadata)
    assert plan["status"] == "planned" and plan["pending"]
    assert plan["jobs"][0]["end_tick"] == 67
    assert plan["jobs"][0]["fps"] == 30
    plan["jobs"][0]["camera"]["samples"].clear()
    assert data == original


@pytest.mark.parametrize("field", ["demo_sha256"])
def test_requires_decoded_identity_not_filename(field):
    data, metadata = inputs()
    metadata[field] = None
    with pytest.raises(ShotCollectionError, match=field):
        plan_reconstruction(data, metadata)
    metadata[field] = "different"
    with pytest.raises(ShotCollectionError, match=field):
        plan_reconstruction(data, metadata)


def test_selected_shots_and_independent_frame_rates():
    data, metadata = inputs()
    second = copy.deepcopy(data["shots"][0])
    second["id"] = "second"
    second["camera"].update(fps=60, frame_count=1)
    second["camera"]["samples"] = second["camera"]["samples"][:1]
    second["range"].update(end_tick=64, end_tick_exact=64)
    data["shots"].append(second)
    assert [job["fps"] for job in plan_reconstruction(data, metadata)["jobs"]] == [30, 60]
    assert [job["shot_id"] for job in plan_reconstruction(data, metadata, shot_ids=["second"])["jobs"]] == ["second"]
    for ids in ([], ["missing"], ["second", "second"], "second"):
        with pytest.raises(ShotCollectionError):
            plan_reconstruction(data, metadata, shot_ids=ids)


def test_local_preflight_resolves_override_and_rechecks_real_bytes(tmp_path, monkeypatch):
    data, metadata = inputs()
    replay = tmp_path / "moved.dem"
    replay.write_bytes(b"local replay bytes")
    data["replay"]["path"] = "old-location.dem"
    data["replay"]["sha256"] = hashlib.sha256(replay.read_bytes()).hexdigest()
    package = tmp_path / "shot.crav"
    package.write_text(json.dumps(data))
    calls = []
    def inspect(path):
        calls.append(path)
        return metadata
    monkeypatch.setattr(crav_import, "inspect_replay", inspect)
    plan = crav_import.preflight_reconstruction(package, replay=replay)
    assert calls == [replay.resolve()]
    assert plan["status"] == "planned" and plan["pending"]
    assert plan["replay_sha256"] == data["replay"]["sha256"]
    assert plan["package_sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()
    assert plan["replay_metadata"]["demo_sha256"] != metadata["demo_sha256"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["moved.dem", "shot.crav"]
    with pytest.raises(ShotCollectionError, match="Cannot read"):
        crav_import.preflight_reconstruction(package)
    replay.write_bytes(b"different replay bytes")
    with pytest.raises(ShotCollectionError, match="SHA-256"):
        crav_import.preflight_reconstruction(package, replay=replay)
    assert len(calls) == 1  # Mismatch refused before decoding.


@pytest.mark.parametrize("change", ["replay"])
def test_preflight_never_returns_plan_after_changed_input_or_cancel(tmp_path, monkeypatch, change):
    data, metadata = inputs()
    replay = tmp_path / "source.dem"
    replay.write_bytes(b"original replay")
    data["replay"].update(path=replay.name, sha256=hashlib.sha256(replay.read_bytes()).hexdigest())
    package = tmp_path / "shot.crav"
    package.write_text(json.dumps(data))
    cancelled = False
    def inspect(path):
        nonlocal cancelled
        if change == "replay":
            replay.write_bytes(b"different replay")
        else:
            cancelled = True
        return metadata
    monkeypatch.setattr(crav_import, "inspect_replay", inspect)
    with pytest.raises(ShotCollectionError, match={"replay": "Replay changed", "cancel": "cancelled"}[change]):
        crav_import.preflight_reconstruction(package, cancelled=lambda: cancelled)


def test_preflight_cancellation_precedes_io_and_cli_routes(monkeypatch, capsys):
    from agr.cli import main
    with pytest.raises(ShotCollectionError, match="cancelled"):
        crav_import.preflight_reconstruction("missing.crav", cancelled=lambda: True)
    calls = []
    def preflight(package, **options):
        calls.append((package, options))
        return {"status": "planned", "pending": ["reconstruction"]}
    monkeypatch.setattr(crav_import, "preflight_reconstruction", preflight)
    assert main(["plan-crav", "--package", "shot.crav", "--demo", "moved.dem", "--shot", "shot-1"]) == 0
    assert calls == [(Path("shot.crav"), dict(replay=Path("moved.dem"), shot_ids=["shot-1"], map_package=None))]
    assert json.loads(capsys.readouterr().out)["pending"] == ["reconstruction"]
    assert main(["plan-crav", "--package", "shot.crav", "--map-package", "midtown"]) == 0
    assert calls[-1][1]["map_package"] == Path("midtown")


def prepared_map(folder, *, lighting=True):
    from agr import map_package as packages
    folder.mkdir()
    rows = []
    files = [("RenderLockMap.blend", b"fixture library"), ("textures/wall.png", b"fixture pixels")]
    if lighting:
        files.append(("lightmaps/irradiance.exr", b"fixture lightmap"))
    for name, data in files:
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        rows.append(dict(file=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    doc = dict(format=packages.FORMAT, schema_version=2, package_id="midtown-fixture-v2",
               builder=dict(commit=packages.LEGACY_BUILDER_COMMIT, map_builder=14),
               source=dict(kind="installed_game", game_version="6861", native_blend_sha256="a"*64,
                           build_request_sha256="d"*64, map_archive_sha256="e"*64, root_archive_sha256="f"*64,
                           source2viewer_sha256=next(iter(packages.CONVERTER_SHA256)),
                           adapter_sha256={name: "1"*64 for name in
                                           ("blender_prepared_map.py", "blender_prepared_proxy.py", "map_package.py")}),
               map_name="dl_midtown", blender_min=[5, 2, 0], source_frame="vrf_blender_metres",
               collection=packages.COLLECTION, world="Midtown sky", namespace=packages.NAMESPACE,
               inventory=dict(objects=20, meshes=10, vertices=100, faces=50, file_images=1, geometry_sha256="b"*64),
               proxy=dict(status="ready", algorithm="grid-0.25m-v2", collection="RenderLockProxy",
                          source_meshes=8, source_vertices=90, source_faces=40, proxy_meshes=2, proxy_vertices=20,
                          proxy_faces=10, full_map_hidden_in_viewport=True, proxy_hidden_in_render=True),
               library=rows[0], files=rows)
    (folder / packages.MANIFEST).write_text(json.dumps(doc))
    return folder


def test_explicit_full_map_resolves_path_selection_but_not_capture_time_content(tmp_path):
    from agr.map_package import load_package
    data, metadata = inputs()
    metadata["map_name"] = data["replay"]["map"] = "start"   # header label is kept, never used to choose
    plan = plan_reconstruction(data, metadata, map_package=load_package(prepared_map(tmp_path / "map")))
    assert plan["map"]["map_name"] == "dl_midtown" and plan["map"]["replay_header_map"] == "start"
    assert plan["map"]["selection"] == "explicit" and plan["map"]["lighting_files"] == 1
    assert crav_import.MAP_PENDING not in plan["pending"]
    assert crav_import.CAPTURE_MAP_PENDING in plan["pending"]
    assert "saved-scene and visual verification" in plan["pending"]
    assert "map" not in plan_reconstruction(data, metadata)


def test_full_map_without_lighting_dependencies_is_refused(tmp_path):
    from agr.map_package import load_package
    data, metadata = inputs()
    with pytest.raises(ShotCollectionError, match="lighting dependencies"):
        plan_reconstruction(data, metadata, map_package=load_package(prepared_map(tmp_path / "map", lighting=False)))


@pytest.mark.parametrize("change", ["none", "lightmap"])
def test_preflight_rechecks_prepared_map_after_decode(tmp_path, monkeypatch, change):
    data, metadata = inputs()
    replay = tmp_path / "source.dem"
    replay.write_bytes(b"original replay")
    data["replay"].update(path=replay.name, sha256=hashlib.sha256(replay.read_bytes()).hexdigest())
    package = tmp_path / "shot.crav"
    package.write_text(json.dumps(data))
    folder = prepared_map(tmp_path / "map")
    def inspect(path):
        if change == "lightmap":
            (folder / "lightmaps/irradiance.exr").write_bytes(b"edited lightmap")
        return metadata
    monkeypatch.setattr(crav_import, "inspect_replay", inspect)
    if change == "none":
        plan = crav_import.preflight_reconstruction(package, map_package=folder)
        assert plan["map"]["package_id"] == "midtown-fixture-v2"
    else:
        with pytest.raises(ShotCollectionError, match="External map"):
            crav_import.preflight_reconstruction(package, map_package=folder)


def test_broken_prepared_map_is_refused_before_replay_decode(tmp_path, monkeypatch):
    data, metadata = inputs()
    replay = tmp_path / "source.dem"
    replay.write_bytes(b"original replay")
    data["replay"].update(path=replay.name, sha256=hashlib.sha256(replay.read_bytes()).hexdigest())
    package = tmp_path / "shot.crav"
    package.write_text(json.dumps(data))
    folder = prepared_map(tmp_path / "map")
    (folder / "textures/wall.png").write_bytes(b"changed pixels")
    calls = []
    monkeypatch.setattr(crav_import, "inspect_replay", lambda path: calls.append(path) or metadata)
    with pytest.raises(ShotCollectionError, match="External map"):
        crav_import.preflight_reconstruction(package, map_package=folder)
    assert calls == []
