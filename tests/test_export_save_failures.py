"""A final save error must not leave a mixture of old and new export files."""
import json
import os
from pathlib import Path

import pytest

from agr import cli
from agr.scene_package import save_scene
from blender_fixture import write_blender_completion


@pytest.fixture
def export_job(tmp_path,monkeypatch):
    assets=tmp_path/'assets';assets.mkdir()
    (assets/'model.blend').write_bytes(b'model fixture, not loaded')
    (assets/'assets.json').write_text(json.dumps({'heroes':{'13':{'model_file':'model.blend'}}}))
    engine=tmp_path/'blender.exe';engine.write_bytes(b'not executed')
    data=dict(schema_version=1,tick_rate=64,start_tick=10,end_tick=10,metadata={'generation':'new'},
              entities=[dict(id='5:1',hero_id=13,samples=[dict(tick=10,position=[1,2,3],body_angles=[0,0,0])])])
    segment=save_scene(data,tmp_path/'input.json');output=tmp_path/'shot.blend'
    args=cli.parser().parse_args(['convert','--segment',str(segment),'--assets',str(assets),
        '--blender',str(engine),'--output',str(output),'--motion-only','--no-effects','--overwrite','--keep-work'])
    monkeypatch.setattr('agr.blender_runtime.check_blender',lambda *a,**k:{'fixture':True})
    monkeypatch.setattr(cli.subprocess,'run',lambda command,check:write_blender_completion(command))
    return args,data


def snapshot(output):
    result={}
    for path in (output,output.with_suffix('.agr-report.json'),output.with_suffix('.agrscene')):
        if path.is_dir():
            result.update({f.relative_to(output.parent).as_posix():f.read_bytes() for f in path.rglob('*') if f.is_file()})
        elif path.exists():result[path.name]=path.read_bytes()
    return result


@pytest.mark.parametrize('existing',[False,True])
@pytest.mark.parametrize('failure',['scene','package','report'])
def test_cli_failed_final_save_preserves_all_previous_outputs(export_job,monkeypatch,failure,existing):
    args,data=export_job;output=args.output
    if existing:
        output.write_bytes(b'previous valid Blender scene')
        output.with_suffix('.agr-report.json').write_text('{"generation":"old"}')
        package=output.with_suffix('.agrscene');package.mkdir()
        save_scene(dict(data,metadata={'generation':'old'}),package/'manifest.json')
        (package/'format.json').write_text(json.dumps({'format':'deadlock-agr-scene','version':1}))
    before=snapshot(output)
    replace=cli.os.replace
    write=Path.write_text
    def locked_scene(source,destination):
        blocked={'scene':output,'package':output.with_suffix('.agrscene'),'report':output.with_suffix('.agr-report.json')}[failure]
        if Path(destination)==blocked:raise PermissionError('Final output is locked by another program')
        return replace(source,destination)
    def locked_report(path,*a,**k):
        if path==output.with_suffix('.agr-report.json'):raise PermissionError('Report cannot be written')
        return write(path,*a,**k)
    monkeypatch.setattr(cli.os,'replace',locked_scene)
    if failure=='report':monkeypatch.setattr(Path,'write_text',locked_report)
    with pytest.raises(PermissionError):cli.convert(args)
    assert snapshot(output)==before
    failures=list(output.parent.glob('agr-*/failure.json'))
    assert len(failures)==1
    assert json.loads(failures[0].read_text())['stage']=='publish_scene'


def previous_outputs(args,data):
    output=args.output
    output.write_bytes(b'previous Blender scene')
    output.with_suffix('.agr-report.json').write_text('{"generation":"old"}')
    package=output.with_suffix('.agrscene');package.mkdir()
    save_scene(dict(data,metadata={'generation':'old'}),package/'manifest.json')
    (package/'format.json').write_text(json.dumps({'format':'deadlock-agr-scene','version':1}))
    return snapshot(output)


