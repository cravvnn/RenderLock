"""All optional components reject cross-run inputs before preparing output."""
import copy
import pytest
from agr.observed_slide_cohort import REFERENCE_KEYS, validate_slide_cohort
from agr.observed_slide_model_states import OWNER, RESOURCES, SCOPE
from agr.observed_slide_model_effects import build_observed_slide_model_effects, merge_observed_slide_model_report
from agr.observed_slide_sprite_effects import build_observed_slide_sprite_effects
from agr.observed_slide_trail_effects import build_observed_slide_trail_effects
from agr.observed_slide_light_effects import build_observed_slide_light_effects


def references():
    return {key: dict(capture=dict(source=dict(report_sha256='a'*64))) for key in REFERENCE_KEYS}


@pytest.mark.parametrize('changed', REFERENCE_KEYS)
def test_every_component_refuses_mixed_run_before_package_or_allocation(tmp_path, changed):
    runtime = dict(references(), output_dir=str(tmp_path))
    runtime[changed]['capture']['source']['report_sha256'] = 'b'*64
    calls = (
        lambda: build_observed_slide_sprite_effects({}, None, None, {OWNER:{}}, runtime, None, None, None),
        lambda: build_observed_slide_trail_effects({}, None, None, {OWNER:{}}, runtime, None, None, None),
        lambda: build_observed_slide_light_effects({}, None, None, {OWNER:{}}, runtime),
        lambda: build_observed_slide_model_effects({}, None, None, {OWNER:{}}, runtime),
    )
    for call in calls:
        result = call()
        assert result['status'] == 'unavailable' and 'cohort' in result['reason']
        assert result['objects'] == result['point_lights'] == 0
    assert not list(tmp_path.iterdir())


def test_shared_capture_and_absent_optional_components():
    validate_slide_cohort(references())
    validate_slide_cohort({})
    validate_slide_cohort({key: None for key in REFERENCE_KEYS})
    for value in (True, [], dict(capture=[]), dict(capture=dict(source=None))):
        with pytest.raises(ValueError, match='cohort'):
            validate_slide_cohort({REFERENCE_KEYS[0]: value})


def test_model_merge_preserves_recorded_births_and_replaces_only_renderer_note():
    resource = sorted(RESOURCES)[0]
    component_note = dict(resource=resource, occurrences=1,
        reason='Native inputs captured; this sprite component does not qualify this renderer',
        instance_examples=['native-owner:'+OWNER],
        coverage_scope='Unresolved native renderer component; not a recorded birth count')
    recorded_birth = dict(component_note, coverage_scope='Recorded history birth', occurrences=9)
    report = dict(objects=5, point_lights=2, rendered_instances=91, verified_empty_instances=1,
                  unsupported_by_birth_scope={'history':28}, particle_tracks=12,
                  unsupported=[component_note, recorded_birth])
    old = copy.deepcopy(report)
    model = dict(objects=34, point_lights=0, rendering={'objects':34}, coverage_scope=SCOPE,
                 unsupported=[dict(resource=resource, occurrences=1, reason='Appearance remains partial')])
    merge_observed_slide_model_report(report, model)
    assert report['objects'] == 39 and component_note not in report['unsupported']
    assert recorded_birth in report['unsupported']
    for key in ('point_lights', 'rendered_instances', 'verified_empty_instances', 'unsupported_by_birth_scope', 'particle_tracks'):
        assert report[key] == old[key]
    assert build_observed_slide_model_effects({}, None, None, {}, {})['status'] == 'not_selected'
