"""Selected-material preflight ordering; synthetic sources, no installed game."""
import copy
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from agr import installed_assets, source_npr_scene, source_npr_assets, material_expressions
from agr.asset_converter import ConversionError, sha
from agr.scene_extensions import prepare_scene_extensions
from agr.vendor.resource_hash import resource_id


def write_model(path, names):
    document=dict(materials=[dict(extras=dict(vmat=dict(Name=name,ShaderName='pbr.vfx',
        IntParams={'F_USE_NPR_LIGHTING':1},TextureParams={key:'textures/input.vtex' for key in source_npr_assets.PARAMETERS})))
        for name in names],meshes=[dict(primitives=[dict(material=index) for index in range(len(names))])])
    payload=json.dumps(document).encode();payload+=b' '*(-len(payload)%4)
    path.write_bytes(struct.pack('<4sIII4s',b'glTF',2,20+len(payload),len(payload),b'JSON')+payload)
    return document


@pytest.fixture
def installed(tmp_path,monkeypatch):
    from agr import compatibility, model_mesh_groups, model_material_groups, scene_extensions
    resources={};models={};sources={};calls=[]
    for name in ('a','b'):
        resource='models/'+name+'.vmdl_c';resources[resource]=object()
        path=tmp_path/(name+'.glb');write_model(path,['materials/'+name+'.vmat']);models[resource]=path
        sources['materials/'+name+'.vmat_c']=dict(m_shaderName='pbr.vfx',
            m_intParams=[dict(m_name='F_USE_NPR_LIGHTING',m_nValue=1)],
            m_textureParams=[dict(m_name=key,m_pValue='textures/input.vtex') for key in source_npr_assets.PARAMETERS])
    provider=SimpleNamespace(resources=resources,install_path=tmp_path/'game',cache_dir=tmp_path/'cache',snapshot={})
    data=struct.pack('<HH4fHHHBBIII',1,0,.1,.2,.3,0,4,4,1,20,1,0,0,0)
    metadata=struct.pack('<IHHII',28+len(data),12,1,8,1)+struct.pack('<4sII',b'DATA',8,len(data))+data
    (tmp_path/'source').write_bytes(metadata+bytes(16))
    provider.resolve=lambda name:SimpleNamespace(sha256='hash:'+name,local_path=tmp_path/'source')
    class Decoder:
        identity={};root=tmp_path/'cache';stats={'cache_hits':0,'conversions':0}
        def model(self,resource,provider):
            calls.append('model:'+resource)
            return models[resource],{}
    decoder=Decoder()
    monkeypatch.setattr(installed_assets,'GameAssetProvider',lambda *a,**kw:provider)
    monkeypatch.setattr(installed_assets,'AssetConverter',lambda *a,**kw:decoder)
    monkeypatch.setattr(installed_assets,'_cached_material_repairs',lambda model,*a,**kw:(model,{}))
    monkeypatch.setattr(model_mesh_groups,'prepare_model_mesh_groups',lambda model,*a:(model,{}))
    monkeypatch.setattr(model_material_groups,'prepare_model_material_groups',lambda *a,**kw:None)
    monkeypatch.setattr(compatibility,'installed_compatibility',lambda *a:dict(game={},
        character_lighting={'status':'selected_material_check_required'},static_map_lighting={'status':'ready'},
        hero_lighting={'status':'ready'},npr_lighting={'status':'selected_material_check_required','resource_status':'ready'}))
    monkeypatch.setattr(source_npr_assets,'dump_map_data',lambda decoder,name,provider:copy.deepcopy(sources[name]))
    verify_calls=[]
    monkeypatch.setattr(material_expressions,'verify_expression_module',lambda *a:verify_calls.append(True) or dict(
        resource='game/bin/win64/materialsystem2.dll',sha256='a'*64,semantics_sha256=material_expressions.MODULE_SHA256))
    segment=tmp_path/'segment.json'
    original=dict(schema_version=1,start_tick=10,end_tick=11,tick_rate=64,entities=[dict(id=name,hero_id=index+1,
        model_handle=str(resource_id(resource[:-2])),samples=[dict(tick=10,position=[0,0,0],body_angles=[0,0,0])])
        for index,(name,resource) in enumerate(zip(('a','b'),models))])
    segment.write_text(json.dumps(original))
    def animation(*args,**kw):
        calls.append('animation')
        assert all(entity.get('model_file') for entity in json.loads(segment.read_text())['entities'])
        assert kw['strict'] is True
        return dict(status='prepared',sampled_clip_count=1)
    monkeypatch.setattr(installed_assets,'prepare_animation_assets',animation)
    passed=[]
    def extensions(*args,**kw):
        calls.append('extensions');passed.append(kw['npr_material_plan'])
        return {}
    monkeypatch.setattr(scene_extensions,'prepare_scene_extensions',extensions)
    def prepare(npr=True,**kwargs):
        return installed_assets.prepare_installed_assets(segment,tmp_path/'work',provider.install_path,
            provider.cache_dir,tmp_path/'converter',source_npr_lighting=npr,source_hero_lighting=True,
            source_lighting=True,include_map=True,character_only=True,allow_partial_lighting=True,
            include_effects=True,log=lambda _:None,**kwargs)
    return SimpleNamespace(prepare=prepare,segment=segment,sources=sources,models=models,provider=provider,
        calls=calls,passed=passed,verify_calls=verify_calls,tmp=tmp_path,original=original)


