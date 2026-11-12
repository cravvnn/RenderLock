import copy,json
from types import SimpleNamespace
import pytest
import agr.particle_model_rest_assets as module


@pytest.fixture
def case(tmp_path,monkeypatch):
    model=dict(resource='models/actor.vmdl_c',resource_sha256='a'*64,names=['root','crossbow'],parents=[-1,0])
    inventory=dict(schema_version=1,policy='agr_authored_model_rest_v1',model='models/actor.vmdl',source_sha256='a'*64,
        names=model['names'],parents=model['parents'],parent_transforms=[[0,0,0,1,0,0,0,1],[2,0,0,1,0,0,0,1]])
    probe=tmp_path/'ParticleGeometryProbe.dll';probe.write_bytes(b'helper')
    (tmp_path/'ValveResourceFormat.dll').write_bytes(b'parser');dotnet=tmp_path/'dotnet.exe';dotnet.write_bytes(b'host')
    provider=SimpleNamespace(install_path=tmp_path,resolve=lambda resource:SimpleNamespace(sha256='a'*64))
    monkeypatch.setattr(module,'geometry_runtime',lambda runtime:(probe,dotnet))
    calls=[]
    def run(command,**kwargs):
        calls.append(command);(tmp_path/'result.json').write_text(json.dumps(inventory))
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(module.subprocess,'run',run)
    return model,inventory,probe,provider,calls


def test_fresh_inventory_binds_source_order_runtime_and_exact_output_bytes(case,tmp_path):
    model,inventory,probe,provider,calls=case
    actual,proof=module.collect_model_rest(provider,{},model,tmp_path/'result.json')
    assert actual==inventory and proof['resource']==model['resource'] and len(proof['inventory_sha256'])==64
    assert calls[0][2:] == ['--inventory-model-rest',str(tmp_path/'game/citadel/gameinfo.gi'),'models/actor.vmdl',str(tmp_path/'result.json')]


@pytest.mark.parametrize('directory',[False,True])
def test_existing_output_never_becomes_a_cached_success_or_helper_target(case,tmp_path,directory):
    model,_,_,provider,calls=case;output=tmp_path/'result.json'
    if directory:output.mkdir()
    else:output.write_bytes(b'prior')
    with pytest.raises(ValueError,match='fresh'):module.collect_model_rest(provider,{},model,output)
    assert not calls
    if not directory:assert output.read_bytes()==b'prior'


@pytest.mark.parametrize('change',[
    lambda x:x.update(schema_version=True),lambda x:x.update(source_sha256='b'*64),
    lambda x:x.update(names=['crossbow','root']),lambda x:x.update(parents=[-1,-1]),
    lambda x:x.update(parent_transforms=[]),lambda x:x['parent_transforms'][0].__setitem__(3,0),
])
def test_incorrect_typed_metadata_cannot_bind_to_a_matching_model_label(case,tmp_path,change):
    model,inventory,_,provider,_=case;change(inventory)
    with pytest.raises(ValueError):module.collect_model_rest(provider,{},model,tmp_path/'result.json')


def test_failed_helper_cannot_supply_a_valid_looking_partial_result(case,tmp_path,monkeypatch):
    model,inventory,_,provider,_=case
    def fail(*args,**kwargs):
        (tmp_path/'result.json').write_text(json.dumps(inventory))
        return SimpleNamespace(returncode=23,stderr='incomplete',stdout='')
    monkeypatch.setattr(module.subprocess,'run',fail)
    with pytest.raises(ValueError,match='helper failed'):module.collect_model_rest(provider,{},model,tmp_path/'result.json')


def test_changed_helper_after_a_successful_parse_refuses_the_inventory(case,tmp_path,monkeypatch):
    model,inventory,probe,provider,_=case
    def change(*args,**kwargs):
        (tmp_path/'result.json').write_text(json.dumps(inventory));probe.write_bytes(b'changed helper')
        return SimpleNamespace(returncode=0,stderr='',stdout='')
    monkeypatch.setattr(module.subprocess,'run',change)
    with pytest.raises(ValueError,match='runtime changed'):module.collect_model_rest(provider,{},model,tmp_path/'result.json')
