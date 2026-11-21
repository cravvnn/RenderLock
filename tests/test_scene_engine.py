from agr.scene_engine import scene_render_engine


def test_plain_scene_stays_cycles():
    assert scene_render_engine() == 'CYCLES'


def test_prepared_map_or_character_shader_selects_eevee():
    assert scene_render_engine(external_map='map') == 'BLENDER_EEVEE'
    assert scene_render_engine(character_shader='library') == 'BLENDER_EEVEE'
    # The prepared-map route builds characters first and composes the map afterwards.
    assert scene_render_engine(map_pending=True) == 'BLENDER_EEVEE'
