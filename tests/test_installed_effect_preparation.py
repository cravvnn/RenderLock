import copy
import json
from types import SimpleNamespace

import pytest

from agr.effect_assets import EffectAssetAdapter
from agr.installed_effects import prepare_installed_effects
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

    def prepare():
        path.write_text(json.dumps(scene), encoding='utf-8')
        report = prepare_installed_effects(path, pack, SimpleNamespace(resources=[model]),
                                          SimpleNamespace(dump=lambda resource, _: definitions[resource]),
                                          log=lambda _: None)
        manifest = json.loads((pack / 'effects/effects_manifest.json').read_text())
        return report, manifest, json.loads(path.read_text())

    return SimpleNamespace(scene=scene, definitions=definitions, prepare=prepare, pack=pack)


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
