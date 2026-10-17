"""Split topology/dynamic recipe contracts, independent of game assets."""
from copy import deepcopy

import pytest

from test_animation import REGISTRY, silver_binding_inputs
from agr.animation import _bind, AnimationError


def pack(items):
    value = offset = 0
    for item, width in items:
        value |= item << offset
        offset += width
    return list(value.to_bytes((offset + 7) // 8, 'little'))


def v3_inputs(sample_id=1, blend_id=6):
    raw, recorded, entry, skeleton = silver_binding_inputs()
    # Three tasks: two samples followed by their blend. Dependencies follow
    # their owning task ID in topology, never in the dynamic values stream.
    topology = pack([(2, 4), (3, 2), (sample_id, 4), (sample_id, 4),
                     (blend_id, 4), (0, 2), (1, 2)])
    raw['fields'].pop('m_nSerializePoseRecipeSizeAG2')
    raw['fields'].update(m_nSerializePoseRecipeVersionAG2=3,
                        m_nSerializePoseRecipeAG2ActiveSlot=2,
                        m_SerializePoseRecipeAG2Slots=32)
    raw['fields']['m_SerializePoseRecipeAG2Slots.2.m_topology'] = topology
    dynamic = pack([(100, 32), (0, 1), (1234, 16), (0, 1), (4321, 16), (128, 8), (0, 1)])
    # Capacity is retained across updates: nonzero bytes after task data are
    # not another task and must not be interpreted as an active recipe.
    raw['serialized_pose_recipe_array'] = dynamic + [0xff] * 16
    raw['fields']['m_SerializePoseRecipeAG2Dynamic'] = len(raw['serialized_pose_recipe_array'])
    return raw, recorded, entry, skeleton


def test_v3_binds_active_topology_and_dynamic_values():
    raw, recorded, entry, skeleton = v3_inputs()
    row = _bind(raw, 100, recorded, REGISTRY, entry, skeleton)
    assert row['complete_layout'] and row['fresh_recipe']
    assert [t['type_id'] for t in row['tasks']] == [1, 1, 6]
    assert [t['phase_u16'] for t in row['tasks'][:2]] == [1234, 4321]
    assert row['tasks'][2]['dependencies'] == [0, 1]
    assert row['tasks'][2]['weight_u8'] == 128
    assert row['consumed_bits'] == 75


def test_v3_dispatch_follows_recorded_registry_order():
    registry = REGISTRY.copy()
    registry[1], registry[6] = registry[6], registry[1]
    raw, recorded, entry, skeleton = v3_inputs(sample_id=6, blend_id=1)
    row = _bind(raw, 100, recorded, registry, entry, skeleton)
    assert [t['type_id'] for t in row['tasks']] == [1, 1, 6]


@pytest.mark.parametrize('mutation', ['bad_slot', 'lossy_string', 'missing_byte', 'forward_dependency', 'unknown_task', 'truncated_dynamic'])
def test_v3_rejects_malformed_recorded_data(mutation):
    raw, recorded, entry, skeleton = v3_inputs()
    key = 'm_SerializePoseRecipeAG2Slots.2.m_topology'
    if mutation == 'bad_slot': raw['fields']['m_nSerializePoseRecipeAG2ActiveSlot'] = 32
    elif mutation == 'lossy_string': raw['fields'][key] = '\ufffd'
    elif mutation == 'missing_byte': raw['serialized_pose_recipe_array'][5] = None
    elif mutation == 'forward_dependency': raw['fields'][key] = pack([(2,4),(3,2),(1,4),(1,4),(6,4),(2,2),(1,2)])
    elif mutation == 'unknown_task': raw['fields'][key] = pack([(1,4),(1,1),(15,4)])
    elif mutation == 'truncated_dynamic':
        raw['serialized_pose_recipe_array'] = [100,0,0,0]
        raw['fields']['m_SerializePoseRecipeAG2Dynamic'] = 4
    with pytest.raises((ValueError, TypeError, AnimationError)):
        _bind(raw, 100, recorded, REGISTRY, entry, skeleton)


def test_mask_case_alias_is_reported_without_mutating_skeleton():
    raw, recorded, entry, skeleton = v3_inputs()
    recorded[entry['skeleton']]['masks'][0] = 'Upper_Body'
    before = deepcopy(skeleton)
    row = _bind(raw, 100, recorded, REGISTRY, entry, skeleton)
    assert row['mask_name_aliases'] == [{'index':0, 'recorded':'Upper_Body', 'asset':'upper_body'}]
    assert skeleton == before


def test_case_alias_does_not_permit_mask_collisions():
    raw, recorded, entry, skeleton = v3_inputs()
    recorded[entry['skeleton']]['masks'] = ['root', 'ROOT']
    skeleton['m_maskDefinitions'] = [{'m_ID':'root'}, {'m_ID':'ROOT'}]
    with pytest.raises(AnimationError):
        _bind(raw, 100, recorded, REGISTRY, entry, skeleton)


def test_v3_keeps_supplied_cache_prehistory():
    from agr.animation import _trim_prehistory
    raw, recorded, entry, skeleton = v3_inputs()
    first = _bind(raw, 100, recorded, REGISTRY, entry, skeleton)
    later = dict(first, demo_tick=first['demo_tick'] + 10)
    assert _trim_prehistory([first, later], later['demo_tick'], later['demo_tick']) == [first, later]
