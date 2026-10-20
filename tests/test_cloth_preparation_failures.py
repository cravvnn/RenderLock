from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import sys

import pytest

from agr import installed_cloth,reconstructed_cloth
from agr.cli import validate_cloth_preparation
from agr.asset_converter import ConversionError
from agr.coverage_diagnostics import merge_secondary_motion_coverage,scene_coverage_warnings,scene_completion_message


def prepare(tmp_path,monkeypatch,exception):
    def decode(*args):raise exception
    monkeypatch.setattr(installed_cloth,'decode_physics',decode)
    segment={'entities':[{'id':'8:1','hero_name':'McGinnis','secondary_motion_file':'stale.json'}]}
    pack=tmp_path/'pack'
    report=installed_cloth.prepare_cloth_assets(segment,[{'id':'8:1','resource':'models/test.vmdl_c'}],
        SimpleNamespace(cache_dir=tmp_path/'cache'),None,pack,log=lambda _:None)
    assert 'secondary_motion_file' not in segment['entities'][0]
    assert json.loads((pack/'secondary-motion/report.json').read_text())==report
    return report


@pytest.mark.parametrize('error',[
    ConversionError('PHYS decoder process failed'),OSError('Cache write denied'),
    subprocess.TimeoutExpired('extractor',180),KeyError('files'),TypeError('Malformed native report'),
    json.JSONDecodeError('Invalid native JSON','',0)])
def test_operational_preparation_failure_is_retained_as_failed(tmp_path,monkeypatch,error):
    report=prepare(tmp_path,monkeypatch,error)
    assert report['status']=='failed'
    assert report['unsupported_entities']==[] and report['supported_entities']==[]
    assert report['failed_entities'][0]['error_type']==type(error).__name__
    result=merge_secondary_motion_coverage(report,[])
    assert result['status']=='failed' and result['failed_entity_count']==1
    assert result['unsupported_entity_count']==result['baked_entity_count']==0
    assert 'Cloth preparation failed: McGinnis [8:1]' in scene_coverage_warnings({'secondary_motion':result})[0]
    assert 'cloth failed for McGinnis [8:1]' in scene_completion_message({'secondary_motion':result})


def test_declared_unsupported_layout_retains_separate_scope(tmp_path,monkeypatch):
    report=prepare(tmp_path,monkeypatch,installed_cloth.UnsupportedCloth('Unqualified hinge layout'))
    assert report['status']=='no_supported_entities' and report['failed_entities']==[]
    assert report['unsupported_entities'][0]['reason']=='Unqualified hinge layout'


def test_preparation_failure_cannot_be_erased_by_a_later_claimed_bake(tmp_path,monkeypatch):
    report=prepare(tmp_path,monkeypatch,ConversionError('Missing extractor'))
    report['supported_entities']=[dict(id='8:1',hero_name='McGinnis',status='ready_to_bake')]
    result=merge_secondary_motion_coverage(report,[dict(id='8:1',secondary_motion=dict(status='baked',dynamic_control_count=10))])
    assert result['status']=='failed' and result['failed_entity_count']==1
    assert result['baked_entity_count']==result['dynamic_control_count']==0


def test_missing_extractor_is_operational_failure(tmp_path,monkeypatch):
    physics={'m_pFeModel':{'m_nNodeCount':3}}
    monkeypatch.setattr(installed_cloth,'decode_physics',lambda *args:(physics,'a'*64))
    report=installed_cloth.prepare_cloth_assets({'entities':[dict(id='8:1')]},
        [dict(id='8:1',resource='models/test.vmdl_c')],
        SimpleNamespace(cache_dir=tmp_path/'cache',resolve=lambda _:SimpleNamespace(sha256='a'*64)),
        None,tmp_path/'pack',log=lambda _:None,extractor_command=[str(tmp_path/'missing.dll')])
    assert report['status']=='failed' and report['unsupported_entities']==[]
    assert report['failed_entities'][0]['error_type']=='ConversionError'
    assert 'extractor is unavailable' in report['failed_entities'][0]['error']


def test_real_reconstruction_nonzero_helper_exit_is_not_unsupported(tmp_path,monkeypatch):
    tool=tmp_path/'probe.dll';tool.write_bytes(b'fixture')
    provider=SimpleNamespace(install_path=tmp_path,resolve=lambda _:SimpleNamespace(sha256='a'*64))
    class Decoder:
        def _entry(self,operation,inputs,settings,build):
            output=tmp_path/'work';output.mkdir();build(output);return output
    monkeypatch.setattr(reconstructed_cloth.subprocess,'run',lambda *a,**k:
        SimpleNamespace(returncode=1,stderr='Required runtime dependency missing'))
    with pytest.raises(ConversionError,match='Required runtime dependency missing'):
        reconstructed_cloth.reconstruct('models/test.vmdl_c',provider,Decoder(),{},command=[str(tool)])


