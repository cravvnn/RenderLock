"""Separate requested output timing from explicitly recovered particle history."""
from __future__ import annotations
import copy
from .scene_package import slice_scene


def history_start(data):
    descriptor = data.get('particle_history')
    if descriptor is None:
        return data['start_tick']
    if not isinstance(descriptor, dict):
        raise ValueError('Invalid recovered particle history descriptor')
    start = descriptor.get('start_tick')
    if (descriptor.get('schema_version') != 1 or type(start) is not int
            or not 0 <= start <= data['start_tick']):
        raise ValueError('Invalid recovered particle history range')
    return start


def select_output_with_history(data, start):
    """Retain the evaluated root tracks; frame 1 still means requested start."""
    result = slice_scene(data, start, data['end_tick'])
    originals = {e['id']: e for e in data['entities']}
    for entity in result['entities']:
        original = originals[entity['id']]
        entity['samples'] = copy.deepcopy(original['samples'])
        # Availability flags belong to the restored samples, including earlier
        # unavailable inputs. Selected-output diagnostics/coverage remain sliced.
        if 'pose_unavailable_ticks' in original:
            entity['pose_unavailable_ticks'] = list(original['pose_unavailable_ticks'])
    result['particle_history'] = {'schema_version': 1, 'start_tick': data['start_tick'],
                                  'scope': 'Explicit recovered motion; older effects remain unsupported'}
    result['coverage']['particle_history'] = copy.deepcopy(result['particle_history'])
    for instance in result.get('particle_instances', []):
        instance['created_frame'] = instance['created_tick'] - start + 1
        for event in instance.get('events', []):
            event['frame'] = event['tick'] - start + 1
    return result


def require_history_samples(instance, end, root_ticks, pose_ticks):
    """Do not extrapolate roots or bone poses into missing history."""
    for tick in range(instance['created_tick'], end + 1):
        if tick not in root_ticks or tick not in pose_ticks:
            raise ValueError(f'Incomplete particle attachment motion at tick {tick}')
