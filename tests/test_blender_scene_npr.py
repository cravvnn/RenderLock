import pytest
from agr.blender_scene_npr import validate_npr_runtime


def fixture():
    return dict(hero_lighting={},npr_materials={},npr_runtime=dict(
        mode='partial_indirect_reference',assume_camera_forward=True,
        assume_white_instance_color=True))


@pytest.mark.parametrize('case',['strict','missing_hero','missing_materials','implicit_camera','numeric_white','extra'])
def test_runtime_rejects_incomplete_or_implicit_inputs(case):
    data=fixture()
    if case=='missing_hero':data.pop('hero_lighting')
    if case=='missing_materials':data.pop('npr_materials')
    if case=='implicit_camera':data['npr_runtime'].pop('assume_camera_forward')
    if case=='numeric_white':data['npr_runtime']['assume_white_instance_color']=1
    if case=='extra':data['npr_runtime']['rim']='complete'
    with pytest.raises(ValueError):validate_npr_runtime(data,allow_partial=case!='strict')


def test_runtime_absent_is_not_implicitly_activated_by_material_package():
    assert validate_npr_runtime(dict(npr_materials={}),allow_partial=True) is None
    data=fixture()
    assert validate_npr_runtime(data,allow_partial=True)==data['npr_runtime']
