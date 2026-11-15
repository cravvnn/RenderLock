"""General 3D model layers for effects built by the unverified paths.

Any fixed model the converter can export is placed once per particle as a static mesh. The model's own materials
are used as converted; an override material is approximated from its colour texture and blend flags. Animation,
body groups, per-particle material variables and shader behaviour are not reproduced, and nothing here is compared
with the game.
"""
import math

import numpy as np

from .asset_converter import compiled, sha
from .game_assets import safe_resource_path
from .installed_effects import UnsupportedEffect
from .particle_model_math import model_matrix

POLICY = 'agr_particle_generic_model_unverified_v1'
NOTE = ('3D model layer built as a static mesh with an approximate material; animation, body groups and shader '
        'behaviour are not reproduced')
COLOUR_PARAMETERS = ('g_tColor', 'g_tColor1', 'g_tColorA', 'g_tColor0')


def fixed_model(renderer):
    """(model resource, override material or '') for a layer that draws one fixed model."""
    models = renderer.get('m_ModelList')
    if (renderer.get('_class') != 'C_OP_RenderModels' or not isinstance(models, list) or len(models) != 1
            or not isinstance(models[0], dict) or not isinstance(models[0].get('m_model'), str)):
        raise UnsupportedEffect('model layer needs exactly one fixed model')
    if renderer.get('m_modelInput') not in (None, {}):
        raise UnsupportedEffect('model chosen at run time by a control point is not supported')
    resource = safe_resource_path(models[0]['m_model'])
    if not resource.endswith('.vmdl') or resource == 'models/null.vmdl':
        raise UnsupportedEffect('model layer names no drawable model')
    override = renderer.get('m_hOverrideMaterial') or ''
    if override:
        override = safe_resource_path(override)
        if not override.endswith('.vmat'):
            raise UnsupportedEffect('model override material is not a material resource')
    return resource, override


def key(renderer):
    return '|'.join(fixed_model(renderer))


def _table(material, group, value):
    rows = material.get(group) or []
    return {row['m_name']: row[value] for row in rows
            if isinstance(row, dict) and isinstance(row.get('m_name'), str) and value in row}


def override_profile(material):
    """Blend flags and colour texture of an override material, as far as the approximation uses them."""
    ints = _table(material, 'm_intParams', 'm_nValue')
    textures = _table(material, 'm_textureParams', 'm_pValue')
    shader = str(material.get('m_shaderName', ''))
    colour = next((textures[name] for name in COLOUR_PARAMETERS if isinstance(textures.get(name), str)), None)
    additive = ints.get('F_ADDITIVE_BLEND') == 1
    return dict(shader=shader, colour_texture=colour, additive=additive,
                translucent=additive or ints.get('F_TRANSLUCENT') == 1 or ints.get('F_ALPHA_TEST') == 1,
                emissive=additive or ints.get('F_SELF_ILLUM') == 1 or 'unlit' in shader or 'pbr' not in shader)


