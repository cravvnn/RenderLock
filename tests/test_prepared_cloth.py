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
    assert load(tmp_path,'other.vmdl_c',digest,physics) is None
    assert load(tmp_path,'model.vmdl_c',digest,{'changed':True}) is None
    record=json.loads(path.read_text());record['definition']['scope']='tampered';path.write_text(json.dumps(record))
    with pytest.raises(ValueError):load(tmp_path,'model.vmdl_c',digest,physics)


def test_numeric_transport_migration_preserves_legacy_cache_and_rebuilds(tmp_path):
    legacy=tmp_path/'prepared-cloth-v1'/('a'*64+'.json')
    legacy.parent.mkdir();legacy.write_text('legacy rounded definitions remain untouched')
    assert load(tmp_path,'model.vmdl_c','a'*64,{'m_pFeModel':None}) is None
    assert legacy.read_text()=='legacy rounded definitions remain untouched'


def test_identical_model_bytes_at_distinct_resource_names_keep_independent_entries(tmp_path):
    physics={'m_pFeModel':None}
    definition=dict(schema='agr_decompiled_fe_proxy_v1',physics=physics,
                    source_sha256={'proxy':'fixture'},scope='fixture only')
    first=register(tmp_path,'first.vmdl_c','a'*64,definition,physics)
    second=register(tmp_path,'second.vmdl_c','a'*64,definition,physics)
    assert first!=second and load(tmp_path,'first.vmdl_c','a'*64,physics)==definition
    assert load(tmp_path,'second.vmdl_c','a'*64,physics)==definition


def test_concurrent_register_publishes_complete_records_without_shared_partial_file(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from threading import Barrier,Lock
    physics={'m_pFeModel':None}
    definitions=[dict(schema='agr_decompiled_fe_proxy_v1',physics=physics,
        source_sha256={'proxy':'fixture'},scope=scope) for scope in ('first','second')]
    barrier=Barrier(2);replace=Path.replace;seen=set();lock=Lock()
    def concurrent_replace(source,target):
        with lock:
            first=source not in seen;seen.add(source)
        if source.suffix=='.part' and first:barrier.wait(timeout=5)
        return replace(source,target)
    monkeypatch.setattr(Path,'replace',concurrent_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(register,tmp_path,'model.vmdl_c','a'*64,value,physics) for value in definitions]
        paths=[future.result(timeout=10) for future in futures]
    assert paths[0]==paths[1] and load(tmp_path,'model.vmdl_c','a'*64,physics) in definitions
    assert not list(tmp_path.rglob('*.part'))


def test_registration_refuses_mismatched_physics_and_unsafe_key(tmp_path):
    definition={'schema':'agr_decompiled_fe_proxy_v1','physics':{},'source_sha256':{'x':'y'},'scope':'fixture'}
    with pytest.raises(ValueError):register(tmp_path,'model','a'*64,definition,{'different':1})
    with pytest.raises(ValueError):register(tmp_path,'model','../bad',definition,{})


def test_automatic_prepared_cache_regenerates_after_solver_or_parser_identity_changes(tmp_path):
    from pathlib import Path
    from agr.asset_converter import sha
    from agr import reconstructed_cloth
    module=Path(reconstructed_cloth.__file__)
    extractor=dict(adapter=sha(module),parser=sha(module.parent/'vendor/datamodel.py'),
                   cloth_modules=reconstructed_cloth.cloth_module_hashes())
    definition=dict(schema='agr_decompiled_fe_proxy_v1',physics={},source_sha256={'proxy':'fixture'},
                    scope='Automatic fixture',extractor=extractor)
    register(tmp_path,'model','a'*64,definition,{})
    assert load(tmp_path,'model','a'*64,{})==definition
    extractor['cloth_modules']['fe_rod_constraints']='old implementation'
    register(tmp_path,'model','a'*64,definition,{})
    assert load(tmp_path,'model','a'*64,{}) is None
    del extractor['cloth_modules']
    register(tmp_path,'model','a'*64,definition,{})
    assert load(tmp_path,'model','a'*64,{}) is None


@pytest.mark.parametrize('parts', [[], [None], [{'schema': 'agr_decompiled_fe_proxy_v1',
                                               'physics': {'wrong': True}, 'mesh_suffix': '.body'}]])
def test_bundle_cannot_report_empty_or_mismatched_garments_as_prepared(tmp_path, parts):
    definition = dict(schema='agr_decompiled_fe_proxy_bundle_v1', physics={}, parts=parts,
                      source_sha256={'proxy': 'fixture'}, scope='fixture')
    with pytest.raises(ValueError, match='garment bundle'):
        register(tmp_path, 'model.vmdl_c', 'a' * 64, definition, {})


def test_imported_node_cache_retains_physics_and_rejects_unimplemented_constraints(tmp_path):
    physics={'m_pFeModel':dict(m_nNodeCount=2,m_nStaticNodes=1,m_CtrlName=['anchor','cloth'],
        m_InitPose=[[0,0,0,1,0,0,0,1],[1,0,0,1,0,0,0,1]],m_NodeInvMasses=[0,1],m_Rods=[],
        m_NodeIntegrator=[dict(flPointDamping=0.,flAnimationForceAttraction=0.,
            flAnimationVertexAttraction=0.,flGravity=0.) for _ in range(2)])}
    definition=dict(schema='agr_imported_fe_nodes_v1',physics=physics,
        source_sha256={'model.vmdl':'verified'},scope='Imported-bone approximation')
    register(tmp_path,'model.vmdl_c','c'*64,definition,physics)
    assert load(tmp_path,'model.vmdl_c','c'*64,physics)==definition
    physics['m_pFeModel']['m_HingeLimits']=[{}]
    with pytest.raises(ValueError,match='hinge'):
        register(tmp_path,'model.vmdl_c','d'*64,definition,physics)
