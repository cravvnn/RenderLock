"""Optional measured slide trails through the existing package transaction."""
import json
from pathlib import Path
import subprocess
from .observed_slide_trail_states import BACKEND, OWNER, RESOURCE, SCOPE, validate_reference, prepare_package


def build_observed_slide_trail_effects(data, scene, camera, entity_objects, runtime, provider, decoder, adapter):
    from .particle_render_assets import prepare_render_assets
    from .particle_sheet_uv import collect_shader_inputs
    from .simulated_effect_package import build_package_effects
    report = dict(backend=BACKEND, status='partial', objects=0, particle_tracks=0, point_lights=0,
                  rendered_collection_layers=0, collection_frame_meshes=0, unsupported=[], coverage_scope=SCOPE)
    if OWNER not in entity_objects:
        report.update(status='not_selected', reason='Native reference owner is not selected')
        return report
    try:
        from .observed_slide_cohort import validate_slide_cohort
        validate_slide_cohort(runtime)
        reference = runtime['observed_slide_trail_reference'];validate_reference(reference, data)
        assets = prepare_render_assets({RESOURCE}, decoder, adapter, provider, runtime)
        assets['provenance']['spritecard_sheet_uv'] = collect_shader_inputs(provider)
        package = prepare_package(data, reference, assets)
        path = Path(runtime['output_dir'])/'native-slide-trails.package.json'
        with path.open('x', encoding='utf-8') as stream:json.dump(package, stream)
        rendered = build_package_effects(package, data, path, scene, camera)
        for key in ('objects', 'particle_tracks', 'point_lights', 'rendered_collection_layers', 'collection_frame_meshes', 'point_samples'):
            report[key] = rendered.get(key, 0)
        report.update(package=str(path), rendering=rendered)
        report['unsupported'].append(dict(resource=RESOURCE, occurrences=1,
            reason='Trail geometry/UV/color component rendered; Source lighting/fog/depth/pixel parity remain unqualified',
            coverage_scope='Unresolved renderer appearance component; not a recorded birth count'))
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(status='unavailable', reason=str(exc))
        report['unsupported'].append(dict(resource=RESOURCE, occurrences=1, reason=str(exc),
            coverage_scope='Native trail stage unavailable; no recorded birth inferred'))
    return report


def merge_observed_slide_trail_report(report, observed):
    report['observed_slide_trail_effects'] = observed
    for key in ('objects', 'point_lights'):report[key] = report.get(key, 0)+observed.get(key, 0)
    report.setdefault('unsupported', []).extend(observed.get('unsupported', []))
    report.setdefault('limitations', []).append(observed['coverage_scope'])