def constant_color(source):
    source['m_dynamicParams']=[dict(m_name='g_vColorTint1',m_value=list(b'\x07'+struct.pack('<f',1.)+b'\0'))]


def test_selected_evaluator_failure_stops_before_animation_and_extensions_with_original_error(installed,monkeypatch):
    constant_color(installed.sources['materials/b.vmat_c'])
    failure=ConversionError('Material expression evaluator module requires requalification')
    failure.details={'code':'material_expression_module_unqualified','actual_sha256':'f'*64}
    def refuse(*args):raise failure
    monkeypatch.setattr(material_expressions,'verify_expression_module',refuse)
    with pytest.raises(ConversionError) as caught:installed.prepare()
    assert caught.value is failure
    assert installed.calls==['model:models/a.vmdl_c','model:models/b.vmdl_c']
    report=json.loads((installed.tmp/'work'/'installed_asset_report.json').read_text())
    assert report['status']=='failed' and report['npr_material_preflight']['status']=='failed'
    details=report['npr_material_preflight']['details']
    assert details['code']=='material_expression_module_unqualified'
    assert details['stage']=='selected_npr_material_preflight'
    assert details['npr_selection']['materials']==['materials/b.vmat']
    assert details['npr_selection']['entity_ids']==['b']
    assert len(json.dumps(details))<4096
    assert all(row.get('model_file') for row in json.loads(installed.segment.read_text())['entities'])


def test_npr_without_constant_overrides_proceeds_and_passes_one_reusable_plan(installed):
    installed.prepare()
    assert not installed.verify_calls and installed.calls[-2:]==['animation','extensions']
    assert len(installed.passed)==1 and installed.passed[0]['status']=='planned_requires_decode'
    assert len(installed.passed[0]['entities'])==2


def test_bad_selected_texture_reports_identity_before_animation(installed):
    (installed.tmp/'source').write_bytes(b'incomplete')
    with pytest.raises(ConversionError) as caught:
        installed.prepare()
    assert 'animation' not in installed.calls and 'extensions' not in installed.calls
    details=caught.value.details
    assert details['code']=='npr_texture_decode_preflight_failed'
    assert details['material']=='materials/a.vmat_c' and details['resource']=='textures/input.vtex_c'
    assert details['stage']=='selected_npr_material_preflight'
    report=json.loads((installed.tmp/'work'/'installed_asset_report.json').read_text())
    assert report['npr_material_preflight']['details']['resource']=='textures/input.vtex_c'


def test_unbound_runtime_expression_keeps_explicit_reference_without_evaluator_gate(installed):
    installed.sources['materials/a.vmat_c']['m_dynamicParams']=[dict(m_name='g_vColorTint1',m_value=[25,1,0])]
    installed.prepare()
    assert not installed.verify_calls
    material_plan=next(iter(installed.passed[0]['models'].values()))
    assert material_plan['materials'][0]['runtime']['status']=='unbound_static_reference'


def test_legacy_resource_ready_compatibility_report_remains_accepted(installed,monkeypatch):
    from agr import compatibility
    monkeypatch.setattr(compatibility,'installed_compatibility',lambda *a:dict(game={},
        character_lighting={'status':'ready'},static_map_lighting={'status':'ready'},
        hero_lighting={'status':'ready'},npr_lighting={'status':'ready'}))
    installed.prepare()
    assert installed.calls[-2:]==['animation','extensions']


def test_scene_extensions_consumes_the_saved_plan_without_replanning(installed,monkeypatch):
    from agr import source_hero_lighting, game_assets
    installed.prepare();plan=installed.passed[0];calls=[]
    monkeypatch.setattr(game_assets,'GameAssetProvider',lambda *a,**kw:installed.provider)
    monkeypatch.setattr(source_hero_lighting,'preflight_hero_lighting',lambda *a,**kw:{})
    monkeypatch.setattr(source_hero_lighting,'prepare_character_lighting_descriptor',lambda *a,**kw:{})
    monkeypatch.setattr(source_hero_lighting,'prepare_hero_lighting',lambda *a,**kw:dict(
        mode='fixture',assumptions=[],limits=[],plan={'probes':[]}))
    monkeypatch.setattr(source_npr_scene,'plan_scene_npr_materials',lambda *a,**kw:pytest.fail('Replanned selected materials'))
    monkeypatch.setattr(source_npr_scene,'decode_npr_material_assets',lambda *a:calls.append('decode') or {})
    monkeypatch.setattr(source_npr_scene,'build_npr_material_package',lambda *a,**kw:{})
    report=prepare_scene_extensions(installed.segment,installed.tmp/'work',installed.provider,
        object(),[],include_effects=False,include_map=True,source_lighting=True,source_hero_lighting=True,
        prepare_npr_materials=True,character_only=True,npr_material_plan=plan,log=lambda _:None)
    assert calls==['decode','decode']
    assert report['npr_materials']['status']=='prepared_requires_blender_validation'


