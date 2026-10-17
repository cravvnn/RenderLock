import numpy as np
import pytest
from agr.source_baked_shadow import baked_visibility, barn_baked_transmission, sample_linear_rgba


def test_channel_is_occlusion_not_visibility():
    samples=np.array([[0,.25,.75,1],[1,.75,.25,0]],dtype=np.float32)
    for channel in range(4):
        np.testing.assert_array_equal(baked_visibility(samples,channel),1-samples[:,channel])
    np.testing.assert_array_equal(baked_visibility(samples,-1),[1,1])


def test_matches_shader_one_hot_dot_product():
    samples=np.random.default_rng(771).uniform(0,1,(17,31,4)).astype(np.float32)
    for channel in range(4):
        mask=np.eye(4,dtype=np.float32)[channel]
        expected=1-np.sum(samples*mask,axis=-1,dtype=np.float32)
        np.testing.assert_array_equal(baked_visibility(samples,channel),expected)


def test_barn_colour_multiplier_squares_visibility():
    # Source barn branch uses colour * (1-mask) * (1-mask). Its sun branch
    # uses (1-mask) before dynamic-shadow modulation, not this square.
    np.testing.assert_array_equal(barn_baked_transmission([[0,.5,.75,1]],1),[.25])
    np.testing.assert_array_equal(barn_baked_transmission([[0,.5,.75,1]],2),[.0625])
    np.testing.assert_array_equal(barn_baked_transmission([[0,.5,.75,1]],-1),[1])


@pytest.mark.parametrize('channel',[-2,4,1.0,True,'0'])
def test_invalid_channel_rejected(channel):
    with pytest.raises(ValueError):baked_visibility([0,0,0,0],channel)


@pytest.mark.parametrize('samples',[[0,0,0],[0,0,0,float('nan')],[0,0,0,float('inf')],[0,0,0,-.1],[0,0,0,1.1]])
def test_invalid_samples_rejected(samples):
    with pytest.raises(ValueError):baked_visibility(samples,0)


def test_pixel_centres_bilinear_and_clamp():
    image=np.zeros((2,2,4),dtype=np.float32)
    image[0,1]=.25;image[1,0]=.75;image[1,1]=1
    uv=np.array([[.25,.25],[.75,.25],[.25,.75],[.75,.75],[.5,.5],[-4,-4],[4,4]],dtype=np.float32)
    np.testing.assert_array_equal(sample_linear_rgba(image,uv),np.repeat(np.array([0,.25,.75,1,.5,0,1])[:,None],4,axis=1))


def test_filter_before_nonlinear_barn_transmission():
    image=np.array([[[0]*4,[1]*4]],dtype=np.float32)
    sampled=sample_linear_rgba(image,[[.5,.5]])
    np.testing.assert_array_equal(barn_baked_transmission(sampled,0),[.25])
    # Filtering already squared visibility would incorrectly give .5 instead.


@pytest.mark.parametrize('uv',[[0,float('nan')],[0,0,0]])
def test_bad_sampling_coordinates_rejected(uv):
    with pytest.raises(ValueError):sample_linear_rgba(np.zeros((1,1,4),dtype=np.float32),uv)
