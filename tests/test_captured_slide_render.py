"""Allocation ownership controls with a fake bpy; actual Blender remains a gate."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from agr import captured_slide_render as render
from agr import simulated_effect_package


@pytest.fixture
def bound():
    captures = [dict(samples=[dict(collections=[dict(renderers=[{}])])]),
                dict(frames=[dict(systems=[dict(points=[{}])])]),
                dict(frames=[dict(systems=[dict(points=[{}])])]),
                dict(frames=[dict(systems=[dict(models=[{}])])])]
    packages = [dict(backend=name, reference=dict(capture=c, capture_sha256=str(i) * 64))
                for i, (name, c) in enumerate(zip(render.BACKENDS, captures))]
    return dict(original=dict(packages=packages, source_scene={'original': True},
                              provenance=dict(descriptor=dict(sha256='a' * 64))),
                binding=dict(content_sha256='b' * 64, source_scene_identity='c' * 64),
                scene_identity='d' * 64, coverage_scope='partial captured-frame coverage')


def component_reports(expected):
    return [dict(status='partial', stage='rendering', backend=row['backend'],
                 scene_identity=expected['source_scene_identity'], entity_ids=['56:895'],
                 observed_reference_sha256=row['reference_sha256'],
                 objects=row['objects'], point_lights=row['point_lights'])
            for row in expected['components']]


@pytest.fixture
def fake_allocation(monkeypatch, bound):
    data = SimpleNamespace(**{key: {object()} for key in render.GROUPS})
    original = {key: set(getattr(data, key)) for key in render.GROUPS}
    def remove(items):
        for key in render.GROUPS:
            getattr(data, key).difference_update(items)
    data.batch_remove = remove
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(data=data))
    monkeypatch.setattr(render, 'validate_bound_cohort', lambda *args: None)
    return data, original


@pytest.mark.parametrize('fail_at', range(4))
def test_failure_removes_prior_siblings_and_failed_allocations(bound, fake_allocation, monkeypatch, fail_at):
    data, original = fake_allocation
    calls = []
    reports = component_reports(render.expected_receipt(bound))
    def build(package, source, manifest, scene, camera):
        i = len(calls)
        calls.append(package['backend'])
        assert source == {'original': True}
        assert manifest == Path('cohort.json')
        data.objects.add(object()); data.images.add(object()); data.actions.add(object())
        if i == fail_at:
            raise RuntimeError('inner allocation failed')
        return reports[i]
    monkeypatch.setattr(simulated_effect_package, 'build_package_effects', build)
    with pytest.raises(RuntimeError, match='allocation failed'):
        render.build_captured_slide_cohort(bound, {}, 'cohort.json', None, None)
    assert len(calls) == fail_at + 1
    assert all(getattr(data, key) == value for key, value in original.items())


def test_success_is_all_four_and_does_not_change_birth_coverage(bound, fake_allocation, monkeypatch):
    data, original = fake_allocation
    rows = iter(component_reports(render.expected_receipt(bound)))
    def build(*args):
        data.objects.add(object())
        return next(rows)
    monkeypatch.setattr(simulated_effect_package, 'build_package_effects', build)
    result = render.build_captured_slide_cohort(bound, {}, 'cohort.json', None, None)
    assert result['objects'] == 4 and result['point_lights'] == 1
    report = dict(status='partial', objects=5, point_lights=2, rendered_instances=7,
                  selected_recorded_instances=8, unsupported_occurrences=9,
                  unsupported=[{'existing': 'recorded rejection'}],
                  unsupported_by_birth_scope={'history': 9})
    before = copy.deepcopy(report)
    render.merge_captured_slide_report(report, result)
    assert report['objects'] == 9 and report['point_lights'] == 3
    for key in ('rendered_instances', 'selected_recorded_instances', 'unsupported_occurrences',
                'unsupported', 'unsupported_by_birth_scope', 'status'):
        assert report[key] == before[key]
    assert original['objects'] < data.objects


def test_late_receipt_failure_also_rolls_back_all_siblings(bound, fake_allocation, monkeypatch):
    data, original = fake_allocation
    rows = component_reports(render.expected_receipt(bound))
    rows[-1]['objects'] = True  # Equality to numeric one must not qualify.
    calls = []
    def build(*args):
        data.objects.add(object())
        row = rows[len(calls)];calls.append(row)
        return row
    monkeypatch.setattr(simulated_effect_package, 'build_package_effects', build)
    with pytest.raises(ValueError, match='count differs'):
        render.build_captured_slide_cohort(bound, {}, 'cohort.json', None, None)
    assert len(calls) == 4
    assert all(getattr(data, key) == value for key, value in original.items())


@pytest.mark.parametrize('change', [
    lambda r: r.update(status='unavailable'),
    lambda r: r.pop('input_receipt'),
    lambda r: r['input_receipt'].update(schema_version=True),
    lambda r: r['components'].pop(),
    lambda r: r['components'].reverse(),
    lambda r: r['components'][0].update(status='failed'),
    lambda r: r['components'][0].update(scene_identity='e' * 64),
    lambda r: r['components'][0].update(observed_reference_sha256='e' * 64),
    lambda r: r['components'][0].update(objects=0),
    lambda r: r.update(objects=1),
])
def test_incomplete_or_substituted_completion_cannot_publish(bound, change):
    expected = render.expected_receipt(bound)
    report = dict(status='partial', backend=render.BACKEND, objects=4, point_lights=1,
                  input_receipt=copy.deepcopy(expected), components=component_reports(expected))
    render.validate_completion(report, expected)
    change(report)
    with pytest.raises(ValueError):
        render.validate_completion(report, expected)


def test_binding_failure_precedes_any_allocation(bound, fake_allocation, monkeypatch):
    data, original = fake_allocation
    def refuse(*args):
        raise ValueError('fresh input changed')
    monkeypatch.setattr(render, 'validate_bound_cohort', refuse)
    monkeypatch.setattr(simulated_effect_package, 'build_package_effects',
                        lambda *a: pytest.fail('must not start an inner builder'))
    with pytest.raises(ValueError, match='fresh input changed'):
        render.build_captured_slide_cohort(bound, {}, 'cohort.json', None, None)
    assert all(getattr(data, key) == value for key, value in original.items())
