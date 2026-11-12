import copy
import hashlib
import json
from pathlib import Path

import pytest

from agr import cli, gui
from agr import particle_observations as observations
from agr.gui_paths import normalize_gui_paths
from test_particle_crossbow_owner import observed
from test_gui_character_workflow import command_values


def write_document(tmp_path, instance, capture):
    path = tmp_path / 'observations.json'
    value = dict(format=observations.FORMAT, schema_version=1,
                 captures=[dict(instance_id=instance['id'], capture=capture)])
    path.write_text(json.dumps(value), encoding='utf-8')
    return path, value


def test_snapshot_survives_file_replacement_and_binds_fresh_inputs(tmp_path, observed):
    scene, instance, capture = observed
    path, _ = write_document(tmp_path, instance, capture)
    raw = path.read_bytes()
    document = observations.read_observations(path)
    path.write_text('changed after read')
    bound, receipt = observations.bind_observations(document, scene)
    assert bound[instance['id']]['capture'] == capture
    assert receipt['sha256'] == hashlib.sha256(raw).hexdigest()
    assert receipt['instance_ids'] == [instance['id']]
    assert receipt['acquisition'] == 'supplied_file'
    bound[instance['id']]['capture']['owner_srt'][0] = 900
    assert document['captures'][instance['id']]['owner_srt'][0] == 10


@pytest.mark.parametrize('change', [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(extra='unknown'),
    lambda d: d.update(captures=[]),
    lambda d: d.update(captures=d['captures'] * 65),
    lambda d: d['captures'].append(copy.deepcopy(d['captures'][0])),
    lambda d: d['captures'][0].update(instance_id='8:01'),
    lambda d: d['captures'][0].update(extra=0),
    lambda d: d['captures'][0]['capture'].update(extra=0),
    lambda d: d['captures'][0]['capture'].update(owner_srt=[float('inf')]),
])
def test_strict_bounded_transport(tmp_path, observed, change):
    _, instance, capture = observed
    path, value = write_document(tmp_path, instance, capture)
    change(value)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        observations.read_observations(path)


@pytest.mark.parametrize('raw', [b'{"format":0,"format":1}', b'\xff', b'{',
                                b'[' * 2000, b' ' * (observations.MAX_BYTES + 1),
                                b'{"n":1e999}'],
                         ids=['duplicate-key', 'invalid-utf8', 'truncated', 'too-deep', 'too-large', 'overflow'])
def test_bad_json(tmp_path, raw):
    path = tmp_path / 'bad.json'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        observations.read_observations(path)


def test_output_and_package_descendant_collisions(tmp_path, observed):
    _, instance, capture = observed
    path, _ = write_document(tmp_path, instance, capture)
    for target in (path, tmp_path):
        with pytest.raises(ValueError, match='collides'):
            observations.read_observations(path, reserved=[target])
    alias = tmp_path / 'alias'
    alias.hardlink_to(path)
    with pytest.raises(ValueError, match='collides'):
        observations.read_observations(alias, reserved=[path])


def test_every_requested_observation_must_bind_without_mutating_scene(tmp_path, observed):
    scene, instance, capture = observed
    path, value = write_document(tmp_path, instance, capture)
    second = copy.deepcopy(value['captures'][0])
    second['instance_id'] = '9:1'
    value['captures'].append(second)
    path.write_text(json.dumps(value))
    before = copy.deepcopy(scene)
    with pytest.raises(ValueError, match='absent'):
        observations.bind_observations(observations.read_observations(path), scene)
    assert scene == before
    path, _ = write_document(tmp_path, instance, capture)
    scene['metadata']['demo_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='replay'):
        observations.bind_observations(observations.read_observations(path), scene)


def test_gui_builds_real_cli_argument_without_native_widgets(tmp_path):
    values = dict(command_values(tmp_path), simulate_effects='1', model_constraints='1',
                  particle_observations=str(tmp_path / 'observations.json'),
                  particle_probe=str(tmp_path / 'simulator.exe'))
    Path(values['particle_observations']).write_text('{}')
    Path(values['particle_probe']).touch()
    args = cli.parser().parse_args(gui.build_convert_command(values)[3:])
    assert args.particle_observations == Path(values['particle_observations'])
    assert args.model_constraints and args.particle_probe
    for field in ('simulate_effects', 'model_constraints', 'effects_enabled'):
        with pytest.raises(ValueError, match='observations require'):
            gui.build_convert_command(dict(values, **{field: '0'}))
    assert normalize_gui_paths({'particle_observations': 'relative.json'}, base=tmp_path)[
        'particle_observations'] == str(tmp_path / 'relative.json')


def test_unconfigured_import_preserves_existing_options():
    observations.require_options(None, simulation=False, constraints=False)
    assert cli.parser().parse_args(['convert', '--segment', 'x.json', '--output', 'x.blend']).particle_observations is None
