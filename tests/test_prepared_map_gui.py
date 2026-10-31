import queue
import json
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk

from agr import gui
from agr.cli import parser


def test_map_build_command_needs_no_replay_and_preserves_explicit_paths(tmp_path):
    blender=tmp_path/'Blender 5.2.exe';blender.touch()
    game=tmp_path/'Game files';game.mkdir()
    cache=tmp_path/'Map cache'
    command=gui.build_map_command(dict(blender=str(blender),game_install=str(game),asset_cache=str(cache)))
    args=parser().parse_args(command[3:])
    assert args.command=='prepare-map' and args.map_name=='dl_midtown'
    assert args.game_install==game and args.asset_cache==cache
    assert not cache.exists()


def test_map_proxy_cli_default_can_be_explicitly_disabled():
    base=['convert','--segment','segment.json','--output','scene.blend']
    assert parser().parse_args(base).map_viewport_proxy is None
    assert parser().parse_args([*base,'--map-viewport-proxy']).map_viewport_proxy is True
    assert parser().parse_args([*base,'--no-map-viewport-proxy']).map_viewport_proxy is False


def test_first_prepared_map_promotes_legacy_opt_in_default_and_then_preserves_explicit_choice(tmp_path):
    interpreter=tk.Tcl();app=gui.AGRApp.__new__(gui.AGRApp)
    app.values={key:tk.StringVar(interpreter,value='0') for key in
        ('map_viewport_proxy','prepared_map_proxy_default_applied')}
    library=tmp_path/'RenderLockMap.blend'
    (tmp_path/'map-package.json').write_text(json.dumps(dict(format='legacy-map')))
    app._apply_prepared_map_defaults(library)
    assert app.values['map_viewport_proxy'].get()=='0'
    (tmp_path/'map-package.json').write_text(json.dumps(dict(format='renderlock-map-v2')))
    app._apply_prepared_map_defaults(library)
    assert app.values['map_viewport_proxy'].get()=='1'
    assert app.values['prepared_map_proxy_default_applied'].get()=='1'
    app.values['map_viewport_proxy'].set('0')
    app._apply_prepared_map_defaults(library)
    assert app.values['map_viewport_proxy'].get()=='0'


def test_successful_map_build_selects_verified_path_without_touching_replay_selection(tmp_path):
    interpreter=tk.Tcl()
    app=gui.AGRApp.__new__(gui.AGRApp)
    app.values={key:tk.StringVar(interpreter,value=value) for key,value in
        dict(external_map='',external_map_enabled='0',input='retained.dem',map_viewport_proxy='1').items()}
    app.status=tk.StringVar(interpreter)
    app.events=queue.Queue();app.cancel_requested=SimpleNamespace(is_set=lambda:False)
    app.close_when_done=False;app._set_busy=lambda busy:None
    saves=[];app._save_settings=lambda:saves.append(True)
    app.root=SimpleNamespace(after=lambda *args:None)
    path=tmp_path/'RenderLockMap.blend';path.touch()
    app.events.put(('finished',('prepare_map',0,'RENDERLOCK_MAP_READY='+str(path)+'\n')))
    app._poll()
    assert app.values['external_map'].get()==str(path)
    assert app.values['external_map_enabled'].get()=='1'
    assert app.values['input'].get()=='retained.dem'
    assert app.values['map_viewport_proxy'].get()=='1'
    assert saves==[True]
