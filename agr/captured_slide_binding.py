"""Bind one retained raw slide cohort to fresh owner/rig inputs.

The legacy fingerprint is frozen, not rewritten. The binding verifies the
source unchanged and compares only inputs consumed by complete captured arrays.
It does not admit a renderer, qualify a new native profile or authenticate data.
"""
import copy
import hashlib
import json
from pathlib import Path
from .simulated_effect_package import scene_identity, model_constraints_identity
from .replay_capture import canonical_sha256

POLICY='agr_retained_slide_owner_binding_v1'
OWNER='56:895'
SOURCE_ID='f6cb0716f8b46ad90e12e44d287621f53cce3c4d0b500034d3f09e08e5be0074'
DEMO='fe409c16fa6f8b003e4b8192a8c602dda1a78f156d91da0db1a796b142782260'
SOURCE_FILES={
 'model_file':'dad18a08283f28950d90c3c83d902a0efd846ccddba513040bb37d03c31af6fd',
 'skeleton_file':'86f7f8d57c6b1c84322246ef5cba48ebf9493e0adb6f58389d539d2fbf21224d',
 'pose_file':'ad67dcfce4db0bb0fb562a63da623608b4a8e1f2c5eb58acd5a1c797df323cc0'}
EFFECT_ASSETS_SOURCE='318e0578617c38cf8b47572ee96df6473af22924a95f0c6cd3c5508fd14a184c'
EFFECT_ASSETS_TARGET='0c1d5e784bfdc201eaa1c34560cdeff13f10cbbb1c4d67c6e0c3172d8ad77267'

