"""Content-verified animated particle geometry, separate from material decoding."""
import json
import math
from pathlib import Path
import subprocess

from .asset_converter import compiled, sha, ConversionError
from .game_assets import safe_resource_path
from .installed_effects import UnsupportedEffect
from .particle_model_geometry import read_model_geometry
from .particle_model_math import component_scale
from .particle_runtime import particle_command, particle_environment

POLICY = 'agr_particle_model_morph_v1'
MATERIAL_VARIABLES = [
    [{'m_strVariable': '$SELFILLUM'}, {'m_strVariable': '$ILLUMCOLOR', 'm_nVariableField': 6}],
    [{'m_strVariable': '$SELFILLUM'}, {'m_strVariable': '$SELFILLUMCOLOR', 'm_nVariableField': 17}],
]


def animation_rate(renderer):
    value = renderer.get('m_flAnimationRate', 30)
    if isinstance(value, dict):
        if (set(value) - {'m_nType', 'm_nMapType', 'm_flLiteralValue'}
                or value.get('m_nType', 'PF_TYPE_LITERAL') != 'PF_TYPE_LITERAL'
                or value.get('m_nMapType', 'PF_MAP_TYPE_DIRECT') != 'PF_MAP_TYPE_DIRECT'):
            raise UnsupportedEffect('Model animation rate requires a literal provider')
        value = value.get('m_flLiteralValue', 30)
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1000:
        raise UnsupportedEffect('Invalid model animation rate')
    return value


def model_resource(renderer):
    from .particle_flashbang_sphere import is_renderer,MODEL
    if is_renderer(renderer):return MODEL
    allowed = {'_class', 'm_ModelList', 'm_nBodyGroupField', 'm_nSubModelField',
        'm_bAnimated', 'm_bForceLoopingAnimation', 'm_hOverrideMaterial', 'm_MaterialVars',
        'm_modelInput', 'm_bDisableShadows', 'm_bAcceptsDecals', 'm_flAnimationRate',
        'm_bIgnoreNormal', 'm_bOrientZ', 'm_bIgnoreRadius', 'm_bManualAnimFrame',
        'm_nManualFrameField', 'm_bLocalScale', 'm_vecComponentScale'}
    if renderer.get('_class') != 'C_OP_RenderModels' or set(renderer) - allowed:
        raise UnsupportedEffect('Unqualified model renderer inputs')
    models = renderer.get('m_ModelList')
    if (not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict)
            or set(models[0]) != {'m_model'} or renderer.get('m_modelInput', {}) != {}):
        raise UnsupportedEffect('Model particles require one fixed model without a dynamic provider')
    for key in ('m_bIgnoreNormal', 'm_bOrientZ', 'm_bIgnoreRadius', 'm_bManualAnimFrame', 'm_bLocalScale'):
        if type(renderer.get(key, False)) is not bool:
            raise UnsupportedEffect('Invalid model renderer boolean: ' + key)
    if (renderer.get('m_bAnimated') is not True or renderer.get('m_bForceLoopingAnimation') is not True
            or renderer.get('m_bDisableShadows') is not True or renderer.get('m_bAcceptsDecals') is not False
            or type(renderer.get('m_nBodyGroupField', 19)) is not int or renderer.get('m_nBodyGroupField', 19) != 19
            or type(renderer.get('m_nSubModelField', 13)) is not int or renderer.get('m_nSubModelField', 13) != 13):
        raise UnsupportedEffect('Unqualified model selector, animation, shadow or decal mode')
    axes = component_scale(renderer)
    if not renderer.get('m_bLocalScale', False) and max(axes) - min(axes) > 1e-8:
        raise UnsupportedEffect('Non-local anisotropic model scale requires a shear-preserving path')
    if renderer.get('m_MaterialVars') not in MATERIAL_VARIABLES:
        raise UnsupportedEffect('Unqualified model material variable bindings')
    if renderer.get('m_bManualAnimFrame', False):
        if type(renderer.get('m_nManualFrameField')) is not int or renderer['m_nManualFrameField'] != 38:
            raise UnsupportedEffect('Model manual animation requires captured field38')
    elif 'm_nManualFrameField' in renderer:
        raise UnsupportedEffect('Unused model manual frame selector')
    resource = safe_resource_path(models[0]['m_model'])
    material = safe_resource_path(renderer.get('m_hOverrideMaterial', ''))
    if not resource.endswith('.vmdl') or not material.endswith('.vmat'):
        raise UnsupportedEffect('Model particle requires explicit model and override material resources')
    animation_rate(renderer)
    return resource


