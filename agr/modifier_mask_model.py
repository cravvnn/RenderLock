"""Exact sustained mask-model inputs, separate from sampled particle lifetimes."""
import copy
import json
import math
from pathlib import Path
import subprocess
from .asset_converter import compiled, sha
from .modifier_mask_plan import compile_mask_plan, MODEL_CHILD, CHILD_SHA, CLIENT_SHA
from .particle_authored_model import validate_authored_model, PARTICLES_SHA, SCENE_SHA, MODEL
from .particle_model_selection import authored_bodygroup_selection
from .replay_capture import canonical_sha256
from .vendor.kv3_text import parse_kv3

BACKEND = 'modifier_attached_models_v1'
VDATA = 'scripts/modifiers.vdata_c'
VDATA_SHA = 'f8472b9e28e744ce167fd0f8af9a14ebdfafdbc650c8327bcd59e1a662be9ffc'
METADATA_POLICY = 'agr_particle_owner_model_metadata_v1'


def require(condition, message):
    if not condition:
        raise ValueError('Modifier mask model: ' + message)


def constant_track(track):
    rows = track.get('gate_samples')
    require(isinstance(rows, list) and rows, 'owner gate samples are missing')
    require(all(row['native_gate_request'] is True and row['owner_present'] is True for row in rows),
            'Owner stop/availability transition requires qualified client scheduling and endcap behavior; immediate gate-hiding is refused')


def validate_owner_model(model, entity):
    """Bind attachment metadata without enabling optional procedural bone rules."""
    from .vendor.resource_hash import resource_id
    from .model_states import model_state_issue
    require(not entity.get('model_tracks') and model_state_issue(entity) is None,
            'owner changes model or has inconsistent recorded model identity')
    require(isinstance(model, dict) and set(model) == {
        'resource', 'resource_sha256', 'source_file', 'skeleton_sha256', 'names'},
        'owner model descriptor fields differ')
    resource = compiled(model['resource'])
    require(resource.endswith('.vmdl_c') and str(resource_id(resource[:-2])) == str(entity.get('model_handle')),
            'owner resource differs from exact recorded model handle')
    source = Path(model['source_file'])
    require(source.is_file() and sha(source) == model['resource_sha256'], 'owner model source bytes changed')
    skeleton = Path(entity.get('skeleton_file', ''))
    require(skeleton.is_file() and skeleton.stat().st_size <= 16*1024*1024
        and sha(skeleton) == model['skeleton_sha256'], 'owner skeleton bytes changed')
    names = json.loads(skeleton.read_text(encoding='utf-8-sig')).get('m_boneIDs')
    require(isinstance(names, list) and 0 < len(names) <= 4096
        and all(isinstance(n, str) and n for n in names) and len(set(names)) == len(names)
        and names == model['names'], 'owner evaluated skeleton names differ')
    constraints = entity.get('model_constraints')
    if constraints is not None:
        require(isinstance(constraints, dict) and compiled(constraints['resource']) == resource
            and constraints['resource_sha256'] == model['resource_sha256'], 'owner constraint source differs')


def prepare_owner_model(entity, provider):
    from .installed_assets import bind_models
    bindings, failures = bind_models({'entities': [entity]}, provider.resources)
    require(not failures and len(bindings) == 1, 'no unique installed owner model matches the recorded handle')
    source = provider.resolve(bindings[0]['resource'])
    skeleton = Path(entity['skeleton_file'])
    require(skeleton.is_file() and skeleton.stat().st_size <= 16*1024*1024, 'owner skeleton unavailable or oversized')
    model = dict(resource=bindings[0]['resource'], resource_sha256=source.sha256,
        source_file=str(source.local_path), skeleton_sha256=sha(skeleton),
        names=json.loads(skeleton.read_text(encoding='utf-8-sig')).get('m_boneIDs'))
    validate_owner_model(model, entity)
    return model


def model_scale(metadata, model):
    require(isinstance(metadata, dict) and set(metadata) == {
        'schema_version', 'policy', 'resource', 'source_sha256', 'settings_class',
        'settings_present', 'settings_kv3', 'scope'}, 'owner metadata fields differ')
    require(type(metadata['schema_version']) is int and metadata['schema_version'] == 1
        and metadata['policy'] == METADATA_POLICY
        and compiled(metadata['resource']) == compiled(model['resource'])
        and metadata['source_sha256'] == model['resource_sha256'], 'owner model metadata identity differs')
    require(metadata['settings_class'] == 'CitadelModelParticleSettings_t'
        and metadata['settings_present'] is True and isinstance(metadata['settings_kv3'], str)
        and len(metadata['settings_kv3']) <= 65536, 'owner particle settings are unavailable')
    values = parse_kv3(metadata['settings_kv3'])
    require(isinstance(values, dict) and set(values) == {'m_flScale'},
            'owner particle overrides require separate qualification')
    scale = values['m_flScale']
    require(type(scale) in (int, float) and math.isfinite(scale) and 0 < scale <= 100,
            'owner particle scale must be positive and finite')
    return float(scale)


