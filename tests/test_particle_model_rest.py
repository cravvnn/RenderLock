import copy
import math
import numpy as np
import pytest
from agr.particle_model_rest import POLICY,model_inverse_rest


def inventory():
    return dict(schema_version=1,policy=POLICY,model='models/actor.vmdl',source_sha256='a'*64,
        names=['child','root'],parents=[1,-1],parent_transforms=[
            [1,0,0,1,0,0,0,1],[10,0,0,2,0,0,math.sin(math.pi/4),math.cos(math.pi/4)]])


def run(value):
    return model_inverse_rest(value,resource='models/actor.vmdl_c',resource_sha256='a'*64,names=['child','root'])


def test_source_parent_rows_preserve_model_order_scale_and_rotated_child_translation():
    value=inventory();before=copy.deepcopy(value);inverse=run(value);world=np.linalg.inv(inverse)
    np.testing.assert_allclose(world[0,:3,3],[10,2,0],atol=2e-6)
    np.testing.assert_allclose(world[1,:3,3],[10,0,0],atol=2e-6)
    np.testing.assert_allclose(world[0,:3,:3],[[0,-2,0],[2,0,0],[0,0,2]],atol=2e-6)
    assert value==before


@pytest.mark.parametrize('field,value',[
    ('schema_version',2),('schema_version',True),('policy','unknown'),('model','models/other.vmdl'),
    ('source_sha256','b'*64),('names',['root','child']),('names',['child','child']),
    ('parents',[0,-1]),('parents',[1,0]),('parents',[2,-1]),('parents',[-2,-1]),
    ('parents',[True,-1]),('parents',[]),('parent_transforms',[]),
    ('parent_transforms',[[0]*8]*2),
])
def test_changed_identity_mapping_hierarchy_or_missing_rows_cannot_create_rest_poses(field,value):
    data=inventory();data[field]=value
    with pytest.raises(ValueError):run(data)


@pytest.mark.parametrize('slot,value',[(0,float('nan')),(3,0),(3,-1),(7,2)])
def test_nonfinite_nonpositive_and_nonunit_rows_reject(slot,value):
    data=inventory();data['parent_transforms'][0][slot]=value
    with pytest.raises(ValueError):run(data)
