"""Evaluated Silence CP/hitbox inputs; no native selection or inferred poses."""
import copy
import math
from pathlib import Path

from .particle_silence_inputs import (prepare_observed_inputs, prepare_hitbox_frames,
    DEFINITIONS, PROFILE, require)


def capture_points(data, instance, entity, objects, scene, manifest_dir, attachments,
                   inventory, birth, child, equivalence=None):
    from .blender_particle_skinning import evaluated_snapshot_rig
    observed = prepare_observed_inputs(data, instance, birth, child, equivalence)
    with evaluated_snapshot_rig(objects.get('rig'), objects.get('root'), scene):
        return _capture(data, instance, entity, objects, scene, manifest_dir,
                        attachments, inventory, observed)


def _capture(data, instance, entity, objects, scene, manifest_dir, attachments, inventory, observed):
    import numpy as np
    from mathutils import Matrix, Quaternion, Vector
    from .blender_particle_model import capture_model_bones
    from .blender_particle_skinning import snapshot_rig_fingerprint
    from .blender_scene import tick_frame
    from .simulated_effect_package import model_constraints_identity
    from .particle_snapshot_provider import digest
    model = entity.get('model_constraints') or {}
    rig, root = objects['rig'], objects['root']
    require(root.get('agr_entity_id') == entity['id'] == instance['entity_id'], 'rig owner differs')
    require(inventory.get('model', '') + '_c' == model.get('resource')
        and inventory.get('model_bone_names') == model.get('names')
        and inventory.get('sources', [{}])[0].get('sha256') == model.get('resource_sha256'),
        'authored hitbox inventory differs from the rig model')
    sets = inventory.get('hitbox_sets')
    count = data['end_tick'] - instance['created_tick'] + 1
    require(isinstance(sets, list) and 0 < len(sets) <= 128
        and all(isinstance(s, dict) and isinstance(s.get('boxes'), list)
                and 0 < len(s['boxes']) <= 4096 for s in sets)
        and 0 < count <= 10000 and sum(len(s['boxes']) for s in sets) * count <= 200000,
        'complete bounded authored hitbox history required')
    required = sorted({box['bone'] for s in sets for box in s['boxes']})
    path = Path(entity.get('pose_file', ''))
    if not path.is_absolute():
        path = Path(manifest_dir) / path
    with np.load(path, allow_pickle=False) as poses:
        available = set(poses['ticks'].tolist()) - set(entity.get('pose_unavailable_ticks', []))
    root_ticks = {s['tick'] for s in entity['samples'] if s.get('active', True) and s.get('present', True)}
    captured = capture_model_bones(rig, model, scene, entity_id=entity['id'],
        start_tick=instance['created_tick'], end_tick=data['end_tick'], scene_start_tick=data['start_tick'],
        tick_rate=data['tick_rate'], available_ticks=available & root_ticks, required_names=required)
    selected = set(required)
    poses = [dict(entity_id=entity['id'], tick=int(tick), control_point=0,
        resource=inventory['model'], resource_sha256=model['resource_sha256'], names=captured['names'],
        transforms=[row.tolist() if name in selected else None for name, row in zip(captured['names'], rows)])
        for tick, rows in zip(captured['ticks'], captured['transforms'])]
    hitboxes = prepare_hitbox_frames(inventory, poses, entity_id=entity['id'],
        start_tick=instance['created_tick'], end_tick=data['end_tick'])
    binding = observed['bindings'][1]
    attachment = attachments[f"{entity['id']}/{binding['attachment']}"]
    definition = attachment['definition']
    require(attachment.get('entity_id') == entity['id'] and attachment.get('source_model') == model['resource']
        and definition.get('m_nInfluences') == 1
        and len(definition.get('m_influenceWeights', [])) >= 1
        and abs(definition['m_influenceWeights'][0] - 1) <= 1e-6
        and not definition.get('m_bIgnoreRotation')
        and len(definition.get('m_bInfluenceRootTransform', [])) >= 1
        and definition['m_bInfluenceRootTransform'][0] is False
        and len(definition.get('m_influenceNames', [])) >= 1,
        'exact single-bone parent-body attachment required')
    bone = definition['m_influenceNames'][0]
    require(bone in rig.pose.bones, 'attachment bone is absent from the evaluated rig')
    q = definition['m_vInfluenceRotations'][0]
    translation = definition['m_vInfluenceOffsets'][0]
    require(isinstance(q, list) and len(q) == 4 and isinstance(translation, list) and len(translation) == 3
        and all(type(v) in (int, float) and math.isfinite(v) for v in q + translation)
        and abs(sum(v*v for v in q) - 1) <= 2e-5, 'finite rigid authored attachment offset required')
    offset = Matrix.LocRotScale(Vector(translation) * .0254,
        Quaternion((q[3], *q[:3])), Vector((1, 1, 1)))
    yaw = entity.get('render_root_yaw_degrees', 0)
    require(type(yaw) in (int, float) and math.isfinite(yaw), 'finite render-root yaw required')
    basis = Matrix.Rotation(math.radians(yaw % 360), 4, 'Z')
    points = []
    literal = observed['bindings'][2]['updates']
    for hitbox in hitboxes['frames']:
        tick = hitbox['tick']
        frame = tick_frame(tick, data['start_tick'], data['tick_rate'], scene.render.fps / scene.render.fps_base)
        scene.frame_set(math.floor(frame), subframe=frame - math.floor(frame))
        samples = []
        for cp, matrix in ((0, root.matrix_world @ basis), (1, rig.matrix_world @ rig.pose.bones[bone].matrix @ offset)):
            array = np.asarray(matrix, dtype=float)
            linear = array[:3, :3]
            require(np.isfinite(array).all() and np.max(abs(linear.T @ linear - np.eye(3))) <= 2e-4
                and abs(np.linalg.det(linear) - 1) <= 2e-4, 'finite proper rigid control-point transform required')
            position, rotation, _ = matrix.decompose()
            samples.append(dict(index=cp, position=[v / .0254 for v in position],
                rotation=[rotation.x, rotation.y, rotation.z, rotation.w]))
        update = next(row for row in reversed(literal) if row['tick'] <= tick)
        samples.append(dict(index=2, position=copy.deepcopy(update['position']), rotation=copy.deepcopy(update['rotation'])))
        points.append(dict(hitbox, position=samples[0]['position'], rotation=samples[0]['rotation'],
                           extra_control_points=samples[1:]))
    provider = dict(profile=PROFILE, policy=hitboxes['policy'], bindings=hitboxes['bindings'],
        entity_id=entity['id'], resource=model['resource'], resource_sha256=model['resource_sha256'],
        model_constraints_sha256=model_constraints_identity(model), rig_fingerprint=snapshot_rig_fingerprint(rig),
        required_names=required, start_tick=instance['created_tick'], end_tick=data['end_tick'],
        definition_sha256=dict(DEFINITIONS), inventory=copy.deepcopy(inventory),
        attachments={f"{entity['id']}/{binding['attachment']}": copy.deepcopy(attachment)},
        points_sha256=digest(points), scope=captured['scope'])
    return points, provider