def _source_scene_identity(scene, entity_ids):
    entities = {str(e['id']): e for e in scene['entities']}
    rows = []
    for identity in sorted(entity_ids):
        if identity not in entities:
            raise ValueError('Particle entity missing from scene: ' + identity)
        e = entities[identity]
        row = {key: e.get(key) for key in ('id', 'hero_id', 'model_handle', 'samples',
               'model_yaw_degrees', 'pose_basis_yaw_degrees', 'model_scale', 'model_local_transform')}
        if 'render_root_yaw_degrees' in e:
            # Preserve the identity of older authored scenes that have no
            # explicit render-root basis, while invalidating stale simulations
            # whenever a newly declared particle-root transform changes.
            row['render_root_yaw_degrees'] = e['render_root_yaw_degrees']
        for field in ('model_file', 'skeleton_file', 'pose_file'):
            path = Path(e.get(field, ''))
            if not path.is_file():
                raise ValueError('Particle binding requires existing ' + field)
            with path.open('rb') as stream:
                row[field] = hashlib.file_digest(stream, 'sha256').hexdigest()
        if e.get('model_constraints') is not None:
            # Point-follow attachments consume constrained bones too; this is
            # not specific to particles that sample the complete body model.
            row['model_constraints'] = model_constraints_identity(e['model_constraints'])
        if e.get('secondary_motion_file'):
            with Path(e['secondary_motion_file']).open('rb') as stream:
                row['secondary_motion_file'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        rows.append(row)
    payload = {key: scene[key] for key in ('start_tick', 'end_tick', 'tick_rate')}
    if 'particle_history' in scene:
        payload['particle_history'] = scene['particle_history']
    payload.update(entities=rows, coordinates=scene.get('metadata', {}).get('coordinate_system'),
                   demo_sha256=scene.get('metadata', {}).get('demo_sha256'))
    if 'particle_instances' in scene:
        # The same rig tracks can carry different attachment/lifecycle events.
        # A cached simulation must not survive a change to those recorded inputs.
        payload['particle_instances'] = [instance for instance in scene['particle_instances']
                                         if instance.get('entity_id') in entity_ids]
    if 'network_projectiles' in scene:
        payload['network_projectiles']=scene['network_projectiles']
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def require(ok, reason):
    if not ok:
        raise ValueError('Captured slide binding: ' + reason)


def _owner(scene):
    rows = [e for e in scene['entities'] if e['id'] == OWNER]
    require(len(rows) == 1, 'owner is missing or ambiguous')
    return rows[0]


def _file_pins(entity):
    for name, expected in SOURCE_FILES.items():
        path = Path(entity[name])
        require(path.is_file() and 0 < path.stat().st_size <= 64 * 1024**2,
                'owner asset is missing or exceeds bounds: ' + name)
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        require(actual == expected, 'owner asset bytes changed: ' + name)


def bind_retained_owner(source, target):
    """Produce immutable evidence for the renderer's later independent checks.

    Full measured frame arrays do not consume unrelated network particle or
    projectile events. Those stay untouched in each scene. Warmup render-color
    metadata is not an input to the byte-identical evaluated bone-pose file.
    """
    try:
        a, b = _owner(source), _owner(target)
        require(all(not e.get('secondary_motion_file') and not e.get('model_tracks') for e in (a, b)),
                'additional owner motion/model providers require qualification')
        _file_pins(a)
        _file_pins(b)
        require(_source_scene_identity(source, [OWNER]) == SOURCE_ID,
                'original retained scene identity changed')
        for scene in (source, target):
            require(scene['metadata']['demo_sha256'] == DEMO
                    and type(scene['start_tick']) is int and type(scene['end_tick']) is int
                    and (scene['start_tick'], scene['end_tick'], scene['tick_rate']) == (17920, 18240, 64)
                    and type(scene['particle_history']['schema_version']) is int
                    and scene['particle_history']['schema_version'] == 1
                    and type(scene['particle_history']['start_tick']) is int
                    and scene['particle_history']['start_tick'] == 17858,
                    'replay, inclusive range or recovered history changed')
        require(canonical_sha256(source['metadata']['coordinate_system']) ==
                canonical_sha256(target['metadata']['coordinate_system']),
                'world coordinate basis changed')
        for field in ('id', 'hero_id', 'model_handle', 'model_yaw_degrees', 'pose_basis_yaw_degrees',
                      'render_root_yaw_degrees', 'model_scale', 'model_local_transform'):
            require(canonical_sha256(a.get(field)) == canonical_sha256(b.get(field)),
                    'owner basis/presence changed: ' + field)
        # The captured output and fresh warmup both keep the owner present for
        # every output tick. The latter may advertise the recovered prefix too.
        output_presence = [{'start_tick': 17920, 'end_tick_exclusive': 18241}]
        warmup_presence = [{'start_tick': 17858, 'end_tick_exclusive': 18241}]
        require(a.get('observed_intervals') == output_presence
                and b.get('observed_intervals') in (output_presence, warmup_presence),
                'owner output presence changed')
        require(len(a['samples']) == len(b['samples']) == 383, 'complete owner history is missing')
        for tick, left, right in zip(range(17858, 18241), a['samples'], b['samples']):
            require(type(left['tick']) is int and left['tick'] == tick
                    and type(right['tick']) is int and right['tick'] == tick,
                    'owner history is not complete and consecutive')
            left, right = copy.deepcopy(left), copy.deepcopy(right)
            if tick < 17920 and left.get('network_render_state') != right.get('network_render_state'):
                require(left.get('network_render_state') == {'material_group': 0}
                        and right.get('network_render_state') == {
                            'rgba8_packed': 0xffffffff, 'mode': 0, 'material_group': 0},
                        'unqualified warmup render metadata difference')
                left.pop('network_render_state')
                right.pop('network_render_state')
            require(canonical_sha256(left) == canonical_sha256(right),
                    'owner sample changed at tick ' + str(tick))
        left, right = copy.deepcopy(a['model_constraints']), copy.deepcopy(b['model_constraints'])
        lp, rp = left.pop('preparation_dependencies'), right.pop('preparation_dependencies')
        require(lp.pop('effect_assets.py') == EFFECT_ASSETS_SOURCE
                and rp.pop('effect_assets.py') == EFFECT_ASSETS_TARGET
                and canonical_sha256(lp) == canonical_sha256(rp)
                and canonical_sha256(left) == canonical_sha256(right),
                'authored constraints or qualified preparation dependencies changed')
        value = dict(schema_version=1, policy=POLICY, entity_id=OWNER,
                     source_scene_identity=SOURCE_ID, target_scene_identity=scene_identity(target, [OWNER]),
                     demo_sha256=DEMO, range_inclusive=[17920, 18240], history_start=17858,
                     owner_files=dict(SOURCE_FILES),
                     scope='Identical owner motion, evaluated poses and assets for complete retained slide arrays; native/frame/material admission remains separate')
        return dict(value, content_sha256=canonical_sha256(value))
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError('Captured slide binding inputs are incomplete') from exc


def validate_retained_binding(binding, source, target):
    require(binding == bind_retained_owner(source, target), 'binding contents or fresh inputs changed')
