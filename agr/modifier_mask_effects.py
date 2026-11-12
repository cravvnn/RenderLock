"""Normal export stage for independently reported client-derived mask models."""
import json
from pathlib import Path
import subprocess
from .modifier_mask_plan import compile_mask_plan, MODEL_CHILD, ROOT
from .modifier_mask_model import BACKEND, prepare_package, constant_track


def merge_modifier_mask_report(report, derived):
    """Keep recorded-instance counts/birth scopes unchanged; add real geometry."""
    report['modifier_model_effects'] = derived
    if derived.get('status') == 'unavailable':
        report.update(status='unavailable', reason=derived.get('reason', 'Modifier-derived model coverage is unavailable'))
    for key in ('objects', 'particle_tracks', 'point_lights'):
        report[key] = report.get(key, 0)+derived.get(key, 0)
    report.setdefault('unsupported', []).extend(derived.get('unsupported', []))
    report.setdefault('limitations', []).append(derived['coverage_scope'])
    if derived.get('reason'):
        report['limitations'].append('Modifier-derived model stage unavailable: '+derived['reason'])


def build_modifier_mask_effects(data, scene, camera, entity_objects, runtime, provider, decoder, adapter):
    from .blender_particle_skinning import snapshot_rig_fingerprint
    from .particle_render_assets import prepare_render_assets
    from .simulated_effect_package import build_package_effects
    report = dict(backend=BACKEND, status='partial', candidate_model_branches=0,
        rendered_model_branches=0, objects=0, particle_tracks=0, point_lights=0, branches=[], unsupported=[],
        coverage_scope='Modifier-derived MODEL components are reported separately from recorded particle instances. '
            'No recovered particle birth/seed/phase or complete parent-root coverage is asserted.')
    selected = sorted({e['id'] for e in data['entities'] if e['id'] in entity_objects})
    try:
        plan = compile_mask_plan(data, selected)
    except (ValueError, KeyError, TypeError) as exc:
        report.update(status='unavailable', reason=str(exc))
        return report
    report['candidate_model_branches'] = len(plan['tracks'])
    if not plan['tracks']:
        report.update(status='not_applicable', reason='No candidates in the qualified sustained mask profile; other modifier effects remain outside this stage.')
        return report
    assets = None
    if any(runtime.get(key) is not None for key in ('modifier_mask_reference', 'modifier_mask_lifecycle_reference')):
        report.update(status='unavailable', reason='Measured mask lifecycle references require separate current-build integration; sustained positive gates are required.')
        return report
    for track in plan['tracks']:
        example = f"modifier:{track['serial_number']}@{track['entity_id']}"
        try:
            constant_track(track)
            objects = entity_objects[track['entity_id']]
            rig = objects.get('rig')
            if rig is None: raise ValueError('Modifier mask owner rig is missing or changes model')
            if assets is None:
                assets = prepare_render_assets({MODEL_CHILD}, decoder, adapter, provider, runtime, allow_authored_models=True)
            package = prepare_package(data, track, assets, adapter, provider, runtime, snapshot_rig_fingerprint(rig))
            path = Path(runtime['output_dir'])/f"modifier-mask-{track['serial_number']}.package.json"
            with path.open('x', encoding='utf-8') as stream: json.dump(package, stream)
            result = build_package_effects(package, data, path, scene, camera)
            report['rendered_model_branches'] += 1
            report['branches'].append(dict(serial_number=track['serial_number'], entity_id=track['entity_id'],
                package=str(path), rendering=result))
            for key in ('objects', 'particle_tracks', 'point_lights'): report[key] += result.get(key, 0)
        except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            report['unsupported'].append(dict(resource=MODEL_CHILD, reason=str(exc), occurrences=1,
                instance_examples=[example], coverage_scope='Client-derived model component; particle birth is unknown'))
    if plan['tracks']:
        report['unsupported'].append(dict(resource=ROOT,
            reason='Parent mask ambient/sugar-rush layer activation and phase remain unqualified',
            occurrences=len(plan['tracks']), instance_examples=[f"modifier:{t['serial_number']}" for t in plan['tracks'][:3]],
            coverage_scope='Unresolved parent-root layers, separately from its model component'))
    if not report['objects']: report['status'] = 'unavailable'
    return report
