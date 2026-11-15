"""Shared installed renderer assets for normal recorded particle exports."""
from .asset_converter import compiled
from .installed_effects import UnsupportedEffect
from .particle_model_assets import prepare_model, model_resource
from .particle_texture_layers import enabled_texture_layers


def prepare_render_assets(names, decoder, adapter, provider, runtime, *, definitions=None, textures=None, allow_authored_models=False,
                          omit_unsupported_layers=False):
    """omit_unsupported_layers (broad, unverified paths only): a model layer or renderer class the importer cannot
    build is recorded under 'omitted_layers' and left out, instead of refusing the whole effect."""
    definitions = {} if definitions is None else definitions
    textures = {} if textures is None else textures
    for name in names:
        if name not in definitions:
            definitions[name] = decoder.dump(name, provider)
    texture_names = {layer['m_hTexture'] for name in names
        for renderer in definitions[name].get('m_Renderers', [])
        for _,layer in enabled_texture_layers(renderer.get('m_vecTexturesInput', [])) if layer.get('m_hTexture')
        and not layer.get('m_bReplaceTextureWithGradient')}
    for name in texture_names:
        if name not in textures:
            directory, descriptor = adapter.texture(name, provider)
            textures[name] = dict(descriptor, directory=str(directory))
        descriptor = textures[name]
        if any(len(sequence.get('frames', [])) > 1 for sequence in descriptor.get('sequences', {}).values()):
            from .particle_model_assets import geometry_runtime
            probe, dotnet = geometry_runtime(runtime)
            sheet = adapter.texture_sheet(name, provider, probe, dotnet)
            if 'source_sheet' in descriptor and descriptor['source_sheet'] != sheet:
                raise UnsupportedEffect('Conflicting original particle sheet for the same texture')
            descriptor['source_sheet'] = sheet
    models = {}
    generic_models = {}
    omitted = {}
    known = {'C_OP_RenderSprites', 'C_OP_RenderTrails', 'C_OP_RenderStandardLight', 'C_OP_RenderRopes',
             'C_OP_RenderModels', 'C_OP_RenderProjected'}
    for name in sorted(names):
        for index, renderer in enumerate(definitions[name].get('m_Renderers', [])):
            if omit_unsupported_layers and renderer.get('_class') not in known:
                omitted.setdefault(name, {})[str(index)] = 'renderer ' + str(renderer.get('_class')) + ' is not supported'
                continue
            if omit_unsupported_layers and renderer.get('_class') in ('C_OP_RenderSprites', 'C_OP_RenderTrails'):
                layers = enabled_texture_layers(renderer.get('m_vecTexturesInput', []))
                if (not layers or any(not layer.get('m_bReplaceTextureWithGradient')
                        and (not isinstance(layer.get('m_hTexture'), str) or not layer['m_hTexture'])
                        for _, layer in layers)):
                    omitted.setdefault(name, {})[str(index)] = 'sprite layer left out: it names no texture'
                continue
            if renderer.get('_class') != 'C_OP_RenderModels':
                continue
            from .particle_authored_model import is_authored_renderer, prepare_authored_model, MODEL
            if omit_unsupported_layers and not (allow_authored_models and is_authored_renderer(renderer)):
                # A model no per-effect profile covers is built by the general static path, or left out.
                import subprocess
                try:
                    resource = model_resource(renderer)
                    model, model_textures = prepare_model(adapter, renderer, provider, runtime)
                    if resource in models and models[resource] != model:
                        raise UnsupportedEffect('Conflicting model material/profile for the same model resource')
                    models[resource] = model
                except (ValueError, OSError, subprocess.SubprocessError):
                    from .particle_generic_model import key, prepare_generic_model
                    try:
                        model, model_textures = prepare_generic_model(decoder, adapter, renderer, provider)
                        generic_models[key(renderer)] = model
                    except (ValueError, OSError, subprocess.SubprocessError) as reason:
                        omitted.setdefault(name, {})[str(index)] = 'model layer left out: ' + str(reason)[:200]
                        continue
                textures.update(model_textures)
                texture_names.update(model_textures)
                continue
            if allow_authored_models and is_authored_renderer(renderer):
                from .particle_model_assets import geometry_runtime
                from .particle_runtime import particle_command, particle_environment
                from pathlib import Path
                probe, dotnet = geometry_runtime(runtime)
                material_command = runtime.get('material_command')
                material_environment = runtime.get('material_environment')
                material_probe = Path(runtime['probe']).resolve().parent.parent / 'particles' / 'ParticleProbe.dll'
                if material_command is None and material_probe.is_file():
                    material_command = particle_command(material_probe, runtime.get('dotnet'))
                    material_environment = particle_environment(material_probe, runtime.get('dotnet'))
                resource = MODEL
                model, model_textures = prepare_authored_model(adapter, renderer, provider, probe, dotnet,
                    material_command=material_command, material_environment=material_environment)
            else:
                resource = model_resource(renderer)
                model, model_textures = prepare_model(adapter, renderer, provider, runtime)
            if resource in models and models[resource] != model:
                raise UnsupportedEffect('Conflicting model material/profile for the same model resource')
            models[resource] = model
            textures.update(model_textures)
            texture_names.update(model_textures)
    result = dict(definitions={n: definitions[n] for n in names},
        textures={n: textures[n] for n in texture_names},
        provenance=dict(converter=decoder.identity,
            input_sha256={n: provider.resolve(compiled(n)).sha256 for n in names | texture_names}))
    if models:
        result['models'] = models
    if generic_models:
        result['generic_models'] = generic_models
    if omitted:
        result['omitted_layers'] = omitted
    from .particle_projected_material import collect_projected_materials
    projected_materials = collect_projected_materials(result['definitions'], decoder, adapter, provider)
    if projected_materials:
        result['projected_materials'] = projected_materials
    from .particle_rope_shader_inputs import needs_inputs,collect_inputs,PROFILE_KEY as ROPE_PROFILE
    if any(r.get('_class')=='C_OP_RenderRopes'and needs_inputs(r)
        for definition in result['definitions'].values()for r in definition.get('m_Renderers',[])):
        result['provenance'][ROPE_PROFILE]=collect_inputs(provider)
    if any(renderer.get('_class')=='C_OP_RenderStandardLight'
           for definition in result['definitions'].values() for renderer in definition.get('m_Renderers',[])):
        from .particle_standard_light import collect_native_inputs,PROFILE_KEY
        result['provenance'][PROFILE_KEY]=collect_native_inputs(provider)
    from .particle_texture_control_defaults import prepare_defaults
    defaults=prepare_defaults(result['definitions'],provider)
    if defaults is not None:
        result['provenance']['texture_control_defaults']=defaults
    if any(renderer.get('_class')=='C_OP_RenderSprites'
           and any(layer.get('m_TextureControls',{}).get('m_bRandomizeOffsets')
                   and textures.get(layer.get('m_hTexture'),{}).get('is_sprite_sheet') is True
                   for _,layer in enabled_texture_layers(renderer.get('m_vecTexturesInput',[])))
           for name in names for renderer in definitions[name].get('m_Renderers',[])):
        from .particle_sheet_uv import collect_shader_inputs
        result['provenance']['spritecard_sheet_uv']=collect_shader_inputs(provider)
    return result
