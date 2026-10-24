import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from agr import external_map as maps
from agr.cli import read_blender_completion
from agr.export_recovery import recovery_choices, recovery_summary
from agr.gui import build_convert_command
from test_blender_completion_stages import completion
from test_export_save_failures import export_job


@pytest.mark.parametrize('version,valid', [([5, 2, 1], True), ([5, 3, 0], True), ([4, 4, 0], False), ([], False)])
def test_map_library_checks_blender_before_expensive_preparation(version, valid):
    if valid:
        maps.validate_map_blender({'version': version})
    else:
        with pytest.raises(ValueError, match='External map:'):
            maps.validate_map_blender({'version': version})


def test_library_identity_requires_original_bytes_and_rejects_zip(monkeypatch, tmp_path):
    path = tmp_path / 'RenderLockMap.blend'
    original = b'qualified fixture bytes'
    path.write_bytes(original)
    monkeypatch.setattr(maps, 'MAP_BLEND_BYTES', len(original))
    monkeypatch.setattr(maps, 'MAP_BLEND_SHA256', hashlib.sha256(original).hexdigest())
    assert maps.validate_map_library(path)['path'] == str(path.resolve())
    path.write_bytes(b'x' * len(original))
    with pytest.raises(ValueError, match='External map:.*differs'):
        maps.validate_map_library(path)
    with pytest.raises(ValueError, match='External map:'):
        maps.validate_map_library(tmp_path / 'missing.blend')
    path.rename(path.with_suffix('.zip'))
    with pytest.raises(ValueError, match='extracting'):
        maps.validate_map_library(path.with_suffix('.zip'))


def test_qualified_older_local_library_keeps_its_own_inventory(monkeypatch, tmp_path):
    old = b'old qualified fixture library'
    digest = hashlib.sha256(old).hexdigest()
    definition = dict(package_id='old-fixture', bytes=len(old), objects=3, meshes=2, vertices=9)
    monkeypatch.setattr(maps, '_LEGACY_MAPS', {digest: definition})
    path = tmp_path / 'RenderLockMap.blend'; path.write_bytes(old)
    library = maps.validate_map_library(path)
    assert library['package_id'] == 'old-fixture'
    # A caller cannot provide its own counts or promote an older map to latest.
    assert maps.map_definition(dict(library, objects=0, package_id='forged')) == definition
    result = map_result()
    result.update(package_id='old-fixture', library_sha256=digest,
                  imported_objects=3, mesh_objects=2, vertices=9)
    maps.validate_map_completion(result, library)
    result['vertices'] += 1
    with pytest.raises(RuntimeError, match='External map:'):
        maps.validate_map_completion(result, library)


def test_current_map_profile_is_returned_as_a_copy_and_unknown_hash_is_refused():
    profile = maps.map_definition({'sha256': maps.MAP_BLEND_SHA256})
    profile['objects'] = 0
    assert maps.map_definition({'sha256': maps.MAP_BLEND_SHA256})['objects'] == maps.MAP_OBJECTS
    with pytest.raises(ValueError, match='External map:'):
        maps.map_definition({'sha256': 'unqualified'})
    with pytest.raises(RuntimeError, match='External map:'):
        maps.validate_map_completion(map_result(), {'sha256':'unqualified'})


def test_source_world_points_align_without_character_model_yaw():
    point = np.array([1234., -567., 890.])
    imported = np.array([point[1], -point[0], point[2], 1]) * [.0254, .0254, .0254, 1]
    coordinates = dict(origin=[50, -25, 100], unit_scale=.01,
                       world_yaw_degrees=90, model_yaw_degrees=180)
    actual = (np.asarray(maps.map_coordinate_matrix(coordinates)) @ imported)[:3]
    translated = (point - [50, -25, 100]) * .01
    np.testing.assert_allclose(actual, [-translated[1], translated[0], translated[2]], rtol=0, atol=1e-12)


def map_result():
    return dict(status='appended', package_id=maps.MAP_PACKAGE_ID,
        library_sha256=maps.MAP_BLEND_SHA256, collection=maps.MAP_COLLECTION,
        character_collection=maps.CHAR_COLLECTION, imported_objects=maps.MAP_OBJECTS,
        mesh_objects=maps.MAP_MESHES, vertices=maps.MAP_VERTICES,
        coordinate_matrix=maps.map_coordinate_matrix(), texture_relink='manual',
        map_cutting='manual', linked_libraries=0)


@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('mutation', ['missing', 'hash', 'count', 'basis', 'linked'])
def test_requested_map_cannot_be_silently_dropped_or_misaligned(tmp_path, mutation, partial):
    result = map_result()
    path, job = completion(tmp_path, external_map=result)
    library = {'sha256': maps.MAP_BLEND_SHA256}
    assert read_blender_completion(path, **job, external_map=library, partial=partial)
    if mutation == 'missing':
        result = None
    elif mutation == 'hash':
        result['library_sha256'] = 'different'
    elif mutation == 'count':
        result['mesh_objects'] -= 1
    elif mutation == 'linked':
        result['linked_libraries'] = 1
    else:
        result['coordinate_matrix'][0][3] = 1
    doc = json.loads(path.read_text()); doc['external_map'] = result; path.write_text(json.dumps(doc))
    with pytest.raises(RuntimeError, match='External map:'):
        read_blender_completion(path, **job, external_map=library, partial=partial)


