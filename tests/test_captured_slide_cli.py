"""Headless orchestration checks; deliberately stub native extraction/Blender."""
import copy
import json
from pathlib import Path

import pytest

from agr import cli, captured_slide_inputs, captured_slide_render
from agr.scene_package import read_scene, save_scene
from blender_fixture import write_blender_completion
from test_asset_entrypoints import inputs, arguments
from test_captured_slide_render import bound, component_reports


@pytest.mark.parametrize('failure', (None, 'missing', 'binding', 'component', 'failed'))
def test_normal_cli_transports_cohort_and_refuses_missing_receipt_even_partial(
        inputs, bound, monkeypatch, failure):
    # Isolate command/runtime/publication wiring from separately tested admission.
    data = read_scene(inputs.segment)
    data.update(start_tick=17858, end_tick=18240)
    for entity in data['entities']:
        sample = entity['samples'][0]
        sample['tick'] = 17858
        entity['samples'].append(dict(sample, tick=18240))
    save_scene(data, inputs.segment)
    probe = inputs.root / 'probe.exe';probe.touch()
    descriptor = inputs.root / 'cohort.json';descriptor.write_text('{}')
    monkeypatch.setattr(cli, 'animation_mode', lambda *a: 'motion')
    monkeypatch.setattr('agr.recorded_effect_simulation.preflight_recorded_bindings',
                        lambda *a, **k: dict(status='unavailable'))
    seen = []
    def read(path, *, reserved):
        assert Path(path) == descriptor and inputs.segment.resolve() in reserved
        return {'cohort_snapshot_fixture': True}
    def bind(loaded, scene):
        assert loaded == {'cohort_snapshot_fixture': True}
        assert scene['start_tick'] == 17920 and scene['end_tick'] == 18240
        assert scene['particle_history']['start_tick'] == 17858
        seen.append(scene)
        return copy.deepcopy(bound)
    monkeypatch.setattr(captured_slide_inputs, 'read_cohort', read)
    monkeypatch.setattr(captured_slide_inputs, 'bind_cohort', bind)
    expected = captured_slide_render.expected_receipt(bound)
    def blender(command, check):
        runtime = json.loads(Path(command[command.index('--particle-runtime') + 1]).read_text())
        assert runtime['captured_slide_cohort'] == bound
        captured = dict(status='partial', backend=captured_slide_render.BACKEND,
                        input_receipt=copy.deepcopy(expected), components=component_reports(expected),
                        objects=4, point_lights=1)
        effects = dict(status='partial', objects=4, point_lights=1, captured_slide_effects=captured)
        if failure == 'missing':
            effects.pop('captured_slide_effects')
        elif failure == 'binding':
            captured['input_receipt']['binding_sha256'] = 'e' * 64
        elif failure == 'component':
            captured['components'].pop()
        elif failure == 'failed':
            effects = dict(status='failed', error='late slide allocation failed')
        write_blender_completion(command, effects=effects)
    monkeypatch.setattr(cli.subprocess, 'run', blender)
    args = arguments(inputs, '--game-install', str(inputs.install), '--source2viewer', str(inputs.converter),
                     '--particle-probe', str(probe), '--model-constraints',
                     '--start-tick', '17920', '--end-tick', '18240', '--particle-history-ticks', '62',
                     '--captured-slide-inputs', str(descriptor), '--partial', '--overwrite')
    args.motion_only = False  # No actual skeletal evaluator is run in this test.
    args.output.write_bytes(b'previous final scene')
    if failure:
        with pytest.raises(ValueError, match='Captured slide inputs'):
            cli.convert(args)
        assert args.output.read_bytes() == b'previous final scene'
        assert not args.output.with_suffix('.agrscene').exists()
        assert not args.output.with_suffix('.agr-report.json').exists()
    else:
        report = cli.convert(args)
        assert report['captured_slide_inputs'] == expected
        assert report['blender']['effects']['captured_slide_effects']['objects'] == 4
        assert args.output.read_bytes() == b'authored scene stub; no Blender executed'
    assert len(seen) == 1


def test_optional_argument_and_exact_current_scope():
    assert cli.parser().parse_args(['convert', '--segment', 'x', '--output', 'x.blend']).captured_slide_inputs is None
    captured_slide_inputs.require_options(None, simulation=False, constraints=False, partial=False,
                                         start_tick=None, end_tick=None, history_ticks=0)
    valid = dict(simulation=True, constraints=True, partial=True, start_tick=17920, end_tick=18240, history_ticks=62)
    captured_slide_inputs.require_options('cohort.json', **valid)
    for key, value in [('simulation', False), ('constraints', False), ('partial', False), ('start_tick', 17921),
                       ('end_tick', 18239), ('history_ticks', 0)]:
        with pytest.raises(ValueError, match='Captured slide inputs'):
            captured_slide_inputs.require_options('cohort.json', **dict(valid, **{key: value}))
