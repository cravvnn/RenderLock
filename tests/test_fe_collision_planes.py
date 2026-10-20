import copy

import numpy as np
import pytest

from agr.fe_collision_planes import project_collision_planes


def fixture():
    controls=np.array([[1.,2,3,7,0,0,2**-.5,2**-.5]])
    points=np.array([[1.,0,3],[1,8,3]])
    row=dict(nCtrlParent=0,nChildNode=0,m_Plane=dict(m_vNormal=[1.,0,0],m_flOffset=2.),flStrength=1.)
    return points,controls,row


def test_rotated_translated_plane_moves_only_penetrating_particle():
    points,controls,row=fixture();original=points.copy();declaration=copy.deepcopy(row)
    result=project_collision_planes(points,controls,[row])
    np.testing.assert_allclose(result,[[1,4,3],[1,8,3]],atol=1e-12)
    np.testing.assert_array_equal(points,original)
    assert row==declaration


def test_strength_relaxes_projection_and_native_parent_scale_is_not_applied():
    points,controls,row=fixture();row['flStrength']=.5
    result=project_collision_planes(points,controls,[row])
    np.testing.assert_allclose(result[0],[1,2,3],atol=1e-12)
    controls[0,3]=.2
    np.testing.assert_allclose(project_collision_planes(points,controls,[row]),result,atol=1e-12)


@pytest.mark.parametrize('kind',['parent','child','normal','strength','offset'])
def test_unqualified_plane_rejected(kind):
    points,controls,row=fixture()
    if kind=='parent':row['nCtrlParent']=1
    elif kind=='child':row['nChildNode']=-1
    elif kind=='normal':row['m_Plane']['m_vNormal']=[2,0,0]
    elif kind=='strength':row['flStrength']=1.1
    else:row['m_Plane']['m_flOffset']=float('nan')
    with pytest.raises(ValueError):project_collision_planes(points,controls,[row])
