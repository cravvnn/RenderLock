"""Read-only compatibility report for the installed game build.

This is a diagnostic, not a new qualification path. It reports which of the
existing qualified resource identities and layouts the installed build still
matches, so a game update can be triaged before a long export.
"""
from __future__ import annotations

from pathlib import Path

from .asset_converter import ConversionError
from .game_assets import GameAssetProvider
from .installed_map import selected_world
from .source_brdf_assets import RESOURCE as BRDF_RESOURCE, SOURCE_SHA256 as BRDF_SHA256
from .source_lightmap import SHADER_RESOURCE as DEFERRED_SHADER, SHADER_SHA256 as DEFERRED_SHA256
from .source_npr_package import SHADER_RESOURCE as NPR_SHADER, QUALIFIED_SHADER_SHA256
from .source_probe_layout import derive_probe_layout, read_texture_layout
from .material_expressions import expression_module_status


def _steam_build(install_path):
    path = Path(install_path) / 'game/citadel/steam.inf'
    result = {'path': str(path), 'exists': path.is_file()}
    if not path.is_file():
        return result
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            if key in ('ClientVersion', 'ServerVersion', 'SourceRevision', 'VersionDate', 'VersionTime'):
                result[key] = value
    return result


def _identity(provider, resource):
    result = dict(provider.resource_identity(resource))
    result['resource'] = resource
    return result


def _probe_resources(world_resource):
    root = str(Path(world_resource).parent).replace('\\', '/')
    return (root + '/lightmaps/env_light_probe_volume_atlas.vtex_c',
            root + '/cubemaps/env_cubemap_array.vtex_c')


def installed_compatibility(provider):
    """Return a JSON-safe report for an already mounted GameAssetProvider."""
    report = {
        'application': 'RenderLock compatibility',
        'game': _steam_build(provider.install_path),
        'map_name': getattr(provider, 'map_name', None),
        'status': 'unknown',
        'character_lighting': {'status': 'unknown'},
        'static_map_lighting': {'status': 'unknown'},
        'probe_layout': None,
        'resources': {},
        'reasons': [],
    }
    reasons = report['reasons']
    layout_reasons = []
    character_reasons = []
    static_reasons = []
    try:
        world = selected_world(provider)
        world_identity = _identity(provider, world)
        report['resources']['world'] = world_identity
        atlas, cubemap = _probe_resources(world)
        atlas_identity = _identity(provider, atlas)
        cubemap_identity = _identity(provider, cubemap)
        report['resources']['atlas'] = atlas_identity
        report['resources']['cubemap'] = cubemap_identity
        try:
            layout = derive_probe_layout(
                read_texture_layout(provider.resolve(atlas).local_path),
                read_texture_layout(provider.resolve(cubemap).local_path),
                atlas_resource=atlas, atlas_sha256=atlas_identity['sha256'],
                cubemap_resource=cubemap, cubemap_sha256=cubemap_identity['sha256'])
            report['probe_layout'] = layout
        except Exception as exc:
            report['probe_layout'] = {'status': 'failed', 'error': str(exc),
                                      'error_type': type(exc).__name__}
            message = 'Probe atlas/cubemap layout: ' + str(exc)
            character_reasons.append(message)
            layout_reasons.append(message)
    except Exception as exc:
        message = 'Map/probe resources: ' + str(exc)
        character_reasons.append(message)
        static_reasons.append(message)
        report['resources']['map_error'] = {'error': str(exc), 'error_type': type(exc).__name__}

    def shader(resource, qualified, label):
        try:
            identity = _identity(provider, resource)
            ok = identity['sha256'] in qualified if isinstance(qualified, (set, frozenset, tuple, list)) else identity['sha256'] == qualified
            report['resources'][label] = dict(identity, qualified=bool(ok))
            reason = None if ok else label + ' changed: ' + identity['sha256']
            return bool(ok), reason
        except Exception as exc:
            report['resources'][label] = {'resource': resource, 'error': str(exc)}
            return False, label + ': ' + str(exc)

    npr_ok, npr_reason = shader(NPR_SHADER, QUALIFIED_SHADER_SHA256, 'npr_pbr_shader')
    evaluator=expression_module_status(provider)
    report['material_expression_evaluator']=evaluator
    brdf_ok, brdf_reason = shader(BRDF_RESOURCE, BRDF_SHA256, 'brdf_lut')
    deferred_ok, deferred_reason = shader(DEFERRED_SHADER, DEFERRED_SHA256, 'static_lightmap_shader')
    if npr_reason:
        character_reasons.append(npr_reason)
    if brdf_reason:
        character_reasons.append(brdf_reason)
        static_reasons.append(brdf_reason)
    if deferred_reason:
        static_reasons.append(deferred_reason)
    layout_ok = isinstance(report['probe_layout'], dict) and report['probe_layout'].get('schema_version') == 1
    hero_ok = layout_ok and brdf_ok
    hero_reasons = list(dict.fromkeys(layout_reasons + ([brdf_reason] if brdf_reason else [])))
    npr_reasons = list(dict.fromkeys(hero_reasons + ([npr_reason] if npr_reason else [])))
    report['hero_lighting'] = {
        'status': 'ready' if hero_ok else 'requalification_required',
        'scope': 'Installed hero probe atlas/cubemap, probe layout and BRDF',
        'reasons': hero_reasons,
    }
    report['npr_lighting'] = {
        'status': 'selected_material_check_required' if hero_ok and npr_ok else 'requalification_required',
        'resource_status': 'ready' if hero_ok and npr_ok else 'requalification_required',
        'scope': 'NPR resource prerequisites; selected material routes and constant expressions require export preflight',
        'reasons': npr_reasons,
    }
    report['character_lighting'] = {
        'status': 'selected_material_check_required' if hero_ok and npr_ok else 'requalification_required',
        'resource_status': 'ready' if hero_ok and npr_ok else 'requalification_required',
        'scope': 'Installed character lighting resources; selected-material and animation validation remains required',
        'reasons': npr_reasons,
    }
    report['selected_materials'] = {
        'status':'not_checked',
        'scope':'Selected NPR models/materials are checked before animation and scene-extension preparation.',
        'conditional_evaluator_required':True,
        'evaluator_status':evaluator['status'],
        'reasons':([] if evaluator['status']=='ready' else
                   ['Selected constant-color expressions require evaluator qualification: '+evaluator.get('reason','unknown module')]),
    }
    report['static_map_lighting'] = {
        'status': 'ready' if deferred_ok and brdf_ok else 'requalification_required',
        'scope': 'Static map diffuse lightmaps; not used by character-only exports',
        'reasons': list(dict.fromkeys(static_reasons)),
    }
    reasons.extend(character_reasons)
    reasons.extend(static_reasons)
    reasons.extend(report['selected_materials']['reasons'])
    report['reasons'] = list(dict.fromkeys(reasons))
    report['status'] = ('selected_material_check_required' if hero_ok and npr_ok
                        else 'requalification_required')
    return report


def check_game_install(install_path, cache_dir=None, map_name=None):
    """Open an installed game and return a compatibility report."""
    import tempfile
    if cache_dir is None:
        with tempfile.TemporaryDirectory(prefix='renderlock-compat-') as temporary:
            provider = GameAssetProvider(install_path, temporary, map_name=map_name)
            return installed_compatibility(provider)
    provider = GameAssetProvider(install_path, cache_dir, map_name=map_name)
    return installed_compatibility(provider)
