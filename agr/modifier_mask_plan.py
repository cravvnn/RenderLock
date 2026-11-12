"""Project sustained cosmetic mask modifiers and network owner gates.

This is an input plan, not a particle renderer or an effect-lifetime oracle.
In particular, a false gate is a native stop REQUEST: live observations retain
Mina's model on the death tick. No disappearance, seed or birth is invented.
The adjacent model covers publication; imported serial/capture models cover
the preceding fold/validation. All work is synchronous on immutable inputs.
"""
import copy
import math
import struct
from .client_modifier_writes import integer, _varint
from .client_effect_owner_states import validate_view as validate_owners
from .modifier_serial_state import fold_retained
from .replay_capture import canonical_sha256

POLICY = 'agr_modifier_mask_inputs_v1'
MASK_TOKEN = 1591258939
ROOT = 'particles/event/halloween/halloween_mask.vpcf'
MODEL_CHILD = 'particles/event/halloween/halloween_mask_model.vpcf'
CLIENT_SHA = 'd0341ce77398614af905ef8669d328fd4d45958282acef66789a250492e466b5'
CHILD_SHA = '083022d215fee38dd81cb7934eae3b8a14bbd50f75195f753470f487d25463c2'
MAX_TICKS = 10000
MAX_TRACKS = 64
MAX_SAMPLES = 200000


def require(condition, message):
    if not condition:
        raise ValueError('Modifier mask inputs: ' + message)


def _sustained_wire_profile(row):
    """Only the audited indefinite, non-aura cosmetic creation profile.

    Proto2 CModifierTableEntry.duration defaults to -1 when absent. We qualify
    absence here, not arbitrary finite/negative durations, attributes or aura
    semantics. Full wire data is retained by the extractor; unknown fields are
    refused rather than guessed. Header parsing was already independently
    checked against the retained bytes. These float fields are provenance only.
    """
    data = bytes(row['wire_bytes']); at = 0; seen = set()
    expected = {1: 0, 2: 0, 3: 0, 4: 0, 7: 5, 9: 0, 15: 5, 25: 0}
    while at < len(data):
        tag, at = _varint(data, at); field, wire = tag >> 3, tag & 7
        require(field in expected and wire == expected[field] and field not in seen,
                'mask duration/aura/flags or duplicate fields require qualification')
        seen.add(field)
        if wire == 0:
            value, at = _varint(data, at)
            if field == 9:
                require(value == row['header']['parent'], 'mask caster differs from owner')
        else:
            require(at+4 <= len(data) and math.isfinite(struct.unpack_from('<f', data, at)[0]),
                    'mask time fields are nonfinite or truncated')
            at += 4
        require(at <= len(data), 'truncated mask payload')
    require(seen == set(expected), 'mask creation profile is incomplete')


def network_external_mask_gate(row):
    """Known network inputs to the qualified external-camera owner predicate.

    Word1 bits1/2 suppress the mask. Word3's local-view bit and view convar only
    suppress when local pawn == this owner; an external Blender camera has no
    local pawn. Disabled-state words are deliberately not ORed into this test.
    Owner PVS/presence is a separate renderer availability input.
    """
    life = row['life_state']
    require(integer(life, 0, 255), 'owner life state is unavailable')
    arrays = [row[name] for name in ('enabled_state_words', 'predicted_state_words')]
    require(all(isinstance(a, list) and len(a) >= 4 for a in arrays),
            'complete owner gate words are unavailable')
    return life == 0 and ((arrays[0][1] | arrays[1][1]) & 6) == 0