def attachment_binding(table, model):
    require(isinstance(table, list) and len(table) <= 4096, 'owner attachment table is missing or exceeds bound')
    matches = [a for a in table if isinstance(a, dict) and a.get('m_name') == 'mask']
    require(len(matches) == 1, 'named mask attachment is absent or ambiguous')
    a = matches[0]
    require(type(a.get('m_nInfluences')) is int and a['m_nInfluences'] == 1
        and a.get('m_bIgnoreRotation') is False, 'mask attachment influence/rotation mode is unqualified')
    fields = ('m_influenceNames', 'm_influenceWeights', 'm_bInfluenceRootTransform',
              'm_vInfluenceOffsets', 'm_vInfluenceRotations')
    require(all(isinstance(a.get(k), list) and len(a[k]) == 3 for k in fields),
            'mask attachment influence arrays differ')
    require(a['m_influenceNames'][0] in model['names']
        and a['m_influenceNames'][1:] == ['', ''] and a['m_influenceWeights'] == [1, 0, 0]
        and all(v is False for v in a['m_bInfluenceRootTransform']),
        'mask attachment requires one exact model bone, no weighted/root/override fallback')
    offset, rotation = a['m_vInfluenceOffsets'][0], a['m_vInfluenceRotations'][0]
    require(isinstance(offset, list) and len(offset) == 3 and isinstance(rotation, list) and len(rotation) == 4
        and all(type(x) in (int, float) and math.isfinite(x) for x in offset+rotation)
        and abs(sum(x*x for x in rotation)-1) <= 2e-4, 'mask attachment transform is not finite/unit')
    return dict(name='mask', bone=a['m_influenceNames'][0], offset=offset[:], rotation_xyzw=rotation[:],
                authored_attachment_sha256=canonical_sha256(a))


def prepare_owner_metadata(model, adapter, provider, runtime):
    """Use the existing content-verified conversion cache; no geometry export."""
    from .particle_runtime import particle_command, particle_environment
    from .particle_model_assets import geometry_runtime
    default_probe, default_dotnet = geometry_runtime(runtime)
    probe = Path(runtime.get('owner_model_probe', default_probe)).resolve()
    dotnet = runtime.get('owner_model_dotnet', default_dotnet)
    source = provider.resolve(compiled(model['resource']))
    require(source.sha256 == model['resource_sha256'], 'installed owner model changed')
    parser = probe.with_name('ValveResourceFormat.dll')
    require(parser.is_file(), 'typed owner metadata parser is missing')
    settings = dict(adapter_sha256=sha(Path(__file__)), helper_sha256=sha(probe), parser_sha256=sha(parser))
    def build(temp):
        target = temp/'owner.json'
        result = subprocess.run(particle_command(probe, dotnet)+['--inventory-particle-owner-model',
            str(provider.install_path/'game/citadel/gameinfo.gi'), model['resource'].removesuffix('_c'), str(target)],
            env=particle_environment(probe, dotnet), capture_output=True, text=True, timeout=90)
        (temp/'metadata.log').write_text(result.stdout+result.stderr, encoding='utf-8')
        require(result.returncode == 0, 'select a particle helper with typed owner metadata support: '+result.stderr[-700:])
        require(target.is_file() and target.stat().st_size <= 128*1024, 'owner metadata output is missing or oversized')
        model_scale(json.loads(target.read_text(encoding='utf-8')), model)
    directory = adapter.decoder._entry('particle_owner_metadata_v1', {compiled(model['resource']): source.sha256}, settings, build)
    path = directory/'owner.json'
    return dict(file=str(path), sha256=sha(path), value=json.loads(path.read_text(encoding='utf-8')),
                helper=dict(path=str(probe), sha256=settings['helper_sha256']),
                parser=dict(path=str(parser), sha256=settings['parser_sha256']))


