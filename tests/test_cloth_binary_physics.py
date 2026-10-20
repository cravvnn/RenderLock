from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from agr import installed_cloth
from agr.asset_converter import AssetConverter,ConversionError,sha


def harness(tmp_path,monkeypatch,encode):
    tool=tmp_path/'probe.dll';tool.write_bytes(b'binary PHYS fixture')
    sources={'models/test.vmdl_c':tmp_path/'test.vmdl_c','models/test.vphys_c':tmp_path/'test.vphys_c'}
    for path in sources.values():path.write_bytes(path.name.encode())
    provider=SimpleNamespace(resolve=lambda name:SimpleNamespace(local_path=sources[name],sha256=sha(sources[name])))
    decoder=AssetConverter.__new__(AssetConverter);decoder.root=tmp_path/'derived';decoder.root.mkdir()
    decoder.identity={'fixture':'converter-cache'};decoder.stats={'cache_hits':0,'conversions':0}
    calls=[]
    def native(command,**kwargs):
        assert command[1]=='--physics-json-v1' and len(command)==5
        source=Path(command[2]);assert source.is_absolute() and command[3]==sha(source)
        calls.append(source.name)
        envelope=dict(schema_version=1,resource_sha256=command[3],numeric_encoding='binary_kv3_roundtrip_v1',
                      status='embedded',referenced_physics=[],physics={'m_pFeModel':{'small':1.2e-7,'identity':2**64-1}})
        encode(envelope,source)
        Path(command[-1]).write_text(json.dumps(envelope),encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(installed_cloth.subprocess,'run',native)
    return provider,decoder,[str(tool)],sources,calls


def test_binary_numeric_transport_preserves_small_coefficients_and_large_integer_ids(tmp_path,monkeypatch):
    provider,decoder,command,sources,calls=harness(tmp_path,monkeypatch,lambda *_:None)
    data,digest=installed_cloth.decode_physics('models/test.vmdl',provider,decoder,command)
    assert data['m_pFeModel']['small']==1.2e-7
    assert data['m_pFeModel']['identity']==2**64-1 and digest==sha(sources['models/test.vmdl_c'])
    assert installed_cloth.decode_physics('models/test.vmdl',provider,decoder,command)[0]==data
    assert calls==['test.vmdl_c'] and decoder.stats['cache_hits']==1


def test_referenced_physics_change_invalidates_its_own_data_with_unchanged_model(tmp_path,monkeypatch):
    def encode(value,source):
        if source.suffix=='.vmdl_c':
            value.update(status='referenced',referenced_physics=['models/test.vphys'],physics=None)
        else:value.update(status='direct',physics={'m_pFeModel':{'small':1.2e-7 if source.read_bytes()==b'old' else 4.9e-7}})
    provider,decoder,command,sources,calls=harness(tmp_path,monkeypatch,encode)
    external=sources['models/test.vphys_c'];external.write_bytes(b'old')
    first,model_digest=installed_cloth.decode_physics('models/test.vmdl',provider,decoder,command)
    external.write_bytes(b'new')
    second,new_model_digest=installed_cloth.decode_physics('models/test.vmdl',provider,decoder,command)
    assert model_digest==new_model_digest and first!=second
    assert first['m_pFeModel']['small']==1.2e-7 and second['m_pFeModel']['small']==4.9e-7
    assert calls==['test.vmdl_c','test.vphys_c','test.vphys_c']


@pytest.mark.parametrize('change',[
    {'resource_sha256':'0'*64},{'schema_version':True},{'numeric_encoding':'display_text'},
    {'status':'not_applicable'},{'physics':{'m_pFeModel':[]}},
    {'status':'referenced','referenced_physics':['../bad.vphys'],'physics':None},
    {'physics':{'m_pFeModel':{'bad':float('nan')}}}])
def test_changed_identity_unsafe_reference_and_nonfinite_payloads_fail(tmp_path,monkeypatch,change):
    provider,decoder,command,_,_=harness(tmp_path,monkeypatch,lambda value,source:value.update(change))
    with pytest.raises(ConversionError):installed_cloth.decode_physics('models/test.vmdl',provider,decoder,command)


def test_absent_fe_is_explicit_and_does_not_infer_a_cloth_model(tmp_path,monkeypatch):
    provider,decoder,command,_,_=harness(tmp_path,monkeypatch,lambda value,source:value.update(
        status='not_applicable',physics={'m_pFeModel':None}))
    assert installed_cloth.decode_physics('models/test.vmdl',provider,decoder,command)[0]=={'m_pFeModel':None}


def test_json_exponent_overflow_is_a_malformed_transport_failure(tmp_path,monkeypatch):
    provider,decoder,command,_,_=harness(tmp_path,monkeypatch,lambda *_:None)
    native=installed_cloth.subprocess.run
    def malformed(command,**kwargs):
        result=native(command,**kwargs)
        path=Path(command[-1]);payload=path.read_text(encoding='utf-8')
        assert '1.2e-07' in payload
        path.write_text(payload.replace('1.2e-07','1e999'),encoding='utf-8')
        return result
    monkeypatch.setattr(installed_cloth.subprocess,'run',malformed)
    with pytest.raises(ConversionError,match='malformed'):
        installed_cloth.decode_physics('models/test.vmdl',provider,decoder,command)


def test_small_binary_inverse_mass_reaches_motion_solver_as_dynamic(tmp_path,monkeypatch):
    import numpy as np
    from agr.fe_bone_motion import simulate_node_motion
    physics={'m_pFeModel':dict(m_nNodeCount=2,m_nStaticNodes=1,m_CtrlName=['anchor','cloth'],
        m_InitPose=[[0,0,0,1,0,0,0,1],[1,0,0,1,0,0,0,1]],m_NodeInvMasses=[0,1.2e-7],m_Rods=[],
        m_NodeIntegrator=[dict(flPointDamping=0.,flAnimationForceAttraction=0.,
            flAnimationVertexAttraction=0.,flGravity=10.) for _ in range(2)])}
    provider,decoder,command,_,_=harness(tmp_path,monkeypatch,lambda value,source:value.update(physics=physics))
    decoded,_=installed_cloth.decode_physics('models/test.vmdl',provider,decoder,command)
    goals=np.broadcast_to(np.eye(4),(3,2,4,4)).copy();goals[:,1,0,3]=1
    positions,_=simulate_node_motion(decoded,goals,64,1.)
    np.testing.assert_array_equal(positions[:,0],goals[:,0,:3,3])
    assert positions[-1,1,2]<0
