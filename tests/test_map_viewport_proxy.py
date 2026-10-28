import json

import pytest

from agr import external_map as maps
from agr.cli import read_blender_completion
from agr.export_recovery import recovery_choices
from agr.gui import build_convert_command
from test_blender_completion_stages import completion
from test_external_map import map_result
from test_export_save_failures import export_job


def proxy_result():
    return dict(status='ready', collection='RenderLockProxy', algorithm='grid-0.25m-v1',
                source_map_sha256=maps.MAP_BLEND_SHA256, source_meshes=maps.MAP_MESHES,
                source_vertices=maps.MAP_VERTICES, source_faces=1000, proxy_meshes=20,
                proxy_vertices=200, proxy_faces=400, full_map_hidden_in_viewport=True,
                proxy_hidden_in_render=True)


@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('mutation', ['missing', 'hash', 'viewport', 'render', 'count', 'boolean'])
def test_requested_proxy_requires_the_completed_map_and_valid_visibility(tmp_path, partial, mutation):
    full_map, proxy = map_result(), proxy_result()
    path, job = completion(tmp_path, external_map=full_map, map_proxy=proxy)
    library = {'sha256': maps.MAP_BLEND_SHA256}
    assert read_blender_completion(path, **job, external_map=library, partial=partial, map_viewport_proxy=True)
    if mutation == 'missing':
        proxy = None
    elif mutation == 'hash':
        proxy['source_map_sha256'] = 'other'
    elif mutation == 'viewport':
        proxy['full_map_hidden_in_viewport'] = False
    elif mutation == 'render':
        proxy['proxy_hidden_in_render'] = False
    elif mutation == 'boolean':
        proxy['proxy_hidden_in_render'] = 1
    else:
        proxy['proxy_faces'] = proxy['source_faces'] + 1
    doc = json.loads(path.read_text())
    doc['map_proxy'] = proxy
    path.write_text(json.dumps(doc))
    with pytest.raises(RuntimeError, match='Viewport proxy:'):
        read_blender_completion(path, **job, external_map=library, partial=partial, map_viewport_proxy=True)


def test_gui_only_requests_proxy_with_map_and_preserves_choice(tmp_path):
    for name in ('replay.dem', 'blender.exe', 'RenderLockMap.blend'):
        (tmp_path / name).touch()
    values = dict(input=str(tmp_path / 'replay.dem'), mode='replay', assets=str(tmp_path),
        asset_source='prepared', blender=str(tmp_path / 'blender.exe'), output=str(tmp_path / 'out.blend'),
        start='1', end='3', fps='64', character_look='standard', external_map_enabled='1',
        external_map=str(tmp_path / 'RenderLockMap.blend'), map_viewport_proxy='1')
    assert '--map-viewport-proxy' in build_convert_command(values)
    values['external_map_enabled'] = '0'
    assert '--map-viewport-proxy' not in build_convert_command(values)
    assert values['map_viewport_proxy'] == '1'


def test_proxy_retry_keeps_full_map_and_map_retry_removes_dependent_proxy():
    command = ['python', '-m', 'agr', 'convert', '--external-map', 'map.blend',
               '--map-viewport-proxy', '--source-lighting', '--output', 'out.blend']
    proxy_failure = dict(stage='build_blender_scene', error='Viewport proxy: unsupported cell')
    choice = recovery_choices(command, proxy_failure)[0]
    assert choice.id == 'without_map_proxy'
    assert '--map-viewport-proxy' not in choice.command
    assert {'--external-map', 'map.blend', '--source-lighting'} <= set(choice.command)
    assert dict(choice.values_overrides) == {'map_viewport_proxy': '0'}
    map_failure = dict(stage='build_blender_scene', error='External map: bad library')
    retry = recovery_choices(command, map_failure)[0].command
    assert '--external-map' not in retry and '--map-viewport-proxy' not in retry
    assert '--source-lighting' in retry and '--partial' not in retry
    assert not recovery_choices(command, dict(stage='evaluate_animation', error=proxy_failure['error']))


def test_proxy_retry_updates_the_headless_gui_controller(tmp_path):
    import tkinter as tk
    from test_gui_export_recovery import controller
    app, starts = controller(tmp_path)
    interpreter = app.values['input']._root
    path = tmp_path / 'RenderLockMap.blend'
    path.touch()
    for key, value in dict(external_map_enabled='1', external_map=str(path),
                           map_viewport_proxy='1', character_only='0', character_look='standard').items():
        app.values[key] = tk.StringVar(interpreter, value=value)
    app._conversion_command = build_convert_command(app._current_values())
    choice = recovery_choices(app._conversion_command,
        dict(stage='build_blender_scene', error='Viewport proxy: unsupported cell'))[0]
    app._retry_conversion(choice)
    assert len(starts) == 1
    assert '--external-map' in starts[0][0] and '--map-viewport-proxy' not in starts[0][0]
    assert app.values['map_viewport_proxy'].get() == '0'
    assert app.values['external_map'].get() == str(path)


@pytest.mark.parametrize('map_kind', ['missing', 'legacy'])
def test_proxy_requires_the_current_map_before_scene_preparation(export_job, monkeypatch, map_kind):
    from agr import cli
    args, _ = export_job
    args.map_viewport_proxy = True
    args.external_map = None if map_kind == 'missing' else args.output.parent / 'old-map.blend'
    if map_kind == 'legacy':
        monkeypatch.setattr(maps, 'validate_map_library', lambda path: {'path':str(path), 'sha256':'legacy'})
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: pytest.fail('Started Blender before proxy preflight'))
    with pytest.raises(ValueError, match='Viewport proxy:'):
        cli.convert(args)
    assert not args.output.exists()


@pytest.mark.parametrize('completed', [False, True])
def test_convert_publishes_proxy_only_after_its_completion(export_job, monkeypatch, completed):
    from agr import cli
    from blender_fixture import write_blender_completion
    args, _ = export_job
    args.external_map = args.output.parent / 'RenderLockMap.blend'
    args.map_viewport_proxy = True
    library = dict(path=str(args.external_map), sha256=maps.MAP_BLEND_SHA256)
    monkeypatch.setattr(maps, 'validate_map_library', lambda path: library)
    monkeypatch.setattr('agr.blender_runtime.check_blender', lambda *a, **k: {'version':[5, 2, 1]})
    def execute(command, check):
        assert '--map-viewport-proxy' in command and '--external-map' in command
        extras = {'external_map': map_result()}
        if completed:
            extras['map_proxy'] = proxy_result()
        return write_blender_completion(command, **extras)
    monkeypatch.setattr(cli.subprocess, 'run', execute)
    if completed:
        cli.convert(args)
        report = json.loads(args.output.with_suffix('.agr-report.json').read_text())
        assert report['map_proxy'] == proxy_result() and report['external_map']['status'] == 'appended'
    else:
        with pytest.raises(RuntimeError, match='Viewport proxy:'):
            cli.convert(args)
        assert not args.output.exists()
