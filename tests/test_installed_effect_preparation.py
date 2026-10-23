import copy
import json
from types import SimpleNamespace

import pytest

from agr.asset_converter import ConversionError
from agr.coverage_diagnostics import merge_stage_coverage, scene_coverage_warnings
from agr.effect_assets import EffectAssetAdapter
from agr.installed_effects import prepare_installed_effects
from agr.recorded_effect_simulation import root_origin_bindings
from agr.vendor.resource_hash import resource_id


@pytest.fixture
def effect_case(tmp_path, monkeypatch):
    model = 'models/test.vmdl_c'
    source_texture = tmp_path / 'source-texture'
    source_texture.mkdir()
    (source_texture / 'image.png').write_bytes(b'texture fixture; no rendering')
    descriptor = {'is_sprite_sheet': False, 'sequences': {
        '0': {'status': 'supported', 'frames': [{'image': 'image.png'}]}}}
    attachments = [{'m_name': name, 'm_nInfluences': 1,
                    'm_influenceNames': ['root'], 'm_influenceWeights': [1]}
                   for name in ('muzzle', 'hand')]
    monkeypatch.setattr(EffectAssetAdapter, 'attachments', lambda *args: attachments)
    monkeypatch.setattr(EffectAssetAdapter, 'texture', lambda *args: (source_texture, descriptor))
    layer = {'_class': 'CParticleSystemDefinition',
             'm_Emitters': [{'_class': 'C_OP_InstantaneousEmitter', 'm_nParticlesToEmit': 1}],
             'm_Initializers': [{'_class': 'C_INIT_InitFloat', 'm_nOutputField': 1,
                                 'm_InputValue': .1}],
             'm_Operators': [{'_class': 'C_OP_PositionLock'}],
             'm_Renderers': [{'_class': 'C_OP_RenderSprites',
                              'm_nOutputBlendMode': 'PARTICLE_OUTPUT_BLEND_MODE_ADD',
                              'm_vecTexturesInput': [{'m_hTexture': 'materials/test.vtex'}]}]}
    definitions = {'particles/test.vpcf': layer}
    event = {'id': '20:1', 'entity_id': '17:3', 'resource': 'particles/test.vpcf',
             'created_tick': 100, 'events': [{'type': 'UPDATE_ENT', 'tick': 100,
                 'data': {'update_particle_ent': {'control_point': 0, 'attach_type': 5,
                     'entity_handle': 17 | (3 << 14), 'attachment': 1}}}]}
    scene = {'start_tick': 100, 'end_tick': 105, 'tick_rate': 64,
             'entities': [{'id': identity, 'model_handle': str(resource_id(model[:-2]))}
                          for identity in ('17:3', '18:4')], 'particle_instances': [event]}
    path = tmp_path / 'segment.json'
    pack = tmp_path / 'assets'
    decoder = SimpleNamespace(dump=lambda resource, _: definitions[resource])

    def prepare(attachments_only=False):
        path.write_text(json.dumps(scene), encoding='utf-8')
        report = prepare_installed_effects(path, pack, SimpleNamespace(resources=[model]),
                                          decoder,
                                          log=lambda _: None, attachments_only=attachments_only)
        manifest = json.loads((pack / 'effects/effects_manifest.json').read_text())
        return report, manifest, json.loads(path.read_text())

    return SimpleNamespace(scene=scene, definitions=definitions, attachments=attachments, decoder=decoder,
                           prepare=prepare, pack=pack)


