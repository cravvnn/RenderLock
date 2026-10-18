"""Installed preparation exercises the existing identity and numerical pose gates."""
from pathlib import Path
from types import SimpleNamespace
import json
import struct
import numpy as np
import pytest
from agr.animation import AnimationError, evaluate_segment
from agr.installed_animation import prepare_animation_assets
from test_animation import workspace, write, provenance, strings

class Provider:
    def __init__(self): self.calls = []
    def resolve(self, path):
        self.calls.append(path)
        return SimpleNamespace(local_path=Path(path + '_c'), sha256='c'*64)

class Converter:
    def __init__(self, source):
        self.source = source; self.decoded = []; self.overrides = {}
    def dump(self, name, provider):
        if name in self.overrides: return self.overrides[name]
        if name.endswith('.vnmgraph'): return {'m_skeleton': 'models/test.vnmskel'}
        return json.loads((self.source/'clip_cache'/f'{name}.json').read_text())
    def decode_clip(self, name, provider):
        self.decoded.append(name)
        return json.loads((self.source/'clip_cache'/f'{name}.json').read_text()), self.source/'clip_cache'/f'{name}.npz'

def test_installed_preparation_then_evaluation_preserves_stale_seed(workspace):
    root, source, inputs = workspace
    provider = Provider(); converter = Converter(source); pack = root/'installed'
    report = prepare_animation_assets(inputs/'segment.json', pack, provider, converter)
    assert report['status'] == 'prepared' and report['sampled_clip_count'] == 1
    assert converter.decoded == ['clips/test.vnmclip']
    assert report['entities'][0]['selected_rows'] == 3
    assert set(provider.calls) == {'graphs/test.vnmgraph', 'models/test.vnmskel', 'clips/test.vnmclip'}
    result = json.loads(evaluate_segment(inputs/'segment.json', root/'output', pack).read_text())
    with np.load(result['entities'][0]['pose_file']) as poses:
        np.testing.assert_array_equal(poses['t'][:,0,0], [10, 0])
    assert result['entities'][0]['samples'][0]['position'] == [1,2,3]

def test_unselected_graph_resources_are_not_decoded(workspace):
    root, source, inputs = workspace
    table = json.loads((inputs/'animation_tables.json').read_text())
    table['initial_tables']['AnimAssetData'][0]['data_hex'] = (struct.pack('<I',1)+strings(
        ['clips/test.vnmclip','clips/not_used.vnmclip'])).hex()
    write(inputs/'animation_tables.json', table)
    raw = json.loads((inputs/'raw_animation_state.json').read_text())
    for row in raw:
        tick = 102 if row['demo_tick'] == 12 else 100
        phase = 0 if row['demo_tick'] == 12 else 65535
        value = tick | 1<<32 | 1<<36 | 1<<37 | phase<<43
        row['serialized_pose_recipe_array'] = list(value.to_bytes(8,'little'))
        row['fields']['m_nSerializePoseRecipeSizeAG2'] = 8
    write(inputs/'raw_animation_state.json', raw); provenance(inputs)
    converter = Converter(source)
    report = prepare_animation_assets(inputs/'segment.json', root/'installed', Provider(), converter)
    assert report['status'] == 'prepared'
    assert converter.decoded == ['clips/test.vnmclip']

def test_recorded_skeleton_order_mismatch_stops_before_clip_decode(workspace):
    root, source, inputs = workspace; converter = Converter(source)
    skeleton = converter.dump('models/test.vnmskel', None)
    skeleton['m_boneIDs'] = ['root', 'tip', 'child']
    converter.overrides['models/test.vnmskel'] = skeleton
    with pytest.raises(AnimationError, match='bone or mask order') as caught:
        prepare_animation_assets(inputs/'segment.json', root/'installed', Provider(), converter)
    assert not converter.decoded
    assert not (root/'installed/animation_catalog.json').exists()
    report = json.loads((root/'installed/animation_asset_report.json').read_text())
    assert report['status'] == 'failed'
    assert report['demo_sha256'] == 'a'*64
    assert report['catalog'] == {} and report['entities'] == []
    assert report['sampled_clip_count'] == 0 and not report['pose_evaluation_complete']
    assert report['resources'] == [
        {'resource':'graphs/test.vnmgraph', 'sha256':'c'*64},
        {'resource':'models/test.vnmskel', 'sha256':'c'*64},
    ]
    diagnostic = report['diagnostics'][0]
    assert diagnostic['id'] == '7' and diagnostic['hero_id'] == 99
    assert diagnostic['message'] == str(caught.value)
    assert diagnostic['details'] == caught.value.details == {
        'code':'animation_skeleton_mismatch', 'component':'bones', 'reason':'order',
        'recorded_count':3, 'asset_count':3, 'first_index':1,
        'recorded_name':'child', 'asset_name':'tip',
        'skeleton':'models/test.vnmskel', 'graph':'graphs/test.vnmgraph',
        'demo_tick':10, 'server_tick':100,
    }