@pytest.mark.parametrize('kind',['source_only','standard'])
def test_non_npr_look_does_not_plan_or_check_expression_module(installed,monkeypatch,kind):
    monkeypatch.setattr(source_npr_scene,'plan_scene_npr_materials',lambda *a,**kw:pytest.fail('NPR planning started'))
    # The installed bridge receives Source-only here; Standard also has NPR off.
    if kind=='source_only':installed.prepare(npr=False)
    else:
        installed_assets.prepare_installed_assets(installed.segment,installed.tmp/'work',installed.provider.install_path,
            installed.provider.cache_dir,installed.tmp/'converter',include_effects=True,log=lambda _:None)
    assert installed.calls[-2:]==['animation','extensions'] and not installed.verify_calls
    assert installed.passed==[None]


def test_unused_converted_material_does_not_trigger_evaluator(installed,monkeypatch):
    original=source_npr_scene.read_glb
    def document(path):
        result=original(path)
        extra=copy.deepcopy(result['materials'][0]);extra['extras']['vmat']['Name']='materials/unused.vmat'
        result['materials'].append(extra)
        return result
    monkeypatch.setattr(source_npr_scene,'read_glb',document)
    installed.sources['materials/unused.vmat_c']=copy.deepcopy(installed.sources['materials/a.vmat_c'])
    constant_color(installed.sources['materials/unused.vmat_c'])
    installed.prepare()
    assert not installed.verify_calls


def test_failure_checkpoint_cannot_replace_original_preflight_exception(installed,monkeypatch):
    failure=ConversionError('selected material refused')
    def refuse(*args,**kw):raise failure
    monkeypatch.setattr(source_npr_scene,'plan_scene_npr_materials',refuse)
    original=installed_assets._write
    def write(path,data):
        if Path(path).name=='installed_asset_report.json' and data.get('status')=='failed':
            raise OSError('checkpoint disk fixture')
        original(path,data)
    monkeypatch.setattr(installed_assets,'_write',write)
    with pytest.raises(ConversionError) as caught:installed.prepare()
    assert caught.value is failure and 'checkpoint disk fixture' in failure.__notes__[0]
    assert 'animation' not in installed.calls


@pytest.mark.parametrize('mutation',['track','bundle'])
def test_reused_plan_rechecks_recorded_material_group_and_variant_bundle(tmp_path,monkeypatch,mutation):
    from agr import model_material_groups as groups
    from agr.source_material_groups import material_group_token
    base=tmp_path/'base.glb';write_model(base,['materials/base.vmat'])
    variant=tmp_path/'variant.glb';write_model(variant,['materials/alt.vmat'])
    authored=[dict(m_name='base',m_materials=['materials/base.vmat']),
              dict(m_name='alt',m_materials=['materials/alt.vmat'])]
    monkeypatch.setattr(groups,'dump_map_data',lambda *args:dict(m_materialGroups=copy.deepcopy(authored)))
    monkeypatch.setattr(groups,'_bundle',lambda *args,**kw:dict(path=str(variant),sha256=sha(variant),materials=['materials/alt.vmat_c']))
    provider=SimpleNamespace(resolve=lambda name:SimpleNamespace(sha256='hash:'+name))
    samples=[dict(tick=10,network_render_state=dict(material_group=material_group_token('alt')))]
    entity=dict(id='hero',model_file=str(base),samples=samples)
    entity['model_material_groups']=groups.prepare_model_material_groups(base,'models/hero.vmdl_c',entity,provider,None)
    seen=[]
    def plan(document,*args):
        names=groups.used_materials(document);seen.append(set(names))
        return dict(materials=[dict(material=name,source_sha256='hash:'+name) for name in names])
    monkeypatch.setattr(source_npr_scene,'plan_npr_material_assets',plan)
    monkeypatch.setattr(source_npr_scene,'decode_npr_material_assets',lambda *args:pytest.fail('Texture decode started'))
    segment=dict(entities=[entity])
    planned=source_npr_scene.plan_scene_npr_materials(segment,provider,None,manifest_dir=tmp_path,allow_partial=True)
    assert seen==[{'materials/base.vmat_c','materials/alt.vmat_c'}]
    if mutation=='track':entity['samples'][0]['network_render_state']['material_group']=0
    else:variant.write_bytes(b'changed bundle')
    with pytest.raises(ValueError,match='selection changed|bundle identity changed'):
        source_npr_scene.prepare_scene_npr_materials(segment,provider,None,manifest_dir=tmp_path,allow_partial=True,plan=planned)
