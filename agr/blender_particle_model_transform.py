"""Shear-preserving normal-frame / anisotropic-scale / Euler hierarchy.

The Source matrix is frame @ scale @ Euler for non-local component scale.
Each Blender node is individually representable by TRS; their product preserves
the full affine matrix and transforms mesh normals through the same hierarchy.
"""
import math
from .installed_effects import UnsupportedEffect
from .particle_model_math import component_scale,model_matrix,model_normal_frame

POLICY='agr_model_frame_scale_euler_hierarchy_v1'

def create_model_hierarchy(obj,renderer,collection):
    import bpy
    axes=component_scale(renderer)
    if obj.parent is not None:raise UnsupportedEffect('Model hierarchy requires its newly owned unparented mesh')
    obj.rotation_mode='QUATERNION'
    if renderer.get('m_bLocalScale',False)or max(axes)-min(axes)<=1e-8:return None
    frame=bpy.data.objects.new(obj.name+' | normal frame',None);scale=bpy.data.objects.new(obj.name+' | component scale',None)
    collection.objects.link(frame);collection.objects.link(scale)
    frame.rotation_mode='QUATERNION';scale.parent=frame;scale.scale=tuple(axes);obj.parent=scale
    obj.rotation_mode='XYZ';obj.location=(0,0,0);obj.scale=(1,1,1)
    for node in(frame,scale,obj):node['agr_model_affine_policy']=POLICY
    return dict(frame=frame,scale=scale,mesh=obj,axes=tuple(float(v)for v in axes))

def keyframe_model_transform(obj,particle,renderer,frame,unit_scale,hierarchy=None):
    from mathutils import Matrix
    # Validate the same complete Source transform in both paths.
    matrix=model_matrix(particle,renderer)
    if type(unit_scale)not in(int,float)or not math.isfinite(unit_scale)or unit_scale<=0:raise UnsupportedEffect('Invalid model unit scale')
    if hierarchy is None:
        matrix[:3,3]*=unit_scale;obj.matrix_world=Matrix(matrix.tolist())
        for prop in('location','rotation_quaternion','scale'):obj.keyframe_insert(prop,frame=frame)
        return
    root,scale=hierarchy['frame'],hierarchy['scale']
    if (hierarchy['mesh']!=obj or obj.parent!=scale or scale.parent!=root or root.parent is not None
            or tuple(float(v)for v in component_scale(renderer))!=hierarchy['axes']):
        raise UnsupportedEffect('Owned model transform hierarchy or component axes changed')
    orientation=model_normal_frame(particle['normal'],renderer)
    root.location=tuple(float(v)*unit_scale for v in particle['position'])
    root.rotation_quaternion=Matrix(orientation.tolist()).to_quaternion()
    radius=1 if renderer.get('m_bIgnoreRadius',False)else particle['radius'];root.scale=(radius,)*3
    scale.scale=hierarchy['axes']
    yaw,pitch,roll=particle['rotation'];obj.rotation_euler=(roll,pitch,yaw)
    for prop in('location','rotation_quaternion','scale'):root.keyframe_insert(prop,frame=frame)
    obj.keyframe_insert('rotation_euler',frame=frame)

def linearize_model_hierarchy(hierarchy):
    if hierarchy is not None:
        from .blender_effects import _linear
        _linear(hierarchy['frame']);_linear(hierarchy['mesh'])
