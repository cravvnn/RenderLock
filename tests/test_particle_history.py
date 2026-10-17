import copy
import pytest
from agr.particle_history import history_start, select_output_with_history, require_history_samples
from agr.scene_package import slice_scene


def scene():
    return {'schema_version': 1, 'start_tick': 10, 'end_tick': 15, 'tick_rate': 64,
            'entities': [{'id': '1:2', 'samples': [
                {'tick': t, 'position': [t, 0, 0], 'body_angles': [0, 0, 0]} for t in range(10, 16)]}],
            'particle_instances': [{'created_tick': 11, 'events': [{'tick': 11}]}]}


def test_output_timing_retains_motion_and_rebases_event_frames_without_mutation():
    data = scene(); original = copy.deepcopy(data)
    result = select_output_with_history(data, 13)
    assert (result['start_tick'], result['end_tick'], history_start(result)) == (13, 15, 10)
    assert [s['tick'] for s in result['entities'][0]['samples']] == list(range(10, 16))
    assert result['particle_instances'][0]['created_frame'] == -1
    assert result['particle_instances'][0]['events'][0]['frame'] == -1
    assert data == original
    clipped = slice_scene(result, 14, 15)
    assert history_start(clipped) == 14 and 'particle_history' not in clipped
    assert [s['tick'] for s in clipped['entities'][0]['samples']] == [14, 15]


@pytest.mark.parametrize('field', ['root', 'pose'])
def test_missing_motion_cannot_be_extrapolated(field):
    root, pose = set(range(10, 16)), set(range(10, 16))
    require_history_samples({'created_tick': 10}, 15, root, pose)
    (root if field == 'root' else pose).remove(12)
    with pytest.raises(ValueError, match='tick 12'):
        require_history_samples({'created_tick': 10}, 15, root, pose)


@pytest.mark.parametrize('start', [-1, True, 11, None])
def test_invalid_history_range_rejected(start):
    data = scene(); data['particle_history'] = {'schema_version': 1, 'start_tick': start}
    with pytest.raises(ValueError, match='history range'):
        history_start(data)
