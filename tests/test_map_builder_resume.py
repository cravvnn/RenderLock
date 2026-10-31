"""Exercise the standalone bridge's interrupted-build contract across processes."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_interrupted_import_reuses_verified_stages_and_tampering_rebuilds(tmp_path):
    source=tmp_path/'upstream';(source/'tools').mkdir(parents=True)
    (source/'LICENSE').write_text('Test fixture')
    (source/'tools/mapbuild.py').write_text('''
import json,os
from pathlib import Path
class MapFiles:
    def __init__(self,slug,work,out):
        self.dir=Path(work)/'maps'/slug;self.out=Path(out)/'maps'/slug
        self.export=self.dir/'export';self.rebatched=self.dir/'rebatched/map.gltf'
        self.lighting=self.dir/'lighting';self.stamp_file=self.out/'map.json'
def game_stamp(*args):return dict(game_version='6839')
class MapBuilder:
    def __init__(self,args):
        self.work=args.work;self.out=args.out;self.pak=args.pak
    def run(self,*args,**kwargs):raise AssertionError('No native tools in this fixture')
    def write(self,name,folder):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        (folder/(name+'.txt')).write_text(name)
        with (Path(self.work).parent/'events.txt').open('a') as stream:stream.write(name+'\\n')
    def export(self,m):self.write('export',m.export)
    def rebatch(self,m):self.write('rebatch',m.rebatched.parent)
    def lighting(self,m):self.write('lighting',m.lighting)
    def import_(self,m):
        self.write('import',m.out)
        if (Path(self.work).parent/'fail-import').exists():raise RuntimeError('Interrupted import')
        (m.out/'dl_midtown.blend').write_text('Completed native output')
    def build_map(self,slug,force=False):
        m=MapFiles(slug,self.work,self.out)
        for step in (self.export,self.rebatch,self.lighting,self.import_):step(m)
        m.stamp_file.write_text(json.dumps(game_stamp()))
''',encoding='utf-8')
    job=tmp_path/'job';job.mkdir()
    marker=job/'fail-import';marker.touch()
    request=dict(root=str(job),builder_commit='0d1958ab553630df997a93fda06d3ab9ed6994c3',
        map='dl_midtown',work=str(job/'work'),out=str(job/'native'),s2v_cache=str(job/'s2v'),
        blender='unused-blender',s2v='unused-s2v',pak='unused-pak',input_identity_sha256='a'*64)
    request_file=job/'request.json';request_file.write_text(json.dumps(request))
    bridge=Path(__file__).resolve().parents[1]/'tools/map_builder/bridge.py'
    def run():
        return subprocess.run([sys.executable,'-B',str(bridge),'--source',str(source),
            '--request',str(request_file)],capture_output=True,text=True,
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert run().returncode!=0
    assert set(json.loads((job/'checkpoints.json').read_text())['stages'])=={'export','rebatch','lighting'}
    marker.unlink()
    resumed=run();assert resumed.returncode==0,resumed.stderr
    assert 'Resuming map import' in resumed.stdout
    events=lambda:(job/'events.txt').read_text().splitlines()
    assert events()==['export','rebatch','lighting','import','import']
    reused=run();assert reused.returncode==0,reused.stderr
    assert 'reusing the completed build' in reused.stdout
    assert len(events())==5
    (job/'native/maps/dl_midtown/dl_midtown.blend').write_text('damaged native output')
    (job/'work/maps/dl_midtown/export/export.txt').write_text('damaged extracted input')
    rebuilt=run();assert rebuilt.returncode==0,rebuilt.stderr
    assert events()[-4:]==['export','rebatch','lighting','import']
    assert list((job/'work/maps').glob('dl_midtown.preserved-*'))
    assert list((job/'native/maps').glob('dl_midtown.preserved-*'))
