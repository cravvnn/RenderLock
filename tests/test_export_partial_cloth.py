"""Cloth consent must never authorize missing character models or animation."""
import json

import pytest

from agr import cli, gui


def completion(tmp_path):
    segment, output = tmp_path / 'segment.json', tmp_path / 'scene.blend'
    data = dict(start_tick=10, end_tick=12, tick_rate=64, coverage={'animation': 'evaluated'},
                entities=[dict(id='actor', hero_id=15, secondary_motion_file='cloth.json')])
    report = dict(schema_version=1, segment=str(segment), output=str(output), start_tick=10,
                  end_tick=12, tick_rate=64, fps=64, warnings=[], entities=[dict(id='actor',
                  model_status='imported', pose={'status': 'baked', 'sample_count': 3},
                  secondary_motion={'status': 'failed', 'error': 'Unsupported cloth operation'})])
    path = tmp_path / 'scene.scene.json'
    path.write_text(json.dumps(report))
    return path, dict(segment=segment, output=output, data=data, fps=64), report


def test_failed_cloth_requires_separate_consent_and_retains_warning(tmp_path):
    path, job, _ = completion(tmp_path)
    with pytest.raises(RuntimeError, match='requested cloth failed'):
        cli.read_blender_completion(path, **job)
    result = cli.read_blender_completion(path, **job, partial_cloth=True)
    assert result['entities'][0]['pose']['status'] == 'baked'
    assert any('Unsupported cloth operation' in warning for warning in result['warnings'])


@pytest.mark.parametrize('change,match', [
    ('model', 'model placeholder'), ('pose', 'skeletal animation'),
    ('cloth', 'cloth outcome'), ('identity', 'character tracks differ')])
def test_cloth_consent_does_not_accept_core_or_missing_completion_data(tmp_path, change, match):
    path, job, report = completion(tmp_path)
    actor = report['entities'][0]
    if change == 'model':
        actor['model_status'] = 'missing asset placeholder'
    elif change == 'pose':
        actor['pose'] = {'status': 'unavailable; imported rest pose'}
    elif change == 'cloth':
        actor.pop('secondary_motion')
    else:
        actor['id'] = 'different-actor'
    path.write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match=match):
        cli.read_blender_completion(path, **job, partial_cloth=True)


def test_gui_cloth_consent_is_independent_of_character_partial_export(tmp_path):
    for name in ('input.dem', 'blender.exe', 'Source2Viewer-CLI.exe'):
        (tmp_path / name).touch()
    values = dict(input=str(tmp_path / 'input.dem'), mode='replay', asset_source='installed',
                  game_install=str(tmp_path), source2viewer=str(tmp_path / 'Source2Viewer-CLI.exe'),
                  blender=str(tmp_path / 'blender.exe'), output=str(tmp_path / 'scene.blend'),
                  start='10', end='12', fps='64', cloth_enabled='1')
    args = cli.parser().parse_args(gui.build_convert_command(values, partial_cloth=True)[3:])
    assert args.partial_cloth and args.secondary_motion and not args.partial
    assert not cli.parser().parse_args(gui.build_convert_command(values)[3:]).partial_cloth
