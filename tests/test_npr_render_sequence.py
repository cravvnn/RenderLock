import json
from types import SimpleNamespace
import pytest
from agr import npr_render_sequence as runner


@pytest.mark.parametrize('failure',[None,'exit','receipt','image','frame'])
def test_sequence_validates_worker_results_and_stops(tmp_path,monkeypatch,failure):
    blender=tmp_path/'blender.exe';blender.write_bytes(b'fixture')
    scene=tmp_path/'scene.blend';scene.write_bytes(b'fixture scene')
    segment=tmp_path/'segment.json';segment.write_text(json.dumps(dict(npr_materials={},entities=[])))
    monkeypatch.setattr(runner,'validate_npr_runtime',lambda *a,**k:{'mode':'fixture'})
    monkeypatch.setattr(runner,'validate_scene_npr_selection',lambda *a,**k:None)
    calls=[]
    def run(command,**kwargs):
        request=json.loads(open(command[-1]).read());calls.append(request['frame'])
        if failure=='exit':return SimpleNamespace(returncode=1)
        out=runner.Path(request['output']);out.mkdir();image=out/'frame.png';image.write_bytes(b'rendered image')
        receipt=dict(status='FAIL' if failure=='receipt' else 'PASS',frame=999 if failure=='frame' else request['frame'],
            scene_sha256=runner.sha(scene),audit={'status':'PASS'},image={'path':'frame.png','sha256':runner.sha(image)})
        (out/'frame-report.json').write_text(json.dumps(receipt))
        if failure=='image':image.write_bytes(b'changed')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner.subprocess,'run',run)
    output=tmp_path/'output'
    if failure:
        with pytest.raises((ValueError,RuntimeError)):
            runner.render_sequence(blender,scene,segment,output,[1,2],allow_partial=True)
        assert calls==[1]
        assert json.loads((output/'sequence-report.json').read_text())['status']=='FAIL'
    else:
        result=runner.render_sequence(blender,scene,segment,output,[1,2],allow_partial=True)
        assert calls==[1,2] and result['status']=='PASS' and len(result['frames'])==2


def test_render_cli_requires_explicit_frames():
    from agr.cli import parser
    args=parser().parse_args(['render-npr','--blender','b','--scene','s','--segment','m','--output','o','--frames','1','121','--partial'])
    assert args.frames==[1,121] and args.partial
