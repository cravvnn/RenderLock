"""Optional source-bound slide models under the existing package transaction."""
import json
from pathlib import Path
from .observed_slide_model_states import BACKEND, OWNER, RESOURCES, SCOPE, validate_reference, prepare_package
from .observed_slide_cohort import validate_slide_cohort


def build_observed_slide_model_effects(data, scene, camera, entity_objects, runtime):
    from .simulated_effect_package import build_package_effects
    report = dict(backend=BACKEND, status='partial', objects=0, point_lights=0, particle_tracks=0,
                  rendered_collection_layers=0, collection_frame_meshes=0, unsupported=[], coverage_scope=SCOPE)
    if OWNER not in entity_objects:
        report.update(status='not_selected', reason='Native reference owner is not selected')
        return report
    try:
        validate_slide_cohort(runtime)
        reference = runtime['observed_slide_model_reference'];validate_reference(reference, data)
        package = prepare_package(data, reference, runtime['observed_slide_model_assets'])
        path = Path(runtime['output_dir'])/'native-slide-models.package.json'
        with path.open('x', encoding='utf-8') as stream:
            json.dump(package, stream, separators=(',', ':'))
        rendered = build_package_effects(package, data, path, scene, camera)
        for key in ('objects', 'point_lights', 'particle_tracks', 'rendered_collection_layers', 'collection_frame_meshes'):
            report[key] = rendered.get(key, 0)
        report.update(package=str(path), rendering=rendered)
        for resource in sorted(RESOURCES):
            report['unsupported'].append(dict(resource=resource, occurrences=1, reason=SCOPE,
                coverage_scope='Remaining model appearance/clock qualification; not a recorded birth count'))
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        report.update(status='unavailable', reason=str(exc))
        for resource in sorted(RESOURCES):
            report['unsupported'].append(dict(resource=resource, occurrences=1, reason=str(exc),
                coverage_scope='Source-bound model stage unavailable; no recorded birth inferred'))
    return report


def merge_observed_slide_model_report(report, observed):
    report['observed_slide_model_effects'] = observed
    if observed.get('rendering') and observed.get('objects', 0):
        # Replace only explicit renderer-component notes, preserving recorded
        # unsupported births and every other coverage counter.
        report['unsupported'] = [row for row in report.get('unsupported', [])
            if not (row.get('resource') in RESOURCES
                and row.get('coverage_scope') == 'Unresolved native renderer component; not a recorded birth count'
                and row.get('instance_examples') == ['native-owner:'+OWNER]
                and row.get('reason') == 'Native inputs captured; this sprite component does not qualify this renderer')]
    for key in ('objects', 'point_lights'):
        report[key] = report.get(key, 0)+observed.get(key, 0)
    report.setdefault('unsupported', []).extend(observed.get('unsupported', []))
    report.setdefault('limitations', []).append(observed['coverage_scope'])
