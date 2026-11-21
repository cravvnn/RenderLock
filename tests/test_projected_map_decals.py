from types import SimpleNamespace

import pytest

from agr.particle_projected_material import collect_projected_materials
from agr.particle_projected_scene import PENDING_POLICY, no_world_receivers, receiver_plan

MATERIAL = 'materials/particle/example_projected.vmat'
DEFINITIONS = {'particles/example.vpcf': {'m_Renderers': [
    {'_class': 'C_OP_RenderSprites'},
    {'_class': 'C_OP_RenderProjected', 'm_vecProjectedMaterials': [{'m_hMaterial': MATERIAL}]}]}}


class PendingScene:
    objects = []

    def get(self, key):
        return key == 'agr_pending_world_receivers'


def test_pending_map_defers_the_projected_layer_instead_of_refusing_it():
    plan = receiver_plan(PendingScene())
    assert plan == dict(policy=PENDING_POLICY, status='deferred_to_map_composition', scope=plan['scope'])
    # The strict no-receiver result is unchanged: a pending map is still not an empty world.
    with pytest.raises(ValueError, match='scheduled for map composition'):
        no_world_receivers(PendingScene())


def test_scene_without_a_map_keeps_the_no_receiver_result():
    assert receiver_plan(SimpleNamespace(objects=[]))['status'] == 'no_world_receivers_in_exported_scene'


class Decoder:
    def __init__(self, shader='projected_decals.vfx'):
        self.shader = shader

    def dump(self, name, provider):
        assert name == MATERIAL
        return {'m_shaderName': self.shader,
                'm_textureParams': [{'m_name': 'g_tColor', 'm_pValue': 'materials/particle/crater.vtex'}],
                'm_floatParams': [{'m_name': 'g_flOpacityScale', 'm_flValue': 0.5}]}


class Adapter:
    def texture(self, name, provider):
        assert name == 'materials/particle/crater.vtex'
        return 'cache/crater', {'sequences': {'0': {'frames': [{'image': 'crater.png', 'sha256': 'ab' * 32}]}}}


def test_projected_material_descriptor_carries_the_colour_texture():
    result = collect_projected_materials(DEFINITIONS, Decoder(), Adapter(), None)
    assert result == {MATERIAL: dict(status='supported', shader='projected_decals.vfx',
        colour_texture='materials/particle/crater.vtex', directory='cache/crater', image='crater.png',
        sha256='ab' * 32, opacity=0.5)}


def test_unknown_projected_shader_is_recorded_not_substituted():
    result = collect_projected_materials(DEFINITIONS, Decoder('other.vfx'), Adapter(), None)
    assert result[MATERIAL]['status'] == 'unsupported' and 'other.vfx' in result[MATERIAL]['reason']