def validate_saved_inputs(package, data, scene, manifest_dir):
    """Re-evaluate this saved rig before allocating any particle geometry."""
    from .particle_snapshot_provider import digest
    provider = package['hitbox_provider']
    identity = provider['entity_id']
    entities = [e for e in data['entities'] if e.get('id') == identity]
    rigs = [o for o in scene.objects if o.type == 'ARMATURE' and o.get('agr_entity_id') == identity]
    require(len(entities) == len(rigs) == 1, 'saved owning rig is missing or ambiguous')
    rig = rigs[0]
    cursor = rig.parent
    seen = set()
    roots = []
    while cursor is not None:
        require(cursor not in seen and len(seen) < 64, 'cyclic saved rig ancestry')
        seen.add(cursor)
        if cursor.get('agr_entity_id') == identity:
            roots.append(cursor)
        cursor = cursor.parent
    require(len(roots) == 1, 'saved replay root is missing or ambiguous')
    row = package['simulation']['instances'][0]
    instances = [i for i in data['particle_instances'] if i['id'] == row['id']]
    require(len(instances) == 1, 'saved particle identity is ambiguous')
    points, actual = capture_points(data, instances[0], entities[0], dict(rig=rig, root=roots[0]),
        scene, manifest_dir, provider['attachments'], provider['inventory'],
        package['silence_birth_capture'], package['client_child_capture'], package.get('silence_input_equivalence'))
    from .recorded_silence_effects import NORMAL_POLICY
    actual['normal_inputs_policy'] = NORMAL_POLICY
    require(digest(actual) == digest(provider)
        and digest(points) == digest(package['simulation_request']['instances'][0]['control_points']),
        'saved rig, root, attachment or hitbox inputs changed; regenerate particles')
