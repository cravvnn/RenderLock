import numpy as np
import pytest

from agr.fe_soft_offsets import apply_soft_offsets


def transforms():
    values=np.zeros((3,8));values[:,3]=1;values[:,7]=1
    values[0,:3]=[1,2,3];values[1,:3]=[9,8,7]
    return values


def row(parent=0,child=1,alpha=.25):
    return dict(nCtrlParent=parent,nCtrlChild=child,vOffset=[1,0,0],flAlpha=alpha)


@pytest.mark.parametrize('alpha',[0,.25,1])
def test_alpha_retains_child_without_changing_rotation_or_scale(alpha):
    current=transforms();original=current.copy()
    actual=apply_soft_offsets(current,[row(alpha=alpha)])
    np.testing.assert_allclose(actual[1,:3],alpha*current[1,:3]+(1-alpha)*np.array([2,2,3]))
    np.testing.assert_array_equal(actual[:,3:],original[:,3:])
    np.testing.assert_array_equal(current,original)


def test_parent_rotation_scale_and_separate_uniform_offset_scale():
    current=transforms();current[0,3]=2
    current[0,4:]=[0,0,2**-.5,2**-.5]
    actual=apply_soft_offsets(current,[row(alpha=0)],offset_scale=3)
    np.testing.assert_allclose(actual[1,:3],[1,8,3],atol=1e-12)


def test_ordered_duplicate_and_self_declarations_use_evolving_transforms():
    current=transforms()
    actual=apply_soft_offsets(current,[row(alpha=0),row(1,2,0),row(2,2,.5)])
    np.testing.assert_allclose(actual[:,:3],[[1,2,3],[2,2,3],[3.5,2,3]])


def test_general_route_derives_offset_from_reference_instead_of_rest_declaration():
    current=transforms();reference=transforms()
    reference[0,:3]=[5,5,5];reference[0,3]=2
    reference[0,4:]=[0,0,2**-.5,2**-.5]
    reference[1,:3]=[5,9,5]
    original=reference.copy()
    actual=apply_soft_offsets(current,[row(alpha=0)],reference=reference)
    np.testing.assert_allclose(actual[1,:3],[3,2,3],atol=1e-12)
    np.testing.assert_array_equal(reference,original)
    with pytest.raises(ValueError,match='already include'):
        apply_soft_offsets(current,[row()],reference=reference,offset_scale=2)


@pytest.mark.parametrize('change',['shape','scale','rotation','position'])
def test_invalid_reference_rejected(change):
    reference=transforms()
    if change=='shape':reference=reference[:2]
    elif change=='scale':reference[0,3]=0
    elif change=='rotation':reference[0,7]=2
    else:reference[0,0]=float('nan')
    with pytest.raises(ValueError,match='reference'):
        apply_soft_offsets(transforms(),[row()],reference=reference)


@pytest.mark.parametrize('change',[
    dict(nCtrlChild=-1),dict(nCtrlParent=3),dict(nCtrlParent=True),
    dict(vOffset=[0,0]),dict(vOffset=[0,0,float('inf')]),
    dict(flAlpha=-.1),dict(flAlpha=1.1),dict(flAlpha=float('nan'))])
def test_invalid_declarations_rejected(change):
    declaration=row();declaration.update(change)
    with pytest.raises(ValueError):apply_soft_offsets(transforms(),[declaration])


@pytest.mark.parametrize('scale',[0,-1,True,float('inf')])
def test_invalid_scale_rejected(scale):
    with pytest.raises(ValueError,match='scale'):apply_soft_offsets(transforms(),[],offset_scale=scale)
