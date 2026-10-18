import json
import pytest
from agr.prepared_cloth import register, load


def test_version_binding_and_corruption(tmp_path):
    physics={'m_pFeModel':{'m_nNodeCount':3}}
    definition={'schema':'agr_decompiled_fe_proxy_v1','physics':physics,
                'source_sha256':{'proxy':'fixture'},'scope':'fixture only'}
    digest='a'*64
    path=register(tmp_path,'model.vmdl_c',digest,definition,physics)
    assert load(tmp_path,'model.vmdl_c',digest,physics)==definition
    assert load(tmp_path,'model.vmdl_c','b'*64,physics) is None
    with pytest.raises(ValueError):load(tmp_path,'other.vmdl_c',digest,physics)
    with pytest.raises(ValueError):load(tmp_path,'model.vmdl_c',digest,{'changed':True})
    record=json.loads(path.read_text());record['definition']['scope']='tampered';path.write_text(json.dumps(record))
    with pytest.raises(ValueError):load(tmp_path,'model.vmdl_c',digest,physics)


def test_registration_refuses_mismatched_physics_and_unsafe_key(tmp_path):
    definition={'schema':'agr_decompiled_fe_proxy_v1','physics':{},'source_sha256':{'x':'y'},'scope':'fixture'}
    with pytest.raises(ValueError):register(tmp_path,'model','a'*64,definition,{'different':1})
    with pytest.raises(ValueError):register(tmp_path,'model','../bad',definition,{})


@pytest.mark.parametrize('parts', [[], [None], [{'schema': 'agr_decompiled_fe_proxy_v1',
                                               'physics': {'wrong': True}, 'mesh_suffix': '.body'}]])
def test_bundle_cannot_report_empty_or_mismatched_garments_as_prepared(tmp_path, parts):
    definition = dict(schema='agr_decompiled_fe_proxy_bundle_v1', physics={}, parts=parts,
                      source_sha256={'proxy': 'fixture'}, scope='fixture')
    with pytest.raises(ValueError, match='garment bundle'):
        register(tmp_path, 'model.vmdl_c', 'a' * 64, definition, {})
