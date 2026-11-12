"""Fake-bpy transaction checks, not saved Blender or rendering evidence."""
import copy
import sys
from types import SimpleNamespace

import pytest

from agr import captured_ambient_inputs as inputs, captured_ambient_render as render
from agr import simulated_effect_package


@pytest.fixture
def bound():
    packages=[]
    for i,name in enumerate(inputs.BACKENDS):
        resource='sprite' if i==0 else 'rope'
        frames=[dict(collections=[dict(resource=resource,points=[{}]*(i+1))]),
                dict(collections=[dict(resource=resource,points=[])])]
        packages.append(dict(backend=name,reference=dict(capture=dict(samples=frames,
            source=dict(report_sha256=str(i)*64)),capture_sha256=str(i)*64),
            assets=dict(definitions={resource:dict(m_Renderers=[{}])})))
    return dict(original=dict(packages=packages,source_scene={'original':True},
        provenance=dict(descriptor=dict(sha256='a'*64))),binding=dict(content_sha256='b'*64,
        source_scene_identity='c'*64),scene_identity='d'*64,coverage_scope='independent captured arrays; partial')


def component_reports(expected):
    rows=[]
    for item in expected['components']:
        layers=[dict({k:v for k,v in layer.items() if k!='maximum_meshes'},meshes=layer['maximum_meshes'])
                for layer in item['layers']]
        rows.append(dict(status='partial',stage='rendering',backend=item['backend'],
            scene_identity=expected['source_scene_identity'],entity_ids=['56:895'],
            observed_reference_sha256=item['reference_sha256'],objects=item['maximum_objects'],
            point_lights=0,layers=layers,collection_frame_meshes=item['maximum_objects'],
            rendered_collection_layers=sum(bool(layer['meshes']) for layer in layers)))
    return rows


def completed(bound):
    expected=render.expected_receipt(bound); rows=component_reports(expected)
    return dict(status='partial',backend=render.BACKEND,input_receipt=expected,components=rows,
                objects=sum(row['objects'] for row in rows),point_lights=0)


@pytest.fixture
def allocations(monkeypatch):
    data=SimpleNamespace(**{key:{object()} for key in render.GROUPS})
    old={key:set(getattr(data,key)) for key in render.GROUPS}
    def remove(items):
        for key in old:getattr(data,key).difference_update(items)
    data.batch_remove=remove
    monkeypatch.setitem(sys.modules,'bpy',SimpleNamespace(data=data))
    monkeypatch.setattr(render,'validate_bound_inputs',lambda *a:None)
    return data,old


@pytest.mark.parametrize('fail_at',[0,1,'receipt'])
def test_failure_removes_all_new_siblings_preserving_old_ids(bound,allocations,monkeypatch,fail_at):
    data,old=allocations; calls=[]
    rows=component_reports(render.expected_receipt(bound))
    def build(package,source,manifest,scene,camera):
        i=len(calls); calls.append(package['backend'])
        assert source=={'original':True}
        for key in old:getattr(data,key).add(object())
        if i==fail_at:raise RuntimeError('late ambient allocation')
        result=rows[i]
        if fail_at=='receipt' and i==1:result['layers'][0]['complete_array_samples']-=1
        return result
    monkeypatch.setattr(simulated_effect_package,'build_package_effects',build)
    with pytest.raises((RuntimeError,ValueError)):
        render.build_captured_ambient(bound,{},'ambient.json',None,None)
    assert len(calls)==(fail_at+1 if type(fail_at)is int else 2)
    assert all(getattr(data,key)==value for key,value in old.items())


def test_completion_preserves_independent_provenance_and_recorded_coverage(bound,allocations,monkeypatch):
    rows=iter(component_reports(render.expected_receipt(bound)))
    monkeypatch.setattr(simulated_effect_package,'build_package_effects',lambda *a:next(rows))
    result=render.build_captured_ambient(bound,{},'ambient.json',None,None)
    sources=[r['capture_source']['report_sha256'] for r in result['input_receipt']['components']]
    assert sources==['0'*64,'1'*64]
    report=dict(status='partial',objects=3,point_lights=1,rendered_instances=7,
        unsupported_occurrences=8,unsupported=[{'parent_scope':'held'}],unsupported_by_birth_scope={'history':8})
    before=copy.deepcopy(report); render.merge_captured_ambient_report(report,result)
    for key in ('status','rendered_instances','unsupported_occurrences','unsupported','unsupported_by_birth_scope'):
        assert report[key]==before[key]
    assert report['objects']==5 and report['point_lights']==1


@pytest.mark.parametrize('change',[
    lambda r:r.pop('input_receipt'),
    lambda r:r['input_receipt']['components'][0]['capture_source'].update(report_sha256='f'*64),
    lambda r:r['components'].pop(),
    lambda r:r['components'].reverse(),
    lambda r:r['components'][0].update(objects=True),
    lambda r:r['components'][0]['layers'][0].update(point_samples=0),
    lambda r:r['components'][1]['layers'][0].update(complete_array_samples=1),
    lambda r:r['components'][1]['layers'][0].update(complete_array_samples=True),
    lambda r:r['components'][1]['layers'][0].update(empty_array_samples=0),
    lambda r:r['components'][1]['layers'][0].update(meshes=99999),
    lambda r:r['components'][1]['layers'][0].update(unknown=True),
    lambda r:r['components'][0]['layers'][0].update(meshes=0),
    lambda r:r['components'][1].update(rendered_collection_layers=0),
    lambda r:r.update(objects=1),
])
def test_substituted_or_incomplete_processing_receipts_refuse(bound,change):
    report=completed(bound); expected=copy.deepcopy(report['input_receipt']);change(report)
    with pytest.raises(ValueError):render.validate_completion(report,expected)


def test_camera_clipped_rope_is_distinct_from_unprocessed_arrays(bound):
    report=completed(bound); expected=copy.deepcopy(report['input_receipt'])
    rope=report['components'][1]
    rope.update(objects=0,collection_frame_meshes=0,rendered_collection_layers=0)
    rope['layers'][0]['meshes']=0;report['objects']-=1
    render.validate_completion(report,expected)
    rope['layers'][0]['complete_array_samples']-=1
    with pytest.raises(ValueError,match='processing incomplete'):render.validate_completion(report,expected)
