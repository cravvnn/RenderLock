"""Numeric product/keying protocol with fake Blender; not actual saved matrices."""
import math
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from agr import blender_particle_model_transform as transform
from agr.particle_model_math import model_matrix
from agr.installed_effects import UnsupportedEffect


class Node(dict):
    def __init__(self,name):
        self.name=name;self.parent=None;self.keys=[];self.rotation_mode='XYZ'
    def keyframe_insert(self,prop,frame):self.keys.append((prop,frame))


@pytest.fixture
def world(monkeypatch):
    nodes=[]
    def new(name,data):
        node=Node(name);nodes.append(node);return node
    monkeypatch.setitem(sys.modules,'bpy',SimpleNamespace(data=SimpleNamespace(objects=SimpleNamespace(new=new))))
    class Matrix:
        def __init__(self,rows):self.rows=np.asarray(rows)
        def to_quaternion(self):return self.rows
    monkeypatch.setitem(sys.modules,'mathutils',SimpleNamespace(Matrix=Matrix))
    collection=SimpleNamespace(objects=SimpleNamespace(link=lambda node:None))
    return nodes,collection


def renderer():return dict(m_vecComponentScale=dict(m_vLiteralValue=[1,1,2]),m_bOrientZ=True)
def particle():return dict(position=[3,4,5],normal=[.3,-.4,.8],rotation=[.2,.4,.6],radius=2)


def euler_xyz(value):
    x,y,z=value;sx,cx=math.sin(x),math.cos(x);sy,cy=math.sin(y),math.cos(y);sz,cz=math.sin(z),math.cos(z)
    rx=np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]])
    ry=np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])
    rz=np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]])
    return rz@ry@rx


def test_owned_hierarchy_preserves_frame_scale_euler_product_and_key_targets(world):
    nodes,collection=world;obj=Node('cylinder');r=renderer();p=particle()
    hierarchy=transform.create_model_hierarchy(obj,r,collection)
    assert len(nodes)==2 and obj.parent is hierarchy['scale'] and obj.rotation_mode=='XYZ'
    transform.keyframe_model_transform(obj,p,r,12.25,.0254,hierarchy)
    root,scale=hierarchy['frame'],hierarchy['scale']
    product=root.rotation_quaternion@np.diag(root.scale)@np.diag(scale.scale)@euler_xyz(obj.rotation_euler)
    expected=model_matrix(p,r)
    np.testing.assert_allclose(product,expected[:3,:3],atol=1e-12)
    np.testing.assert_allclose(root.location,expected[:3,3]*.0254)
    assert root.keys==[('location',12.25),('rotation_quaternion',12.25),('scale',12.25)]
    assert obj.keys==[('rotation_euler',12.25)] and not scale.keys


@pytest.mark.parametrize('local',[True,False])
def test_representable_path_sets_quaternion_mode_before_keying(world,local):
    _,collection=world;obj=Node('uniform')
    r=dict(m_bLocalScale=local,m_vecComponentScale=dict(m_vLiteralValue=[1,1,1]))
    assert transform.create_model_hierarchy(obj,r,collection)is None
    assert obj.rotation_mode=='QUATERNION'


def test_foreign_parent_is_refused_before_rotation_mode_or_allocations_change(world):
    nodes,collection=world;obj=Node('foreign child');obj.parent=Node('foreign parent')
    with pytest.raises(UnsupportedEffect,match='newly owned'):
        transform.create_model_hierarchy(obj,renderer(),collection)
    assert obj.rotation_mode=='XYZ' and not nodes


@pytest.mark.parametrize('corruption',['parent','axes','units'])
def test_changed_ownership_or_axes_refuses_without_keying(world,corruption):
    _,collection=world;obj=Node('cylinder');r=renderer()
    hierarchy=transform.create_model_hierarchy(obj,r,collection);unit=.0254
    if corruption=='parent':obj.parent=Node('foreign')
    if corruption=='axes':r['m_vecComponentScale']['m_vLiteralValue'][2]=3
    if corruption=='units':unit=True
    with pytest.raises(UnsupportedEffect):transform.keyframe_model_transform(obj,particle(),r,3,unit,hierarchy)
    assert not obj.keys and not hierarchy['frame'].keys
