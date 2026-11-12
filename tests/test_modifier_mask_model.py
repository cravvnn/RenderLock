import copy
import pytest
from agr.modifier_mask_model import model_scale, attachment_binding, constant_track, METADATA_POLICY
from agr.modifier_mask_effects import merge_modifier_mask_report

HEADER = '<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->\n'

def metadata():
    return dict(schema_version=1, policy=METADATA_POLICY, resource='models/hero.vmdl',
        source_sha256='a'*64, settings_class='CitadelModelParticleSettings_t',
        settings_present=True, settings_kv3=HEADER+'{ m_flScale = 0.81 }', scope='Typed metadata only')


def model():
    return dict(resource='models/hero.vmdl_c', resource_sha256='a'*64, names=['head'])


def attachment():
    return dict(m_name='mask', m_nInfluences=1, m_bIgnoreRotation=False,
        m_influenceNames=['head', '', ''], m_influenceWeights=[1, 0, 0],
        m_bInfluenceRootTransform=[False]*3, m_vInfluenceOffsets=[[1, 2, 3], [0]*3, [0]*3],
        m_vInfluenceRotations=[[0, 0, 0, 1]]*3)


def test_scale_and_attachment_come_from_typed_authored_inputs_not_hero_defaults():
    md = metadata(); a = attachment(); before = copy.deepcopy((md, a))
    assert model_scale(md, model()) == .81
    b = attachment_binding([dict(m_name='other'), a], model())
    assert b['bone'] == 'head' and b['offset'] == [1, 2, 3] and b['rotation_xyzw'] == [0, 0, 0, 1]
    assert (md, a) == before
    b['offset'][0] = 99
    assert a['m_vInfluenceOffsets'][0] == [1, 2, 3]


@pytest.mark.parametrize('change', [
    lambda d:d.update(source_sha256='b'*64), lambda d:d.update(resource='models/other.vmdl'),
    lambda d:d.update(settings_present=False), lambda d:d.update(settings_class='OtherSettings'),
    lambda d:d.update(settings_kv3='{}'), lambda d:d.update(settings_kv3='{ m_flScale = 0 }'),
    lambda d:d.update(settings_kv3='{ m_flScale = -1 }'), lambda d:d.update(settings_kv3='{ m_flScale = true }'),
    lambda d:d.update(settings_kv3='{ m_flScale = 0.81 m_Offset = 1 }'),
])
def test_missing_changed_or_extended_settings_do_not_get_an_invented_scale(change):
    d = metadata(); change(d)
    if not d['settings_kv3'].startswith('<!-- kv3'): d['settings_kv3'] = HEADER+d['settings_kv3']
    with pytest.raises(ValueError): model_scale(d, model())


@pytest.mark.parametrize('change', [
    lambda a:a.update(m_nInfluences=2), lambda a:a.update(m_bIgnoreRotation=True),
    lambda a:a.update(m_influenceNames=['other', '', '']),
    lambda a:a.update(m_influenceWeights=[.5, .5, 0]),
    lambda a:a.update(m_bInfluenceRootTransform=[True, False, False]),
    lambda a:a.update(m_vInfluenceRotations=[[0, 0, 0, 2]]*3),
    lambda a:a.update(m_vInfluenceOffsets=[[float('nan'), 0, 0]]*3),
])
def test_unsupported_attachment_provider_does_not_redirect_or_drop_rotation(change):
    a = attachment(); change(a)
    with pytest.raises(ValueError): attachment_binding([a], model())


def test_attachment_name_is_exact_and_unique():
    with pytest.raises(ValueError, match='absent'): attachment_binding([], model())
    with pytest.raises(ValueError, match='ambiguous'): attachment_binding([attachment(), attachment()], model())


@pytest.mark.parametrize('present,gate', [(False, True), (True, False), (False, False)])
def test_transition_is_withheld_without_a_guessed_hide_or_delay(present, gate):
    with pytest.raises(ValueError, match='scheduling and endcap'):
        constant_track(dict(gate_samples=[dict(owner_present=present, native_gate_request=gate)]))


def test_derived_geometry_does_not_inflate_recorded_instance_or_birth_counts():
    report = dict(objects=12, particle_tracks=5, point_lights=2, rendered_instances=97,
        pre_shot_rendered_instances=80, candidate_by_birth_scope={'in_output': 17}, unsupported=[], limitations=[])
    derived = dict(objects=6, particle_tracks=1, point_lights=0, rendered_model_branches=1,
        unsupported=[dict(resource='mask', occurrences=1, reason='unqualified stop')], coverage_scope='component only')
    merge_modifier_mask_report(report, derived)
    assert report['objects'] == 18 and report['particle_tracks'] == 6 and report['point_lights'] == 2
    assert report['rendered_instances'] == 97 and report['pre_shot_rendered_instances'] == 80
    assert report['candidate_by_birth_scope'] == {'in_output': 17}
    assert report['modifier_model_effects']['rendered_model_branches'] == 1
    assert report['unsupported'] == derived['unsupported']


def test_optional_unavailable_capture_stays_visible_in_coverage():
    report = {}
    merge_modifier_mask_report(report, dict(coverage_scope='component only', status='unavailable', reason='old helper'))
    assert report['modifier_model_effects']['status'] == 'unavailable'
    assert 'old helper' in report['limitations'][-1]


def test_normal_owner_binding_does_not_require_optional_bone_rules(tmp_path):
    import json
    from types import SimpleNamespace
    from agr.asset_converter import sha
    from agr.vendor.resource_hash import resource_id
    from agr.modifier_mask_model import prepare_owner_model, validate_owner_model
    resource = 'models/hero.vmdl_c'
    source = tmp_path/'hero.vmdl_c'; source.write_bytes(b'authored owner')
    skeleton = tmp_path/'skeleton.json'; skeleton.write_text(json.dumps({'m_boneIDs': ['root', 'head']}))
    entity = dict(id='56:895', hero_id=65, model_handle=str(resource_id(resource[:-2])),
        skeleton_file=str(skeleton), samples=[{'tick': 10}])
    provider = SimpleNamespace(resources=[resource], resolve=lambda r: SimpleNamespace(
        sha256=sha(source), local_path=source))
    descriptor = prepare_owner_model(entity, provider)
    validate_owner_model(descriptor, entity)
    assert 'model_constraints' not in entity
    assert descriptor['names'] == ['root', 'head']
    for key, value in [('resource', 'models/other.vmdl_c'), ('resource_sha256', 'a'*64),
                       ('skeleton_sha256', 'b'*64), ('names', ['root', 'different'])]:
        changed = dict(descriptor, **{key: value})
        with pytest.raises(ValueError): validate_owner_model(changed, entity)
    with pytest.raises(ValueError, match='unique'):
        prepare_owner_model(entity, SimpleNamespace(resources=[], resolve=provider.resolve))
    skeleton.write_text(json.dumps({'m_boneIDs': ['root', 'other']}))
    with pytest.raises(ValueError, match='skeleton bytes'): validate_owner_model(descriptor, entity)
