"""Simulation and package preparation for unverified unowned world effects (see particle_world_literal)."""
import hashlib
import json
from pathlib import Path
import subprocess
from .particle_world_literal import (BACKEND, SCOPE, request_header, control_points, world_identity,
                                     validate_response, validate_package, literal_controls, without_model_events,
                                     models_not_applied, placed_positions, simulator_events)

MAX_SYSTEMS = 256


def definition_tree(resource, decoder, provider):
    """The effect and every child it references, as decoded from the installed game."""
    definitions, pending = {}, [resource]
    while pending:
        name = pending.pop()
        if name in definitions:
            continue
        if len(definitions) >= MAX_SYSTEMS:
            raise ValueError('World-origin particles: effect tree exceeds the supported size')
        definition = decoder.dump(name, provider)
        definitions[name] = definition
        for child in definition.get('m_Children', []):
            reference = child.get('m_ChildRef') if isinstance(child, dict) else None
            if isinstance(reference, str) and reference:
                pending.append(reference)
    return definitions


def prepare_world_literal_package(data, instance, runtime, provider, decoder, adapter, directory):
    from .particle_runtime import particle_command, particle_environment
    from .recorded_effect_simulation import substitute_seed, validate_request_response, validate_seed_response
    header = request_header(data, instance)
    definitions = definition_tree(instance['resource'], decoder, provider)
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=False)
    plan = {key: data[key] for key in ('start_tick', 'end_tick', 'tick_rate')}
    plan.update(gameinfo=str(Path(runtime['install_path']) / 'game/citadel/gameinfo.gi'),
                seed=substitute_seed(data, instance),
                instances=[dict(instance, events=simulator_events(instance, literal_controls(data, instance)),
                                world_origin_provider=header,
                                control_points=control_points(data, instance))])
    plan_path = out / 'request.json'
    result_path = out / 'simulation.json'
    with plan_path.open('x', encoding='utf-8') as stream:
        json.dump(plan, stream)
    request_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    proc = subprocess.run(particle_command(runtime['probe'], runtime.get('dotnet')) + [str(plan_path), str(result_path)],
                          env=particle_environment(runtime['probe'], runtime.get('dotnet')),
                          capture_output=True, text=True, timeout=120)
    (out / 'simulation.log').write_text(proc.stdout + proc.stderr, encoding='utf-8')
    if proc.returncode not in (0, 2):
        raise ValueError(f'World particle simulator exit {proc.returncode}; see {out}/simulation.log')
    if not result_path.is_file() or result_path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('World particle response is missing or exceeds the bounded 32 MiB output')
    sim = json.loads(result_path.read_text(encoding='utf-8'))
    validate_request_response(sim, instance, data['start_tick'], data['end_tick'], data['tick_rate'], request_sha)
    validate_seed_response(sim, plan['seed'])
    validate_response(sim, data, instance)
    if sim.get('warnings'):
        raise ValueError('World particle simulator diagnostics: ' + '; '.join(sim['warnings']))
    names = {system['name'].removesuffix('_c') for frame in sim['instances'][0]['frames'] for system in frame['systems']}
    if not names or not names <= set(definitions):
        raise ValueError('World simulation system tree differs from the decoded effect tree')
    sim['instances'][0]['entity_id'] = None
    points = literal_controls(data, instance)
    models = models_not_applied(instance)
    if models:
        sim['instances'][0]['control_point_models_not_applied'] = models
    if (sim['instances'][0].get('unprovided_control_point_indices') or models
            or points[0][0].get('assumed_world_origin')):
        from .particle_owned_broad import leave_out_world_origin_systems
        leave_out_world_origin_systems(sim['instances'][0], placed_positions(points))
    # The same asset collection as owned effects: sprite sheets, texture defaults, light and rope inputs, models.
    from .particle_render_assets import prepare_render_assets
    assets = prepare_render_assets(set(definitions), decoder, adapter, provider, runtime, definitions=definitions,
                                   omit_unsupported_layers=True)
    assets.setdefault('models', {})
    package = dict(schema_version=1, backend=BACKEND, entity_ids=[], scene_identity=world_identity(data, instance),
                   world_origin_provider=header, simulation_request_sha256=request_sha, simulation=sim, assets=assets,
                   coverage_scope=SCOPE)
    validate_package(package, data)
    package_path = out / 'package.json'
    with package_path.open('x', encoding='utf-8') as stream:
        json.dump(package, stream)
    return package, package_path
