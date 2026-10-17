from types import SimpleNamespace
import pytest
from agr.blender_npr_selection import actor_meshes


class Object(dict):
    __hash__=object.__hash__
    def __init__(self,kind='MESH',**properties):
        super().__init__(properties);self.type=kind


def test_attached_particle_meshes_do_not_enter_character_material_or_probe_selection():
    body=Object()
    particle=Object(agr_particle_instance='123:1',agr_particle_resource='particles/test.vpcf')
    helper=Object('EMPTY')
    root=SimpleNamespace(children_recursive=[body,particle,helper])
    assert actor_meshes(root)=={body}
    assert root.children_recursive==[body,particle,helper]


@pytest.mark.parametrize('properties',[
    {'agr_particle_instance':''},
    {'agr_particle_instance':'123:1'},
    {'agr_particle_instance':'123:1','agr_particle_resource':'models/test.vmdl'},
])
def test_incomplete_particle_tags_cannot_silently_hide_a_character_mesh(properties):
    with pytest.raises(ValueError,match='particle identity'):
        actor_meshes(SimpleNamespace(children_recursive=[Object(**properties)]))
