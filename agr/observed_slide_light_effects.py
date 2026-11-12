"""Optional evaluated slide light stage; existing package transaction owns data."""
import json
from pathlib import Path
from .observed_slide_light_states import BACKEND, OWNER, RESOURCE, SCOPE, validate_reference, prepare_package


def build_observed_slide_light_effects(data, scene, camera, entity_objects, runtime):
    from .simulated_effect_package import build_package_effects
    report = dict(backend=BACKEND, status='partial', objects=0, point_lights=0, particle_tracks=0,
                  rendered_collection_layers=0, unsupported=[], coverage_scope=SCOPE)
    if OWNER not in entity_objects:
        report.update(status='not_selected', reason='Native reference owner is not selected')
        return report
    try:
        from .observed_slide_cohort import validate_slide_cohort
        validate_slide_cohort(runtime)
        reference = runtime['observed_slide_light_reference'];c = validate_reference(reference, data)
        package = prepare_package(data, reference)
        path = Path(runtime['output_dir'])/'native-slide-lights.package.json'
        with path.open('x', encoding='utf-8') as stream:json.dump(package, stream)
        rendered = build_package_effects(package, data, path, scene, camera)
        for key in ('objects', 'point_lights', 'rendered_collection_layers', 'point_samples', 'light_adaptation'):
            if key in rendered:report[key] = rendered[key]
        report.update(package=str(path), rendering=rendered)
        report['unsupported'].append(dict(resource=RESOURCE, occurrences=1, reason=SCOPE,
            coverage_scope='Remaining appearance/clock qualification; not a recorded birth count'))
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        report.update(status='unavailable', reason=str(exc))
        report['unsupported'].append(dict(resource=RESOURCE, occurrences=1, reason=str(exc),
            coverage_scope='Evaluated light stage unavailable; no recorded birth inferred'))
    return report


def merge_observed_slide_light_report(report, observed):
    report['observed_slide_light_effects'] = observed
    for key in ('objects', 'point_lights'):report[key] = report.get(key, 0)+observed.get(key, 0)
    report.setdefault('unsupported', []).extend(observed.get('unsupported', []))
    report.setdefault('limitations', []).append(observed['coverage_scope'])
