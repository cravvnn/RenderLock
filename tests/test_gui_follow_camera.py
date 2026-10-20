"""The named camera menu drives the existing validated export command."""
import tkinter as tk

import pytest

from agr import cli, gui, gui_theme, gui_widgets
from agr.gui_characters import FollowHeroPicker


@pytest.fixture(scope='module')
def camera_root():
    try:root=tk.Tk()
    except tk.TclError as exc:pytest.skip(f'Tk display unavailable: {exc}')
    root.withdraw();root.geometry('560x260+30000+30000')
    gui_widgets._CORNER_CACHE.clear();gui_theme.initialize(root)
    root.deiconify();root.update()
    yield root
    root.destroy();gui_widgets._CORNER_CACHE.clear()


@pytest.fixture
def picker(camera_root):
    parent=tk.Frame(camera_root,background=gui_theme.CARD);parent.pack(fill='both',expand=True)
    hero=tk.StringVar(master=camera_root)
    widget=FollowHeroPicker(parent,hero=hero);widget.pack(fill='x')
    camera_root.update()
    yield widget,hero
    parent.destroy();camera_root.update()


def menu_choices(widget):
    choices=[]
    for i in range(widget.menu.index('end')+1):
        if widget.menu.type(i)!='radiobutton':continue
        value=widget.menu.entrycget(i,'value')
        choices.append((i,widget.menu.entrycget(i,'label'),'' if value==widget._OVERVIEW else value))
    return choices


def choose(widget,identity):
    index,= [i for i,_,value in menu_choices(widget) if value==str(identity)]
    widget.menu.invoke(index)


def roster():
    return dict(heroes=[dict(hero_id=76,hero_name='Graves'),dict(hero_id=11,hero_name='Dynamo')])


def test_only_inspected_heroes_populate_menu_and_selection_drives_real_cli(picker,tmp_path):
    widget,hero=picker
    assert menu_choices(widget)==[(0,'Overview (no follow)','')]
    assert widget._choice.get()==widget.menu.entrycget(0,'value')
    assert 'Inspect' in widget._notice.get()
    widget.load(roster())
    assert [(name,value) for _,name,value in menu_choices(widget)]==[
        ('Overview (no follow)',''),('Dynamo','11'),('Graves','76')]
    choose(widget,76)
    assert hero.get()=='76' and 'Graves' in widget.button.cget('text')
    for name in ('replay.dem','blender.exe','Source2Viewer-CLI.exe'):(tmp_path/name).touch()
    values=dict(input=str(tmp_path/'replay.dem'),mode='replay',asset_source='installed',
        game_install=str(tmp_path),source2viewer=str(tmp_path/'Source2Viewer-CLI.exe'),
        blender=str(tmp_path/'blender.exe'),output=str(tmp_path/'result.blend'),
        start='10',end='20',fps='64',heroes='76',heroes_empty='0',shot_hero=hero.get())
    args=cli.parser().parse_args(gui.build_convert_command(values)[3:])
    assert args.shot_hero==76 and args.heroes==[76]
    choose(widget,'')
    assert hero.get()=='' and widget._choice.get()==widget.menu.entrycget(0,'value')
    values['shot_hero']=hero.get()
    assert cli.parser().parse_args(gui.build_convert_command(values)[3:]).shot_hero is None


def test_reinspection_replaces_names_without_silently_changing_saved_target(picker):
    widget,hero=picker;hero.set('76');widget.load(roster())
    widget.load(dict(heroes=[dict(hero_id=2,hero_name='Seven')]))
    assert hero.get()=='76' and widget.hero_ids=={2}
    assert 'Unavailable' in widget.button.cget('text')
    assert 'not in this recording' in widget._notice.get()
    choose(widget,2)
    assert hero.get()=='2' and 'Seven' in widget.button.cget('text')


def test_changing_recording_clears_target_but_stale_revision_preserves_value(picker):
    widget,hero=picker;widget.load(roster());choose(widget,11)
    widget.clear(reset_selection=False)
    assert hero.get()=='11' and widget.hero_ids==set()
    assert 'Saved follow target' in widget.button.cget('text')
    widget.clear(reset_selection=True)
    assert hero.get()=='' and menu_choices(widget)==[(0,'Overview (no follow)','')]


def test_busy_menu_cannot_change_target_and_returns_to_actual_selection(picker):
    widget,hero=picker;widget.load(roster());choose(widget,11)
    widget.set_busy(True);choose(widget,76)
    assert hero.get()=='11' and widget._choice.get()=='11'
    assert widget.button.cget('state')=='disabled'
    widget.set_busy(False);choose(widget,76)
    assert hero.get()=='76' and widget.button.cget('state')=='normal'


def test_saved_package_duplicates_and_future_hero_names_keep_recorded_identity(picker):
    widget,hero=picker
    widget.load(dict(roster=[dict(hero_id=11,hero_name='Dynamo',id='1:2'),
        dict(hero_id=11,hero_name='Dynamo',id='3:4'),dict(hero_id=9001),
        dict(hero_id=True),dict(hero_id=0),dict(hero_id='76'),None]))
    assert widget.hero_ids=={11,9001}
    assert [name for _,name,_ in menu_choices(widget)]==['Overview (no follow)','Dynamo','Hero 9001']
    choose(widget,9001)
    assert hero.get()=='9001'


def test_restored_setting_syncs_label_and_destroy_removes_variable_callback(picker):
    widget,hero=picker;hero.set('11')
    assert 'Saved follow target' in widget.button.cget('text')
    widget.load(roster())
    assert 'Dynamo' in widget.button.cget('text')
    trace=widget._trace
    widget.destroy()
    assert trace not in str(hero.trace_info())
    hero.set('76')
