import numpy as np
import pytest
from agr.fe_body_collisions import project_contact, project_sphere, project_capsule, relative_collision_frame


def test_batched_capsule_matches_independent_axial_sphere_contacts():
    coefficient=.6
    # Equal-radius axial capsule has an analytic per-node sphere center. Cover
    # both caps, interior contacts, noncontacts and a float32 branch boundary.
    points=np.array([[0.,0.,-1.], [.1,0.,2.], [0.,0.,5.],
                     [10.,0.,2.], [.5,.25,3.], [0.,0.,2.]])
    radii=np.array([.2,0.,.1,.3,.2,0.])
    selected=[4,1,3,0,2]
    kwargs=dict(previous=points+[.1,.2,0.],
        relative_transform=np.c_[np.eye(3),[.2,.1,0.]],friction=np.full(len(points),coefficient))
    expected=points.copy()
    for node in selected:
        center=[0.,0.,min(4.,max(0.,points[node,2])),1.]
        expected=project_sphere(expected,center,radii,[node],**kwargs)
    actual=project_capsule(points,[[0.,0.,0.,1.],[0.,0.,4.,1.]],radii,selected,**kwargs)
    np.testing.assert_array_equal(actual,expected)
    np.testing.assert_array_equal(actual[5],points[5])


def test_friction_transfers_only_bounded_tangential_collider_motion():
    points=np.zeros((2,3));prior=points.copy();motion=np.c_[np.eye(3),[10,7,9]]
    result=project_contact(points,prior,motion,1,[0,0,1],2.,.5)
    np.testing.assert_array_equal(result[0],points[0])
    assert result[1,2]==2 and np.linalg.norm(result[1,:2])==pytest.approx(1)
    np.testing.assert_allclose(result[1,:2]/np.linalg.norm(result[1,:2]),np.array([10,7])/np.sqrt(149))
    np.testing.assert_array_equal(points,np.zeros((2,3)))


@pytest.mark.parametrize('normal,depth,coefficient',[([0,0,0],1,1)])
def test_unqualified_contact_inputs_are_rejected(normal,depth,coefficient):
    with pytest.raises(ValueError):project_contact([[0,0,0]],[[0,0,0]],np.c_[np.eye(3),np.zeros(3)],0,normal,depth,coefficient)


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


def test_float32_near_center_branch_uses_native_subtraction_and_addition_order():
    point=np.array([[16.08110809326172,20.46473503112793,-12.478939056396484]])
    sphere=[16.011470794677734,20.399398803710938,-12.449243545532227,23.867155075073242]
    result=project_sphere(point,sphere,[0],[0],previous=point,
        relative_transform=np.c_[np.eye(3),np.zeros(3)],friction=[0])
    assert result[0,0]>point[0,0]+10
    assert result[0,2]<point[0,2]


def test_sphere_rejects_invalid_selection_and_radii():
    with pytest.raises(ValueError):project_sphere([[0,0,0]],[0,0,0,1],[0],[0,0])


def test_sphere_friction_never_invents_prior_positions_or_collider_motion():
    with pytest.raises(ValueError,match='previous points'):
        project_sphere([[0,0,0]],[0,0,0,1],[0],[0],friction=[1])
    with pytest.raises(ValueError,match='explicit friction'):
        project_sphere([[0,0,0]],[0,0,0,1],[0],[0],previous=[[0,0,0]])


def test_unequal_capsule_radii_shift_contact_from_the_closest_centerline_point():
    result=project_capsule([[1,0,2]],[[0,0,0,1],[0,0,4,3]],[0],[0])
    np.testing.assert_allclose(result,[[2.01246118,0,1.49376941]],atol=2e-6)


def test_capsule_list_history_preserves_selection_and_bounded_friction():
    points=[[.5,0,2]]*3
    prior=[[.5,.2,2],[.5,2,2],[.5,2,2]]
    result=project_capsule(points,[[0,0,0,1],[0,0,4,1]],[0]*3,[2,0],
        previous=prior,relative_transform=[[1,0,0,0],[0,1,0,0],[0,0,1,0]],friction=[.5]*3)
    np.testing.assert_allclose(result,[[1,.2,2],[.5,0,2],[1,.25,2]],atol=1e-7)
    assert points==[[.5,0,2]]*3
