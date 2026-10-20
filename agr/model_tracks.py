"""Separate recorded model runs while preserving one network pawn/root.

Model tracks are render/rig segments, never new players or inferred lifetimes.
The original network identity and per-tick transforms remain authoritative.
"""
from __future__ import annotations
import copy
from .model_states import model_states

DERIVED = ('model_file', 'pose_file', 'skeleton_file', 'secondary_motion_file',
           'asset_objects', 'asset_collection', 'rig_object', 'model_local_transform',
           'model_constraints', 'model_mesh_groups', 'model_material_groups',
           'animation_status', 'pose_unavailable_ticks')


def actor_id(entity):
    track = entity.get('model_track')
    return track['source_entity_id'] if track is not None else entity['id']


def expand_model_tracks(data):
    """Split only actual model changes, retaining all selected movement samples."""
    expanded = []
    for entity in data['entities']:
        if entity.get('model_track') is not None:
            expanded.append(entity)
            continue
        states = model_states(entity)
        if len({s['model_handle'] for s in states}) <= 1:
            expanded.append(entity)
            continue
        runs = []
        for state in states:
            if runs and runs[-1]['model_handle'] == state['model_handle']:
                runs[-1]['end_tick_exclusive'] = state['end_tick_exclusive']
            else:
                runs.append(dict(state))
        for index, run in enumerate(runs):
            track = copy.deepcopy(entity)
            for key in DERIVED:
                track.pop(key, None)
            # Keep the original ID on the first model track for old consumers;
            # all consumers of a pawn identity must still use actor_id().
            track['id'] = (entity['id'] if index == 0 else
                           f"{entity['id']}@model:{run['start_tick']}")
            track['model_handle'] = run['model_handle']
            track['samples'] = [s for s in track['samples']
                                if run['start_tick'] <= s['tick'] < run['end_tick_exclusive']]
            ticks = {s['tick'] for s in track['samples']}
            track['observed_intervals'] = []
            for tick in sorted(ticks):
                intervals = track['observed_intervals']
                if intervals and intervals[-1]['end_tick_exclusive'] == tick:
                    intervals[-1]['end_tick_exclusive'] = tick + 1
                else:
                    intervals.append({'start_tick':tick, 'end_tick_exclusive':tick+1})
            track['model_track'] = dict(run, schema_version=1, source_entity_id=entity['id'],
                                       initial_model_track=index == 0)
            expanded.append(track)
    data['entities'] = expanded
    logical_entities(expanded)  # Fail before asset work if track identity is invalid.
    return data


def logical_entities(entities):
    """Validate and merge model-track root samples; never merge different pawns."""
    groups = {}
    for entity in entities:
        descriptor = entity.get('model_track')
        if descriptor is not None:
            if not isinstance(descriptor, dict) or descriptor.get('schema_version') != 1:
                raise ValueError('Invalid recorded model-track schema')
            identity = descriptor.get('source_entity_id')
            if (type(entity.get('entity_index')) is not int or type(entity.get('entity_serial')) is not int
                    or identity != f"{entity['entity_index']}:{entity['entity_serial']}"):
                raise ValueError('Model tracks must retain the exact original pawn index and serial')
            start, end = descriptor.get('start_tick'), descriptor.get('end_tick_exclusive')
            if type(start) is not int or type(end) is not int or not 0 <= start < end:
                raise ValueError('Invalid recorded model-track interval')
            states = model_states(entity)
            if (not states or any(s['model_handle'] != entity.get('model_handle') for s in states)
                    or descriptor.get('model_handle') != entity.get('model_handle')
                    or any(not start <= s['tick'] < end for s in entity['samples'])):
                raise ValueError('Model-track samples differ from the recorded model/interval')
        else:
            identity = entity['id']
        groups.setdefault(identity, []).append(entity)
    result = {}
    for identity, tracks in groups.items():
        if len(tracks) == 1 and tracks[0].get('model_track') is None:
            result[identity] = dict(tracks[0])
            continue
        if len(tracks) > 1:
            if (any(t.get('model_track') is None for t in tracks)
                    or len({t.get('hero_id') for t in tracks}) != 1):
                raise ValueError('Conflicting logical pawn/model-track identity')
            ordered = sorted(tracks, key=lambda t:t['model_track']['start_tick'])
            if any(a['model_track']['end_tick_exclusive'] > b['model_track']['start_tick']
                   for a,b in zip(ordered,ordered[1:])):
                raise ValueError('Recorded model-track intervals overlap')
        root = dict(tracks[0], id=identity)
        root['samples'] = sorted((s for t in tracks for s in t['samples']), key=lambda s:s['tick'])
        ticks = [s['tick'] for s in root['samples']]
        if len(set(ticks)) != len(ticks):
            raise ValueError('Duplicate movement samples across model tracks')
        intervals = []
        for tick in ticks:
            if intervals and intervals[-1]['end_tick_exclusive'] == tick:
                intervals[-1]['end_tick_exclusive'] = tick + 1
            else:
                intervals.append({'start_tick':tick, 'end_tick_exclusive':tick+1})
        root['observed_intervals'] = intervals
        if any(t.get('model_track') is not None for t in tracks):
            if len(tracks) > 1:
                for key in DERIVED:
                    root.pop(key, None)
                root.pop('model_handle', None)
            root.pop('model_track', None)
            if len(tracks) > 1:
                root['model_tracks'] = [t['id'] for t in tracks]
        result[identity] = root
    return result
