"""Shared installed renderer assets for normal recorded particle exports."""
from .asset_converter import compiled
from .installed_effects import UnsupportedEffect
from .particle_model_assets import prepare_model, model_resource
from .particle_texture_layers import enabled_texture_layers


def prepare_render_assets(names, decoder, adapter, provider, runtime, *, definitions=None, textures=None):
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
    models = {}
    for name in sorted(names):
        for renderer in definitions[name].get('m_Renderers', []):
            if renderer.get('_class') != 'C_OP_RenderModels':
                continue
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
    from .particle_texture_control_defaults import prepare_defaults
    defaults=prepare_defaults(result['definitions'],provider)
    if defaults is not None:
        result['provenance']['texture_control_defaults']=defaults
    return result
