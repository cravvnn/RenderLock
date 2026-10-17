import math
import numpy as np
import pytest

from agr.source_lightmap import evaluate_diffuse


def scalar_shader_branch(dc, textures, n):
    """Scalar transcription of installed SPIR-V reflection lines312..342.

    Kept separate from the vectorized implementation; this checks arithmetic,
    not shader decompiler correctness or engine framebuffer equivalence.
    """
    sh = [[dc[c]] + [(2*textures[c][i]-1)*dc[c]*1.7320507764816284
                    for i in range(3)] for c in range(3)]
    basis = [.886227548122406, -n[1]*1.0233277082443237,
             n[2]*1.0233277082443237, -n[0]*1.0233277082443237]
    luma = [.2125999927520752,.7152000069618225,.0722000002861023]
    direction = [-sum(sh[c][3]*luma[c] for c in range(3)),
                 -sum(sh[c][1]*luma[c] for c in range(3)),
                 sum(sh[c][2]*luma[c] for c in range(3))]
    length = max(1e-5, math.sqrt(sum(x*x for x in direction)))
    direction = [x/length for x in direction]
    nd = sum(a*b for a,b in zip(direction,n))
    output = []
    for c in range(3):
        ratio = abs((-sh[c][3]*direction[0]-sh[c][1]*direction[1]+sh[c][2]*direction[2])/max(1e-5,dc[c]))
        linear = sum(a*b for a,b in zip(sh[c],basis))*.31830987334251404
        correction = dc[c]*ratio*(.07999999821186066+ratio*.6000000238418579)*.25*.31539157032966614*(3*nd*nd-1)
        output.append(max(0,linear+correction))
    return output


def test_random_inputs_match_independent_scalar_shader_transcription():
    rng = np.random.default_rng(6774)
    dc = rng.uniform(0,16,(2048,3)).astype('f4')
    encoded = rng.uniform(0,1,(2048,3,3)).astype('f4')
    normals = rng.normal(size=(2048,3)).astype('f4')
    normals /= np.linalg.norm(normals,axis=1,keepdims=True)
    expected = [scalar_shader_branch(d.tolist(),t.tolist(),n.tolist()) for d,t,n in zip(dc,encoded,normals)]
    np.testing.assert_allclose(evaluate_diffuse(dc,encoded,normals),expected,rtol=2e-5,atol=8e-6)


def test_neutral_encoding_has_sh_normalization_not_raw_dc():
    np.testing.assert_allclose(evaluate_diffuse([1,2,3],np.full((3,3),.5),[0,0,1]),
                              np.array([1,2,3])*.886227548122406*.31830987334251404,rtol=1e-6)


def test_zero_dc_and_nearly_zero_direction_stay_finite():
    assert np.array_equal(evaluate_diffuse([0,0,0],np.ones((3,3)),[1,0,0]),[0,0,0])
    result = evaluate_diffuse([1e-8,2e-8,3e-8],np.full((3,3),.50001),[0,1,0])
    assert np.isfinite(result).all()


def test_source_basis_signs_and_rgb_direction_are_distinct():
    texture = np.full((3,3),.5)
    texture[0,2] = 0  # red toward +X
    texture[1,0] = 0  # green toward +Y
    texture[2,1] = 1  # blue toward +Z
    colors = evaluate_diffuse([1,1,1],texture,np.eye(3))
    assert np.array_equal(colors.argmax(axis=1),[0,1,2])
    assert np.all(colors >= 0)


@pytest.mark.parametrize('dc,texture,normal', [
    ([-1,1,1],np.zeros((3,3)),[1,0,0]),
    ([1,1,1],np.full((3,3),1.1),[1,0,0]),
    ([1,1,1],np.zeros((3,3)),[0,0,0]),
    ([1,1,1],np.zeros((3,3)),[2,0,0]),
    ([float('nan'),1,1],np.zeros((3,3)),[1,0,0]),
    ([1,1],np.zeros((3,3)),[1,0,0]),
])
def test_invalid_inputs_rejected(dc,texture,normal):
    with pytest.raises(ValueError):
        evaluate_diffuse(dc,texture,normal)