def compile_mask_plan(scene, selected_ids):
    """Require complete sustained pre-shot state and dense owner inputs.

    The output can drive a separately qualified attached-model backend. It must
    not be counted as rendered coverage. Raw availability/empty capture does not
    establish absence of client-created effects. Starts, removals, reapplications
    or other writes to a target serial require a separate activation backend.
    """
    start, end = scene.get('start_tick'), scene.get('end_tick')
    require(integer(start, 1, 2**31-2) and integer(end, start, 2**31-2)
            and end-start+1 <= MAX_TICKS, 'inclusive tick range exceeds plan bound')
    require(isinstance(selected_ids, (list, tuple, set)) and len(selected_ids) <= MAX_TRACKS
            and all(isinstance(v, str) for v in selected_ids)
            and len(set(selected_ids)) == len(selected_ids), 'selected owner identities differ')
    ids = set(selected_ids)
    entities = scene.get('entities')
    require(isinstance(entities, list), 'scene entities missing')
    chosen = [e for e in entities if e.get('id') in ids]
    require(len(chosen) == len(ids) and len({e['id'] for e in chosen}) == len(ids),
            'selected scene owner identity is missing or duplicated')
    entities = {e['id']: e for e in chosen}
    modifiers = scene.get('client_modifier_writes')
    owner_envelope = scene.get('client_effect_owner_states')
    begin = fold_retained(modifiers, scene, through_tick=start)
    finish = fold_retained(modifiers, scene)
    validate_owners(owner_envelope, demo_sha256=scene.get('metadata', {}).get('demo_sha256'),
                    start_tick=start, end_tick=end)
    require(owner_envelope['availability'] == 'available' and owner_envelope['capture'] is not None,
            'owner capture is unavailable')
    require(modifiers['provenance']['helper_sha256'] == owner_envelope['provenance']['helper_sha256'],
            'modifier and owner captures use different extraction helpers')

    # Match canonical index+serial, never an entity slot or a UI character name.
    def identity(header):
        parent = header.get('parent')
        require(integer(parent, 0, 0xffffff-1), 'mask parent identity is unavailable')
        return f'{parent & 0x3fff}:{parent >> 14}'

    candidates = {}
    for event in finish['events']:
        state = event['state']
        if state and state['header'].get('modifier_subclass') == MASK_TOKEN:
            owner = identity(state['header'])
            if owner in ids:
                candidates[event['serial_number']] = owner
    require(len(candidates) <= MAX_TRACKS and len(candidates)*(end-start+1) <= MAX_SAMPLES,
            'mask track/sample budget exceeded')
    beginning = {s['header']['serial_number']: s for s in begin['states']}
    ending = {s['header']['serial_number']: s for s in finish['states']}
    owners = {}
    for row in owner_envelope['capture']['rows']:
        if row['entity_id'] in ids and start <= row['tick'] <= end:
            require(row['hero_id'] == entities[row['entity_id']]['hero_id'], 'owner hero identity differs')
            owners[row['entity_id'], row['tick']] = row
    tracks = []
    for serial, owner in sorted(candidates.items()):
        state = beginning.get(serial)
        require(state is not None and state == ending.get(serial)
                and state['first_active_tick'] < start, 'mask has an unqualified start/update/stop')
        events = [e for e in finish['events'] if e['serial_number'] == serial]
        require(len(events) == 1 and events[0]['action'] == 'raw_first_active',
                'mask reapplication or update requires activation qualification')
        source = modifiers['capture']['rows'][state['first_active_row_index']]
        _sustained_wire_profile(source)
        header = state['header']; choice = header['int1']
        require(integer(choice, 0, 5), 'mask selector is unavailable or outside authored choices')
        samples = entities[owner].get('samples')
        require(isinstance(samples, list) and len(samples) <= MAX_SAMPLES
                and all(isinstance(s, dict) and integer(s.get('tick'), 0, 2**31-2) for s in samples),
                'owner scene samples missing or outside bound')
        actor_ticks = [s for s in samples if start <= s['tick'] <= end]
        require([s['tick'] for s in actor_ticks] == list(range(start, end+1)),
                'owner scene samples are not dense, ordered and inclusive')
        gates = []
        for sample in actor_ticks:
            tick = sample['tick']; row = owners.get((owner, tick))
            require(row is not None, 'owner gate snapshot is missing')
            require(type(sample.get('active')) is bool and type(sample.get('present')) is bool
                    and sample['active'] == row['active'] == sample['present'],
                    'owner scene availability differs from extraction')
            gates.append(dict(tick=tick, native_gate_request=network_external_mask_gate(row),
                              owner_present=sample['present']))
        tracks.append(dict(serial_number=serial, entity_id=owner, selector=choice,
            raw_first_observed_tick=state['first_active_tick'],
            source_row_index=state['first_active_row_index'], gate_samples=gates))
    return dict(schema_version=1, policy=POLICY, start_tick=start, end_tick=end,
        root_resource=ROOT, model_child_resource=MODEL_CHILD, child_definition_sha256=CHILD_SHA,
        required_client_sha256=CLIENT_SHA, tracks=tracks,
        source_identity=copy.deepcopy(dict(modifiers=modifiers['provenance'], owners=owner_envelope['provenance'])),
        input_sha256=canonical_sha256(dict(modifiers=modifiers, owners=owner_envelope)),
        coverage=dict(status='inputs_only', rendered_instances=0, actual_model_lifetime='unqualified',
            empty_means='No qualified sustained masks found; not proof of no client effects'),
        limitations=['First raw observation is not effect birth; no seed/age is synthesized.',
            'Native gate requests do not establish stop/endcap scheduling or object visibility.',
            'External-camera network owner projection only; local/first-person camera gating is unqualified.',
            'Parent mask root ambient/sugar-rush children, phase, model attachment and shading require separate qualification.'])
