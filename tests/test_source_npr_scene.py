import json
import copy
from types import SimpleNamespace
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


def reusable_scene(tmp_path,monkeypatch):
    paths=[];documents={};hashes={};calls=[]
    for name in ('a','b'):
        path=tmp_path/(name+'.glb');path.write_bytes(name.encode());paths.append(path)
        resource='materials/'+name+'.vmat_c';hashes[resource]=name*64
        documents[path.name]=dict(materials=[dict(extras=dict(vmat=dict(Name=resource[:-2])))],
                                   meshes=[dict(primitives=[dict(material=0)])])
    monkeypatch.setattr(stage,'read_glb',lambda path:copy.deepcopy(documents[path.name]))
    def plan(document,*args):
        calls.append('plan')
        name=document['materials'][0]['extras']['vmat']['Name']+'_c'
        return dict(status='sources_resolved_not_decoded',materials=[dict(material=name,source_sha256=hashes[name])])
    monkeypatch.setattr(stage,'plan_npr_material_assets',plan)
    monkeypatch.setattr(stage,'decode_npr_material_assets',lambda *a:calls.append('decode') or {})
    monkeypatch.setattr(stage,'build_npr_material_package',lambda *a,**kw:calls.append('package') or {})
    provider=SimpleNamespace(resolve=lambda name:SimpleNamespace(sha256=hashes[name]))
    segment=dict(entities=[dict(id=name,model_file=path.name) for name,path in zip(('a','b'),paths)])
    return segment,provider,hashes,documents,calls


def test_plan_can_be_reused_without_replanning_and_all_models_plan_before_decode(tmp_path,monkeypatch):
    segment,provider,_,_,calls=reusable_scene(tmp_path,monkeypatch)
    plan=stage.plan_scene_npr_materials(segment,provider,None,manifest_dir=tmp_path,allow_partial=True)
    assert calls==['plan','plan'] and plan['status']=='planned_requires_decode'
    result=stage.prepare_scene_npr_materials(segment,provider,None,manifest_dir=tmp_path,
                                             allow_partial=True,plan=plan)
    assert calls==['plan','plan','decode','package','decode','package']
    assert result['entities']==plan['entities']


@pytest.mark.parametrize('mutation',['model','actor','duplicate_actor','missing_plan','changed_plan','source_coverage','document','consent'])
def test_reused_plan_rejects_changed_selection_or_material_union_before_any_decode(tmp_path,monkeypatch,mutation):
    segment,provider,_,documents,calls=reusable_scene(tmp_path,monkeypatch)
    plan=stage.plan_scene_npr_materials(segment,provider,None,manifest_dir=tmp_path,allow_partial=True)
    if mutation=='model':(tmp_path/'b.glb').write_bytes(b'changed')
    elif mutation=='actor':segment['entities'][0]['id']='other'
    elif mutation=='duplicate_actor':segment['entities'].append(copy.deepcopy(segment['entities'][0]))
    elif mutation=='missing_plan':plan['models'].pop(next(iter(plan['models'])))
    elif mutation=='changed_plan':next(iter(plan['models'].values()))['materials'][0]['source_sha256']='f'*64
    elif mutation=='source_coverage':
        next(iter(plan['models'].values()))['materials']*=2
        plan['content_sha256']=stage.canonical_sha({k:v for k,v in plan.items() if k!='content_sha256'})
    elif mutation=='document':documents['b.glb']['materials'][0]['extras']['vmat']['Name']='materials/other.vmat'
    with pytest.raises(ValueError):
        stage.prepare_scene_npr_materials(segment,provider,None,manifest_dir=tmp_path,
                                          allow_partial=mutation!='consent',plan=plan)
    assert calls==['plan','plan']


def test_reused_plan_checks_later_installed_material_before_decoding_first_model(tmp_path,monkeypatch):
    segment,provider,hashes,_,calls=reusable_scene(tmp_path,monkeypatch)
    plan=stage.plan_scene_npr_materials(segment,provider,None,manifest_dir=tmp_path,allow_partial=True)
    hashes['materials/b.vmat_c']='f'*64
    with pytest.raises(ValueError,match='source changed') as caught:
        stage.prepare_scene_npr_materials(segment,provider,None,manifest_dir=tmp_path,allow_partial=True,plan=plan)
    assert calls==['plan','plan']
    assert caught.value.details['stage']=='selected_npr_material_preflight'
    assert caught.value.details['npr_selection']['materials']==['materials/b.vmat']


def test_reused_plan_allows_identical_model_relocation(tmp_path,monkeypatch):
    segment,provider,_,documents,calls=reusable_scene(tmp_path,monkeypatch)
    plan=stage.plan_scene_npr_materials(segment,provider,None,manifest_dir=tmp_path,allow_partial=True)
    (tmp_path/'relocated.glb').write_bytes(b'a');documents['relocated.glb']=documents['a.glb']
    segment['entities'][0]['model_file']='relocated.glb'
    result=stage.prepare_scene_npr_materials(segment,provider,None,manifest_dir=tmp_path,allow_partial=True,plan=plan)
    assert result['entities'][0]['model_file']==str((tmp_path/'relocated.glb').resolve())
    assert calls.count('plan')==2