def geometry_runtime(runtime):
    """Explicit development override or separate bundled geometry process."""
    if runtime.get('geometry_probe'):
        probe = Path(runtime['geometry_probe']).resolve()
    else:
        simulator = Path(runtime['probe']).resolve()
        probe = simulator.parent.parent / 'particle_geometry' / ('ParticleGeometryProbe' + simulator.suffix)
    dotnet = runtime.get('geometry_dotnet', runtime.get('dotnet'))
    if not probe.is_file():
        raise UnsupportedEffect('Animated model particles require the separate ParticleGeometryProbe runtime')
    simulator_parser = Path(runtime['probe']).resolve().parent / 'ValveResourceFormat.dll'
    parser = probe.parent / 'ValveResourceFormat.dll'
    if parser.resolve() == simulator_parser.resolve():
        raise UnsupportedEffect('Particle geometry and material parsers must use separate directories')
    particle_command(probe, dotnet)
    return probe, dotnet


def prepare_model(adapter, renderer, provider, runtime):
    from .particle_flashbang_sphere import is_renderer,POLICY as STATIC_POLICY
    static_sphere=is_renderer(renderer)
    resource = model_resource(renderer)
    material_name = renderer['m_hOverrideMaterial']
    probe, dotnet = geometry_runtime(runtime)
    decoder = adapter.decoder
    source = provider.resolve(compiled(resource))
    pending = [compiled(resource)]
    inputs = {}
    while pending:
        name = pending.pop()
        if name in inputs:
            continue
        if len(inputs) >= 256:
            raise ConversionError('Particle model dependency bound exceeded')
        resolved = provider.resolve(name)
        inputs[name] = resolved.sha256
        pending.extend(compiled(n) for n in resolved.dependencies)
    material = decoder.dump(material_name, provider)
    inputs[compiled(material_name)] = provider.resolve(compiled(material_name)).sha256
    if static_sphere:
        from .particle_flashbang_sphere import MODEL_SHA,MATERIAL_SHA,material_profile,require
        require(source.sha256==MODEL_SHA and inputs[compiled(material_name)]==MATERIAL_SHA,
            'installed static model/material source changed')
        material_profile(material)
    identity = dict(policy=STATIC_POLICY if static_sphere else POLICY, adapter_sha256=sha(Path(__file__)),
        reader_sha256=sha(Path(__file__).with_name('particle_model_geometry.py')),
        helper_sha256=sha(probe), parser_sha256=sha(probe.parent / 'ValveResourceFormat.dll'))
    if static_sphere:
        identity['static_profile_sha256']=sha(Path(__file__).with_name('particle_flashbang_sphere.py'))
    def build(temp):
        output = temp / 'model'
        result = subprocess.run(particle_command(probe, dotnet) + ['--particle-static-model-v1' if static_sphere else '--particle-model-v1',
            str(Path(provider.install_path) / 'game/citadel/gameinfo.gi'), resource, str(output)],
            env=particle_environment(probe, dotnet), capture_output=True, text=True, timeout=180)
        (temp / 'model.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise ConversionError('Particle geometry-only export failed: ' + (result.stdout + result.stderr)[-1500:])
        descriptor = json.loads((output / 'model.json').read_text(encoding='utf-8'))
        if descriptor.get('source_sha256') != source.sha256 or descriptor.get('resource') != resource:
            raise ConversionError('Particle model source identity mismatch')
        read_model_geometry(output / 'model.glb')
        if static_sphere:
            from .particle_flashbang_sphere import validate_descriptor
            validate_descriptor(renderer,dict(descriptor,directory=str(output),geometry_sha256=sha(output/'model.glb'),
                material=material,material_resource=material_name,material_sha256=inputs[compiled(material_name)]))
    destination = decoder._entry('particle_model_geometry', inputs, identity, build) / 'model'
    descriptor = json.loads((destination / 'model.json').read_text(encoding='utf-8'))
    if descriptor.get('source_sha256') != source.sha256 or descriptor.get('resource') != resource:
        raise ConversionError('Cached particle model source identity mismatch')
    descriptor.update(directory=str(destination), geometry_sha256=sha(destination / 'model.glb'),
        material=material, material_resource=material_name, material_sha256=inputs[compiled(material_name)],
        converter_identity=identity)
    validate_model(renderer, {'models': {resource: descriptor}})
    textures = {}
    for name in sorted({p['m_pValue'] for p in material.get('m_textureParams', [])}):
        directory, value = adapter.texture(name, provider)
        textures[name] = dict(value, directory=str(directory))
    return descriptor, textures


def validate_model(renderer, assets):
    resource = model_resource(renderer)
    descriptor = assets.get('models', {}).get(resource)
    from .particle_flashbang_sphere import is_renderer,validate_descriptor
    if is_renderer(renderer):return validate_descriptor(renderer,descriptor)
    if (not isinstance(descriptor, dict) or descriptor.get('schema_version') != 1
            or descriptor.get('policy') != POLICY or descriptor.get('resource') != resource
            or type(descriptor.get('bones')) is not int or descriptor['bones'] != 0
            or descriptor.get('material_exported') is not False
            or descriptor.get('material_resource') != renderer['m_hOverrideMaterial']
            or not isinstance(descriptor.get('mesh_groups'), list) or len(descriptor['mesh_groups']) > 1
            or type(descriptor.get('body_groups')) is not int or descriptor['body_groups'] != 0):
        raise UnsupportedEffect('Missing or unqualified particle model rig/material descriptor')
    animations = descriptor.get('animations', [])
    if not isinstance(animations, list) or len(animations) != 1 or not isinstance(animations[0], dict):
        raise UnsupportedEffect('Model particle requires one authored morph animation')
    animation = animations[0]
    if (animation.get('has_flex_data') is not True or type(animation.get('index')) is not int
            or animation['index'] != 0 or type(animation.get('fps')) not in (int, float)
            or not math.isfinite(animation['fps']) or not 0 < animation['fps'] <= 1000
            or type(animation.get('frames')) is not int or not 2 <= animation['frames'] <= 4096
            or type(animation.get('cycle_frames')) is not int or animation['cycle_frames'] != animation['frames'] - 1):
        raise UnsupportedEffect('Unqualified particle model animation metadata')
    path = Path(descriptor['directory']) / 'model.glb'
    if descriptor.get('geometry_file') != 'model.glb' or not path.is_file() or sha(path) != descriptor.get('geometry_sha256'):
        raise UnsupportedEffect('Particle model geometry identity mismatch')
    geometry = read_model_geometry(path)
    import numpy as np
    if (geometry.get('static') or geometry['animation_name'] != animation.get('name')
            or len(geometry['times']) != animation['frames']
            or not np.allclose(geometry['times'], np.arange(animation['frames']) / animation['fps'], atol=2e-7, rtol=0)):
        raise UnsupportedEffect('Particle morph animation metadata differs from geometry')
    from .blender_particle_models import material_profile
    _, profile = material_profile(descriptor['material'])
    if profile['_particle_emission_tint'] != (renderer['m_MaterialVars'] == MATERIAL_VARIABLES[0]):
        raise UnsupportedEffect('Model renderer attributes differ from the material profile')
    return descriptor
