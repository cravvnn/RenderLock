"""Transport/cohesion controls; real component admission is checked separately."""
import copy
import hashlib
import json
import os

import pytest

from agr import captured_ambient_inputs as inputs
from agr.replay_capture import canonical_sha256
from test_captured_slide_binding import pair


@pytest.fixture
def supplied(pair, tmp_path):
    source,target = pair
    source['schema_version'] = 1
    packages = [dict(backend=name, reference=dict(capture=dict(source=dict(report_sha256=str(i)*64))), assets={})
                for i,name in enumerate(inputs.BACKENDS)]
    def put(name,value):
        path=tmp_path/name; raw=json.dumps(value).encode(); path.write_bytes(raw)
        return dict(path=name,sha256=hashlib.sha256(raw).hexdigest())
    document=dict(format=inputs.FORMAT,schema_version=1,source_scene=put('source.json',source),
        packages=[dict(backend=name,file=put(str(i)+'.json',package))
                  for i,(name,package) in enumerate(zip(inputs.BACKENDS,packages))])
    path=tmp_path/'ambient.json';path.write_text(json.dumps(document))
    return path,document,target


def save_descriptor(supplied):
    path, document, _ = supplied
    path.write_text(json.dumps(document))
    return path


def allow_component_fixtures(monkeypatch):
    # Only isolates transport/cohesion. This is not native/frame admission proof.
    from agr import simulated_effect_package
    calls = []
    monkeypatch.setattr(simulated_effect_package, 'validate_package',
                        lambda package, source: calls.append(package['backend']))
    return calls


def test_read_once_and_bind_originals_without_rehashing(supplied, monkeypatch):
    path, descriptor, target = supplied
    calls = allow_component_fixtures(monkeypatch)
    loaded = inputs.read_inputs(path)
    before = copy.deepcopy(loaded)
    # JSON input files can disappear after snapshot; there is no later re-read.
    for row in descriptor['packages']:
        (path.parent / row['file']['path']).unlink()
    path.unlink()
    bound = inputs.bind_inputs(loaded, target)
    inputs.validate_bound_inputs(bound, target)
    assert calls == list(inputs.BACKENDS) * 2
    assert loaded == before and bound['original'] == before
    assert bound['binding']['source_scene_identity'] != bound['scene_identity']
    bound['original']['packages'][0]['assets']['changed'] = True
    assert loaded == before
    with pytest.raises(ValueError, match='snapshot changed'):
        inputs.validate_bound_inputs(bound, target)


@pytest.mark.parametrize('raw', [b'{"format":1,"format":2}', b'NaN', b'1e999',
                               b'{', b'\xff', b'[' * 70 + b'0' + b']' * 70])
def test_invalid_or_unbounded_json(supplied, raw):
    path = supplied[0]
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        inputs.read_inputs(path)


@pytest.mark.parametrize('change', [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(unknown=True),
    lambda d: d['packages'].pop(),
    lambda d: d['packages'].reverse(),
    lambda d: d['packages'][0].update(backend='unqualified'),
    lambda d: d['source_scene'].update(sha256='bad'),
    lambda d: d['source_scene'].update(extra=1),
    lambda d: d['source_scene'].update(sha256='0' * 64),
    lambda d: d['packages'][0].update(file=d['source_scene']),
])
def test_bad_descriptors_never_return_subset(supplied, change):
    change(supplied[1])
    with pytest.raises(ValueError):
        inputs.read_inputs(save_descriptor(supplied))


@pytest.mark.parametrize('which', ('descriptor', 'source', 'package', 'asset', 'directory'))
def test_reserved_output_collisions(supplied, which):
    path, descriptor, target = supplied
    reserved = {'descriptor': path, 'source': path.parent / descriptor['source_scene']['path'],
                'package': path.parent / descriptor['packages'][1]['file']['path'],
                'asset': target['entities'][0]['pose_file'], 'directory': path.parent}[which]
    with pytest.raises(ValueError, match='collides'):
        inputs.read_inputs(path, reserved=[reserved])


