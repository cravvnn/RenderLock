"""Prepare and render recorded instances during a normal Blender export.

The optional runtime is supplied by the CLI, never read from saved replay data.
Unsupported lifecycles, simulation diagnostics and renderer failures remain
visible. Each instance gets its own process because VRF deduplicates warnings.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import subprocess
from .particle_runtime import particle_command, particle_environment
from .particle_control_events import compile_control_events, require_owned_event_provider
from .particle_control_provenance import (confirmed_points as _confirmed_points,
    confirmed_materialized_points as _confirmed_materialized_points)

SEED_POLICY = 'agr_substitute_sha256_v1'
REGEN_RESOURCE = 'particles/generic/generic_health_regen.vpcf'
# Exact installed definitions audited on build 6774. Both children use CP0;
# the recorded CP1 attachment is unused. A changed tree needs another audit.
REGEN_DEFINITIONS = {
    REGEN_RESOURCE: '56165973b0b9ff76aa69836a73ee254ce4b46cb56fab394a311b33283fb22a78',
    'particles/generic/generic_health_regen_cross.vpcf':
        'b44246cde6485bb6ab3dae7e0355fa3dbe26d9f86a4daa40554d5ce4716ce20e',
    'particles/generic/generic_health_regen_glow.vpcf':
        '2065367ee88fafe63ed8fd44888bd6c91c9c22ca1e1f0911380bc70db7fd1d74',
}


def validate_regeneration_definitions(definitions):
    if set(definitions) != set(REGEN_DEFINITIONS):
        raise ValueError('Regeneration definition tree is incomplete or changed')
    for name, expected in REGEN_DEFINITIONS.items():
        actual = hashlib.sha256(json.dumps(definitions[name], sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if actual != expected:
            raise ValueError('Regeneration definition changed; provider qualification required: ' + name)


def zero_binding_offsets(binding):
    for field, components in (('offset_position', 'xyzw'), ('offset_angles', 'xyz')):
        if field not in binding:
            continue
        value = binding[field]
        if (not isinstance(value, dict) or set(value) - set(components)
                or any(type(v) not in (int, float) or not math.isfinite(v) or v != 0
                       for v in value.values())):
            raise ValueError(f'Unsupported attachment {field}; only absent or zero offsets are supported')


def regeneration_bindings(instance, start, end):
    """Narrow root-model lifecycle; use only with the qualified definition tree.

    Unnamed ABSORIGIN_FOLLOW CP0 does not enter the native wearable search.
    Optional named CP1 is retained as provenance, not evaluated or redirected.
    """
    controls = compile_control_events(instance, start, end)
    require_owned_event_provider(instance, controls)
    birth = instance['created_tick']
    if type(birth) is not int or not 0 <= start <= birth <= end:
        raise ValueError('Regeneration requires complete creation-time history')
    handle = instance.get('entity_handle')
    if (instance.get('resource') != REGEN_RESOURCE or type(handle) is not int
            or not 0 <= handle < 0xffffff
            or instance.get('entity_id') != f'{handle & 0x3fff}:{handle >> 14}'):
        raise ValueError('Regeneration requires the exact recorded entity and resource')
    events = instance.get('events', [])
    if (not isinstance(events, list) or any(not isinstance(e, dict) or type(e.get('tick')) is not int
            or not birth <= e['tick'] <= end for e in events)
            or [e['tick'] for e in events] != sorted(e['tick'] for e in events)):
        raise ValueError('Particle events must have ordered integer ticks within the instance range')
    if any(e['type'] not in ('CREATE', 'UPDATE_ENT', 'RELEASE', 'DESTROY')
           or type(e['tick']) is not int or not birth <= e['tick'] <= end for e in events):
        raise ValueError('Unsupported regeneration lifecycle/control event')
    creates = [e for e in events if e['type'] == 'CREATE']
    if len(creates) != 1 or creates[0]['tick'] != birth:
        raise ValueError('Regeneration requires one creation event')
    creation = creates[0].get('data', {}).get('create_particle', {})
    if (creation.get('entity_handle') != handle or creation.get('attach_type') != 2
            or creation.get('cluster', False) is not False):
        raise ValueError('Unsupported regeneration creation provider')
    bindings = {}
    for event in (e for e in events if e['type'] == 'UPDATE_ENT'):
        binding = event['data']['update_particle_ent']
        cp = binding.get('control_point')
        if (event['tick'] != birth or type(cp) is not int or cp not in (0, 1)
                or cp in bindings or binding.get('entity_handle') != handle
                or type(binding.get('attachment')) is not int
                or type(binding.get('attach_type')) is not int
                or type(binding.get('include_wearables', False)) is not bool
                or set(binding) - {'control_point', 'entity_handle', 'attach_type',
                    'attachment', 'include_wearables', 'fallback_position',
                    'offset_position', 'offset_angles'}):
            raise ValueError('Unsupported regeneration control-point binding')
        zero_binding_offsets(binding)
        fallback = binding.get('fallback_position', {})
        if (not isinstance(fallback, dict) or set(fallback) - set('xyz')
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       or (cp == 0 and v != 0) for v in fallback.values())):
            raise ValueError('Unsupported regeneration fallback position')
        if ((cp == 0 and (binding['attach_type'] != 1 or binding['attachment'] != 0))
                or (cp == 1 and (binding['attach_type'] != 5 or binding['attachment'] <= 0))):
            raise ValueError('Regeneration requires unnamed root CP0 and optional point-follow CP1')
        bindings[cp] = binding
    if 0 not in bindings:
        raise ValueError('Regeneration requires root control-point 0')
    return [bindings[index] for index in sorted(bindings)]


def root_origin_bindings(instance, start, end):
    """Current native unnamed root CP0 plus complete literal control points.

    Named entity attachments must acquire a separately qualified ordered child
    inventory. A valid parent attachment alone cannot establish that provider.
    """
    controls = compile_control_events(instance, start, end)
    require_owned_event_provider(instance, controls, always=True)
    birth = instance['created_tick']
    bindings = {}
    for event in controls['entity_binding_events']:
        binding = event['data'].get('update_particle_ent')
        if not isinstance(binding, dict):
            raise ValueError('Invalid particle entity binding')
        if binding.get('control_point') != 0:
            raise ValueError('Named particle control points require qualified client-child attachment inventory')
        if (event['tick'] != birth or bindings or type(binding.get('control_point')) is not int
                or binding.get('entity_handle') != instance['entity_handle']
                or type(binding.get('entity_handle')) is not int
                or type(binding.get('attach_type')) is not int or binding['attach_type'] != 1
                or type(binding.get('attachment')) is not int or binding['attachment'] != 0
                or type(binding.get('include_wearables', False)) is not bool
                or set(binding) - {'control_point', 'entity_handle', 'attach_type', 'attachment',
                    'include_wearables', 'offset_position', 'offset_angles', 'fallback_position'}):
            raise ValueError('Root particles require an exact unnamed creation-time ABSORIGIN_FOLLOW CP0')
        zero_binding_offsets(binding)
        fallback = binding.get('fallback_position', {})
        if (not isinstance(fallback, dict) or set(fallback) - set('xyz')
                or any(type(value) not in (int, float) or not math.isfinite(value) or value != 0
                       for value in fallback.values())):
            raise ValueError('Root particle fallback requires separate provider qualification')
        bindings[0] = dict(binding, provider='root_origin')
    if 0 not in bindings:
        raise ValueError('Root particles require an explicit unnamed CP0 entity binding')
    for update in controls['literal_transforms']:
        cp = update['control_point']
        if cp == 0:
            raise ValueError('Literal transforms cannot replace the root entity provider')
        if cp not in bindings:
            if update['tick'] != birth:
                raise ValueError('Literal control points require an explicit creation-time transform')
            bindings[cp] = dict(control_point=cp, provider='literal_transform', updates=[])
        bindings[cp]['updates'].append(update)
    return [bindings[index] for index in sorted(bindings)]


def validate_model_response(simulation, instance_id, bone_count, control_points=None):
    rows = simulation.get('instances', [])
    expected = [0] if control_points is None else control_points
    if (type(bone_count) is not int or bone_count <= 0 or len(rows) != 1
            or rows[0].get('id') != instance_id
            or not isinstance(expected, list) or not expected or expected[0] != 0
            or any(type(value) is not int or not 0 <= value <= 63 for value in expected)
            or expected != sorted(set(expected))
            or not _confirmed_points(rows[0], 'control_point_indices', expected)
            or not _confirmed_materialized_points(rows[0], expected)
            or type(rows[0].get('model_bone_count')) is not int
            or rows[0]['model_bone_count'] != bone_count):
        raise ValueError('Simulator did not confirm the requested model bones; rebuild ParticleProbe')


def substitute_seed(data, instance):
    """Stable instance identity, independent of export range/order and file paths.

    This is an AGR substitute for the unavailable engine random seed.
    """
    identity = {key: instance.get(key) for key in ('id', 'entity_id', 'resource', 'created_tick')}
    identity['demo_sha256'] = data.get('metadata', {}).get('demo_sha256')
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).digest()
    return int.from_bytes(digest[:4], 'little')


def validate_seed_response(simulation, requested):
    if simulation.get('seed_policy') != SEED_POLICY or simulation.get('seed') != requested:
        raise ValueError('Simulator did not honor the requested substitute seed; rebuild ParticleProbe')
    for instance in simulation.get('instances', []):
        rows = instance.get('system_seeds', [])
        if not rows or rows[0].get('path') != 'root' or len({r.get('path') for r in rows}) != len(rows):
            raise ValueError('Simulator seed provenance is missing or ambiguous')
        if any(type(r.get('seed')) is not int or not 0 <= r['seed'] <= 4095 or not r.get('resource') for r in rows):
            raise ValueError('Invalid simulator system seed provenance')


def fixed_point_bindings(instance, start, end):
    """Creation-time point-follow CPs on the exact selected entity only.

    Root-origin/model providers remain excluded until separately qualified.
    """
    controls = compile_control_events(instance, start, end)
    require_owned_event_provider(instance, controls)
    birth = instance['created_tick']
    if type(birth) is not int:
        raise ValueError('Particle birth requires an integer tick')
    if birth < start: raise ValueError('Earlier birth requires pre-shot control-point history')
    if birth > end: raise ValueError('Instance begins after selected range')
    events = instance.get('events', [])
    if (not isinstance(events, list) or any(not isinstance(e, dict) or type(e.get('tick')) is not int
            or not birth <= e['tick'] <= end for e in events)
            or [e['tick'] for e in events] != sorted(e['tick'] for e in events)):
        raise ValueError('Particle events must have ordered integer ticks within the instance range')
    if any(e['type'] not in ('CREATE', 'UPDATE_ENT', 'UPDATE_TRANSFORM', 'RELEASE', 'DESTROY') for e in events):
        raise ValueError('Unsupported lifecycle/control event')
    creates = [e for e in events if e['type'] == 'CREATE']
    updates = [e for e in events if e['type'] == 'UPDATE_ENT']
    if len(creates) != 1 or creates[0]['tick'] != birth or not updates:
        raise ValueError('Expected creation-time attachment bindings')
    handle = instance.get('entity_handle')
    if type(handle) is not int or not 0 <= handle < 0xffffffff:
        raise ValueError('Particle binding requires an exact full integer entity handle')
    exact = f'{handle&0x3fff}:{handle>>14}'
    bindings = {}
    for event in updates:
        binding = event['data']['update_particle_ent']
        cp = binding.get('control_point')
        if (event['tick'] != birth or type(cp) is not int or not 0 <= cp <= 63
                or cp in bindings or type(binding.get('attach_type')) is not int or binding['attach_type'] != 5
                or type(binding.get('entity_handle')) is not int or binding['entity_handle'] != handle or exact != instance.get('entity_id')
                or type(binding.get('attachment')) is not int or binding['attachment'] <= 0):
            raise ValueError('Expected unique exact-serial own-entity creation-time point-follow bindings')
        zero_binding_offsets(binding)
        bindings[cp] = binding
    literal = {}
    for update in controls['literal_transforms']:
        literal.setdefault(update['control_point'], []).append(update)
    for cp, updates_for_cp in literal.items():
        if cp in bindings:
            raise ValueError('Literal transforms cannot replace an entity-bound control point without provider-change qualification')
        if updates_for_cp[0]['tick'] != birth:
            raise ValueError('Literal control points require an explicit creation-time transform')
        bindings[cp] = dict(control_point=cp,provider='literal_transform',updates=updates_for_cp)
    if 0 not in bindings:
        raise ValueError('A control-point 0 binding is required')
    return [bindings[index] for index in sorted(bindings)]


def validate_event_response(simulation, instance, start, end):
    """Old helpers cannot silently accept new literal updates or stop events."""
    expected = compile_control_events(instance, start, end)
    if not expected['literal_transforms'] and expected['destroy'] is None:
        return
    rows = simulation.get('instances', [])
    if (len(rows) != 1 or rows[0].get('id') != instance['id']
            or rows[0].get('replay_event_policy') != 'agr_recorded_control_lifecycle_v1'
            or rows[0].get('literal_transforms_applied') != expected['literal_transforms']
            or rows[0].get('destroy_applied') != expected['destroy']
            or rows[0].get('release_ticks') != expected['release_ticks']):
        raise ValueError('Simulator did not confirm recorded literal controls and lifecycle; rebuild ParticleProbe')


def fixed_attachment(instance, start, end):
    """Legacy single-CP contract retained for callers that require it."""
    bindings = fixed_point_bindings(instance, start, end)
    if len(bindings) != 1:
        raise ValueError('Expected one creation-time attachment binding')
    return bindings[0]


def validate_control_response(simulation, instance_id, bindings):
    """Do not discard supplied CPs or silently use unsupplied default points."""
    rows = simulation.get('instances', [])
    expected = [binding['control_point'] for binding in bindings]
    if (len(rows) != 1 or rows[0].get('id') != instance_id
            or not _confirmed_points(rows[0], 'control_point_indices', expected)):
        raise ValueError('Simulator did not confirm all requested control points; rebuild ParticleProbe')
    if not _confirmed_materialized_points(rows[0], expected):
        raise ValueError('Simulator used unqualified control-point dependencies or lacks their provenance; rebuild ParticleProbe or qualify the missing provider')


def validate_request_response(simulation, instance, start, end, tick_rate, request_sha256):
    """Bind the returned states to this exact request, resource and output clock."""
    if (not isinstance(request_sha256, str) or len(request_sha256) != 64
            or simulation.get('request_sha256') != request_sha256):
        raise ValueError('Simulator did not confirm the exact request identity; rebuild ParticleProbe')
    if type(simulation.get('schema_version')) is not int or simulation['schema_version'] != 1:
        raise ValueError('Unsupported simulator response schema')
    for field, expected in (('start_tick', start), ('end_tick', end), ('tick_rate', tick_rate)):
        if type(simulation.get(field)) is not int or simulation[field] != expected:
            raise ValueError('Simulator response timing mismatch: ' + field)
    rows = simulation.get('instances')
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError('Simulator instance identity mismatch')
    row = rows[0]
    if (row.get('id') != instance['id'] or row.get('resource') != instance['resource']
            or type(row.get('birth')) is not int or row['birth'] != instance['created_tick']):
        raise ValueError('Simulator instance resource/birth identity mismatch')
    frames = row.get('frames')
    first = max(start, row['birth'])
    if (not isinstance(frames, list) or len(frames) != end - first + 1
            or any(not isinstance(frame, dict) or type(frame.get('tick')) is not int
                   or frame['tick'] != first + index for index, frame in enumerate(frames))):
        raise ValueError('Simulator response has missing or invalid output ticks')


def build_recorded_effects(data, manifest_dir, scene, camera, entity_objects, runtime):
    import bpy
    from mathutils import Matrix, Quaternion, Vector
    from .asset_converter import AssetConverter, compiled
    from .game_assets import GameAssetProvider
    from .effect_assets import EffectAssetAdapter
    from .simulated_effect_package import build_package_effects,scene_identity
    from .blender_scene import tick_frame
    from .particle_history import history_start, require_history_samples
    import numpy as np

    coordinates=data['metadata']['coordinate_system']
    if (coordinates.get('unit_scale',.0254)!=.0254 or any(coordinates.get('origin',[0,0,0]))
            or coordinates.get('world_yaw_degrees',0)!=0):
        raise ValueError('Recorded simulation currently requires original world origin/yaw and metre conversion')
    out=Path(runtime['output_dir']);out.mkdir(parents=True,exist_ok=True)
    provider=GameAssetProvider(runtime['install_path'],runtime['cache_dir'])
    decoder=AssetConverter(runtime['converter'],runtime['cache_dir'])
    adapter=EffectAssetAdapter(decoder)
    descriptor=data.get('effects',{})
    source=Path(descriptor.get('manifest_file',''))
    if not source.is_absolute():source=Path(manifest_dir)/source
    attachments=json.loads(source.read_text(encoding='utf-8')).get('attachments',{})
    selected=set(entity_objects)
    from .model_tracks import logical_entities
    entities=logical_entities(data['entities'])
    motion_start=history_start(data)
    history_ticks={}
    if motion_start < data['start_tick']:
        for entity in entities.values():
            if entity['id'] in selected and entity.get('pose_file'):
                with np.load(entity['pose_file'], allow_pickle=False) as poses:
                    history_ticks[entity['id']]=({s['tick'] for s in entity['samples']}, set(poses['ticks'].tolist()))
    instances=[i for i in data.get('particle_instances',[]) if i.get('entity_id') in selected]
    report={'status':'partial','stage':'rendering','backend':'recorded_simulation_v1',
            'attachment_provider':'selected_hero_model',
            'wearable_redirection':'not_reconstructed',
            'artifact_dir':str(out),
            'selected_recorded_instances':len(instances),'attempted_instances':0,
            'rendered_instances':0,'simulator_completed_instances':0,'zero_object_instances':0,
            'objects':0,'particle_tracks':0,'point_lights':0,
            'partial_effects_consent':runtime.get('partial_effects',False),
            'accepted_simulator_diagnostics':[],
            'history_start_tick':motion_start,'pre_shot_rendered_instances':0,
            'unsupported':[],'instances':[],
            'limitations':['Fixed point-follow CPs and exact unnamed root-origin CP0 with model bones/literal controls',
                'Wearable child redirection is not reconstructed; attachments are evaluated on the selected hero model',
                'Qualified regeneration uses CP0 body bones; recorded CP1 is unused by its exact audited definition tree',
                'Literal CPs require complete creation-time values and zero interpolation; entity-provider changes remain unqualified',
                'Recorded non-immediate destruction preserves authored tails/endcaps; immediate destruction clears the entire tree',
                'Earlier-than-recovered history is not simulated',
                'Unresolved simulator diagnostics withhold that entire instance; explicit partial-effects consent can retain the documented scalar-expression omission',
                'Repeatable AGR substitute seeds are used; the original engine seeds are unavailable',
                'Blender particle shaders/lights and random seeds approximate Source behavior'],
            'probe_sha256':hashlib.sha256(Path(runtime['probe']).read_bytes()).hexdigest(),
            'diagnostic_isolation':'One OS process per instance'}
    definitions={};textures={};grouped={}
    from .effect_lifetimes import partition_expired_history
    def get_definition(name):
        if name not in definitions:
            definitions[name] = decoder.dump(name, provider)
        return definitions[name]
    instances, expiry = partition_expired_history(instances, data['start_tick'], data['tick_rate'], get_definition)
    report['authored_expiry_pruning'] = expiry
    report['candidate_instances'] = len(instances)
    from .coverage_diagnostics import recorded_birth_scope
    from collections import Counter
    def birth_scope(instance):
        return recorded_birth_scope(instance, data['start_tick'], data['end_tick'], motion_start)
    report['candidate_by_birth_scope'] = dict(Counter(birth_scope(i) for i in instances))
    rejected_scopes = Counter()

    def reject(instance, reason):
        key=(instance.get('resource'),reason)
        row=grouped.setdefault(key,{'resource':key[0],'reason':reason,'occurrences':0,'instance_examples':[]})
        row['occurrences']+=1
        scope = birth_scope(instance)
        rejected_scopes[scope] += 1
        row.setdefault('by_birth_scope', {})[scope] = row.get('by_birth_scope', {}).get(scope, 0) + 1
        if len(row['instance_examples'])<3:row['instance_examples'].append(instance['id'])

    for number,instance in enumerate(instances):
        try:
            if entities[instance['entity_id']].get('model_tracks'):
                raise ValueError('Particle model/attachment providers across recorded model changes are not yet qualified')
            model_profile=instance.get('resource')==REGEN_RESOURCE
            root_profile=model_profile or any(isinstance(e, dict) and e.get('type') == 'UPDATE_ENT'
                and isinstance(e.get('data'), dict)
                and isinstance(e['data'].get('update_particle_ent'), dict)
                and e['data']['update_particle_ent'].get('control_point') == 0
                and e['data']['update_particle_ent'].get('attach_type') == 1
                for e in instance.get('events', []))
            binding_provider=(regeneration_bindings if model_profile else root_origin_bindings
                              if root_profile else fixed_point_bindings)
            bindings=binding_provider(instance,motion_start,data['end_tick'])
            model_provenance=None
            if root_profile:
                from .blender_particle_model import capture_root_model_points
                if model_profile:
                    for name in REGEN_DEFINITIONS:
                        if name not in definitions:definitions[name]=decoder.dump(name,provider)
                    validate_regeneration_definitions({name:definitions[name] for name in REGEN_DEFINITIONS})
                model=entities[instance['entity_id']].get('model_constraints')
                if (not model or provider.resolve(compiled(model['resource'])).sha256!=model['resource_sha256']):
                    raise ValueError('Root model particles require the matching installed model and authored constraints')
                points,model_provenance=capture_root_model_points(data,entities[instance['entity_id']],
                    entity_objects[instance['entity_id']],scene,instance,manifest_dir)
                model_provenance.update(profile='generic_health_regen_cp0_bones_v1' if model_profile else 'owned_absorigin_follow_model_v1',
                    unused_recorded_control_points=[b['control_point'] for b in bindings if b['control_point']!=0] if model_profile else [])
                if model_profile:model_provenance['definition_sha256']=dict(REGEN_DEFINITIONS)
            if instance['created_tick'] < data['start_tick']:
                root_ticks, pose_ticks=history_ticks.get(instance['entity_id'], (set(),set()))
                require_history_samples(instance,data['end_tick'],root_ticks,pose_ticks)
            rig=entity_objects[instance['entity_id']]['rig']
            transforms=[]
            literal_bindings = []
            for binding in ([] if model_profile else bindings):
                if binding.get('provider') == 'root_origin':
                    continue
                if binding.get('provider') == 'literal_transform':
                    literal_bindings.append(binding)
                    continue
                definition=attachments[f"{instance['entity_id']}/{binding['attachment']}"]['definition']
                if (definition['m_nInfluences']!=1 or abs(definition['m_influenceWeights'][0]-1)>1e-6
                        or definition.get('m_bIgnoreRotation') or definition['m_bInfluenceRootTransform'][0]):
                    raise ValueError('Unsupported attachment influence/rotation mode')
                bone=definition['m_influenceNames'][0]
                if rig is None or bone not in rig.pose.bones:raise ValueError('Attachment bone absent from exported rig')
                q=definition['m_vInfluenceRotations'][0]
                offset=Matrix.LocRotScale(Vector(definition['m_vInfluenceOffsets'][0])*.0254,
                                         Quaternion((q[3],*q[:3])),Vector((1,1,1)))
                transforms.append((binding['control_point'],bone,offset))
            root_points = {point['tick']: point for point in points} if root_profile else {}
            if not root_profile:points=[]
            for tick in ([] if model_profile else range(instance['created_tick'],data['end_tick']+1)):
                frame=tick_frame(tick,data['start_tick'],data['tick_rate'],scene.render.fps/scene.render.fps_base)
                scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame))
                original = root_points.get(tick)
                samples=[dict(index=0,position=original['position'],rotation=original['rotation'])] if original else []
                for cp,bone,offset in transforms:
                    matrix=rig.matrix_world@rig.pose.bones[bone].matrix@offset
                    pos,rot,scale=matrix.decompose()
                    samples.append(dict(index=cp,position=[x/.0254 for x in pos],
                                        rotation=[rot.x,rot.y,rot.z,rot.w]))
                for binding in literal_bindings:
                    update = next(row for row in reversed(binding['updates']) if row['tick'] <= tick)
                    samples.append(dict(index=binding['control_point'],position=update['position'],rotation=update['rotation']))
                samples.sort(key=lambda row: row['index'])
                primary=samples[0]
                point=dict(tick=tick,position=primary['position'],rotation=primary['rotation'])
                if len(samples)>1:point['extra_control_points']=samples[1:]
                if original:
                    original.update(point)
                else:points.append(point)
            plan={key:data[key] for key in ('start_tick','end_tick','tick_rate')}
            plan.update(gameinfo=str(Path(runtime['install_path'])/'game/citadel/gameinfo.gi'),
                        seed=substitute_seed(data,instance),instances=[dict(instance,control_points=points)])
            if model_provenance:plan['model_provider']=model_provenance
            stem=f'instance-{number:06d}'
            plan_path=out/f'{stem}.plan.json';result_path=out/f'{stem}.simulation.json'
            plan_path.write_text(json.dumps(plan),encoding='utf-8')
            request_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
            # Never accept a result left by an earlier failed attempt.
            if result_path.exists():result_path.unlink()
            report['attempted_instances']+=1
            print('AGR particle simulation:',instance['id'],instance.get('resource'),flush=True)
            proc=subprocess.run(particle_command(runtime['probe'], runtime.get('dotnet')) + [str(plan_path),str(result_path)],
                    env=particle_environment(runtime['probe'], runtime.get('dotnet')),
                    capture_output=True,text=True,timeout=120)
            (out/f'{stem}.log').write_text(proc.stdout+proc.stderr,encoding='utf-8')
            if proc.returncode not in (0,2):raise ValueError(f'Simulator exit {proc.returncode}; see {stem}.log')
            simulation=json.loads(result_path.read_text(encoding='utf-8'))
            validate_request_response(simulation, instance, data['start_tick'], data['end_tick'],
                                      data['tick_rate'], request_sha256)
            validate_seed_response(simulation,plan['seed'])
            validate_event_response(simulation,instance,motion_start,data['end_tick'])
            if root_profile:
                validate_model_response(simulation,instance['id'],model_provenance['bone_count'],
                    [0] if model_profile else [binding['control_point'] for binding in bindings])
            else:validate_control_response(simulation,instance['id'],bindings)
            report['simulator_completed_instances'] += 1
            from .particle_diagnostics import simulation_diagnostics
            diagnostics = simulation_diagnostics(simulation,
                allow_partial_effects=runtime.get('partial_effects',False))
            if diagnostics['status'] == 'partial':
                report['accepted_simulator_diagnostics'].append(dict(id=instance['id'],
                    resource=instance['resource'], **diagnostics))
            simulation['instances'][0]['entity_id']=instance['entity_id']
            names={s['name'].removesuffix('_c') for f in simulation['instances'][0]['frames'] for s in f['systems']}
            if model_profile and names!=set(REGEN_DEFINITIONS):
                raise ValueError('Simulator regeneration system tree differs from qualified definitions')
            for name in names:
                if name not in definitions:definitions[name]=decoder.dump(name,provider)
            texture_names={t['m_hTexture'] for name in names for renderer in definitions[name].get('m_Renderers',[])
                           for t in renderer.get('m_vecTexturesInput',[]) if t.get('m_hTexture')}
            for name in texture_names:
                if name not in textures:
                    directory,descriptor=adapter.texture(name,provider)
                    textures[name]={**descriptor,'directory':str(directory)}
            assets={'definitions':{n:definitions[n] for n in names},'textures':{n:textures[n] for n in texture_names},
                    'provenance':{'converter':decoder.identity,'input_sha256':{n:provider.resolve(compiled(n)).sha256 for n in names|texture_names}}}
            package={'schema_version':1,'backend':'simulated_states_v1','entity_ids':[instance['entity_id']],
                     'scene_identity':scene_identity(data,[instance['entity_id']]),'simulation':simulation,'assets':assets,
                     'simulation_request_sha256':request_sha256,
                     'coverage_scope':'One recorded instance; automatic isolated simulation, approximate rendering. Other instances have separate coverage.'}
            if model_provenance:package['model_provider']=model_provenance
            package_path=out/f'{stem}.package.json';package_path.write_text(json.dumps(package),encoding='utf-8')
            result=build_package_effects(package,data,package_path,scene,camera,
                allow_partial_effects=runtime.get('partial_effects',False))
            if result.get('objects',0) > 0:
                report['rendered_instances']+=1
            else:
                report['zero_object_instances']+=1
            entry={'id':instance['id'],'resource':instance['resource'],'package':str(package_path),'rendering':result}
            if model_provenance:entry['model_provider']=model_provenance
            report['instances'].append(entry)
            for key in ('objects','particle_tracks','point_lights'):report[key]+=result.get(key,0)
            if result.get('objects',0) > 0 and instance['created_tick'] < data['start_tick']:
                report['pre_shot_rendered_instances']+=1
        except (ValueError,KeyError,OSError,RuntimeError,subprocess.SubprocessError) as exc:
            reject(instance,str(exc))
    report['unsupported']=list(grouped.values())
    report['unsupported_occurrences']=sum(r['occurrences'] for r in report['unsupported'])
    report['unsupported_by_birth_scope'] = dict(rejected_scopes)
    if not report['objects']:report.update(status='unavailable',reason='No recorded instance produced renderable objects in this clip')
    report = retain_recorded_fallback(report, data, manifest_dir, scene, camera, entity_objects,
                                      allow_partial_effects=runtime.get('partial_effects',False))
    (out/'coverage.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    scene['agr_simulated_effects_report']=json.dumps(report)
    return report


def retain_recorded_fallback(report, data, manifest_dir, scene, camera, entity_objects, *, allow_partial_effects=False):
    """Use one backend at a time, only after a zero-object simulation outcome."""
    descriptor = data.get('effects', {})
    if (report.get('status') == 'unavailable' and not report['objects'] and allow_partial_effects is True
            and descriptor.get('recorded_fallback')):
        from .blender_effects import build_effects
        fallback = build_effects(dict(data, effects=descriptor['recorded_fallback']), manifest_dir,
                                scene, camera, entity_objects,
                                scene.render.fps / scene.render.fps_base)
        if fallback.get('status') == 'failed':
            raise ValueError('Recorded effects fallback failed: ' + str(fallback.get('error', 'see effects report')))
        if fallback.get('objects', 0) > 0:
            simulation_report = report
            report = dict(report, status='partial', backend='recorded_layers_fallback_v1',
                simulation_attempt=simulation_report, recorded_layer_fallback=fallback,
                objects=fallback['objects'], layers=fallback.get('layers', 0),
                recorded_layer_objects=fallback['objects'],
                limitations=report['limitations'] + ['Simulation produced no renderable objects. Explicit partial-effects consent retained supported recorded visual layers instead; backends were not combined.'])
            report.pop('reason', None)
        else:
            report['recorded_layer_fallback'] = fallback
    return report
