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
    if package.get('backend') == 'observed_rope_frames_v1':
        from .observed_rope_states import validate_package as validate_ambient_ropes
        validate_ambient_ropes(package, scene)
        return
    if package.get('backend') == 'observed_sprite_frames_v1':
        from .observed_sprite_states import validate_package as validate_ambient_sprites
        validate_ambient_sprites(package, scene)
        return
    if package.get('backend') == 'observed_slide_model_frames_v1':
        from .observed_slide_model_states import validate_package as validate_slide_models
        validate_slide_models(package, scene)
        return
    if package.get('backend') == 'observed_slide_light_frames_v1':
        from .observed_slide_light_states import validate_package as validate_slide_lights
        validate_slide_lights(package, scene)
        return
    if package.get('backend') == 'observed_slide_trail_frames_v1':
        from .observed_slide_trail_states import validate_package as validate_slide_trails
        validate_slide_trails(package, scene)
        return
    if package.get('backend') == 'observed_slide_sprite_frames_v1':
        from .observed_slide_sprite_states import validate_package as validate_slide_sprites
        validate_slide_sprites(package, scene)
        return
    if package.get('backend') == 'modifier_attached_models_v1':
        from .modifier_mask_model import validate_package as validate_mask_package
        validate_mask_package(package, scene)
        return
    provenance=package.get('assets',{}).get('provenance',{})
    if 'native_rope_shader'in provenance:
        from .particle_rope_shader_inputs import validate_inputs
        validate_inputs(provenance['native_rope_shader'])
    if 'native_standard_light' in provenance:
        from .particle_standard_light import validate_native_inputs
        validate_native_inputs(provenance['native_standard_light'])
    if 'spritecard_sheet_uv' in provenance:
        from .particle_sheet_uv import validate_shader_inputs
        validate_shader_inputs(provenance['spritecard_sheet_uv'])
    if package.get('backend') == 'simulated_projectile_flight_v2':
        from .projectile_particles import validate_package as validate_projectile_package
        validate_projectile_package(package,scene)
        return
    if package.get('backend') == 'simulated_world_origin_v1':
        from .particle_world_origin import validate_package as validate_world_package
        validate_world_package(package, scene)
        return
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
    from .particle_silence_inputs import SILENCE,validate_package as validate_silence_package
    silence_package=('silence_birth_capture' in package or any(row.get('resource')==SILENCE or 'silence_birth_provider' in row
                    for row in sim.get('instances',[])))
    if silence_package:validate_silence_package(package,scene)
    from .particle_snapshot_provider import RESOURCE as CROSSBOW_RESOURCE
    if not silence_package and ('snapshot_model_provider' in package or 'crossbow_owner_capture' in package
            or any(row.get('resource') == CROSSBOW_RESOURCE or 'snapshot_model_input_policy' in row
                   or 'crossbow_owner_provider' in row for row in sim.get('instances', []))):
        _validate_crossbow_package(package, scene)
    from .particle_owned_literal import RESOURCE as LITERAL_RESOURCE,validate_package as validate_literal_package
    if 'owned_literal_provider'in package or any(row.get('resource')==LITERAL_RESOURCE for row in sim.get('instances',[])):
        validate_literal_package(package,scene)
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
                    or not ((silence_package and instance.get('resource')==SILENCE)
                            or confirmed_materialized_points(instance, supplied))):
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


def _validate_crossbow_package(package, scene):
    from .particle_snapshot_provider import (RESOURCE, validate_provider, validate_definitions,
        validate_response as validate_snapshot_response, request_inputs, digest)
    from .particle_crossbow_owner import request_header, validate_response as validate_owner_response
    sim = package['simulation']
    rows = sim.get('instances')
    if not isinstance(rows, list) or len(rows) != 1 or rows[0].get('resource') != RESOURCE:
        raise ValueError('Snapshot package requires exactly the qualified Crossbow instance')
    originals = [i for i in scene.get('particle_instances', []) if i.get('id') == rows[0].get('id')]
    if len(originals) != 1 or package.get('entity_ids') != [originals[0].get('entity_id')]:
        raise ValueError('Crossbow package original instance/owner differs')
    instance = originals[0]
    header = request_header(package.get('crossbow_owner_capture'), scene, instance)
    snapshot = validate_provider(package.get('snapshot_model_provider'), scene, instance)
    validate_owner_response(sim, instance['id'], header)
    validate_snapshot_response(sim, instance['id'], snapshot['bindings'])
    validate_definitions(package.get('assets', {}).get('definitions'))
    if not isinstance(package.get('simulation_request_sha256'), str):
        raise ValueError('Crossbow package is missing its simulation request identity')
    request = package.get('simulation_request')
    if (not isinstance(request, dict) or digest(request) != package['simulation_request_sha256']
            or any(request.get(k) != scene[k] for k in ('start_tick', 'end_tick', 'tick_rate'))
            or not isinstance(request.get('instances'), list) or len(request['instances']) != 1):
        raise ValueError('Crossbow package request content/range differs from its executed request')
    row = request['instances'][0]
    if (not isinstance(row, dict) or not isinstance(row.get('control_points'), list)
            or len(row['control_points']) != scene['end_tick'] - instance['created_tick'] + 1
            or any(not isinstance(point, dict) for point in row['control_points'])):
        raise ValueError('Crossbow request is missing its complete control frames')
    points = [{k:v for k,v in point.items() if k != 'snapshot_model_bones'} for point in row['control_points']]
    expected = dict(instance, **request_inputs(snapshot, scene, instance, points), crossbow_owner_provider=header)
    if digest(row) != digest(expected):
        raise ValueError('Crossbow request does not contain its exact snapshot frames and original replay events')


