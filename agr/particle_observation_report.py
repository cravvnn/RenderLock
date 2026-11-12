"""Normalize an already captured report; never read or launch a game process.

The report producer remains responsible for native-reader qualification and
truthful observations. Hashes bind its bytes, not authenticity or run cleanup.
The ordinary importer independently checks fresh replay/rig/event inputs.
"""
import copy
import hashlib
import json
from pathlib import Path

from . import particle_crossbow_owner as owner
from .particle_observations import FORMAT, MAX_BYTES, _object, _constant

MAX_REPORT_BYTES = 8 * 1024 * 1024


def read_report(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError('Particle capture report exceeds 8 MiB')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid particle capture report JSON') from exc
    return value, hashlib.sha256(raw).hexdigest()


def normalize_report(report, scene, instance_ids, report_sha256):
    """All requested birth observations must match; no default/empty evidence."""
    if (not isinstance(instance_ids, list) or not 1 <= len(instance_ids) <= 64
            or any(not isinstance(k, str) for k in instance_ids)
            or len(set(instance_ids)) != len(instance_ids) or not owner._sha(report_sha256)):
        raise ValueError('Choose 1..64 distinct recorded particle instance IDs')
    if (not isinstance(report, dict) or report.get('phase') != 'closed'
            or report.get('workflow_passed') is not True
            or type(report.get('exit_code')) is not int or report['exit_code'] != 0
            or report.get('cleanup_errors') != [] or report.get('error')
            or report.get('watchdog_fired') or report.get('monitor_error')
            or not isinstance(report.get('pending_runtime_restoration'), dict)
            or any(report['pending_runtime_restoration'].values())
            or any(report.get(k) is not True for k in ('config_unchanged_after_cleanup',
                'retained_baseline_unchanged', 'source_unchanged_after_run', 'product_unchanged_after_run'))
            or not owner._sha(report.get('replay_sha256'))
            or report['replay_sha256'] != scene.get('metadata', {}).get('demo_sha256')
            or not isinstance(report.get('seeks'), list) or not 1 <= len(report['seeks']) <= 64):
        raise ValueError('Particle observation preparation requires a matching closed successful capture report')
    rows = []
    try:
        for key in sorted(instance_ids):
            instances = [i for i in scene['particle_instances'] if i['id'] == key]
            if len(instances) != 1:
                raise ValueError('Requested particle instance is missing or ambiguous: ' + key)
            instance = instances[0]
            if instance['resource'] != owner.RESOURCE:
                raise ValueError('Only the qualified Crossbow observation profile can be prepared')
            # Runtime property groups have no recorded network instance ID.
            # Do not guess which group belongs to simultaneous identical births.
            same_birth = [i for i in scene['particle_instances']
                          if (i.get('resource'), i.get('entity_id'), i.get('created_tick')) ==
                          (owner.RESOURCE, instance['entity_id'], instance['created_tick'])]
            if len(same_birth) != 1:
                raise ValueError('Runtime observation cannot distinguish simultaneous matching particle births')
            seeks = [s for s in report['seeks'] if s.get('target') == instance['created_tick']]
            if len(seeks) != 1:
                raise ValueError('Exactly one birth-tick observation is required: ' + key)
            seek = seeks[0]
            sample = seek['cp_owner_capture']
            if (type(seek.get('after')) is not int or seek['after'] != instance['created_tick']
                    or type(sample.get('tick')) is not int or sample['tick'] != instance['created_tick']):
                raise ValueError('Observation was not captured at the recorded birth tick')
            runtime_handle = (instance['entity_handle'] >> 14) << 15 | (instance['entity_handle'] & 0x3fff)
            actors = [a for a in sample['actors'] if a['handle'] == runtime_handle
                      and a['entity_index'] == (instance['entity_handle'] & 0x3fff)]
            if len(actors) != 1:
                raise ValueError('Observed particle owner is missing or ambiguous')
            actor = actors[0]
            if (not isinstance(actor['nodes'], list) or not 1 <= len(actor['nodes']) <= 64
                    or any(not isinstance(n, dict) for n in actor['nodes'])):
                raise ValueError('Observed owner graph is malformed or exceeds bounds')
            effects = [e for e in actor['effects'] if e.get('resource', {}).get('name') == owner.RESOURCE]
            if len(effects) != 1:
                raise ValueError('Observed Crossbow property group is missing or ambiguous')
            effect = effects[0]
            slots = [(j, b) for j, b in enumerate(effect['bindings']) if b['cp'] == 0]
            if len(slots) != 1 or not isinstance(effect['raw'], str) or len(effect['raw']) != 176 * len(effect['bindings']):
                raise ValueError('Observed Crossbow CP records are incomplete or ambiguous')
            slot, cp0 = slots[0]
            if cp0['mode'] != 1 or cp0['owner'] != runtime_handle:
                raise ValueError('Observed CP0 decode differs from the qualified owner/mode')
            body = actor['body_model']
            if body['model'] != owner.MODEL.removesuffix('_c'):
                raise ValueError('Observed owner model differs from the qualified profile')
            axes = sample['runtime_axes']
            capture = dict(schema_version=1, policy=owner.POLICY,
                client_sha256=sample['client_sha256'], particles_sha256=sample['particles_sha256'],
                resource=owner.RESOURCE, entity_id=instance['entity_id'], tick=sample['tick'],
                demo_sha256=report['replay_sha256'], raw_capture_sha256=report_sha256,
                model=owner.MODEL, model_sha256=owner.MODEL_SHA256,
                coherent=body['coherent'], identity_flags=body['identity_flags'], model_method=body['method'],
                runtime_owner_handle=actor['handle'], collection_owner_handle=cp0['collection_owner'],
                skeleton_vtable_rva=body['skeleton_vtable_rva'], body_component_type_index=body['body_component_type_index'],
                body_component_offset=body['body_component_offset'], owner_srt=copy.deepcopy(body['owner_srt']),
                runtime_axes_rvas=copy.deepcopy(axes['rvas']), runtime_axes_raw=copy.deepcopy(axes['raw']),
                runtime_axes_coherent=axes['coherent'], graph_coherence_policy=actor['graph_coherence_policy'],
                graph_coherent=actor['graph_coherent'], child_model_inputs_complete=actor['child_model_inputs_complete'],
                eligible_render_children=actor['eligible_render_children'], graph_nodes=len(actor['nodes']),
                graph_sha256=owner.digest(actor['nodes']), cp0_record=effect['raw'][slot * 176:(slot + 1) * 176],
                collection_record=cp0['collection_record_raw'])
            owner.validate_capture(capture, scene, instance)
            rows.append(dict(instance_id=key, capture=capture))
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError('Capture report lacks the qualified observation fields') from exc
    document = dict(format=FORMAT, schema_version=1, captures=rows)
    if len(json.dumps(document, allow_nan=False).encode('utf-8')) > MAX_BYTES:
        raise ValueError('Prepared observations exceed the importer bounds')
    return document
