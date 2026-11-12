"""Prepared input admission only; synthetic definitions/frames do not render."""
import copy
import hashlib
import json

import pytest

from agr import particle_silence_inputs as inputs
from agr.particle_silence_capture import bind_silence_capture, request_header
from agr.particle_client_capture import bind_client_child_capture
from agr.simulated_effect_package import scene_identity,model_constraints_identity,validate_package
from test_particle_silence_capture import source,response


@pytest.fixture
def package(source,monkeypatch):
    raw,scene,instance,child=source
    birth=bind_silence_capture(raw,scene,instance);child=bind_client_child_capture(child,scene,instance)
    definitions={name:{} for name in inputs.DEFINITIONS}
    monkeypatch.setattr(inputs,'DEFINITIONS',{name:inputs.digest(value) for name,value in definitions.items()})
    model=scene['entities'][0]['model_constraints']
    bone_hash=hashlib.sha256(json.dumps(model['names'],separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
    h=dict(control_point=0,entity_id=instance['entity_id'],model=model['resource'],model_sha256=model['resource_sha256'],bone_order_sha256=bone_hash)
    h['binding_id']=hashlib.sha256('\n'.join(str(h[k])for k in ('control_point','entity_id','model','model_sha256','bone_order_sha256')).encode()).hexdigest()
    row=response(instance,request_header(birth,scene,instance))
    row.update(resource=inputs.SILENCE,entity_id=instance['entity_id'],birth=instance['created_tick'],
        frames=[dict(tick=t,systems=[])for t in range(10,13)],snapshot_model_input_policy=None,snapshot_model_bindings=None,
        model_hitbox_input_policy=inputs.HITBOX_POLICY,model_hitbox_bindings=[h])
    sim=dict(schema_version=1,backend='input_fixture',start_tick=10,end_tick=12,tick_rate=64,warnings=[],instances=[row])
    provider=dict(policy=inputs.HITBOX_POLICY,profile=inputs.PROFILE,definition_sha256=inputs.DEFINITIONS,
        entity_id=instance['entity_id'],resource=model['resource'],resource_sha256=model['resource_sha256'],
        model_constraints_sha256=model_constraints_identity(model),start_tick=10,end_tick=12,bindings=[h])
    value=dict(schema_version=1,backend='simulated_states_v1',entity_ids=[instance['entity_id']],
        scene_identity=scene_identity(scene,[instance['entity_id']]),simulation=sim,assets=dict(definitions=definitions),
        coverage_scope='Synthetic complete input contract only; no rendering',silence_birth_capture=birth,client_child_capture=child,hitbox_provider=provider)
    return value,scene


def test_prepared_boundary_requires_complete_silence_input_not_crossbow_null_snapshot(package):
    value,scene=package;validate_package(value,scene)
    value['simulation']['instances'][0]['snapshot_model_input_policy']='unqualified'
    with pytest.raises(ValueError,match='cannot be combined'):validate_package(value,scene)


@pytest.mark.parametrize('key',['silence_birth_capture','client_child_capture','hitbox_provider'])
def test_no_input_packet_can_be_dropped_even_with_complete_simulated_frames(package,key):
    value,scene=package;value.pop(key)
    with pytest.raises(ValueError):validate_package(value,scene)


@pytest.mark.parametrize('change',[
    lambda p:p['simulation']['instances'][0].update(silence_birth_provider=None),
    lambda p:p['simulation']['instances'][0].update(authored_root_position_indices=[3]),
    lambda p:p['simulation']['instances'][0].update(unprovided_control_point_indices=[4]),
    lambda p:p['simulation']['instances'][0].update(declared_control_point_inputs=[0,True,2]),
    lambda p:p['simulation']['instances'][0]['model_hitbox_bindings'][0].update(control_point=False),
    lambda p:p['simulation']['instances'][0].update(model_hitbox_input_policy='unknown'),
    lambda p:p['hitbox_provider'].update(start_tick=True),
    lambda p:p['hitbox_provider'].update(model_constraints_sha256='0'*64),
    lambda p:p['hitbox_provider']['bindings'][0].update(bone_order_sha256='0'*64),
    lambda p:p['assets']['definitions'].pop(next(iter(p['assets']['definitions']))),
    lambda p:p.update(snapshot_model_provider=None),
    lambda p:p['simulation']['instances'][0].update(crossbow_owner_provider={}),
])
def test_missing_changed_or_cross_profile_inputs_refuse(package,change):
    value,scene=package;change(value)
    with pytest.raises(ValueError):validate_package(value,scene)


def test_non_silence_null_snapshot_retains_existing_crossbow_refusal(package):
    value,scene=package
    for key in ('silence_birth_capture','client_child_capture','hitbox_provider'):value.pop(key)
    row=value['simulation']['instances'][0];row.pop('silence_birth_provider');row['resource']='particles/other.vpcf'
    with pytest.raises(ValueError,match='Crossbow instance'):validate_package(value,scene)