def build_package_effects(package, data, manifest_file, scene, camera):
    import bpy
    from .blender_simulated_effects import build_simulated_effects
    validate_package(package, data)
    if 'snapshot_model_provider' in package:
        from .blender_particle_skinning import validate_source_snapshot_rig
        validate_source_snapshot_rig(package['snapshot_model_provider'], data, scene, Path(manifest_file).parent)
    assets = copy.deepcopy(package['assets'])
    for texture in assets.get('textures', {}).values():
        directory = Path(texture['directory'])
        if not directory.is_absolute():
            texture['directory'] = str((Path(manifest_file).parent / directory).resolve())
        if 'source_sheet' in texture:
            from .particle_texture_animation import validate_asset_sheet
            validate_asset_sheet(texture, assets.get('provenance', {}).get('input_sha256', {}))
            sheet = texture['source_sheet']
            directory = Path(sheet['directory'])
            if not directory.is_absolute():
                sheet['directory'] = str((Path(manifest_file).parent / directory).resolve())
    for model in assets.get('models', {}).values():
        directory = Path(model['directory'])
        if not directory.is_absolute():
            model['directory'] = str((Path(manifest_file).parent / directory).resolve())
    groups = ('objects', 'collections', 'meshes', 'shape_keys', 'materials', 'lights', 'images', 'actions', 'node_groups', 'texts')
    before = {key: set(getattr(bpy.data, key)) for key in groups}
    try:
        if package['backend'] == 'observed_rope_frames_v1':
            from .blender_observed_ropes import build_observed_ropes
            report = build_observed_ropes(package, data, assets, scene, camera)
        elif package['backend'] == 'observed_sprite_frames_v1':
            from .blender_observed_sprites import build_observed_sprites
            report = build_observed_sprites(package, data, assets, scene, camera)
        elif package['backend'] == 'observed_slide_model_frames_v1':
            from .blender_observed_slide_models import build_observed_slide_models
            report = build_observed_slide_models(package, data, assets, scene, camera)
        elif package['backend'] == 'observed_slide_light_frames_v1':
            from .blender_observed_slide_lights import build_observed_slide_lights
            report = build_observed_slide_lights(package, data, assets, scene, camera)
        elif package['backend'] == 'observed_slide_trail_frames_v1':
            from .blender_observed_slide_trails import build_observed_slide_trails
            report = build_observed_slide_trails(package, data, assets, scene, camera)
        elif package['backend'] == 'observed_slide_sprite_frames_v1':
            from .blender_observed_slide_sprites import build_observed_slide_sprites
            report = build_observed_slide_sprites(package, data, assets, scene, camera)
        elif package.get('backend') == 'modifier_attached_models_v1':
            from .blender_modifier_masks import build_modifier_mask
            report = build_modifier_mask(package, data, assets, scene, camera)
        else:
            report = build_simulated_effects(package['simulation'], assets, scene, camera)
        if (package.get('backend')=='simulated_projectile_flight_v2'
                and report.get('objects',0)<=0 and report.get('point_lights',0)<=0):
            raise ValueError('Projectile package produced no rendered geometry or lights')
    except Exception:
        created = [item for key in groups for item in set(getattr(bpy.data, key)) - before[key]]
        bpy.data.batch_remove(created)
        raise
    report.update(status='partial', stage='rendering', simulation_backend=report['backend'],
                  backend=package['backend'] if package.get('backend') in (
                      'modifier_attached_models_v1', 'observed_rope_frames_v1',
                      'observed_sprite_frames_v1', 'observed_slide_model_frames_v1',
                      'observed_slide_light_frames_v1', 'observed_slide_trail_frames_v1',
                      'observed_slide_sprite_frames_v1') else 'simulated_states_v1',
                  scene_identity=package['scene_identity'], entity_ids=package['entity_ids'],
                  coverage_scope=package['coverage_scope'])
    return report
