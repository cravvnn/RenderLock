import copy
from types import SimpleNamespace

import pytest

from agr.blender_simulated_effects import validate_renderer_classes
from agr.particle_projected_scene import RESOURCE, no_world_receivers, validate_profile


RENDERER = dict(_class='C_OP_RenderProjected', m_bEnableProjectedDepthControls=True,
    m_flMinProjectionDepth=-40.0, m_flMaxProjectionDepth=40.0,
    m_vecProjectedMaterials=[dict(m_hMaterial='materials/particle/abilities/vampirebat/vampirebat_nox_nostra_explode_ground_projected.vmat')])


class Object:
    def __init__(self, kind='MESH', parent=None, instance_type='NONE', **props):
        self.type = kind
        self.name = 'test-object'
        self.parent = parent
        self.instance_type = instance_type
        self.props = props
        self.hide_render = True

    def get(self, key):
        return self.props.get(key)

    def __getitem__(self, key):
        return self.props[key]


def test_actual_renderer_requires_actual_scene_and_keeps_explicit_result():
    tracks = {('root', 'path', RESOURCE, 0): []}
    assets = dict(definitions={RESOURCE: dict(m_Renderers=[RENDERER])})
    with pytest.raises(ValueError, match='actual scene'):
        validate_renderer_classes(tracks, assets)
    report = validate_renderer_classes(tracks, assets, scene=SimpleNamespace(objects=[]))
    assert report[RESOURCE]['status'] == 'no_world_receivers_in_exported_scene'


@pytest.mark.parametrize('key,value', [
    ('m_bProjectCharacter', True), ('m_bProjectWorld', False),
    ('m_flMinProjectionDepth', -41), ('m_vecProjectedMaterials', []),
])
def test_changed_renderer_never_inherits_empty_layer_qualification(key, value):
    renderer = copy.deepcopy(RENDERER)
    renderer[key] = value
    with pytest.raises(ValueError, match='qualified'):
        validate_profile(RESOURCE, renderer)


@pytest.mark.parametrize('kind', ['MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'VOLUME', 'POINTCLOUD'])
def test_hidden_unknown_geometry_still_refuses(kind):
    with pytest.raises(ValueError, match='classify'):
        no_world_receivers(SimpleNamespace(objects=[Object(kind)]))


@pytest.mark.parametrize('props', [dict(agr_map_geometry=True), dict()])
def test_adding_geometry_invalidates_preflight_even_if_hidden(props):
    scene = SimpleNamespace(objects=[])
    assert no_world_receivers(scene)['world_receivers'] == 0
    scene.objects.append(Object(**props))
    with pytest.raises(ValueError):
        no_world_receivers(scene)


@pytest.mark.parametrize('instance_type', ['COLLECTION', 'VERTS', 'FACES'])
def test_instancing_refuses_even_on_an_otherwise_excluded_role(instance_type):
    obj = Object('EMPTY', instance_type=instance_type, agr_entity_id='hero')
    with pytest.raises(ValueError, match='instanced'):
        no_world_receivers(SimpleNamespace(objects=[obj]))


def test_supported_scene_roles_include_hidden_meshes_and_parented_heroes():
    hero = Object('ARMATURE', agr_entity_id='hero')
    objects = [hero, Object(parent=hero), Object(agr_particle_resource='particles/test.vpcf'),
        Object(agr_modifier_mask_backend='modifier_attached_models_v1', agr_mask_owner='hero')]
    report = no_world_receivers(SimpleNamespace(objects=objects))
    assert report['excluded_roles'] == dict(characters=1, particles=1, modifier_models=1, reference_helpers=0, non_geometry=1)
    # An explicit map marker wins over a coincident particle role.
    objects[2].props['agr_map_geometry'] = True
    with pytest.raises(ValueError, match='world geometry'):
        no_world_receivers(SimpleNamespace(objects=objects))


def test_bad_modifier_role_and_parent_cycle_refuse():
    obj = Object(agr_modifier_mask_backend='unknown', agr_mask_owner='hero')
    with pytest.raises(ValueError, match='classify'):
        no_world_receivers(SimpleNamespace(objects=[obj]))
    obj.parent = obj
    with pytest.raises(ValueError, match='cycle'):
        no_world_receivers(SimpleNamespace(objects=[obj]))


def test_only_explicit_curve_review_helper_is_excluded():
    grid = Object('CURVE', agr_scene_role='reference_grid')
    assert no_world_receivers(SimpleNamespace(objects=[grid]))['excluded_roles']['reference_helpers'] == 1
    grid.type = 'MESH'
    with pytest.raises(ValueError, match='classify'):
        no_world_receivers(SimpleNamespace(objects=[grid]))


def test_empty_character_stage_with_pending_map_is_not_an_empty_world_result():
    class Scene:
        objects = []
        def get(self, key):
            return key == 'agr_pending_world_receivers'
    with pytest.raises(ValueError, match='scheduled for map composition'):
        no_world_receivers(Scene())


def test_prepared_map_passes_pending_receiver_context_before_character_build(tmp_path, monkeypatch):
    import sys
    from agr.blender_map_scene import build_map_first
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace())
    class ReachedCharacterStage(Exception):
        pass
    def build(*args, **kwargs):
        assert kwargs['external_map'] is None
        assert kwargs['pending_world_receivers'] is True
        raise ReachedCharacterStage
    with pytest.raises(ReachedCharacterStage):
        build_map_first(build, 'segment.json', {}, tmp_path / 'output.blend',
            library={}, project_scene=None, map_viewport_proxy=True)