@pytest.mark.skipif(os.name!='nt',reason='Actual Windows file-sharing lock')
@pytest.mark.parametrize('locked',['scene','report'])
def test_real_windows_locked_output_preserves_all_previous_files(export_job,locked):
    import ctypes
    from ctypes import wintypes
    args,data=export_job;before=previous_outputs(args,data)
    target=args.output if locked=='scene' else args.output.with_suffix('.agr-report.json')
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    api.CreateFileW.restype=wintypes.HANDLE
    api.CloseHandle.argtypes=[wintypes.HANDLE];api.CloseHandle.restype=wintypes.BOOL
    handle=api.CreateFileW(str(target),0x80000000,1,None,3,0x80,None)
    assert handle!=ctypes.c_void_p(-1).value
    try:
        with pytest.raises(PermissionError):cli.convert(args)
        assert snapshot(args.output)==before
    finally:api.CloseHandle(handle)


def test_report_staging_failure_never_changes_previous_files(export_job,monkeypatch):
    args,data=export_job;before=previous_outputs(args,data);original=Path.write_text
    def full_disk(path,*a,**k):
        if path.name=='publish-report.json':raise OSError('No space left in report staging fixture')
        return original(path,*a,**k)
    monkeypatch.setattr(Path,'write_text',full_disk)
    with pytest.raises(OSError,match='No space'):cli.convert(args)
    assert snapshot(args.output)==before


def test_successful_overwrite_publishes_matching_triplet_and_keeps_old_backup(export_job):
    args,data=export_job;before=previous_outputs(args,data)
    report=cli.convert(args)
    assert report['status']=='converted'
    assert args.output.read_bytes()==b'authored scene stub; no Blender executed'
    package=args.output.with_suffix('.agrscene')
    assert json.loads((package/'manifest.json').read_text())['metadata']['generation']=='new'
    assert json.loads(args.output.with_suffix('.agr-report.json').read_text())==report
    backup=next(args.output.parent.glob('agr-*/publish-backup'))
    assert (backup/args.output.name).read_bytes()==before[args.output.name]
    assert not list(args.output.parent.glob('agr-*/failure.json'))


def test_cleanup_error_is_reported_without_failing_a_successful_save(export_job,monkeypatch,capsys):
    args,data=export_job;previous_outputs(args,data);args.keep_work=False
    original=cli.shutil.rmtree
    def locked_cleanup(path,*a,**k):
        path=Path(path)
        if path.parent==args.output.parent and path.name.startswith('agr-'):
            raise PermissionError('Cleanup fixture is locked')
        return original(path,*a,**k)
    monkeypatch.setattr(cli.shutil,'rmtree',locked_cleanup)
    report=cli.convert(args)
    assert report['status']=='converted'
    assert json.loads(args.output.with_suffix('.agr-report.json').read_text())==report
    assert 'Scene saved; temporary files retained at' in capsys.readouterr().out


def test_restore_failure_retains_old_backup_and_reports_its_location(export_job,monkeypatch):
    args,data=export_job;before=previous_outputs(args,data)
    replace=cli.os.replace;rename=Path.rename
    def locked_report(source,destination):
        if Path(destination)==args.output.with_suffix('.agr-report.json'):raise PermissionError('Report destination locked')
        return replace(source,destination)
    def blocked_restore(source,destination):
        if source.parent.name=='publish-backup' and source.name.endswith('.agrscene'):
            raise PermissionError('Previous package restore blocked')
        return rename(source,destination)
    monkeypatch.setattr(cli.os,'replace',locked_report);monkeypatch.setattr(Path,'rename',blocked_restore)
    with pytest.raises(RuntimeError,match='Previous files are retained at'):
        cli.convert(args)
    backup=next(args.output.parent.glob('agr-*/publish-backup'))
    assert (backup/args.output.with_suffix('.agrscene').name/'manifest.json').read_bytes()==before['shot.agrscene/manifest.json']
    failure=json.loads(next(args.output.parent.glob('agr-*/failure.json')).read_text())
    assert str(backup) in failure['error'] and failure['stage']=='publish_scene'
