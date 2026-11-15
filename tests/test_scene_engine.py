from pathlib import Path

from agr.scene_engine import scene_render_engine


def test_plain_scene_stays_cycles():
    assert scene_render_engine() == 'CYCLES'


def test_prepared_map_or_character_shader_selects_eevee():
    assert scene_render_engine(external_map='map') == 'BLENDER_EEVEE'
    assert scene_render_engine(character_shader='library') == 'BLENDER_EEVEE'
    # The prepared-map route builds characters first and composes the map afterwards.
    assert scene_render_engine(map_pending=True) == 'BLENDER_EEVEE'


def test_scene_build_uses_the_choice_before_creating_content():
    source = (Path(__file__).resolve().parents[1] / 'agr' / 'blender_scene.py').read_text(encoding='utf-8')
    assert source.count('scene.render.engine = scene_render_engine(character_shader, external_map, pending_world_receivers)') == 1


def test_particle_point_light_power_follows_blender_point_light_convention():
    import math
    from agr import particle_standard_light as light
    assert light.BLENDER_POINT_WATTS_PER_SOURCE_INTENSITY == 4 * math.pi ** 3
    source = Path(light.__file__).read_text(encoding='utf-8')
    assert source.count('energy=BLENDER_POINT_WATTS_PER_SOURCE_INTENSITY*peak*unit_scale**2') == 1