@pytest.fixture(params=[False, True], ids=['root_cp0', 'root_cp0_literal_cp2'])
def root_effect_case(effect_case, request):
    case = effect_case
    row = case.scene['particle_instances'][0]
    handle = 17 | (3 << 14)
    row.update(index=20, generation=1, entity_handle=handle)
    row['events'][0]['data']['update_particle_ent'].update(
        attach_type=1, attachment=0, include_wearables=True)
    row['events'].insert(0, {'type': 'CREATE', 'tick': 100, 'data': {
        'index': 20, 'create_particle': {
            'entity_handle': handle, 'attach_type': 5, 'cluster': False}}})
    if request.param:
        row['events'].append({'type': 'UPDATE_TRANSFORM', 'tick': 100, 'data': {
            'index': 20, 'update_particle_transform': {
                'control_point': 2, 'position': {'x': .82}}}})
    row['events'].append({'type': 'RELEASE', 'tick': 100})
    if request.param:
        row['events'].append({'type': 'UPDATE_TRANSFORM', 'tick': 102, 'data': {
            'index': 20, 'update_particle_transform': {
                'control_point': 2, 'position': {'x': 3, 'y': 4, 'z': 5}}}})
    return case


def test_prepared_layers_keep_exact_attachment_keys_through_children_and_manifest(effect_case):
    case = effect_case
    root = case.definitions['particles/test.vpcf']
    child = copy.deepcopy(root)
    unsupported = copy.deepcopy(root)
    unsupported['m_Operators'].append({'_class': 'C_OP_CollideWithWorld'})
    root['m_Children'] = [{'m_ChildRef': 'particles/child.vpcf', 'm_flDelay': 1 / 64},
                          {'m_ChildRef': 'particles/unsupported.vpcf'}]
    case.definitions.update({'particles/child.vpcf': child,
                             'particles/unsupported.vpcf': unsupported})
    other = copy.deepcopy(case.scene['particle_instances'][0])
    other['id'] = '21:1'
    # The recorded CP target overrides the instance's owner, including serial.
    other['events'][0]['data']['update_particle_ent'].update(
        entity_handle=18 | (4 << 14), attachment=2)
    case.scene['particle_instances'].append(other)

    report, manifest, saved = case.prepare()

    assert report['status'] == 'partial'
    assert report['prepared_layer_count'] == report['prepared_particle_count'] == 4
    assert [(layer['instance']['id'], layer['instance']['entity_id'], layer['attachment'],
             layer['instance']['created_tick']) for layer in manifest['layers']] == [
        ('20:1', '17:3', '17:3/1', 100), ('20:1', '17:3', '17:3/1', 101),
        ('21:1', '18:4', '18:4/2', 100), ('21:1', '18:4', '18:4/2', 101)]
    assert set(manifest['attachments']) == {'17:3/1', '18:4/2'}
    for layer in manifest['layers']:
        binding = manifest['attachments'][layer['attachment']]
        assert binding['entity_id'] == layer['instance']['entity_id']
    assert manifest['attachments']['17:3/1']['definition']['m_name'] == 'muzzle'
    assert manifest['attachments']['18:4/2']['definition']['m_name'] == 'hand'
    assert report['unsupported_occurrences'] == 2
    assert len(report['unsupported']) == 1
    assert report['unsupported'][0]['reason'] == 'Operator C_OP_CollideWithWorld'
    assert manifest['coverage']['unsupported'] == report['unsupported']
    texture = manifest['textures']['materials/test.vtex']
    assert (case.pack / 'effects' / texture['directory'] / 'image.png').is_file()
    assert saved['effects']['status'] == saved['coverage']['particles'] == 'partial'
    assert saved['particle_instances'] == case.scene['particle_instances']


@pytest.mark.parametrize('change,reason', [
    ('serial', 'Attachment target is not an exported exact-serial hero'),
    ('index', 'Recorded attachment index absent from installed model'),
    ('model_change', 'Particle attachments across recorded model changes are not yet qualified'),
])
def test_attachment_identity_failures_still_produce_explicit_unsupported_coverage(effect_case, change, reason):
    case = effect_case
    binding = case.scene['particle_instances'][0]['events'][0]['data']['update_particle_ent']
    if change == 'serial':
        binding['entity_handle'] = 17 | (4 << 14)
    elif change == 'index':
        binding['attachment'] = 3
    else:
        case.scene['entities'][0]['model_tracks'] = ['human', 'werewolf']

    report, manifest, saved = case.prepare()

    assert report['status'] == 'unavailable'
    assert report['prepared_layer_count'] == report['prepared_particle_count'] == 0
    assert manifest['layers'] == [] and manifest['attachments'] == {}
    assert report['unsupported'][0]['reason'] == reason
    assert manifest['coverage']['reason_code'] == 'no_supported_particle_layers'
    assert saved['coverage']['particles'] == 'unavailable'
    assert saved['particle_instances'] == case.scene['particle_instances']


