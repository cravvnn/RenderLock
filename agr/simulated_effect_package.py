"""Bind prepared particle states to the exact replay tracks and rig inputs."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from .particle_control_provenance import confirmed_materialized_points


def model_constraints_identity(model):
    if not isinstance(model, dict) or model.get('schema_version') != 1:
        raise ValueError('Model particles require authored model constraints')
    return hashlib.sha256(json.dumps(model, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def scene_identity(scene, entity_ids):
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
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_package(package, scene):
    if package.get('schema_version') != 1 or package.get('backend') != 'simulated_states_v1':
        raise ValueError('Unsupported simulated effect package')
    if not isinstance(package.get('coverage_scope'), str) or not package['coverage_scope'].strip():
        raise ValueError('Particle package must declare its coverage scope')
    ids = package.get('entity_ids')
    if not ids or len(ids) != len(set(ids)) or not all(isinstance(i, str) for i in ids):
        raise ValueError('Particle package requires unique entity identities')
    if package.get('scene_identity') != scene_identity(scene, ids):
        raise ValueError('Prepared particles do not match replay range, tracks, coordinates or rig inputs; regenerate them')
    if 'model_provider' in package:
        provider = package['model_provider']
        if not isinstance(provider, dict) or provider.get('entity_id') not in ids:
            raise ValueError('Particle model provider entity mismatch')
        entity = next(e for e in scene['entities'] if e['id'] == provider['entity_id'])
        model = entity.get('model_constraints')
        if (provider.get('model_constraints_sha256') != model_constraints_identity(model)
                or provider.get('resource') != model.get('resource')
                or provider.get('resource_sha256') != model.get('resource_sha256')
                or provider.get('bone_count') != len(model.get('names', []))):
            raise ValueError('Prepared model particles do not match authored constraints; regenerate them')
    sim = package['simulation']
    if ('simulation_request_sha256' in package
            and sim.get('request_sha256') != package['simulation_request_sha256']):
        raise ValueError('Prepared particles have a mismatched simulator request identity')
    if sim.get('schema_version') != 1 or not sim.get('backend') or sim.get('warnings'):
        raise ValueError('Particle simulation has an unsupported schema or unresolved diagnostics')
    for field in ('start_tick', 'end_tick', 'tick_rate'):
        if sim[field] != scene[field]:
            raise ValueError('Particle simulation timing mismatch: ' + field)
    instance_ids = set()
    for instance in sim['instances']:
        if instance['id'] in instance_ids:
            raise ValueError('Duplicate simulated instance identity')
        instance_ids.add(instance['id'])
        if instance.get('entity_id') not in ids:
            raise ValueError('Simulated instance has no selected entity binding')
        if (('simulation_request_sha256' in package or instance.get('control_point_input_policy') is not None)
                and 'materialized_control_point_indices' not in instance):
            raise ValueError('Prepared particles lack control-point dependency provenance; regenerate them')
        if 'materialized_control_point_indices' in instance:
            supplied, observed = (instance.get('control_point_indices'), instance['materialized_control_point_indices'])
            if (not isinstance(supplied, list) or not isinstance(observed, list)
                    or not supplied or any(type(i) is not int or not 0 <= i <= 63 for i in supplied + observed)
                    or supplied != sorted(set(supplied))
                    or not confirmed_materialized_points(instance, supplied)):
                raise ValueError('Prepared particles have unqualified control-point dependencies')
        birth = instance.get('birth', scene['start_tick'])
        if type(birth) is not int or not 0 <= birth <= scene['end_tick']:
            raise ValueError('Invalid particle birth tick')
        ticks = [f['tick'] for f in instance['frames']]
        if (not ticks or any(type(t) is not int for t in ticks)
                or ticks != list(range(max(scene['start_tick'], birth), scene['end_tick'] + 1))):
            raise ValueError('Invalid particle sample tick order/range')
    if not instance_ids:
        raise ValueError('Empty prepared particle package')


def build_package_effects(package, data, manifest_file, scene, camera):
    import bpy
    from .blender_simulated_effects import build_simulated_effects
    validate_package(package, data)
    assets = copy.deepcopy(package['assets'])
    for texture in assets.get('textures', {}).values():
        directory = Path(texture['directory'])
        if not directory.is_absolute():
            texture['directory'] = str((Path(manifest_file).parent / directory).resolve())
    groups = ('objects', 'collections', 'meshes', 'materials', 'lights', 'images', 'actions', 'node_groups', 'texts')
    before = {key: set(getattr(bpy.data, key)) for key in groups}
    try:
        report = build_simulated_effects(package['simulation'], assets, scene, camera)
    except Exception:
        created = [item for key in groups for item in set(getattr(bpy.data, key)) - before[key]]
        bpy.data.batch_remove(created)
        raise
    report.update(status='partial', stage='rendering', simulation_backend=report['backend'], backend='simulated_states_v1',
                  scene_identity=package['scene_identity'], entity_ids=package['entity_ids'],
                  coverage_scope=package['coverage_scope'])
    return report
