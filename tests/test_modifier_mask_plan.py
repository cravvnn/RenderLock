"""Sustained modifier input boundary; no game, invented lifetimes or Blender."""
import copy
import struct
import pytest
from agr import modifier_mask_plan as m
from agr import client_modifier_writes as raw
from agr import client_effect_owner_states as owners
from test_client_modifier_writes import capture, row, payload, varint, advertise, store
from test_client_effect_owner_states import owner, capture as owner_capture
from test_client_effect_owner_states import advertise as owner_advertise, store as owner_store


def mask_bytes(**changes):
    header = dict(entry_type=1, parent=4636730, serial_number=189,
                  modifier_subclass=m.MASK_TOKEN, int1=0)
    header.update(changes)
    return (payload(**header) + varint(7 << 3 | 5) + struct.pack('<f', 8.09375)
            + varint(9 << 3) + varint(header['parent'])
            + varint(15 << 3 | 5) + struct.pack('<f', 8.09375))


def scene(tmp_path, *, modifier_rows=None, owner_rows=None):
    modifier_rows = [row(1, data=mask_bytes())] if modifier_rows is None else modifier_rows
    owner_rows = [owner(t) for t in range(10, 21)] if owner_rows is None else owner_rows
    c = capture(modifier_rows); store(tmp_path, c)
    e = raw.retain_capture(tmp_path, advertise(c), 10, 20, 'a'*64, 'b'*64)
    o = owner_capture(owner_rows); owner_store(tmp_path, o)
    oe = owners.retain_capture(tmp_path, owner_advertise(o), 10, 20, 'a'*64, 'b'*64)
    return dict(metadata={'demo_sha256': 'a'*64}, start_tick=10, end_tick=20,
        entities=[dict(id='58:283', hero_id=63, samples=[dict(tick=t, active=True, present=True)
                  for t in range(10, 21)])], client_modifier_writes=e, client_effect_owner_states=oe)


def test_sustained_mask_has_no_fake_birth_seed_age_or_render_admission(tmp_path):
    s = scene(tmp_path); before = copy.deepcopy(s)
    plan = m.compile_mask_plan(s, ['58:283'])
    assert s == before
    assert plan['policy'] == m.POLICY and len(plan['tracks']) == 1
    track = plan['tracks'][0]
    assert track['raw_first_observed_tick'] == 1 and track['serial_number'] == 189
    assert track['entity_id'] == '58:283' and track['selector'] == 0
    assert [r['tick'] for r in track['gate_samples']] == list(range(10, 21))
    assert all(r['native_gate_request'] for r in track['gate_samples'])
    assert plan['coverage']['status'] == 'inputs_only'
    assert plan['coverage']['rendered_instances'] == 0
    assert plan['coverage']['actual_model_lifetime'] == 'unqualified'
    assert not set(track) & {'created_tick', 'birth', 'age', 'seed', 'simulation'}
    plan['source_identity']['owners']['helper_sha256'] = 'c'*64
    assert s == before


def test_death_and_owner_presence_are_separate_inputs_not_model_death(tmp_path):
    rows = [owner(t) for t in range(10, 21)]
    for r in rows[6:]: r['life_state'] = 2
    rows[-1]['active'] = False
    s = scene(tmp_path, owner_rows=rows)
    s['entities'][0]['samples'][-1].update(active=False, present=False)
    plan = m.compile_mask_plan(s, ['58:283']); samples = plan['tracks'][0]['gate_samples']
    assert [r['native_gate_request'] for r in samples] == [True]*6 + [False]*5
    assert all(r['owner_present'] for r in samples[:-1]) and not samples[-1]['owner_present']
    assert not any('hide' in key or 'death' in key for r in samples for key in r)


@pytest.mark.parametrize('field,word,bits,expected', [
    ('enabled_state_words', 1, 2, False), ('predicted_state_words', 1, 4, False),
    ('enabled_state_words', 1, 1, True), ('enabled_state_words', 0, 6, True),
    ('disabled_state_words', 1, 6, True), ('enabled_state_words', 3, 2**31, True),
])
def test_external_gate_uses_exact_word_and_camera_semantics(field, word, bits, expected):
    r = owner(); r[field][word] = bits
    assert m.network_external_mask_gate(r) is expected


