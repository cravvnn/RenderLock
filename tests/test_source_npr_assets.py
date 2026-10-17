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
    provider=SimpleNamespace(resolve=lambda resource:SimpleNamespace(sha256='hash:'+resource,local_path=resource))
    return document,source,provider


def test_source_plan_accounts_for_used_material_once(monkeypatch):
    doc,source,provider=setup(monkeypatch)
    result=assets.plan_npr_material_assets(doc,provider,None)
    assert result['status']=='sources_resolved_not_decoded'
    assert len(result['materials'])==1
    assert result['materials'][0]['textures']['g_tNprTransmissiveColor']['resource']=='textures/0.vtex_c'


@pytest.mark.parametrize('failure',['index','duplicate','shader','texture'])
def test_unqualified_material_rejected(monkeypatch,failure):
    doc,source,provider=setup(monkeypatch)
    if failure=='index':doc['meshes'][0]['primitives'][0]['material']=-1
    if failure=='duplicate':
        doc['materials'].append(copy.deepcopy(doc['materials'][0]));doc['meshes'][0]['primitives'][1]['material']=1
    if failure=='shader':source['m_shaderName']='other.vfx'
    if failure=='texture':doc['materials'][0]['extras']['vmat']['TextureParams']['g_tNprTransmissiveColor']='wrong.vtex'
    with pytest.raises(ConversionError):assets.plan_npr_material_assets(doc,provider,None)
