import copy
import numpy as np
import pytest
from agr.particle_model_sampling import sample_model_shapes


def shape():
    return dict(low=[-8]*3,high=[8]*3,translation=[10,20,30],scale=2.,
                rotation=[0,0,0,1],mapping=19,weight=1.)


def sample(shapes,**options):
    defaults=dict(scale=[.1]*3,bias=[0]*3,shell=1,count=2,weighted=False,random_value=lambda extra:.5)
    return sample_model_shapes(shapes,[1,2,3],**(defaults|options))


def test_empty_provider_uses_origin_without_random_draws():
    def random(extra):raise AssertionError('Empty provider consumed RNG')
    points,coordinates,indices=sample([],random_value=random)
    np.testing.assert_array_equal(points,[[1,2,3]]*2)
    assert not coordinates.any() and not indices.any()


def test_midpoint_transform_mapping_and_exact_draw_contract():
    boxes=[shape()];before=copy.deepcopy(boxes);draws=[]
    def random(extra):draws.append(extra);return .5
    points,coordinates,indices=sample(boxes,random_value=random)
    np.testing.assert_array_equal(points,[[10,20,30]]*2)
    np.testing.assert_array_equal(coordinates,np.full((2,3),.5))
    np.testing.assert_array_equal(indices,[19,19])
    assert draws==[False,True,True,True]*2
    assert boxes==before


def test_negative_mapping_uses_shape_order_and_zero_count_is_empty():
    box=shape();box['mapping']=-1
    assert sample([box])[2].tolist()==[0,0]
    assert [v.shape for v in sample([box],count=0)]==[(0,3),(0,3),(0,)]


@pytest.mark.parametrize('options',[dict(count=-1),dict(count=True),dict(shell=2),
    dict(scale=[1,float('nan'),1]),dict(random_value=lambda extra:1),dict(weighted=1)])
def test_unsupported_parameters_rejected(options):
    with pytest.raises(ValueError):sample([shape()],**options)


@pytest.mark.parametrize('change',[dict(mapping=32767),dict(weight=-1),dict(scale=0),
                                   dict(low=[9]*3),dict(rotation=[0,0,float('inf'),1])])
def test_invalid_shape_rejected(change):
    with pytest.raises(ValueError):sample([shape()|change])
