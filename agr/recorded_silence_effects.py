"""Normal-export Silence with original immutable observations and fresh rig poses.

Imported historical observations keep their original native qualification. This
does not read the running game or admit a different current native reader.
"""
import copy
import json
from pathlib import Path
import subprocess

from .asset_converter import sha
from .particle_silence_inputs import (SILENCE, DEFINITIONS, prepare_observed_inputs,
    validate_definitions, validate_hitbox_response, require)
from .particle_silence_capture import validate_response as validate_birth_response
from .particle_snapshot_provider import digest
from .particle_runtime import particle_command, particle_environment

NORMAL_POLICY = 'agr_normal_silence_evaluated_inputs_v1'


def request_inputs(data, instance, observed, points, hitboxes):
    require(observed == prepare_observed_inputs(data, instance, observed['birth'], observed['child']),
            'observations changed during preparation')
    require(instance.get('resource') == SILENCE and hitboxes.get('points_sha256') == digest(points)
        and [p.get('tick') for p in points] == list(range(instance['created_tick'], data['end_tick'] + 1)),
        'complete exact evaluated input frames required')
    row = copy.deepcopy(instance)
    row.update(control_points=copy.deepcopy(points), model_hitbox_policy=hitboxes['policy'],
        model_hitbox_bindings=copy.deepcopy(hitboxes['bindings']), silence_birth_provider=copy.deepcopy(observed['birth_header']))
    return row


def validate_normal_request(package, data, instance):
    """Keep exact recorded events/creation and the canonical executed request."""
    from .recorded_effect_simulation import substitute_seed
    provider = package['hitbox_provider']
    require(provider.get('normal_inputs_policy') == NORMAL_POLICY, 'unqualified normal input provider')
    observed = prepare_observed_inputs(data, instance, package['silence_birth_capture'], package['client_child_capture'])
    request = package.get('simulation_request')
    require(isinstance(request, dict) and set(request) == {'start_tick', 'end_tick', 'tick_rate', 'seed', 'gameinfo', 'instances'}
        and isinstance(request['instances'], list) and len(request['instances']) == 1,
        'exact single-instance normal simulation request required')
    expected = request_inputs(data, instance, observed, request['instances'][0].get('control_points', []), provider)
    require(digest(request['instances'][0]) == digest(expected)
        and digest({k:request[k] for k in ('start_tick', 'end_tick', 'tick_rate')})
            == digest({k:data[k] for k in ('start_tick', 'end_tick', 'tick_rate')})
        and type(request['seed']) is int and request['seed'] == substitute_seed(data, instance)
        and package.get('simulation_request_sha256') == digest(request), 'normal request identity/events/digest differ')


def prepare_package(data, instance, entity, objects, scene, manifest_dir, attachments,
                    runtime, provider, decoder, adapter, out):
    from .blender_silence_inputs import capture_points
    from .particle_hitbox_assets import collect_inventory
    from .particle_render_assets import prepare_render_assets
    from .particle_history import history_start
    from .recorded_effect_simulation import (substitute_seed, validate_request_response,
        validate_seed_response, validate_event_response)
    from .simulated_effect_package import scene_identity, validate_package
    birth = (runtime.get('silence_birth_captures') or {}).get(instance['id'])
    child = (runtime.get('client_child_captures') or {}).get(instance['id'])
    observed = prepare_observed_inputs(data, instance, birth, child)
    out = Path(out)
    out.mkdir(exist_ok=False)
    definitions = {name: decoder.dump(name, provider) for name in DEFINITIONS}
    validate_definitions(definitions)
    inventory, provenance = collect_inventory(provider, runtime, entity['model_constraints'], out / 'hitboxes.json')
    points, hitboxes = capture_points(data, instance, entity, objects, scene, manifest_dir,
        attachments, inventory, birth, child)
    hitboxes['normal_inputs_policy'] = NORMAL_POLICY
    plan = {key: data[key] for key in ('start_tick', 'end_tick', 'tick_rate')}
    plan.update(gameinfo=str(Path(runtime['install_path']) / 'game/citadel/gameinfo.gi'),
        seed=substitute_seed(data, instance), instances=[request_inputs(data, instance, observed, points, hitboxes)])
    request = out / 'plan.json'
    request.write_text(json.dumps(plan, sort_keys=True, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    request_sha = sha(request)
    result = out / 'simulation.json'
    probe = Path(runtime['probe']).resolve()
    before = {str(p): sha(p) for p in (probe, probe.parent / 'ValveResourceFormat.dll')}
    process = subprocess.run(particle_command(probe, runtime.get('dotnet')) + [str(request), str(result)],
        env=particle_environment(probe, runtime.get('dotnet')), capture_output=True, text=True, timeout=120)
    (out / 'simulation.log').write_text(process.stdout + process.stderr, encoding='utf-8')
    require(process.returncode in (0, 2), 'Silence simulation failed; see its retained log')
    require(all(sha(Path(p)) == value for p, value in before.items()), 'Silence helper changed during preparation')
    simulation = json.loads(result.read_text(encoding='utf-8'))
    validate_request_response(simulation, instance, data['start_tick'], data['end_tick'], data['tick_rate'], request_sha)
    validate_seed_response(simulation, plan['seed'])
    validate_event_response(simulation, instance, history_start(data), data['end_tick'])
    validate_birth_response(simulation, instance['id'], observed['birth_header'])
    validate_hitbox_response(simulation, instance['id'], hitboxes['bindings'])
    require(not simulation['warnings'], 'Silence simulator diagnostics: ' + '; '.join(simulation['warnings']))
    simulation['instances'][0]['entity_id'] = instance['entity_id']
    names = {system['name'].removesuffix('_c') for frame in simulation['instances'][0]['frames'] for system in frame['systems']}
    require(names == set(DEFINITIONS), 'complete four-system Silence simulation required')
    assets = prepare_render_assets(names, decoder, adapter, provider, runtime, definitions=definitions)
    require(all(provider.resolve(name + '_c').sha256 == value['sha256'] for value in inventory['sources']
                for name in [value['resource']]), 'hitbox sources changed during simulation')
    package = dict(schema_version=1, backend='simulated_states_v1', entity_ids=[instance['entity_id']],
        scene_identity=scene_identity(data, [instance['entity_id']]), simulation=simulation, assets=assets,
        simulation_request_sha256=request_sha, simulation_request=copy.deepcopy(plan),
        silence_birth_capture=copy.deepcopy(birth), client_child_capture=copy.deepcopy(child),
        hitbox_provider=hitboxes, authored_hitbox_provenance=provenance,
        coverage_scope='One supplied historical Silence birth/body selection; fresh evaluated replay hitboxes and approximate rendering. No current live acquisition or complete native pose/shader parity.')
    validate_package(package, data)
    path = out / 'package.json'
    path.write_text(json.dumps(package, allow_nan=False), encoding='utf-8')
    return package, path