def test_hardlinked_input_alias_refused(supplied):
    path, descriptor, _ = supplied
    row = descriptor['packages'][0]['file']
    duplicate = path.parent / 'hardlink.json'
    os.link(path.parent / row['path'], duplicate)
    descriptor['packages'][1]['file'] = dict(path=duplicate.name, sha256=row['sha256'])
    with pytest.raises(ValueError, match='aliased input'):
        inputs.read_inputs(save_descriptor(supplied))


@pytest.mark.parametrize('assets', [[], {'models': []}, {'models': {'m': {}}},
                                  {'textures': {'t': {}}},
                                  {'textures': {'t': {'directory': 'relative'}}},
                                  {'textures': {'t': {'directory': 'D:/absolute', 'source_sheet': []}}}])
def test_bad_or_unqualified_asset_paths_refused(supplied, assets):
    path, document, _ = supplied
    ref = document['packages'][0]['file']
    package_path = path.parent / ref['path']
    package = json.loads(package_path.read_text())
    package['assets'] = assets
    raw = json.dumps(package).encode()
    package_path.write_bytes(raw)
    ref['sha256'] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        inputs.read_inputs(save_descriptor(supplied))


def test_scene_boolean_schema_is_not_integer_one(supplied):
    path, document, _ = supplied
    source_path = path.parent / document['source_scene']['path']
    source = json.loads(source_path.read_text())
    source['schema_version'] = True
    raw = json.dumps(source).encode()
    source_path.write_bytes(raw)
    document['source_scene']['sha256'] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match='scene schema'):
        inputs.read_inputs(save_descriptor(supplied))


def test_changed_bytes_and_per_file_aggregate_node_bounds(supplied, monkeypatch):
    path, descriptor, _ = supplied
    file = path.parent / descriptor['packages'][1]['file']['path']
    file.write_bytes(file.read_bytes() + b' ')
    with pytest.raises(ValueError, match='byte digest changed'):
        inputs.read_inputs(path)
    descriptor['packages'][1]['file']['sha256'] = hashlib.sha256(file.read_bytes()).hexdigest()
    save_descriptor(supplied)
    with monkeypatch.context() as patch:
        patch.setattr(inputs, 'MAX_SOURCE_BYTES', 100)
        with pytest.raises(ValueError, match='byte budget'):
            inputs.read_inputs(path)
    with monkeypatch.context() as patch:
        patch.setattr(inputs, 'MAX_TOTAL_BYTES', path.stat().st_size + 100)
        with pytest.raises(ValueError, match='byte budget'):
            inputs.read_inputs(path)
    monkeypatch.setattr(inputs.transport, 'MAX_NODES', 10)
    with pytest.raises(ValueError, match='node'):
        inputs.read_inputs(path)



def test_existing_component_validator_failure_propagates(supplied, monkeypatch):
    from agr import simulated_effect_package
    def refuse(package, source):
        raise ValueError('native/frame/material refusal')
    monkeypatch.setattr(simulated_effect_package, 'validate_package', refuse)
    with pytest.raises(ValueError, match='native/frame/material refusal'):
        inputs.bind_inputs(inputs.read_inputs(supplied[0]), supplied[2])


def test_independent_capture_reports_are_retained_not_equated(supplied, monkeypatch):
    allow_component_fixtures(monkeypatch)
    loaded = inputs.read_inputs(supplied[0])
    bound = inputs.bind_inputs(loaded, supplied[2])
    assert [p['reference']['capture']['source']['report_sha256']
            for p in bound['original']['packages']] == ['0'*64, '1'*64]
    assert 'No shared capture/hero clock' in bound['coverage_scope']


def test_bound_fresh_target_cannot_be_substituted(supplied, monkeypatch):
    allow_component_fixtures(monkeypatch)
    loaded = inputs.read_inputs(supplied[0])
    bound = inputs.bind_inputs(loaded, supplied[2])
    changed = copy.deepcopy(supplied[2])
    changed['particle_instances'].append(dict(id='other:1', entity_id=inputs.OWNER))
    with pytest.raises(ValueError, match='fresh target changed'):
        inputs.validate_bound_inputs(bound, changed)
