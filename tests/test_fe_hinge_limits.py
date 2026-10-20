import math
import numpy as np
import pytest
from agr.fe_hinge_limits import project_hinge_limits


def fixture(angle=110):
    angle=math.radians(angle)
    points=np.array([[0,0,0],[0,0,10],[10,0,0],[10*math.cos(angle),10*math.sin(angle),0]],float)
    masses=np.array([0,0,0,1.])
    row=dict(nNode=[0,1,2,3,2,3],nFlags=0,flWeight4=0.,flWeight5=0.,
             flAngleCenter=0.,flAngleExtents=math.pi/6)
    return points,masses,row


def angle(points):
    return math.atan2(points[3,1],points[3,0])


def test_pinned_axis_and_reference_remain_exact_while_arm_reaches_interval():
    points,masses,row=fixture();before=points.copy()
    result,report=project_hinge_limits(points,masses,[row])
    np.testing.assert_array_equal(result[:3],before[:3])
    np.testing.assert_array_equal(points,before)
    assert abs(abs(angle(result))-math.pi/6)<math.radians(1)
    assert 0<report['iterations']<=5


def test_inside_interval_and_all_pinned_are_unchanged():
    points,masses,row=fixture(10)
    result,report=project_hinge_limits(points,masses,[row])
    np.testing.assert_array_equal(result,points);assert report['iterations']==0
    points,masses,row=fixture(110);masses[:]=0
    result,report=project_hinge_limits(points,masses,[row])
    np.testing.assert_array_equal(result,points);assert report['iterations']==0
    assert report['maximum_angle_residual_radians']>1


def test_projection_is_invariant_under_rigid_world_transforms():
    points,masses,row=fixture()
    rotation=np.array([[0,0,1],[1,0,0],[0,1,0]],float);translation=np.array([40,-5,13])
    baseline,_=project_hinge_limits(points,masses,[row])
    moved,_=project_hinge_limits(points@rotation.T+translation,masses,[row])
    np.testing.assert_allclose(moved,baseline@rotation.T+translation,atol=1e-12)


def test_wrapped_angle_interval_across_negative_pi():
    points,masses,row=fixture(-179);row['flAngleCenter']=math.radians(175)
    result,report=project_hinge_limits(points,masses,[row])
    np.testing.assert_array_equal(result,points);assert report['iterations']==0


def test_half_turn_uses_source_float_angle_storage_before_wrap_branch():
    points,masses,row=fixture()
    theta=.44159263
    points=points.astype('f4');points[3]=[10*math.cos(theta),10*math.sin(theta),0]
    row['flAngleCenter']=np.float32(-2.7).item();row['flAngleExtents']=math.pi/2
    result,_=project_hinge_limits(points,masses,[row])
    # float32 stores this difference as positive pi; double-only subtraction
    # wraps the same input toward the opposite side of the hinge interval.
    assert angle(result)<0


def test_degenerate_axis_and_reference_are_reported_without_fabrication():
    points,masses,row=fixture();points[1]=points[0]
    result,report=project_hinge_limits(points,masses,[row])
    np.testing.assert_array_equal(result,points);assert report['degenerate_hinges']==1


@pytest.mark.parametrize('key,value',[('nNode',[0,1,2,10,2,3]),('nNode',[False,1,2,3,2,3]),
    ('flWeight4',-.2),('flWeight4',float('nan')),('flWeight5',1),('nFlags',1),('flAngleCenter',float('nan')),
    ('flAngleExtents',-1),('flAngleExtents',4)])
def test_unqualified_or_malformed_declarations_are_rejected(key,value):
    points,masses,row=fixture();row[key]=value
    with pytest.raises(ValueError):project_hinge_limits(points,masses,[row])


@pytest.mark.parametrize('blend',[.25,.5,.75])
def test_blended_arm_moves_both_authored_nodes_with_source_writeback_ratios(blend):
    points,masses,row=fixture();points=np.r_[points,points[2:4]];masses=np.r_[masses,0.,1.]
    row['nNode']=[0,1,2,3,4,5];row['flWeight5']=blend
    result,report=project_hinge_limits(points,masses,[row])
    np.testing.assert_array_equal(result[:3],points[:3])
    delta3=result[3]-points[3];delta5=result[5]-points[5]
    assert np.linalg.norm(delta3)>0 and report['iterations']>0
    np.testing.assert_allclose(delta3,delta5*(blend/(1-blend))**2,atol=1e-12)


def test_blended_endpoint_inside_interval_does_not_write_either_node():
    points,masses,row=fixture(10);points=np.r_[points,points[2:4]];masses=np.r_[masses,0.,1.]
    row['nNode']=[0,1,2,3,4,5];row['flWeight5']=.5
    result,report=project_hinge_limits(points,masses,[row])
    np.testing.assert_array_equal(result,points);assert report['iterations']==0
