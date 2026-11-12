import copy
import hashlib

import pytest

from agr.particle_hitbox_transport import prepare_hitbox_input


def inputs():
    inventory = dict(schema_version=1, model='models/mina.vmdl',
        sources=[dict(route='model', resource='models/mina.vmdl', sha256='a'*64)],
        model_bone_names=['root'], hitbox_sets=[dict(name='core', boxes=[dict(
            index=0, bone='root', model_bone_index=0, shape='Box',
            min=[-1, -2, -3], max=[1, 2, 3], radius=0)])])
    pose = dict(entity_id='58:283', tick=10, control_point=1,
        resource=inventory['model'], resource_sha256='a'*64, names=['root'],
        transforms=[[5, 6, 7, 1, 0, 0, 0, 1]])
    return inventory, pose


def test_frame_has_native_bounds_and_same_header_for_moving_pose():
    inventory, pose = inputs()
    original = copy.deepcopy((inventory, pose))
    header, frame = prepare_hitbox_input(inventory, pose, entity_id='58:283', tick=10, control_point=1)
    assert header['model'] == 'models/mina.vmdl_c'
    canonical = '\n'.join(str(header[k]) for k in ('control_point', 'entity_id', 'model',
        'model_sha256', 'bone_order_sha256'))
    assert header['binding_id'] == hashlib.sha256(canonical.encode()).hexdigest()
    assert frame['sets']['core'][0]['min'] == [-1, -2, -3]
    assert frame['sets']['core'][0]['transform'] == [5, 6, 7, 1, 0, 0, 0, 1]
    assert (inventory, pose) == original
    pose['tick'] = 11; pose['transforms'][0][0] = 10
    other_header, other = prepare_hitbox_input(inventory, pose, entity_id='58:283', tick=11, control_point=1)
    assert header == other_header and other['sets']['core'][0]['transform'][0] == 10
    assert frame['sets']['core'][0]['transform'][0] == 5


@pytest.mark.parametrize('change', ['cp', 'tick', 'entity', 'hash', 'bone_order'])
def test_different_snapshot_binding_is_refused(change):
    inventory, pose = inputs()
    if change == 'cp': pose['control_point'] = 0
    elif change == 'tick': pose['tick'] = 11
    elif change == 'entity': pose['entity_id'] = '58:284'
    elif change == 'hash': pose['resource_sha256'] = 'b'*64
    elif change == 'bone_order': pose['names'] = ['different']
    with pytest.raises(ValueError):
        prepare_hitbox_input(inventory, pose, entity_id='58:283', tick=10, control_point=1)