def convert_effect_model(decoder, resource, provider):
    """Geometry and materials of one model as GLB. Kept apart from the hero conversion so its cache is untouched."""
    import json
    import shutil
    import subprocess
    from pathlib import Path
    from .asset_converter import ConversionError, read_glb
    visual = {'.vmdl_c', '.vmesh_c', '.vmat_c', '.vtex_c', '.vnmskel_c'}
    queue, records = [compiled(resource)], {}
    while queue:
        path = queue.pop()
        if path in records:
            continue
        if len(records) >= 512:
            raise ConversionError('Effect model dependency bound exceeded')
        records[path] = provider.resolve(path)
        queue.extend(dep for dep in map(compiled, records[path].dependencies) if Path(dep).suffix.lower() in visual)
    # Material texture channels are discovered from compiled shader files, as for hero models.
    for path in sorted(p for p in provider.resources if p.endswith('.vcs')):
        resolved = provider.resolve(path)
        records[resolved.resource_path] = resolved
    inputs = {name: record.sha256 for name, record in sorted(records.items())}

    def build(temp):
        tree = temp / 'input'
        tree.mkdir()
        for name, record in records.items():
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(record.local_path, target)
        gameinfo = tree / 'gameinfo.gi'
        gameinfo.write_text('"GameInfo" { "FileSystem" { "SearchPaths" { "Game" '
                            + json.dumps(tree.resolve().as_posix(), ensure_ascii=False) + ' } } }\n', encoding='utf-8')
        out = temp / 'model.glb'
        proc = subprocess.run([str(decoder.executable), '-i', str(tree / compiled(resource)), '-o', str(out), '-d',
                               '--game', str(gameinfo), '--gltf_export_format', 'glb', '--gltf_export_materials',
                               '--gltf_export_animations', '--gltf_animation_list', '__agr_no_source_actions__',
                               '--gltf_textures_adapt'],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
        (temp / 'converter.log').write_text(proc.stdout + '\n' + proc.stderr, encoding='utf-8')
        if proc.returncode or not out.is_file():
            raise ConversionError(f'Effect model conversion failed for {resource}: {(proc.stdout + proc.stderr)[-1500:]}')
        if not read_glb(out).get('meshes'):
            raise ConversionError(f'Converted effect model has no geometry: {resource}')
        shutil.rmtree(tree)
        for physics in temp.glob('model_physics.*'):
            physics.unlink()
    settings = dict(format='glb', materials=True, textures_adapt=True, policy=POLICY)
    return decoder._entry('effect_model_glb', inputs, settings, build) / 'model.glb'


def prepare_generic_model(decoder, adapter, renderer, provider):
    resource, override = fixed_model(renderer)
    glb = convert_effect_model(decoder, resource, provider)
    descriptor = dict(policy=POLICY, resource=resource, glb=str(glb), glb_sha256=sha(glb),
                      source_sha256=provider.resolve(compiled(resource)).sha256)
    textures = {}
    if override:
        profile = override_profile(decoder.dump(override, provider))
        profile.update(resource=override, sha256=provider.resolve(compiled(override)).sha256)
        name = profile['colour_texture']
        if name:
            try:
                directory, value = adapter.texture(name, provider)
                textures[name] = dict(value, directory=str(directory))
            except (ValueError, OSError):
                profile['colour_texture'] = None
        descriptor['override'] = profile
    return descriptor, textures


def _literal_vector(value):
    if value is None:
        return None
    if (isinstance(value, dict) and value.get('m_nType', 'PVEC_TYPE_LITERAL') == 'PVEC_TYPE_LITERAL'
            and isinstance(value.get('m_vLiteralValue'), (list, tuple)) and len(value['m_vLiteralValue']) == 3
            and all(type(v) in (int, float) and math.isfinite(v) for v in value['m_vLiteralValue'])):
        return [float(v) for v in value['m_vLiteralValue']]
    raise UnsupportedEffect('not a literal vector')


def placement(renderer):
    """Renderer settings reduced to what the static placement uses, plus notes for what was dropped."""
    notes = []
    reduced = {name: renderer[name] for name in ('m_bIgnoreNormal', 'm_bOrientZ', 'm_bIgnoreRadius', 'm_bLocalScale')
               if type(renderer.get(name)) is bool}
    try:
        scale = _literal_vector(renderer.get('m_vecComponentScale'))
        if scale is not None and all(0 <= v <= 1e6 for v in scale):
            reduced['m_vecComponentScale'] = dict(m_nType='PVEC_TYPE_LITERAL', m_vLiteralValue=scale)
    except UnsupportedEffect:
        notes.append('model per-axis scale is driven at run time and was left at 1')
    rotation = None
    try:
        rotation = _literal_vector(renderer.get('m_vecLocalRotation'))
    except UnsupportedEffect:
        notes.append('model local rotation is driven at run time and was left out')
    return reduced, rotation, notes


def particle_matrix(particle, reduced, rotation):
    """World matrix in Source units; local rotation is pitch, yaw, roll in degrees applied in the model's frame."""
    matrix = model_matrix(particle, reduced)
    if rotation and any(rotation):
        pitch, yaw, roll = (math.radians(v) for v in rotation)
        sy, cy, sp, cp, sr, cr = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch), math.sin(roll), math.cos(roll)
        local = np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                          [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr], [-sp, cp * sr, cp * cr]])
        matrix[:3, :3] = matrix[:3, :3] @ local
    return matrix