def test_mask_mismatch_is_preserved_in_partial_diagnostics(workspace):
    root, source, inputs = workspace; converter = Converter(source)
    table = json.loads((inputs/'animation_tables.json').read_text())
    table['initial_tables']['AnimAssetData'][1]['data_hex'] = (
        struct.pack('<I',1)+strings(['root','child','tip'])+strings(['upper','lower'])).hex()
    write(inputs/'animation_tables.json', table)
    skeleton = converter.dump('models/test.vnmskel', None)
    skeleton['m_maskDefinitions'] = [{'m_ID':'lower'}, {'m_ID':'upper'}]
    converter.overrides['models/test.vnmskel'] = skeleton
    report = prepare_animation_assets(inputs/'segment.json', root/'installed', Provider(), converter, strict=False)
    assert not converter.decoded
    assert report['status'] == 'partial' and report['catalog'] == {} and report['entities'] == []
    details = report['diagnostics'][0]['details']
    assert details['component'] == 'masks' and details['reason'] == 'order'
    assert details['recorded_name'] == 'upper' and details['asset_name'] == 'lower'
    assert details['skeleton'] == 'models/test.vnmskel' and details['demo_tick'] == 10
    assert json.loads((root/'installed/animation_asset_report.json').read_text()) == report


def test_installed_preparation_keeps_same_index_bone_and_mask_case_aliases(workspace):
    root, source, inputs = workspace; converter = Converter(source)
    table = json.loads((inputs/'animation_tables.json').read_text())
    table['initial_tables']['AnimAssetData'][1]['data_hex'] = (
        struct.pack('<I',1)+strings(['root','child','tip'])+strings(['upper'])).hex()
    write(inputs/'animation_tables.json', table)
    skeleton = converter.dump('models/test.vnmskel', None)
    skeleton['m_boneIDs'][1] = 'Child'
    skeleton['m_maskDefinitions'] = [{'m_ID':'Upper'}]
    converter.overrides['models/test.vnmskel'] = skeleton
    report = prepare_animation_assets(inputs/'segment.json', root/'installed', Provider(), converter)
    assert report['status'] == 'prepared' and not report['diagnostics']
    assert converter.decoded == ['clips/test.vnmclip']
    assert report['entities'][0]['bone_name_aliases'] == [{'index':1,'recorded':'child','asset':'Child'}]
    assert report['entities'][0]['mask_name_aliases'] == [{'index':0,'recorded':'upper','asset':'Upper'}]

def test_unknown_recorded_task_excluded_without_forged_catalog(workspace):
    root, source, inputs = workspace
    table = json.loads((inputs/'animation_tables.json').read_text())
    table['initial_tables']['AnimTaskTypes'][1]['name'] = 'CNmUnimplementedTask'
    write(inputs/'animation_tables.json', table); converter = Converter(source)
    report = prepare_animation_assets(inputs/'segment.json', root/'installed', Provider(), converter, strict=False)
    assert report['status'] == 'partial' and report['catalog'] == {}
    assert 'CNmUnimplementedTask' in report['diagnostics'][0]['message']
    assert converter.decoded == []

def test_raw_sidecar_tamper_rejected_before_installation_reads(workspace):
    root, source, inputs = workspace
    (inputs/'raw_animation_state.json').write_text('[]'); provider = Provider()
    with pytest.raises(AnimationError, match='checksum mismatch'):
        prepare_animation_assets(inputs/'segment.json', root/'installed', provider, Converter(source))
    assert provider.calls == []

def test_clip_skeleton_retarget_requirement_remains_explicit(workspace):
    root, source, inputs = workspace
    metadata = json.loads((source/'clip_cache/clips/test.vnmclip.json').read_text())
    metadata['m_skeleton'] = 'models/other.vnmskel'
    write(source/'clip_cache/clips/test.vnmclip.json', metadata)
    skeleton = json.loads((source/'clip_cache/models/test.vnmskel.json').read_text())
    skeleton['m_boneIDs'][-1] = 'different_tip'
    write(source/'clip_cache/models/other.vnmskel.json', skeleton)
    with pytest.raises(AnimationError, match='retargeting'):
        prepare_animation_assets(inputs/'segment.json', root/'installed', Provider(), Converter(source))
