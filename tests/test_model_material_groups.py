"""Selection/cache contracts; authentic conversion/render proof is separate."""
import copy
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import pytest
from agr.asset_converter import sha
from agr import model_material_groups as stage
from agr.source_material_groups import material_group_token
from agr.source_hero_lighting_package import canonical_sha


def glb(path,materials):
    document=dict(materials=[dict(name=Path(name).stem,extras=dict(vmat=dict(Name=name,ShaderName='pbr.vfx',IntParams={}))) for name in materials],
        meshes=[dict(primitives=[dict(material=index) for index in range(len(materials))])],skins=[dict(joints=[0])],nodes=[dict(name='preserved joint')])
    payload=json.dumps(document).encode();payload+=b' '*(-len(payload)%4)
    path.write_bytes(struct.pack('<4sIII4s',b'glTF',2,20+len(payload),len(payload),b'JSON')+payload)
    return document


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    model=tmp_path/'original.glb';document=glb(model,['materials/head.vmat','materials/body.vmat'])
    variant=tmp_path/'variants.glb';glb(variant,['materials/head_alt.vmat'])
    groups=[dict(m_name='base',m_materials=['materials/head.vmat','materials/body.vmat']),
        dict(m_name='alternate',m_materials=['materials/head_alt.vmat',None])]
    monkeypatch.setattr(stage,'dump_map_data',lambda *a:dict(m_materialGroups=copy.deepcopy(groups)))
    bundle=dict(path=str(variant),sha256=sha(variant),materials=['materials/head_alt.vmat_c'])
    calls=[]
    monkeypatch.setattr(stage,'_bundle',lambda resources,*a,**kw:calls.append(set(resources)) or copy.deepcopy(bundle))
    provider=SimpleNamespace(resolve=lambda _:SimpleNamespace(sha256='a'*64))
    samples=[dict(tick=t,network_render_state=dict(material_group=token)) for t,token in [(10,0),(11,material_group_token('alternate')),(12,0xffffffff)]]
    def prepare(samples=samples):return stage.prepare_model_material_groups(model,'models/owned.vmdl_c',dict(samples=samples),provider,None)
    return SimpleNamespace(model=model,document=document,variant=variant,calls=calls,samples=samples,prepare=prepare)


def test_exact_selected_union_uses_separate_bundle_and_keeps_original_geometry_and_skin(prepared):
    before=prepared.model.read_bytes();contract=prepared.prepare();planning=stage.material_union_document(prepared.document,contract)
    assert prepared.calls==[{'materials/head_alt.vmat_c'}]
    assert prepared.model.read_bytes()==before
    assert planning['skins']==prepared.document['skins'] and planning['nodes']==prepared.document['nodes']
    assert planning['meshes'][:-1]==prepared.document['meshes']
    assert contract['track']['selections'][1]['replacements']=={'materials/head.vmat_c':'materials/head_alt.vmat_c'}
    assert contract['track']['selections'][0]['group_index']==-1 and contract['track']['selections'][2]['group_index']==-1
    stage.validate_material_group_contract(contract,prepared.samples,model=prepared.model)


def test_unchanged_group_needs_no_bundle_or_converter(prepared):
    contract=prepared.prepare([dict(tick=10,network_render_state=dict(material_group=material_group_token('base')))])
    assert contract['bundle'] is None and not prepared.calls


def test_missing_recorded_group_is_explicit_reference_and_requires_broad_partial_for_animation(prepared):
    contract=prepared.prepare([dict(tick=10)])
    assert stage.material_group_summary(contract)['basis']=='not_recorded'
    with pytest.raises(ValueError,match='--partial'):stage.validate_material_group_consent(contract,poses_requested=True,partial=False)
    stage.validate_material_group_consent(contract,poses_requested=True,partial=True)
    stage.validate_material_group_consent(contract,poses_requested=False,partial=False)
    with pytest.raises(ValueError,match='gaps'):prepared.prepare([prepared.samples[0],dict(tick=11)])


@pytest.mark.parametrize('mutation',['model','bundle','track','dropped_bundle','wrong_bundle_materials','base_inventory'])
def test_stale_or_incomplete_material_contract_rejected(prepared,mutation):
    contract=prepared.prepare()
    if mutation=='model':prepared.model.write_bytes(b'changed')
    elif mutation=='bundle':prepared.variant.write_bytes(b'changed')
    elif mutation=='track':contract['track']['selections'][1]['token']=0
    elif mutation=='dropped_bundle':contract['bundle']=None
    elif mutation=='wrong_bundle_materials':contract['bundle']['materials']=[]
    else:contract['base_materials']=['materials/invented.vmat_c']
    # Recomputing the envelope hash must not bypass semantic coverage checks.
    contract['content_sha256']=canonical_sha({key:value for key,value in contract.items() if key!='content_sha256'})
    with pytest.raises(ValueError):stage.validate_material_group_contract(contract,prepared.samples,model=prepared.model)


def test_two_actors_same_model_with_different_selected_materials_get_distinct_npr_sets(prepared,monkeypatch):
    from agr import source_npr_scene as npr
    a=prepared.prepare();b=prepared.prepare([dict(tick=10,network_render_state=dict(material_group=0))])
    entities=[dict(id='a',model_file=str(prepared.model),model_material_groups=a,samples=prepared.samples),
        dict(id='b',model_file=str(prepared.model),model_material_groups=b,samples=[dict(tick=10,network_render_state=dict(material_group=0))])]
    seen=[]
    monkeypatch.setattr(npr,'plan_npr_material_assets',lambda doc,*a:seen.append(doc) or doc)
    monkeypatch.setattr(npr,'decode_npr_material_assets',lambda *a:{})
    monkeypatch.setattr(npr,'build_npr_material_package',lambda doc,*a,**kw:dict(materials=list(stage.used_materials(doc))))
    package=npr.prepare_scene_npr_materials(dict(entities=entities),None,None,manifest_dir=prepared.model.parent,allow_partial=True)
    assert len(package['models'])==2
    assert {len(stage.used_materials(doc)) for doc in seen}=={2,3}
    monkeypatch.setattr(npr,'validate_npr_material_package',lambda _:None)
    selected=npr.validate_scene_npr_selection(package,entities,manifest_dir=prepared.model.parent)
    assert len(selected['a']['materials'])==3 and len(selected['b']['materials'])==2
    entities[0]['model_material_groups']=b
    with pytest.raises(ValueError,match='coverage changed'):npr.validate_scene_npr_selection(package,entities,manifest_dir=prepared.model.parent)


@pytest.mark.parametrize('partial',[False,True])
def test_completion_cannot_drop_or_shorten_material_slot_outcome_even_partial(prepared,tmp_path,partial):
    from agr.cli import read_blender_completion
    from test_blender_completion_stages import completion
    path,job=completion(tmp_path);contract=prepared.prepare()
    job['data']['entities'][0].update(model_file=str(prepared.model),samples=prepared.samples,model_material_groups=contract)
    report=json.loads(path.read_text());expected=stage.material_group_summary(contract)
    report['entities'][0]['material_groups']=copy.deepcopy(expected);path.write_text(json.dumps(report))
    assert read_blender_completion(path,**job,partial=partial)
    for mutation in ('missing','last_selection'):
        if mutation=='missing':report['entities'][0].pop('material_groups')
        else:
            report['entities'][0]['material_groups']=copy.deepcopy(expected)
            report['entities'][0]['material_groups']['selections'].pop()
        path.write_text(json.dumps(report))
        with pytest.raises(RuntimeError,match='exact material-group slot coverage'):
            read_blender_completion(path,**job,partial=partial)
