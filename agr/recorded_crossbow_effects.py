"""Normal-export Crossbow preparation with independent observed owner input.

The optional runtime capture is supplied by the caller, never inferred from a
saved scene. Missing current capture remains unsupported. Snapshot poses and
authored rest are generated from this export's rig and installed model.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess

from .particle_crossbow_owner import (RESOURCE, bindings, validate_bound_capture,
    request_header, validate_response as validate_owner_response, CLIENT_SHA256, PARTICLES_SHA256)
from .particle_snapshot_provider import (DEFINITIONS, validate_definitions,
    request_inputs, validate_response as validate_snapshot_response)
from .particle_runtime import particle_command, particle_environment


def capture_points(data, instance, entity, objects, scene, manifest_dir, attachments, inventory):
    from .blender_particle_skinning import evaluated_snapshot_rig
    with evaluated_snapshot_rig(objects.get('rig'), objects.get('root'), scene):
        return _capture_points(data, instance, entity, objects, scene, manifest_dir, attachments, inventory)


def _capture_points(data, instance, entity, objects, scene, manifest_dir, attachments, inventory):
    """Borrow the scene frame, capture selected Source-rest bone and CP0/1/2/5."""
    import numpy as np
    from mathutils import Matrix, Quaternion, Vector
    from .blender_scene import tick_frame
    from .blender_particle_skinning import capture_source_snapshot_provider
    rig, root = objects['rig'], objects['root']
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    try:
        snapshot = capture_source_snapshot_provider(data, entity, objects, scene, instance, manifest_dir, inventory)
        rows = bindings(data, instance)
        transforms = []
        for b, expected_name in zip(rows[1:3], ('crossbow_muzzle_fx', 'head_fx')):
            attachment = attachments[f"{entity['id']}/{b['attachment']}"]
            definition = attachment['definition']
            if (definition.get('m_name') != expected_name or attachment.get('entity_id') != entity['id']
                    or attachment.get('source_model') != entity['model_constraints']['resource']
                    or definition['m_nInfluences'] != 1
                    or abs(definition['m_influenceWeights'][0] - 1) > 1e-6
                    or definition.get('m_bIgnoreRotation') or definition['m_bInfluenceRootTransform'][0]):
                raise ValueError('Crossbow requires its exact single-bone named attachment definitions')
            bone = definition['m_influenceNames'][0]
            if bone not in rig.pose.bones:
                raise ValueError('Crossbow attachment bone is absent from the selected rig')
            q = definition['m_vInfluenceRotations'][0]
            offset = Matrix.LocRotScale(Vector(definition['m_vInfluenceOffsets'][0]) * .0254,
                Quaternion((q[3], *q[:3])), Vector((1, 1, 1)))
            transforms.append((b['control_point'], bone, offset))
        yaw = entity.get('render_root_yaw_degrees', 0)
        if type(yaw) not in (int, float) or not math.isfinite(yaw):
            raise ValueError('Crossbow render-root yaw must be finite')
        basis = Matrix.Rotation(math.radians(yaw % 360), 4, 'Z')
        literals = rows[3]['updates']
        points = []
        for tick in range(instance['created_tick'], data['end_tick'] + 1):
            frame = tick_frame(tick, data['start_tick'], data['tick_rate'], scene.render.fps / scene.render.fps_base)
            scene.frame_set(math.floor(frame), subframe=frame - math.floor(frame))
            samples = []
            matrices = [(0, root.matrix_world @ basis)]
            matrices.extend((cp, rig.matrix_world @ rig.pose.bones[bone].matrix @ offset)
                            for cp, bone, offset in transforms)
            for cp, matrix in matrices:
                array = np.asarray(matrix, dtype=float)
                linear = array[:3, :3]
                if (not np.isfinite(array).all() or np.max(abs(linear.T @ linear - np.eye(3))) > 2e-4
                        or abs(np.linalg.det(linear) - 1) > 2e-4):
                    raise ValueError('Crossbow control provider requires a finite proper rigid transform')
                position, rotation, _ = matrix.decompose()
                samples.append(dict(index=cp, position=[v / .0254 for v in position],
                    rotation=[rotation.x, rotation.y, rotation.z, rotation.w]))
            literal = next(row for row in reversed(literals) if row['tick'] <= tick)
            samples.append(dict(index=5, position=literal['position'], rotation=literal['rotation']))
            points.append(dict(tick=tick, position=samples[0]['position'], rotation=samples[0]['rotation'],
                               extra_control_points=samples[1:]))
        return points, snapshot
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)


def prepare_package(data, instance, entity, objects, scene, manifest_dir, attachments,
                    runtime, provider, decoder, adapter, out):
    from .asset_converter import sha
    from .particle_model_rest_assets import collect_model_rest
    from .particle_render_assets import prepare_render_assets
    from .recorded_effect_simulation import (substitute_seed, validate_request_response,
        validate_seed_response, validate_event_response)
    from .particle_history import history_start
    from .simulated_effect_package import scene_identity, validate_package
    capture = (runtime.get('crossbow_owner_captures') or {}).get(instance['id'])
    validate_bound_capture(capture, data, instance)
    modules = {Path(provider.install_path) / 'game/citadel/bin/win64/client.dll': CLIENT_SHA256,
               Path(provider.install_path) / 'game/bin/win64/particles.dll': PARTICLES_SHA256}
    if any(not path.is_file() or sha(path) != value for path, value in modules.items()):
        raise ValueError('Crossbow capture requires its exact qualified installed Windows client/particle module pair')
    out = Path(out)
    out.mkdir(exist_ok=False)
    definitions = {name: decoder.dump(name, provider) for name in DEFINITIONS}
    validate_definitions(definitions)
    inventory, rest_provenance = collect_model_rest(provider, runtime, entity['model_constraints'], out / 'model-rest.json')
    points, snapshot = capture_points(data, instance, entity, objects, scene, manifest_dir, attachments, inventory)
    plan = {key: data[key] for key in ('start_tick', 'end_tick', 'tick_rate')}
    header = request_header(capture, data, instance)
    row = copy.deepcopy(instance)
    row.update(request_inputs(snapshot, data, instance, points))
    row['crossbow_owner_provider'] = header
    plan.update(gameinfo=str(Path(runtime['install_path']) / 'game/citadel/gameinfo.gi'),
                seed=substitute_seed(data, instance), instances=[row])
    request = out / 'plan.json'
    request.write_text(json.dumps(plan, sort_keys=True, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    request_sha = sha(request)
    result = out / 'simulation.json'
    probe = Path(runtime['probe']).resolve()
    parser = probe.parent / 'ValveResourceFormat.dll'
    helper_before = {str(p): sha(p) for p in (probe, parser)}
    process = subprocess.run(particle_command(probe, runtime.get('dotnet')) + [str(request), str(result)],
        env=particle_environment(probe, runtime.get('dotnet')), capture_output=True, text=True, timeout=120)
    (out / 'simulation.log').write_text(process.stdout + process.stderr, encoding='utf-8')
    if process.returncode not in (0, 2):
        raise ValueError('Crossbow simulation failed; see its retained simulation log')
    if any(sha(Path(p)) != value for p, value in helper_before.items()):
        raise ValueError('Crossbow simulation helper changed during preparation')
    simulation = json.loads(result.read_text(encoding='utf-8'))
    validate_request_response(simulation, instance, data['start_tick'], data['end_tick'], data['tick_rate'], request_sha)
    validate_seed_response(simulation, plan['seed'])
    validate_event_response(simulation, instance, history_start(data), data['end_tick'])
    validate_owner_response(simulation, instance['id'], header)
    validate_snapshot_response(simulation, instance['id'], snapshot['bindings'])
    if simulation['warnings']:
        raise ValueError('Crossbow simulator diagnostics: ' + '; '.join(simulation['warnings']))
    simulation['instances'][0]['entity_id'] = instance['entity_id']
    names = {system['name'].removesuffix('_c') for frame in simulation['instances'][0]['frames'] for system in frame['systems']}
    if names != set(DEFINITIONS):
        raise ValueError('Crossbow simulator system tree differs from its exact definition inventory')
    assets = prepare_render_assets(names, decoder, adapter, provider, runtime, definitions=definitions)
    if any(sha(path) != value for path, value in modules.items()):
        raise ValueError('Crossbow native modules changed during export preparation')
    package = dict(schema_version=1, backend='simulated_states_v1', entity_ids=[instance['entity_id']],
        scene_identity=scene_identity(data, [instance['entity_id']]), simulation=simulation, assets=assets,
        simulation_request_sha256=request_sha, simulation_request=copy.deepcopy(plan), crossbow_owner_capture=copy.deepcopy(capture),
        snapshot_model_provider=snapshot, authored_rest_provenance=rest_provenance,
        coverage_scope='One observed current-build Crossbow instance; selected replay rig/rest snapshot and approximate rendering. No positive render children or general live acquisition.')
    validate_package(package, data)
    path = out / 'package.json'
    path.write_text(json.dumps(package, allow_nan=False), encoding='utf-8')
    return package, path
