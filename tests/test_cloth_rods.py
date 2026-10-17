import numpy as np
import pytest

from agr.cloth_rods import ProxyRod, decode_proxy_rods, project_rods, rod_residuals, noncofacial_rod_edges


def definition():
    return {'proxy':{'vertices':[[0,0,0],[10,0,0]],'paint':{'cloth_enable$0':[0,1]}},
            'physics':{'m_pFeModel':{
                'm_CtrlName':['$cloth_m0p0','$cloth_m0p1'],
                'm_InitPose':[[0,0,0,1,0,0,0,1],[10,0,0,1,0,0,0,1]],
                'm_Rods':[{'nNode':[0,1],'flMinDist':5,'flMaxDist':10,'flWeight0':0,'flRelaxationFactor':1}],
                'm_SimdRods':[{'intentionally_not_decoded':True}]}}}


def test_decode_preserves_limits_and_ignores_simd_duplicates():
    rods=decode_proxy_rods(definition())
    assert rods==(ProxyRod((0,1),.127,.254,0,1),)


@pytest.mark.parametrize('change', ['position','identity','static_weight','negative_limit','nan_weight','unmapped'])
def test_invalid_correspondence_or_constraints_fail(change):
    d=definition();f=d['physics']['m_pFeModel'];row=f['m_Rods'][0]
    if change=='position':f['m_InitPose'][1][0]=11
    elif change=='identity':f['m_CtrlName'][1]='$cloth_m0p0'
    elif change=='static_weight':row['flWeight0']=.2
    elif change=='negative_limit':row['flMinDist']=-1
    elif change=='nan_weight':row['flWeight0']=float('nan')
    elif change=='unmapped':f['m_CtrlName'][1]='pelvis'
    with pytest.raises(ValueError):decode_proxy_rods(d)


@pytest.mark.parametrize('start,expected', [(3,2),(.5,1),(1.5,1.5)])
def test_pinned_endpoint_and_min_max_band(start,expected):
    before=np.array([[0.,0,0],[start,0,0]])
    rod=ProxyRod((0,1),1,2,0,1)
    result=project_rods(before,[rod])
    np.testing.assert_array_equal(before,[[0,0,0],[start,0,0]])
    np.testing.assert_allclose(result,[[0,0,0],[expected,0,0]])
    assert rod_residuals(result,[rod])[0]==0


def test_authored_weight_and_relaxation_are_not_replaced_by_equal_masses():
    result=project_rods([[0,0,0],[4,0,0]],[ProxyRod((0,1),1,2,.25,.5)])
    np.testing.assert_allclose(result,[[.25,0,0],[3.25,0,0]])


def test_collapsed_rod_fails_without_inventing_direction():
    with pytest.raises(ValueError,match='orientation'):
        project_rods([[0,0,0],[0,0,0]],[ProxyRod((0,1),1,2,0,1)])


def test_coupled_chain_converges_and_keeps_anchor():
    rods=[ProxyRod((0,1),1,1,0,1),ProxyRod((1,2),1,1,.5,1)]
    result=project_rods([[0,0,0],[2,0,0],[4,0,0]],rods,iterations=40)
    np.testing.assert_array_equal(result[0],[0,0,0])
    assert rod_residuals(result,rods).max()<1e-10


def test_extra_edges_exclude_face_diagonals_and_deduplicate():
    proxy={'faces':[[0,1,2,3]]}
    rods=[ProxyRod(pair,1,2,.5,1) for pair in [(0,1),(0,2),(0,4),(4,0)]]
    assert noncofacial_rod_edges(proxy,rods)==[(0,4)]
