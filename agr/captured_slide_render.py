"""One allocation transaction for an explicitly supplied bound slide cohort."""
from pathlib import Path

from .captured_slide_inputs import BACKENDS, require, validate_bound_cohort
from .replay_capture import canonical_sha256

GROUPS = ('objects', 'collections', 'meshes', 'shape_keys', 'materials', 'lights',
          'images', 'actions', 'node_groups', 'texts')
BACKEND = 'bound_captured_slide_cohort_v1'


def expected_receipt(bound):
    """Caller validates bound inputs first; these counts are frame-local meshes."""
    packages = bound['original']['packages']
    sprite, trail, light, model = [p['reference']['capture'] for p in packages]
    counts = [sum(len(row['renderers']) for frame in sprite['samples'] for row in frame['collections']),
              sum(bool(row['points']) for frame in trail['frames'] for row in frame['systems']),
              sum(len(row['points']) for frame in light['frames'] for row in frame['systems']),
              sum(len(row['models']) for frame in model['frames'] for row in frame['systems'])]
    return dict(schema_version=1, backend=BACKEND,
                descriptor_sha256=bound['original']['provenance']['descriptor']['sha256'],
                binding_sha256=bound['binding']['content_sha256'],
                source_scene_identity=bound['binding']['source_scene_identity'],
                target_scene_identity=bound['scene_identity'],
                components=[dict(backend=name, reference_sha256=p['reference']['capture_sha256'],
                                 objects=count, point_lights=count if i == 2 else 0)
                            for i, (name, p, count) in enumerate(zip(BACKENDS, packages, counts))])


def validate_completion(report, expected):
    """A requested imported cohort must finish even in a partial scene export."""
    require(type(report) is dict and report.get('status') == 'partial'
            and report.get('backend') == BACKEND
            and canonical_sha256(report.get('input_receipt')) == canonical_sha256(expected),
            'requested cohort completion receipt missing or different')
    rows = report.get('components')
    require(type(rows) is list and len(rows) == len(BACKENDS), 'incomplete cohort rendering')
    for row, wanted in zip(rows, expected['components']):
        require(type(row) is dict and row.get('backend') == wanted['backend']
                and row.get('status') == 'partial' and row.get('stage') == 'rendering'
                and row.get('scene_identity') == expected['source_scene_identity']
                and row.get('entity_ids') == ['56:895']
                and row.get('observed_reference_sha256') == wanted['reference_sha256'],
                'component rendering identity differs')
        for key in ('objects', 'point_lights'):
            require(type(row.get(key)) is int and row[key] == wanted[key],
                    'component rendering count differs: ' + key)
    for key in ('objects', 'point_lights'):
        require(type(report.get(key)) is int and report[key] == sum(r[key] for r in rows),
                'cohort aggregate rendering count differs')


def build_captured_slide_cohort(bound, data, manifest_file, scene, camera):
    from .simulated_effect_package import build_package_effects
    # Complete source/fresh binding and all four native/material validators run
    # before any allocation. The inner transactions cannot own earlier siblings.
    validate_bound_cohort(bound, data)
    expected = expected_receipt(bound)
    import bpy
    before = {key: set(getattr(bpy.data, key)) for key in GROUPS}
    try:
        rows = [build_package_effects(package, bound['original']['source_scene'],
                                      Path(manifest_file), scene, camera)
                for package in bound['original']['packages']]
        report = dict(status='partial', stage='rendering', backend=BACKEND,
                      objects=sum(r['objects'] for r in rows),
                      point_lights=sum(r.get('point_lights', 0) for r in rows),
                      particle_tracks=0, components=rows, input_receipt=expected,
                      coverage_scope=bound['coverage_scope'])
        validate_completion(report, expected)
        return report
    except Exception:
        created = [item for key in GROUPS for item in set(getattr(bpy.data, key)) - before[key]]
        bpy.data.batch_remove(created)
        raise


def merge_captured_slide_report(report, captured):
    report['captured_slide_effects'] = captured
    for key in ('objects', 'point_lights'):
        report[key] = report.get(key, 0) + captured[key]
    report.setdefault('limitations', []).append(captured['coverage_scope'])
    # Captured frame arrays do not establish recorded births, erase unsupported
    # instances or turn the surrounding replay export into complete coverage.