def test_root_attachment_inventory_is_neutral_including_synthetic_zero_failure_merge(root_effect_case, monkeypatch):
    case = root_effect_case
    row = case.scene['particle_instances'][0]
    expected_providers = ['root_origin']
    if any(event['type'] == 'UPDATE_TRANSFORM' for event in row['events']):
        expected_providers.append('literal_transform')
    assert [binding['provider'] for binding in root_origin_bindings(row, 100, 105)] == expected_providers

    def forbidden(*args):
        pytest.fail('Empty point inventory must not decode a definition, attachment table or texture')

    monkeypatch.setattr(case.decoder, 'dump', forbidden)
    monkeypatch.setattr(EffectAssetAdapter, 'attachments', forbidden)
    monkeypatch.setattr(EffectAssetAdapter, 'texture', forbidden)

    report, manifest, saved = case.prepare(attachments_only=True)

    assert report['status'] == saved['effects']['status'] == saved['coverage']['particles'] == 'prepared'
    assert report['backend'] == 'recorded_simulation_attachments'
    assert report['attachments'] == manifest['attachments'] == {}
    assert report['unsupported'] == manifest['coverage']['unsupported'] == []
    assert report['unsupported_occurrences'] == manifest['coverage']['unsupported_occurrences'] == 0
    assert report['prepared_layer_count'] == report['prepared_particle_count'] == 0
    assert manifest['layers'] == [] and manifest['textures'] == {}
    assert saved['particle_instances'] == case.scene['particle_instances']
    assert report['authored_expiry_pruning']['retained_instances'] == 1

    # Synthetic rendering summary only: this pure test performs no simulation or rendering.
    synthetic_rendering = {'status': 'partial', 'stage': 'rendering',
                           'backend': 'recorded_simulation_v1', 'objects': 1,
                           'rendered_instances': 1, 'selected_recorded_instances': 1,
                           'unsupported': [], 'unsupported_occurrences': 0}
    for preparation in (report, manifest['coverage']):
        merged = merge_stage_coverage(preparation, synthetic_rendering)
        assert merged['unsupported'] == [] and merged['unsupported_occurrences'] == 0
        assert merged['rendered_instances'] == 1
        assert merge_stage_coverage(preparation, merged) == merged
        assert any('0 unsupported or unavailable layer occurrences' in warning
                   for warning in scene_coverage_warnings({'particles': merged}))


def test_neutral_point_inventory_does_not_admit_root_controls_to_classic_preparation(root_effect_case):
    report, manifest, _ = root_effect_case.prepare()

    assert report['status'] == 'unavailable'
    assert manifest['layers'] == [] and manifest['attachments'] == {}
    assert report['unsupported_occurrences'] == 1
    assert report['unsupported'][0]['reason'] == 'Only recorded point-follow attachment type 5 is implemented'


def test_neutral_point_inventory_does_not_qualify_custom_origin_creation(root_effect_case):
    case = root_effect_case
    row = case.scene['particle_instances'][0]
    row['events'][0]['data']['create_particle']['attach_type'] = 0

    report, manifest, saved = case.prepare(attachments_only=True)

    assert report['unsupported'] == [] and manifest['attachments'] == {}
    assert report['unsupported_occurrences'] == 0
    assert saved['particle_instances'] == case.scene['particle_instances']
    with pytest.raises(ValueError, match='creation provider'):
        root_origin_bindings(saved['particle_instances'][0], 100, 105)


