import numpy as np
import pytest
from agr.source_diffuse_material import diffuse_material


def test_dielectric_mixed_and_metal():
    np.testing.assert_array_equal(diffuse_material([2,4,8],[.5,.25,.125],[0,.5,1]),[[1,1,1],[.5,.5,.5],[0,0,0]])


def test_accumulation_matches_unsimplified_shader_factor():
    rng=np.random.default_rng(807);light=rng.uniform(0,32,(256,3)).astype('f4')
    albedo=rng.uniform(0,1,(256,3)).astype('f4');metal=rng.uniform(0,1,256).astype('f4')
    expected=np.array([[float(light[i,j])*float(albedo[i,j])*(1-float(metal[i])) for j in range(3)] for i in range(256)])
    np.testing.assert_allclose(diffuse_material(light,albedo,metal),expected,rtol=2e-6,atol=1e-6)


@pytest.mark.parametrize('lighting,albedo,metal', [([1,1],[1,1,1],0),([1,1,1],[1,1,float('nan')],0),
    ([-1,0,0],[1,1,1],0),([1,1,1],[1,1,1],1.1),([1,1,1],[1,1,1],float('inf'))])
def test_invalid_material_inputs_rejected(lighting,albedo,metal):
    with pytest.raises(ValueError):diffuse_material(lighting,albedo,metal)
