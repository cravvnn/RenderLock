"""All-roster selection and explicit missing-model behavior."""
import json
from pathlib import Path

import pytest

from agr.cli import convert, parser
from agr.scene_package import read_scene, save_scene
from blender_fixture import write_blender_completion


@pytest.fixture(autouse=True)
def blender_preflight(monkeypatch):
    monkeypatch.setattr('agr.blender_runtime.check_blender', lambda *a, **k: {'fixture': True})


def roster():
    hero_ids = [1, 2, 4, 8, 13, 15, 20, 25, 31, 69, 76, 80]
    return {"schema_version": 1, "tick_rate": 64, "start_tick": 10, "end_tick": 10,
            "metadata": {}, "entities": [
                {"id": f"{index}:1", "hero_id": hero, "hero_name": f"Hero {hero}",
                 "samples": [{"tick": 10, "position": [index, 0, 0], "body_angles": [0, 0, 0]}]}
                for index, hero in enumerate(hero_ids)], "markers": []}


def setup(tmp_path, extra=()):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "four.blend").write_bytes(b"stub model file")
    (assets / "assets.json").write_text(json.dumps({"heroes": {
        str(hero): {"model_file": "four.blend", "asset_objects": [str(hero)]}
        for hero in [13, 2, 15, 31]}}))
    segment = save_scene(roster(), tmp_path / "source.json")
    engine = tmp_path / "blender"
    engine.write_bytes(b"stub executable")
    args = parser().parse_args(["convert", "--segment", str(segment), "--assets", str(assets),
                               "--blender", str(engine), "--output", str(tmp_path / "shot.blend"),
                               "--motion-only", *extra])
    return args


def fake_blender(command, check):
    write_blender_completion(command)


def test_default_strict_reports_eight_missing_without_silently_dropping_tracks(tmp_path):
    args = setup(tmp_path)
    with pytest.raises(ValueError, match="Missing model files for 8 of 12"):
        convert(args)
    retained = next(tmp_path.glob("agr-*/extracted/segment.json"))
    data = read_scene(retained)
    assert len(data["entities"]) == 12
    assert data["coverage"]["hero_models"]["selection"] == "all_heroes"
    assert not args.output.exists()


def test_default_partial_publishes_all_twelve_tracks_and_missing_coverage(tmp_path, monkeypatch):
    args = setup(tmp_path, ["--partial"])
    monkeypatch.setattr("agr.cli.subprocess.run", fake_blender)
    report = convert(args)
    assert report["entities"] == 12
    assert report["coverage"]["hero_models"]["missing_model_tracks"] == 8
    assert len(read_scene(args.output.with_suffix(".agrscene"))["entities"]) == 12


def test_explicit_subset_remains_available_and_is_reported(tmp_path, monkeypatch):
    args = setup(tmp_path, ["--heroes", "13,2,15,31"])
    monkeypatch.setattr("agr.cli.subprocess.run", fake_blender)
    report = convert(args)
    assert report["entities"] == 4
    assert report["coverage"]["hero_models"]["missing_model_tracks"] == 0
    assert report["coverage"]["hero_models"]["selection"] == "explicit_hero_ids"


def test_replay_default_passes_all_roster_to_native_adapter(tmp_path, monkeypatch):
    args = setup(tmp_path, ["--partial"])
    args.demo, args.segment = args.segment, None
    called = []
    def extract(demo, output, start, end, heroes):
        called.append(heroes)
        return save_scene(roster(), output / "segment.json")
    args.start_tick = args.end_tick = 10
    monkeypatch.setattr("agr.replay.extract_replay", extract)
    monkeypatch.setattr("agr.cli.subprocess.run", fake_blender)
    report = convert(args)
    assert called == [None]
    assert report["entities"] == 12

@pytest.mark.parametrize('omit', [True, False])
def test_saved_effects_follow_current_explicit_omission(tmp_path, monkeypatch, omit):
    args = setup(tmp_path, ['--heroes', '13'])
    args.no_effects = omit
    args.partial = not omit  # Retaining a known unavailable requested stage needs consent.
    data = read_scene(args.segment)
    data['effects'] = {'manifest_file': str(tmp_path / 'old-effects.json')}
    data['particle_instances'] = [{'id': 'fx:1', 'created_tick': 10}]
    data['coverage'] = {'scene_extras': {
        'particles': {'status': 'unavailable', 'reason': 'Old effect preparation failure'},
        'map': {'status': 'failed', 'error': 'Unrelated map failure'}}}
    save_scene(data, args.segment)
    original = args.segment.read_bytes()
    seen = []
    def inspect_blender(command, check):
        current = read_scene(Path(command[command.index('--segment') + 1]))
        seen.append(current)
        assert ('effects' not in current) == omit
        assert current['particle_instances'] == data['particle_instances']
        assert current['source_coverage'] == data['coverage']
        fake_blender(command, check)
        write_blender_completion(command, **{
            'effects': {'status': 'not_requested'} if 'effects' not in current else {
                'status': 'unavailable', 'reason': 'Old effect preparation failure'},
            'map': {'status': 'failed', 'error': 'Unrelated map failure'}})
    monkeypatch.setattr('agr.cli.subprocess.run', inspect_blender)
    report = convert(args)
    assert report['particles']['status'] == ('not_requested' if omit else 'unavailable')
    assert report['map']['status'] == 'failed'
    assert any('Particles' in warning for warning in report['warnings']) != omit
    published = read_scene(args.output.with_suffix('.agrscene'))
    # Publication strips external render descriptors in both modes; raw events
    # remain reusable, and the Blender invocation above tests the actual opt-out.
    assert 'effects' not in published
    assert published['particle_instances'] == data['particle_instances']
    assert args.segment.read_bytes() == original
    assert len(seen) == 1