def validate_package(package, data):
    from .simulated_effect_package import scene_identity
    require(not any(key in package for key in ('model_reference', 'lifecycle_reference',
        'observed_model_samples', 'lifecycle_model_samples')),
        'Measured mask references require separate current-build integration')
    require(type(package.get('schema_version')) is int and package['schema_version'] == 1
        and package.get('backend') == BACKEND and isinstance(package.get('coverage_scope'), str)
        and package['coverage_scope'], 'package backend/scope differs')
    ids = package.get('entity_ids')
    require(isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], str), 'one exact owner required')
    require(package.get('scene_identity') == scene_identity(data, ids), 'scene rig/pose identity changed')
    plan = compile_mask_plan(data, ids)
    track = package.get('track')
    require(isinstance(track, dict) and track in plan['tracks'] and package.get('input_sha256') == plan['input_sha256'],
            'modifier/owner state or track changed')
    constant_track(track)
    entities = [e for e in data['entities'] if e['id'] == ids[0]]
    require(len(entities) == 1 and not entities[0].get('model_tracks'), 'owner changes model')
    model = package.get('owner_model')
    validate_owner_model(model, entities[0])
    metadata = package.get('owner_metadata', {})
    path = Path(metadata.get('file', ''))
    require(path.is_file() and path.stat().st_size <= 128*1024 and sha(path) == metadata.get('sha256')
        and json.loads(path.read_text(encoding='utf-8')) == metadata.get('value'), 'owner metadata bytes changed')
    scale = model_scale(metadata['value'], model)
    for record in (metadata.get('helper', {}), metadata.get('parser', {})):
        path = Path(record.get('path', ''))
        require(path.is_file() and sha(path) == record.get('sha256'), 'owner metadata helper/parser changed')
    require(package.get('model_scale') == scale, 'owner scale differs from typed metadata')
    table = package.get('attachment_table')
    require(package.get('attachment') == attachment_binding(table, model), 'authored attachment changed')
    assets = package.get('assets', {})
    definition = assets.get('definitions', {}).get(MODEL_CHILD)
    require(canonical_sha256(definition) == CHILD_SHA, 'model child definition changed')
    renderer = definition['m_Renderers'][0]
    descriptor = assets.get('models', {}).get(MODEL)
    validate_authored_model(renderer, descriptor)
    selection = authored_bodygroup_selection(descriptor['inventory'], descriptor['bindings'],
        initial_mask=descriptor['default_mesh_group_mask'], body_group=0, submodel=track['selector'])
    require(selection == package.get('selection') and len(selection['source_mesh_indices']) == 1,
            'authored mesh selector differs')
    required = {'client': CLIENT_SHA, 'particles': PARTICLES_SHA, 'scenesystem': SCENE_SHA, 'modifier_vdata': VDATA_SHA}
    native = package.get('native_inputs', {})
    require(set(native) == set(required), 'native/definition fingerprints missing')
    for name, expected in required.items():
        record = native[name]; path = Path(record.get('path', ''))
        require(record.get('sha256') == expected and path.is_file() and sha(path) == expected,
                'installed native/definition input changed: '+name)
    require(isinstance(package.get('rig_fingerprint'), str) and len(package['rig_fingerprint']) == 64,
            'evaluated rig identity missing')


def prepare_package(data, track, assets, adapter, provider, runtime, rig_fingerprint):
    from .simulated_effect_package import scene_identity
    constant_track(track)
    entity = next(e for e in data['entities'] if e['id'] == track['entity_id'])
    require(not entity.get('model_tracks'), 'mask provider across model changes is unqualified')
    model = prepare_owner_model(entity, provider)
    metadata = prepare_owner_metadata(model, adapter, provider, runtime)
    table = adapter.attachments(model['resource'], provider)
    binding = attachment_binding(table, model)
    native = {}
    for name, path, expected in (
        ('client', provider.install_path/'game/citadel/bin/win64/client.dll', CLIENT_SHA),
        ('particles', provider.install_path/'game/bin/win64/particles.dll', PARTICLES_SHA),
        ('scenesystem', provider.install_path/'game/bin/win64/scenesystem.dll', SCENE_SHA),
        ('modifier_vdata', provider.resolve(VDATA).local_path, VDATA_SHA)):
        require(sha(path) == expected, 'installed mask provider changed: '+name)
        native[name] = dict(path=str(path), sha256=expected)
    model_asset = assets['models'][MODEL]
    selection = authored_bodygroup_selection(model_asset['inventory'], model_asset['bindings'],
        initial_mask=model_asset['default_mesh_group_mask'], body_group=0, submodel=track['selector'])
    ids = [track['entity_id']]
    package = dict(schema_version=1, backend=BACKEND, entity_ids=ids,
        scene_identity=scene_identity(data, ids), input_sha256=compile_mask_plan(data, ids)['input_sha256'],
        track=copy.deepcopy(track), assets=copy.deepcopy(assets), owner_model=model, owner_metadata=metadata,
        model_scale=model_scale(metadata['value'], model), attachment_table=table, attachment=binding,
        native_inputs=native, selection=selection, rig_fingerprint=rig_fingerprint,
        coverage_scope='One sustained modifier-derived mask MODEL component on the evaluated exported hero. '
            'No recorded particle birth/seed/age; parent root ambient/sugar-rush layers and shader/lighting parity remain incomplete.')
    validate_package(package, data)
    return package
