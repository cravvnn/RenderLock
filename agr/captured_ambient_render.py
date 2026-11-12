"""All-component transaction and explicit independent-capture completion receipts."""
import copy
from pathlib import Path

from .captured_ambient_inputs import BACKENDS, require, validate_bound_inputs
from .captured_slide_render import GROUPS
from .replay_capture import canonical_sha256

BACKEND = 'bound_captured_ambient_inputs_v1'


def expected_receipt(bound):
    components = []
    for name, package in zip(BACKENDS, bound['original']['packages']):
        capture = package['reference']['capture']
        layers = []
        for resource in sorted(package['assets']['definitions']):
            rows = [row for frame in capture['samples'] for row in frame['collections']
                    if row['resource'] == resource]
            counts = dict(resource=resource, complete_array_samples=len(rows),
                point_samples=sum(len(row['points']) for row in rows),
                empty_array_samples=sum(not row['points'] for row in rows))
            if name == BACKENDS[0]:
                for i,_ in enumerate(package['assets']['definitions'][resource]['m_Renderers']):
                    layers.append(dict(counts, renderer=i, maximum_meshes=sum(bool(row['points']) for row in rows)))
            else:
                layers.append(dict(counts, maximum_meshes=sum(len(row['points']) >= 2 for row in rows)))
        components.append(dict(backend=name, reference_sha256=package['reference']['capture_sha256'],
            capture_source=copy.deepcopy(capture['source']), layers=layers,
            maximum_objects=sum(layer['maximum_meshes'] for layer in layers), point_lights=0))
    return dict(schema_version=1, backend=BACKEND,
        descriptor_sha256=bound['original']['provenance']['descriptor']['sha256'],
        binding_sha256=bound['binding']['content_sha256'],
        source_scene_identity=bound['binding']['source_scene_identity'],
        target_scene_identity=bound['scene_identity'], components=components,
        capture_policy='independent_capture_groups_no_shared_clock_claim')


def validate_completion(report, expected):
    require(type(report) is dict and report.get('status') == 'partial'
            and report.get('backend') == BACKEND
            and canonical_sha256(report.get('input_receipt')) == canonical_sha256(expected),
            'requested ambient completion receipt missing or different')
    rows = report.get('components')
    require(type(rows) is list and len(rows) == len(BACKENDS), 'incomplete ambient rendering')
    for row, wanted in zip(rows, expected['components']):
        require(type(row) is dict and row.get('backend') == wanted['backend']
                and row.get('status') == 'partial' and row.get('stage') == 'rendering'
                and row.get('scene_identity') == expected['source_scene_identity']
                and row.get('entity_ids') == ['56:895']
                and row.get('observed_reference_sha256') == wanted['reference_sha256'],
                'ambient component rendering identity differs')
        require(type(row.get('objects')) is int and 0 <= row['objects'] <= wanted['maximum_objects']
                and type(row.get('point_lights')) is int and row['point_lights'] == 0
                and type(row.get('collection_frame_meshes')) is int
                and row['collection_frame_meshes'] == row['objects'], 'component rendering count differs')
        layers = row.get('layers')
        require(type(layers) is list and len(layers) == len(wanted['layers']), 'ambient layer coverage differs')
        for actual, layer in zip(layers, wanted['layers']):
            require(type(actual) is dict and set(actual) == (set(layer)-{'maximum_meshes'})|{'meshes'},
                    'ambient layer receipt fields differ')
            require(all(type(actual[k]) is type(v) and actual[k] == v
                        for k,v in layer.items() if k != 'maximum_meshes'), 'ambient array processing incomplete')
            require(type(actual['meshes']) is int and 0 <= actual['meshes'] <= layer['maximum_meshes'],
                    'ambient layer mesh count differs')
            if row['backend'] == BACKENDS[0]:
                require(actual['meshes'] == layer['maximum_meshes'], 'sprite array geometry incomplete')
        require(sum(layer['meshes'] for layer in layers) == row['objects'], 'ambient layer totals differ')
        require(type(row.get('rendered_collection_layers')) is int
                and row['rendered_collection_layers'] == sum(bool(layer['meshes']) for layer in layers),
                'rendered ambient layer count differs')
    for key in ('objects','point_lights'):
        require(type(report.get(key)) is int and report[key] == sum(row[key] for row in rows),
                'ambient aggregate rendering count differs')


def build_captured_ambient(bound, data, manifest_file, scene, camera):
    from .simulated_effect_package import build_package_effects
    validate_bound_inputs(bound, data)
    expected = expected_receipt(bound)
    import bpy
    before = {key:set(getattr(bpy.data,key)) for key in GROUPS}
    try:
        rows = [build_package_effects(package, bound['original']['source_scene'],
                    Path(manifest_file), scene, camera) for package in bound['original']['packages']]
        report = dict(status='partial', stage='rendering', backend=BACKEND,
            objects=sum(row['objects'] for row in rows), point_lights=sum(row['point_lights'] for row in rows),
            particle_tracks=0, components=rows, input_receipt=expected, coverage_scope=bound['coverage_scope'])
        validate_completion(report, expected)
        return report
    except Exception:
        bpy.data.batch_remove([item for key in GROUPS for item in set(getattr(bpy.data,key))-before[key]])
        raise


def merge_captured_ambient_report(report, captured):
    report['captured_ambient_effects'] = captured
    for key in ('objects','point_lights'):
        report[key] = report.get(key,0)+captured[key]
    report.setdefault('limitations',[]).append(captured['coverage_scope'])
    # Do not erase parent/lifecycle or recorded-birth coverage from other paths.
