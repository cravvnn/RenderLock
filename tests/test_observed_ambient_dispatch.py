"""Component dispatch/ownership only; fake bpy is not actual Blender evidence."""
import sys
from types import SimpleNamespace

import pytest

from agr import simulated_effect_package as packages
from agr import blender_observed_ropes, blender_observed_sprites


@pytest.fixture(params=[
    ('observed_rope_frames_v1', blender_observed_ropes, 'build_observed_ropes'),
    ('observed_sprite_frames_v1', blender_observed_sprites, 'build_observed_sprites'),
])
def transaction(request, monkeypatch):
    backend, module, function = request.param
    groups = ('objects','collections','meshes','shape_keys','materials','lights',
              'images','actions','node_groups','texts')
    data = SimpleNamespace(**{key:{object()} for key in groups})
    original = {key:set(getattr(data,key)) for key in groups}
    def remove(items):
        for key in groups:
            getattr(data,key).difference_update(items)
    data.batch_remove = remove
    monkeypatch.setitem(sys.modules,'bpy',SimpleNamespace(data=data))
    monkeypatch.setattr(packages,'validate_package',lambda *args:None)
    package = dict(backend=backend, assets={}, scene_identity='a'*64,
                   entity_ids=['56:895'], coverage_scope='partial frame arrays')
    return package, module, function, data, original


def test_component_report_keeps_its_backend_and_partial_scope(transaction, monkeypatch):
    package,module,function,data,original = transaction
    def build(p,source,assets,scene,camera):
        assert p is package and source == {'original':True}
        data.objects.add(object())
        return dict(backend=package['backend'],objects=1,point_lights=0)
    monkeypatch.setattr(module,function,build)
    report = packages.build_package_effects(package,{'original':True},'package.json',None,None)
    assert report['backend'] == report['simulation_backend'] == package['backend']
    assert report['status'] == 'partial' and report['stage'] == 'rendering'
    assert report['coverage_scope'] == package['coverage_scope']
    assert original['objects'] < data.objects


def test_late_component_exception_removes_every_new_datablock(transaction, monkeypatch):
    package,module,function,data,original = transaction
    def build(*args):
        for key in original:
            getattr(data,key).add(object())
        raise RuntimeError('late ambient material failure')
    monkeypatch.setattr(module,function,build)
    with pytest.raises(RuntimeError,match='late ambient'):
        packages.build_package_effects(package,{},'package.json',None,None)
    assert all(getattr(data,key) == value for key,value in original.items())


def test_refusal_precedes_component_allocation(transaction, monkeypatch):
    package,module,function,data,original = transaction
    def refuse(*args):
        raise ValueError('unqualified ambient reference')
    def unexpected(*args):
        pytest.fail('renderer called before successful admission')
    monkeypatch.setattr(packages,'validate_package',refuse)
    monkeypatch.setattr(module,function,unexpected)
    with pytest.raises(ValueError,match='unqualified ambient'):
        packages.build_package_effects(package,{},'package.json',None,None)
    assert all(getattr(data,key) == value for key,value in original.items())
