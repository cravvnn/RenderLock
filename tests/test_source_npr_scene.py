import json
import pytest
from agr import source_npr_scene as stage


def setup_models(tmp_path,monkeypatch):
    model=tmp_path/'model.glb';model.write_bytes(b'model')
    monkeypatch.setattr(stage,'read_glb',lambda p:{'model':p.name})
    calls=[]
    monkeypatch.setattr(stage,'plan_npr_material_assets',lambda *a:calls.append('plan') or {})
    monkeypatch.setattr(stage,'decode_npr_material_assets',lambda *a:calls.append('decode') or {})
    monkeypatch.setattr(stage,'build_npr_material_package',lambda *a,**k:calls.append('package') or {'allow_partial':k['allow_partial']})
    return dict(entities=[dict(id='a',model_file=model.name),dict(id='b',model_file=model.name)]),calls


def test_shared_model_prepared_once_with_exact_entity_selection(tmp_path,monkeypatch):
    segment,calls=setup_models(tmp_path,monkeypatch)
    before=json.dumps(segment)
    result=stage.prepare_scene_npr_materials(segment,None,None,manifest_dir=tmp_path,allow_partial=True)
    assert calls==['plan','decode','package']
    assert [r['entity_id'] for r in result['entities']]==['a','b']
    assert len(result['models'])==1
    assert result['status']=='prepared_requires_blender_validation'
    assert json.dumps(segment)==before


@pytest.mark.parametrize('case',['implicit','duplicate','missing_model'])
def test_invalid_selection_rejected_before_planning(tmp_path,monkeypatch,case):
    segment,calls=setup_models(tmp_path,monkeypatch)
    if case=='duplicate':segment['entities'][1]['id']='a'
    if case=='missing_model':segment['entities'][1].pop('model_file')
    with pytest.raises(ValueError):
        stage.prepare_scene_npr_materials(segment,None,None,manifest_dir=tmp_path,allow_partial=case!='implicit')
    assert calls==[]


def test_later_unsupported_model_prevents_any_decode(tmp_path,monkeypatch):
    segment,calls=setup_models(tmp_path,monkeypatch)
    (tmp_path/'other.glb').write_bytes(b'other')
    segment['entities'][1]['model_file']='other.glb'
    def plan(document,*args):
        if document['model']=='other.glb':raise ValueError('unsupported model')
        calls.append('plan');return {}
    monkeypatch.setattr(stage,'plan_npr_material_assets',plan)
    with pytest.raises(ValueError,match='unsupported'):
        stage.prepare_scene_npr_materials(segment,None,None,manifest_dir=tmp_path,allow_partial=True)
    assert calls==['plan']


@pytest.mark.parametrize('mutation',['model_bytes','entity_id','missing_model_package','extra_model_package','duplicate_current'])
def test_scene_selection_rejects_stale_or_wrong_actor(tmp_path,monkeypatch,mutation):
    segment,calls=setup_models(tmp_path,monkeypatch)
    package=stage.prepare_scene_npr_materials(segment,None,None,manifest_dir=tmp_path,allow_partial=True)
    monkeypatch.setattr(stage,'validate_npr_material_package',lambda p:None)
    if mutation=='model_bytes':(tmp_path/'model.glb').write_bytes(b'changed')
    if mutation=='entity_id':segment['entities'][0]['id']='wrong'
    if mutation=='missing_model_package':package['models'].clear()
    if mutation=='extra_model_package':package['models']['extra']={}
    if mutation=='duplicate_current':segment['entities'].append(segment['entities'][0])
    with pytest.raises(ValueError):stage.validate_scene_npr_selection(package,segment['entities'],manifest_dir=tmp_path)


def test_scene_selection_uses_current_identical_model_location(tmp_path,monkeypatch):
    segment,calls=setup_models(tmp_path,monkeypatch)
    package=stage.prepare_scene_npr_materials(segment,None,None,manifest_dir=tmp_path,allow_partial=True)
    monkeypatch.setattr(stage,'validate_npr_material_package',lambda p:None)
    (tmp_path/'relocated.glb').write_bytes(b'model')
    segment['entities'][0]['model_file']='relocated.glb'
    result=stage.validate_scene_npr_selection(package,segment['entities'],manifest_dir=tmp_path)
    assert set(result)=={'a','b'}
