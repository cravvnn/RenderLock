import numpy as np
import pytest
from agr.fe_body_collisions import project_contact, project_sphere, project_capsule, world_sphere, relative_collision_frame


def test_friction_transfers_only_bounded_tangential_collider_motion():
    points=np.zeros((2,3));prior=points.copy();motion=np.c_[np.eye(3),[10,7,9]]
    result=project_contact(points,prior,motion,1,[0,0,1],2.,.5)
    np.testing.assert_array_equal(result[0],points[0])
    assert result[1,2]==2 and np.linalg.norm(result[1,:2])==pytest.approx(1)
    np.testing.assert_allclose(result[1,:2]/np.linalg.norm(result[1,:2]),np.array([10,7])/np.sqrt(149))
    np.testing.assert_array_equal(points,np.zeros((2,3)))


def test_small_tangent_is_preserved_and_zero_friction_keeps_only_normal_correction():
    points=np.zeros((1,3));prior=np.array([[.1,.2,100.]])
    identity=np.c_[np.eye(3),np.zeros(3)]
    np.testing.assert_allclose(project_contact(points,prior,identity,0,[0,0,1],1.,1.),[[.1,.2,1]])
    np.testing.assert_allclose(project_contact(points,prior,identity,0,[0,0,1],1.,0.),[[0,0,1]])
    np.testing.assert_array_equal(project_contact(points,prior,identity,0,[0,0,1],0.,1.),points)


@pytest.mark.parametrize('normal,depth,coefficient',[([0,0,0],1,1),([0,0,2],1,1),([0,0,1],-1,1),
    ([0,0,1],1,-1),([0,0,1],float('nan'),1)])
def test_unqualified_contact_inputs_are_rejected(normal,depth,coefficient):
    with pytest.raises(ValueError):project_contact([[0,0,0]],[[0,0,0]],np.c_[np.eye(3),np.zeros(3)],0,normal,depth,coefficient)


def test_local_sphere_rotates_and_scales_with_its_control():
    half=np.sqrt(.5)
    np.testing.assert_allclose(world_sphere([1,0,0,3],[10,20,30,2,0,0,half,half]),[10,22,30,6],atol=2e-6)


def test_relative_collider_motion_discards_scale_and_identity_follows_cache_advance():
    old=[1,2,3,.25,0,0,0,1];new=[5,8,11,4,0,0,np.sqrt(.5),np.sqrt(.5)]
    relative=relative_collision_frame(new,old,history_valid=True)
    np.testing.assert_allclose(relative[:,:3]@old[:3]+relative[:,3],new[:3],atol=1e-12)
    np.testing.assert_allclose(relative[:,:3],[ [0,-1,0],[1,0,0],[0,0,1] ],atol=1e-12)
    identity=np.c_[np.eye(3),np.zeros(3)]
    np.testing.assert_allclose(relative_collision_frame(new,new,history_valid=True),identity,atol=1e-12)
    np.testing.assert_array_equal(relative_collision_frame(new,old,history_valid=False),identity)


def test_sphere_adds_node_radius_to_selected_points_without_moving_other_nodes():
    points=np.array([[.5,0,0],[.5,0,0],[4.,0,0]])
    result=project_sphere(points,[0,0,0,1],[2,2,2],[0,2])
    np.testing.assert_array_equal(result,[[3,0,0],[.5,0,0],[4,0,0]])
    np.testing.assert_array_equal(points,[[.5,0,0],[.5,0,0],[4,0,0]])


def test_friction_array_presence_keeps_distinct_native_near_center_behavior():
    point=np.array([[.01,0,0]])
    ordinary=project_sphere(point,[0,0,0,1],[0],[0])
    frictional=project_sphere(point,[0,0,0,1],[0],[0],previous=point,
        relative_transform=np.c_[np.eye(3),np.zeros(3)],friction=[0])
    np.testing.assert_allclose(ordinary,[[1,0,0]],atol=1e-7)
    np.testing.assert_allclose(frictional,[[.01,0,1]],atol=1e-7)
    np.testing.assert_array_equal(project_sphere([[0,0,0]],[0,0,0,1],[.5],[0]),[[0,0,1.5]])


def test_float32_near_center_branch_uses_native_subtraction_and_addition_order():
    point=np.array([[16.08110809326172,20.46473503112793,-12.478939056396484]])
    sphere=[16.011470794677734,20.399398803710938,-12.449243545532227,23.867155075073242]
    result=project_sphere(point,sphere,[0],[0],previous=point,
        relative_transform=np.c_[np.eye(3),np.zeros(3)],friction=[0])
    assert result[0,0]>point[0,0]+10
    assert result[0,2]<point[0,2]


@pytest.mark.parametrize('selected,radii', [([True],[0]),([0,0],[0]),([1],[0]),([0],[-1]),([0],[float('inf')])])
def test_sphere_rejects_invalid_selection_and_radii(selected,radii):
    with pytest.raises(ValueError):project_sphere([[0,0,0]],[0,0,0,1],radii,selected)


def test_sphere_friction_never_invents_prior_positions_or_collider_motion():
    with pytest.raises(ValueError,match='previous points'):
        project_sphere([[0,0,0]],[0,0,0,1],[0],[0],friction=[1])
    with pytest.raises(ValueError,match='explicit friction'):
        project_sphere([[0,0,0]],[0,0,0,1],[0],[0],previous=[[0,0,0]])


@pytest.mark.parametrize('point,sphere', [([1e100,0,0],[0,0,0,1]),([1e30,0,0],[0,0,0,1]),([0,0,0],[0,0,0,1e30])])
def test_sphere_rejects_overflow_in_source_float_buffers(point,sphere):
    with pytest.raises(ValueError,match='Source coordinates'):
        project_sphere([point],sphere,[0],[0])


def test_unequal_capsule_radii_shift_contact_from_the_closest_centerline_point():
    result=project_capsule([[1,0,2]],[[0,0,0,1],[0,0,4,3]],[0],[0])
    np.testing.assert_allclose(result,[[2.01246118,0,1.49376941]],atol=2e-6)


@pytest.mark.parametrize('spheres,expected', [([[0,0,0,1],[0,0,0,2]],[[0,0,2.5]]),
    ([[0,0,0,2],[0,0,0,1]],[[0,0,2.5]])])
def test_collapsed_capsule_selects_its_native_dominant_sphere(spheres,expected):
    np.testing.assert_allclose(project_capsule([[0,0,0]],spheres,[.5],[0]),expected,atol=1e-7)


def test_capsule_list_history_preserves_selection_and_bounded_friction():
    points=[[.5,0,2]]*3
    prior=[[.5,.2,2],[.5,2,2],[.5,2,2]]
    result=project_capsule(points,[[0,0,0,1],[0,0,4,1]],[0]*3,[2,0],
        previous=prior,relative_transform=[[1,0,0,0],[0,1,0,0],[0,0,1,0]],friction=[.5]*3)
    np.testing.assert_allclose(result,[[1,.2,2],[.5,0,2],[1,.25,2]],atol=1e-7)
    assert points==[[.5,0,2]]*3


@pytest.mark.parametrize('bad_history', [False,True])
def test_capsule_checks_unselected_input_values_before_projecting(bad_history):
    points=[[.5,0,2],[1e100,0,2]]
    kwargs={}
    if bad_history:
        points[1]=[.5,0,2]
        kwargs=dict(previous=points,relative_transform=np.c_[np.eye(3),np.zeros(3)],friction=[.5,float('nan')])
    with pytest.raises(ValueError):
        project_capsule(points,[[0,0,0,1],[0,0,4,1]],[0,0],[0],**kwargs)