@pytest.mark.parametrize('change,reason', [
    ('missing_table', 'Recorded attachment index absent from installed model'),
    ('table_error', 'Attachment table fixture unavailable'),
    ('index', 'Recorded attachment index absent from installed model'),
    ('serial', 'Attachment target is not an exported exact-serial hero'),
    ('model', 'Attachment model resource ID unresolved'),
    ('model_change', 'Particle attachments across recorded model changes are not yet qualified'),
])
def test_point_inventory_retains_actual_type5_preparation_failures(effect_case, monkeypatch, change, reason):
    case = effect_case
    binding = case.scene['particle_instances'][0]['events'][0]['data']['update_particle_ent']
    if change == 'missing_table':
        case.attachments.clear()
    elif change == 'table_error':
        def unavailable(*args):
            raise ConversionError(reason)
        monkeypatch.setattr(EffectAssetAdapter, 'attachments', unavailable)
    elif change == 'index':
        binding['attachment'] = 3
    elif change == 'serial':
        binding['entity_handle'] = 17 | (4 << 14)
    elif change == 'model':
        case.scene['entities'][0]['model_handle'] = '0'
    else:
        case.scene['entities'][0]['model_tracks'] = ['human', 'werewolf']

    report, manifest, saved = case.prepare(attachments_only=True)

    assert report['status'] == 'prepared'  # Inventory status does not establish rendering support.
    assert report['attachments'] == manifest['attachments'] == {}
    assert report['unsupported_occurrences'] == manifest['coverage']['unsupported_occurrences'] == 1
    assert report['unsupported'] == manifest['coverage']['unsupported']
    assert report['unsupported'][0]['reason'] == reason
    assert report['unsupported'][0]['occurrences'] == 1
    assert saved['particle_instances'] == case.scene['particle_instances']


def test_mixed_inventory_retains_independent_failures_and_idempotent_synthetic_merge(root_effect_case):
    case = root_effect_case
    root = case.scene['particle_instances'][0]
    point = copy.deepcopy(root)
    point['id'] = '21:1'
    point['events'][1]['data']['update_particle_ent'].update(attach_type=5, attachment=2)
    bad = copy.deepcopy(point)
    bad['id'] = '22:1'
    bad['events'][1]['data']['update_particle_ent']['entity_handle'] = 17 | (4 << 14)
    repeated_bad = copy.deepcopy(bad)
    repeated_bad['id'] = '23:1'
    # A bad row cannot hide a later valid table; the root row needs no table at all.
    case.scene['particle_instances'] = [root, bad, point, repeated_bad]

    report, manifest, saved = case.prepare(attachments_only=True)

    assert set(report['attachments']) == set(manifest['attachments']) == {'17:3/2'}
    assert report['unsupported_occurrences'] == manifest['coverage']['unsupported_occurrences'] == 2
    assert len(report['unsupported']) == 1
    assert report['unsupported'][0]['reason'] == 'Attachment target is not an exported exact-serial hero'
    assert report['unsupported'][0]['occurrences'] == 2
    assert report['unsupported'][0]['instance_examples'] == ['22:1', '23:1']
    assert saved['particle_instances'] == case.scene['particle_instances']

    # Synthetic independent rendering failures; no renderer or simulator is executed.
    synthetic_rendering = {'status': 'partial', 'stage': 'rendering', 'objects': 1,
                           'unsupported': [{'resource': 'particles/synthetic.vpcf',
                                            'reason': 'Synthetic rendering failure', 'occurrences': 3}],
                           'unsupported_occurrences': 3}
    for preparation in (report, manifest['coverage']):
        merged = merge_stage_coverage(preparation, synthetic_rendering)
        assert merged['preparation']['unsupported_occurrences'] == 2
        assert merged['rendering']['unsupported_occurrences'] == 3
        assert merged['unsupported_occurrences'] == 5
        assert [(item['stage'], item['occurrences']) for item in merged['unsupported']] == [
            ('preparation', 2), ('rendering', 3)]
        assert merge_stage_coverage(preparation, merged) == merged
