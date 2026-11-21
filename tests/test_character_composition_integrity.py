from copy import deepcopy

import pytest

from agr.blender_map_scene import snapshot_differences


def snapshot():
    return dict(transforms='matrix-bytes-before',transform_values=[0.,1.,200.],
        transform_layout=[dict(name='hero',bones=['root'])],geometry='exact-geometry-and-uv',
        animation='exact-animation-keys',packed={'image':'exact-packed-bytes'},
        bindings={'mesh/slot0':['exact-image','sRGB']},frames=[1.,2.,3.],camera='camera')


def test_float32_reevaluation_does_not_report_changed_animation():
    before=snapshot();after=deepcopy(before)
    after['transform_values'][1]+=5.960464477539063e-8
    after['transforms']='matrix-bytes-after'
    assert snapshot_differences(before,after)==[]


@pytest.mark.parametrize('field',['geometry'])
def test_structural_or_resource_changes_remain_exact(field):
    before=snapshot();after=deepcopy(before);after[field]='changed'
    assert field in snapshot_differences(before,after)