@pytest.mark.parametrize('change', [
    lambda r:r.update(life_state=None),
    lambda r:r.update(enabled_state_words=None),
    lambda r:r.update(predicted_state_words=[0, 0, 0]),
])
def test_incomplete_owner_gate_is_not_assumed_visible(change):
    r = owner(); change(r)
    with pytest.raises(ValueError, match='unavailable'): m.network_external_mask_gate(r)


@pytest.mark.parametrize('rows', [
    [row(10, data=mask_bytes())],
    [row(1, data=mask_bytes()), row(15, data=payload(serial_number=189, int1=1))],
    [row(1, data=mask_bytes()), row(15, data=payload(serial_number=189, entry_type=2))],
    [row(1, data=mask_bytes()), row(8, data=payload(serial_number=189, entry_type=2)), row(9, data=mask_bytes())],
])
def test_activation_reapplication_update_and_removal_need_different_backend(tmp_path, rows):
    s = scene(tmp_path, modifier_rows=rows)
    with pytest.raises(ValueError, match='unqualified|activation'): m.compile_mask_plan(s, ['58:283'])


def test_snapshot_and_slot_reuse_do_not_hide_the_sustained_mask(tmp_path):
    rows = [row(1, data=mask_bytes()), row(2, data=payload(entry_type=1, serial_number=777, int1=3)),
            row(15, 'Snapshot', data=payload(entry_type=2, serial_number=189))]
    plan = m.compile_mask_plan(scene(tmp_path, modifier_rows=rows), ['58:283'])
    assert len(plan['tracks']) == 1 and plan['tracks'][0]['serial_number'] == 189


@pytest.mark.parametrize('extra', [
    varint(8 << 3 | 5) + struct.pack('<f', 5),
    varint(11 << 3) + varint(123), varint(16 << 3) + varint(1),
    varint(25 << 3) + varint(0), varint(99 << 3) + varint(0),
])
def test_finite_aura_flags_unknown_and_duplicate_fields_refuse_profile(tmp_path, extra):
    s = scene(tmp_path, modifier_rows=[row(1, data=mask_bytes()+extra)])
    with pytest.raises(ValueError, match='duration/aura/flags|duplicate'): m.compile_mask_plan(s, ['58:283'])


@pytest.mark.parametrize('which', ['helper', 'hero', 'serial', 'missing', 'sparse_actor', 'duplicate_actor', 'present', 'digest'])
def test_join_refuses_wrong_provenance_identity_or_incomplete_dense_tracks(tmp_path, which):
    rows = [owner(t) for t in range(10, 21)]
    if which == 'hero': rows[2]['hero_id'] = 65
    if which == 'serial': rows[2].update(entity_serial=284, entity_id='58:284')
    if which == 'missing': rows.pop(2)
    s = scene(tmp_path, owner_rows=rows)
    if which == 'helper': s['client_effect_owner_states']['provenance']['helper_sha256'] = 'c'*64
    if which == 'sparse_actor': s['entities'][0]['samples'].pop(2)
    if which == 'duplicate_actor': s['entities'].append(copy.deepcopy(s['entities'][0]))
    if which == 'present': s['entities'][0]['samples'][2]['present'] = False
    if which == 'digest': s['client_effect_owner_states']['capture']['rows'][0]['enabled_state_words'][1] = 2
    with pytest.raises(ValueError): m.compile_mask_plan(s, ['58:283'])


def test_unavailable_and_empty_do_not_claim_proven_no_effects(tmp_path):
    s = scene(tmp_path, modifier_rows=[])
    plan = m.compile_mask_plan(s, ['58:283'])
    assert plan['tracks'] == [] and 'not proof' in plan['coverage']['empty_means']
    empty = tmp_path/'old-helper'; empty.mkdir()
    s['client_effect_owner_states'] = owners.retain_capture(empty, None, 10, 20, 'a'*64, 'b'*64)
    with pytest.raises(ValueError, match='unavailable'): m.compile_mask_plan(s, ['58:283'])


def test_track_sample_budget_is_checked_before_output(tmp_path, monkeypatch):
    s = scene(tmp_path); monkeypatch.setattr(m, 'MAX_SAMPLES', 5)
    with pytest.raises(ValueError, match='budget'): m.compile_mask_plan(s, ['58:283'])
