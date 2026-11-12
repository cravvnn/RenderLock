"""Identity-bound birth-time client attachment observations.

The capture producer must verify native acquisition, class eligibility and graph
coherence. This transport prevents stale/cross-entity use; it does not authenticate
edited captures. Only observed parent-body fallback is admitted, never a guessed
wearable, transform, hitbox or model provider. Missing captures stay unsupported.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re

POLICY = 'observed_parent_attachment_selection_v1'
CLIENT_SHA256 = '802d2b73124af86936875b0ccdf4862d95281490faff5ff0b03deea02cf22b45'
CURRENT_CLIENT_SHA256 = '6a7c6d40ed70011884054405846e7a5259fff51578714ffa864cbe80ab527627'
CLIENT_6860_SHA256 = '2007fddaf9f48211e1ed188aff3c31ad47ba667cbaace16f5dd30e9376b884c7'
# Each module has independent named-layout, native-selector and coherent live
# birth-graph evidence. A different build still needs its own qualification.
QUALIFIED_CLIENT_SHA256S = frozenset((CLIENT_SHA256, CURRENT_CLIENT_SHA256, CLIENT_6860_SHA256))

def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None

def _identity(scene, instance):
    from .captured_slide_binding import _source_scene_identity as scene_identity
    if not _sha(scene.get('metadata', {}).get('demo_sha256')):
        raise ValueError('Client attachment capture requires replay SHA-256')
    view = dict(scene, particle_instances=[instance])
    return scene_identity(view, [instance['entity_id']])

def _validate_capture(capture, scene, instance):
    if (not isinstance(capture, dict) or type(capture.get('schema_version')) is not int or capture['schema_version'] != 1
            or capture.get('policy') != POLICY or capture.get('client_sha256') not in QUALIFIED_CLIENT_SHA256S
            or not _sha(capture.get('raw_capture_sha256'))):
        raise ValueError('Unsupported client attachment capture or module identity')
    entities = [e for e in scene['entities'] if e.get('id') == instance.get('entity_id')]
    if len(entities) != 1:
        raise ValueError('Client attachment owner is missing or ambiguous')
    entity = entities[0]
    model = entity.get('model_constraints') or {}
    if not model.get('resource') or not _sha(model.get('resource_sha256')):
        raise ValueError('Client attachment capture requires the exact authored model')
    if (capture.get('entity_id') != instance.get('entity_id')
            or type(capture.get('tick')) is not int or capture['tick'] != instance.get('created_tick')
            or capture.get('model') != model['resource']
            or capture.get('model_sha256') != model['resource_sha256']
            or capture.get('graph_coherence_policy') != 'double_read_links_class_flags_v1'
            or capture.get('graph_coherent') is not True
            or type(capture.get('eligible_render_children')) is not int
            or capture['eligible_render_children'] != 0
            or type(capture.get('graph_nodes')) is not int or not 1 <= capture['graph_nodes'] <= 64
            or not _sha(capture.get('graph_sha256'))):
        raise ValueError('Client attachment capture lacks matching coherent parent selection')
    # This qualification is only the exact owner's creation-time graph. It does
    # not admit later rebinding, cross-entity selection or custom-origin creation.
    for event in instance.get('events', []):
        if event.get('type') != 'UPDATE_ENT':
            continue
        binding = event.get('data', {}).get('update_particle_ent') or {}
        if event.get('tick') != capture['tick'] or binding.get('entity_handle') != instance.get('entity_handle'):
            raise ValueError('Client attachment capture cannot qualify a different target or binding tick')

def bind_client_child_capture(capture, scene, instance):
    _validate_capture(capture, scene, instance)
    payload = dict(schema_version=1, policy=POLICY, instance_id=instance['id'],
                   scene_identity=_identity(scene, instance), capture=copy.deepcopy(capture))
    return dict(payload, content_sha256=_digest(payload))

def validate_client_child_capture(package, scene, instance):
    if (not isinstance(package, dict) or type(package.get('schema_version')) is not int or package['schema_version'] != 1
            or package.get('policy') != POLICY or package.get('instance_id') != instance.get('id')
            or package.get('scene_identity') != _identity(scene, instance)
            or package.get('content_sha256') != _digest({k:v for k,v in package.items() if k != 'content_sha256'})):
        raise ValueError('Client attachment capture replay, instance, rig or contents changed')
    _validate_capture(package.get('capture'), scene, instance)
    return package['capture']
