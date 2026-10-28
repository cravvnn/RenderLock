import copy
from types import SimpleNamespace
import pytest
from agr import source_npr_assets as assets
from agr.asset_converter import ConversionError


def setup(monkeypatch):
    textures={k:'textures/'+str(i)+'.vtex' for i,k in enumerate(assets.PARAMETERS)}
    vmat=dict(Name='models/a.vmat',ShaderName='pbr.vfx',IntParams={'F_USE_NPR_LIGHTING':1},TextureParams=textures)
    document=dict(materials=[dict(extras=dict(vmat=vmat))],meshes=[dict(primitives=[dict(material=0),dict(material=0)])])
    source=dict(m_shaderName='pbr.vfx',m_intParams=[dict(m_name='F_USE_NPR_LIGHTING',m_nValue=1)],m_textureParams=[dict(m_name=k,m_pValue=v) for k,v in textures.items()])
    monkeypatch.setattr(assets,'dump_map_data',lambda *args:source)
    monkeypatch.setattr(assets,'validate_npr_decode_source',lambda path:{'fixture':True})
    provider=SimpleNamespace(resolve=lambda resource:SimpleNamespace(sha256='hash:'+resource,local_path=resource))
    return document,source,provider


def test_source_plan_accounts_for_used_material_once(monkeypatch):
    doc,source,provider=setup(monkeypatch)
    result=assets.plan_npr_material_assets(doc,provider,None)
    assert result['status']=='sources_resolved_not_decoded'
    assert len(result['materials'])==1
    assert result['materials'][0]['textures']['g_tNprTransmissiveColor']['resource']=='textures/0.vtex_c'


def test_source_plan_reports_bad_texture_before_animation_or_decoding(monkeypatch):
    doc,source,provider=setup(monkeypatch)
    def reject(path):
        raise ConversionError('Extended NPR BC5 source requires aligned, uncompressed mip data after DATA')
    monkeypatch.setattr(assets,'validate_npr_decode_source',reject)
    with pytest.raises(ConversionError) as caught:
        assets.plan_npr_material_assets(doc,provider,None)
    assert caught.value.details==dict(code='npr_texture_decode_preflight_failed',
        material='models/a.vmat_c',parameter='g_tNprTransmissiveColor',resource='textures/0.vtex_c')


def test_generic_pbr_does_not_fabricate_npr_inputs_and_records_dynamic_gap(monkeypatch):
    doc,source,provider=setup(monkeypatch)
    source['m_intParams']=[dict(m_name='F_SELF_ILLUM',m_nValue=1)]
    source['m_textureParams']=[]
    source['m_dynamicParams']=[dict(m_name='g_flSelfIllumScale1',m_value=[25,1,0])]
    doc['materials'][0]['extras']['vmat'].update(IntParams={'F_SELF_ILLUM':1},TextureParams={})
    row=assets.plan_npr_material_assets(doc,provider,None)['materials'][0]
    assert row['route']=='generic_pbr_indirect' and row['textures']=={}
    assert row['runtime']==dict(status='unbound_static_reference',dynamic_fields=['m_dynamicParams'])
    assert {gap['feature'] for gap in row['fidelity_gaps']}=={'F_SELF_ILLUM','authored_dynamic_inputs'}


def test_converted_feature_switch_cannot_differ_from_source(monkeypatch):
    doc,source,provider=setup(monkeypatch)
    source['m_intParams'].append(dict(m_name='F_NO_SPECULAR_AT_FULL_ROUGHNESS',m_nValue=1))
    with pytest.raises(ConversionError,match='feature metadata differs'):
        assets.plan_npr_material_assets(doc,provider,None)


@pytest.mark.parametrize('failure',['index','duplicate','shader','texture'])
def test_unqualified_material_rejected(monkeypatch,failure):
    doc,source,provider=setup(monkeypatch)
    if failure=='index':doc['meshes'][0]['primitives'][0]['material']=-1
    if failure=='duplicate':
        doc['materials'].append(copy.deepcopy(doc['materials'][0]));doc['meshes'][0]['primitives'][1]['material']=1
    if failure=='shader':source['m_shaderName']='other.vfx'
    if failure=='texture':doc['materials'][0]['extras']['vmat']['TextureParams']['g_tNprTransmissiveColor']='wrong.vtex'
    with pytest.raises(ConversionError):assets.plan_npr_material_assets(doc,provider,None)


def test_evaluator_required_only_for_selected_constant_color_overrides(monkeypatch):
    from agr import material_expressions as expressions
    from test_material_expressions import vector
    doc,source,provider=setup(monkeypatch)
    source['m_dynamicParams']=[dict(m_name='g_vColorTint1',m_value=list(vector(1,1,1)))]
    calls=[]
    identity=dict(resource=expressions.MODULE_RESOURCE,sha256=expressions.MODULE_SHA256,semantics_sha256=expressions.MODULE_SHA256)
    monkeypatch.setattr(expressions,'verify_expression_module',lambda p:calls.append(p) or identity)
    row=assets.plan_npr_material_assets(doc,provider,None)['materials'][0]
    assert calls==[provider] and row['expression_module']==identity
    source['m_dynamicParams']=[dict(m_name='g_vColorTint1',m_value=[25,1,0])]
    calls.clear()
    row=assets.plan_npr_material_assets(doc,provider,None)['materials'][0]
    assert calls==[] and 'expression_module' not in row


def test_evaluator_refusal_identifies_triggering_material(monkeypatch):
    from agr import material_expressions as expressions
    from test_material_expressions import vector
    doc,source,provider=setup(monkeypatch)
    source['m_dynamicParams']=[dict(m_name='g_vColorTint1',m_value=list(vector(1,1,1)))]
    def refuse(p):
        error=ConversionError('Material expression evaluator module requires requalification')
        error.details=dict(code='material_expression_evaluator_unqualified',sha256='f'*64)
        raise error
    monkeypatch.setattr(expressions,'verify_expression_module',refuse)
    with pytest.raises(ConversionError) as error:assets.plan_npr_material_assets(doc,provider,None)
    assert error.value.details['material']=='models/a.vmat_c'
    assert error.value.details['material_index']==0
    assert error.value.details['sha256']=='f'*64