def test_map_is_independent_of_lighting_and_dormant_path_is_retained(tmp_path):
    for name in ('replay.dem', 'blender.exe', 'RenderLockMap.blend'):
        (tmp_path / name).touch()
    values = dict(input=str(tmp_path / 'replay.dem'), mode='replay', assets=str(tmp_path),
        asset_source='prepared', blender=str(tmp_path / 'blender.exe'), output=str(tmp_path / 'out.blend'),
        start='1', end='3', fps='64', character_look='standard', external_map_enabled='1',
        external_map=str(tmp_path / 'RenderLockMap.blend'), character_shader='dormant.blend')
    command = build_convert_command(values)
    assert '--external-map' in command and '--character-only' in command
    assert not set(command) & {'--source-lighting', '--include-map', '--partial', '--partial-lighting'}
    assert values['character_shader'] == 'dormant.blend'
    values.update(external_map_enabled='0', external_map='nonexistent.blend')
    assert '--external-map' not in build_convert_command(values)


def test_map_omission_is_explicit_and_preserves_core_and_lighting_requests():
    command = ['python', '-m', 'agr', 'convert', '--external-map', '--odd-name.blend',
               '--source-lighting', '--source-hero-lighting', '--cloth', '--output', 'out.blend']
    failure = dict(stage='build_blender_scene', error='ValueError: External map: library missing')
    choices = recovery_choices(command, failure)
    assert len(choices) == 1 and choices[0].id == 'without_external_map'
    retry = choices[0].command
    assert '--external-map' not in retry and '--odd-name.blend' not in retry
    assert {'--source-lighting', '--source-hero-lighting', '--cloth', '--character-only'} <= set(retry)
    assert '--partial' not in retry and dict(choices[0].values_overrides)['external_map_enabled'] == '0'
    assert 'without the map' in recovery_summary(command, failure)
    assert not recovery_choices(command, dict(stage='evaluate_animation', error='External map: injected unrelated stage'))
    assert recovery_choices(command, dict(stage='preflight', error='External map: unavailable library'))[0].id == 'without_external_map'


@pytest.mark.parametrize('kind', [RuntimeError, MemoryError, KeyboardInterrupt])
def test_map_errors_keep_optional_scope_without_hiding_cancellation(monkeypatch, kind):
    from agr import blender_external_map as module
    def fail(*args):
        raise kind('fixture')
    monkeypatch.setattr(module, '_append_external_map', fail)
    if kind is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            module.append_external_map(None, None, None)
    else:
        with pytest.raises(ValueError, match='External map: append failed') as caught:
            module.append_external_map(None, None, None)
        assert isinstance(caught.value.__cause__, kind)


def test_actual_recovery_controller_can_omit_map_and_keep_its_dormant_path(tmp_path):
    import tkinter as tk
    from test_gui_export_recovery import controller
    app, starts = controller(tmp_path)
    root = app.values['input']._root
    path = tmp_path / 'RenderLockMap.blend'; path.touch()
    for key, value in dict(external_map_enabled='1', external_map=str(path),
                           character_only='0', character_look='standard').items():
        app.values[key] = tk.StringVar(root, value=value)
    app._conversion_command = build_convert_command(app._current_values())
    choice = recovery_choices(app._conversion_command,
        dict(stage='build_blender_scene', error='External map: failed to load'))[0]
    app._retry_conversion(choice)
    assert len(starts) == 1 and '--external-map' not in starts[0][0]
    assert '--character-only' in starts[0][0]
    assert not app.partial.get() and not app.partial_cloth.get()
    assert app.values['external_map'].get() == str(path)
    assert app.values['external_map_enabled'].get() == '0'


@pytest.mark.parametrize('completed', [False, True])
def test_cli_passes_requested_library_and_publishes_only_completed_map(export_job, monkeypatch, completed):
    from agr import cli
    from blender_fixture import write_blender_completion
    args, _ = export_job
    args.external_map = args.output.parent / 'RenderLockMap.blend'
    library = dict(path=str(args.external_map), sha256=maps.MAP_BLEND_SHA256)
    monkeypatch.setattr(maps, 'validate_map_library', lambda path: library)
    monkeypatch.setattr('agr.blender_runtime.check_blender', lambda *a, **k: {'fixture': True, 'version': [5, 2, 1]})
    calls = []
    def execute(command, check):
        calls.append(command)
        assert command[command.index('--external-map') + 1] == str(args.external_map)
        assert '--character-only' in command and '--disable-autoexec' in command and '--partial' not in command
        return write_blender_completion(command, **({'external_map': map_result()} if completed else {}))
    monkeypatch.setattr(cli.subprocess, 'run', execute)
    if completed:
        cli.convert(args)
        report = json.loads(args.output.with_suffix('.agr-report.json').read_text())
        assert report['map_workflow'] == 'external_blend_appended'
        assert report['map']['status'] == 'appended' and report['external_map']['library_sha256'] == maps.MAP_BLEND_SHA256
        assert report['export_policy']['partial_animation'] is False
    else:
        with pytest.raises(RuntimeError, match='External map:'):
            cli.convert(args)
        assert not args.output.exists()
    assert len(calls) == 1