def test_actual_helper_process_failure_retains_cause_and_never_attaches_a_bake(tmp_path,monkeypatch):
    worker=tmp_path/'unavailable_runtime.py'
    worker.write_text("import sys\nprint('Required runtime fixture is unavailable',file=sys.stderr)\nsys.exit(19)\n")
    physics={'m_pFeModel':{'m_nNodeCount':3}}
    monkeypatch.setattr(installed_cloth,'decode_physics',lambda *a:(physics,'a'*64))
    class Decoder:
        def _entry(self,operation,inputs,settings,build):
            output=tmp_path/'work';output.mkdir();build(output);return output
    provider=SimpleNamespace(cache_dir=tmp_path/'cache',install_path=tmp_path,
                             resolve=lambda _:SimpleNamespace(sha256='a'*64))
    segment={'entities':[dict(id='8:1',hero_name='McGinnis')]}
    report=installed_cloth.prepare_cloth_assets(segment,[dict(id='8:1',resource='models/test.vmdl_c')],
        provider,Decoder(),tmp_path/'pack',log=lambda _:None,
        extractor_command=[sys.executable,str(worker)])
    assert report['status']=='failed' and report['unsupported_entities']==[]
    assert 'Required runtime fixture is unavailable' in report['failed_entities'][0]['error']
    assert 'secondary_motion_file' not in segment['entities'][0]
    data=dict(segment,coverage={'scene_extras':{'secondary_motion':report}})
    with pytest.raises(RuntimeError,match='Required runtime fixture is unavailable'):
        validate_cloth_preparation(data)


@pytest.mark.parametrize('partial',[False,True])
def test_requested_cloth_never_accepts_a_dropped_preparation_stage(partial):
    for coverage in ({},{'scene_extras':{'secondary_motion':{'status':'not_requested'}}}):
        with pytest.raises(RuntimeError,match='no preparation outcome'):
            validate_cloth_preparation({'entities':[dict(id='8:1')],'coverage':coverage},partial=partial)


def test_strict_cloth_refuses_reported_preparation_failure_but_explicit_partial_retains_it(tmp_path,monkeypatch):
    report=prepare(tmp_path,monkeypatch,ConversionError('Missing runtime dependency'))
    data={'entities':[dict(id='8:1')],'coverage':{'scene_extras':{'secondary_motion':report}}}
    with pytest.raises(RuntimeError,match='Missing runtime dependency'):
        validate_cloth_preparation(data)
    assert validate_cloth_preparation(data,partial=True)==report


def test_cloth_not_applicable_is_valid_but_missing_character_is_not():
    report=dict(status='not_applicable',not_applicable_entities=[dict(id='8:1',status='not_applicable')])
    data={'entities':[dict(id='8:1')],'coverage':{'scene_extras':{'secondary_motion':report}}}
    validate_cloth_preparation(data)
    data['entities'].append(dict(id='15:1'))
    with pytest.raises(RuntimeError,match='omitted selected characters'):
        validate_cloth_preparation(data,partial=True)


@pytest.mark.parametrize('partial',[False,True])
def test_prepared_cloth_claim_needs_a_readable_input_file(tmp_path,partial):
    report=dict(status='prepared',supported_entities=[dict(id='8:1',status='ready_to_bake')])
    data={'entities':[dict(id='8:1',secondary_motion_file=str(tmp_path/'missing.json'))],
          'coverage':{'scene_extras':{'secondary_motion':report}}}
    with pytest.raises(RuntimeError,match='bake input'):
        validate_cloth_preparation(data,partial=partial)


def test_helper_cannot_turn_real_cloth_into_a_not_applicable_success(tmp_path,monkeypatch):
    tool=tmp_path/'probe.dll';tool.write_bytes(b'fixture')
    provider=SimpleNamespace(install_path=tmp_path,resolve=lambda _:SimpleNamespace(sha256='a'*64))
    class Decoder:
        def _entry(self,operation,inputs,settings,build):
            output=tmp_path/'work';output.mkdir();build(output);return output
    def native(command,**kwargs):
        (Path(command[-1])/'cloth-extraction.json').write_text(json.dumps(dict(
            schema_version=1,resource='models/test.vmdl',animation_files=0,fe_node_count=0)))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(reconstructed_cloth.subprocess,'run',native)
    with pytest.raises(ConversionError,match='node count differs'):
        reconstructed_cloth.reconstruct('models/test.vmdl_c',provider,Decoder(),
            {'m_pFeModel':{'m_nNodeCount':3}},command=[str(tool)])
