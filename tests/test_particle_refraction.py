from pathlib import Path

from agr.particle_refraction import NOTE, is_screen_refraction


def test_refracting_sprite_is_recognised():
    assert is_screen_refraction({'_class': 'C_OP_RenderSprites', 'm_bRefract': True})


def test_ordinary_or_malformed_renderers_are_not():
    assert not is_screen_refraction({'_class': 'C_OP_RenderSprites'})
    assert not is_screen_refraction({'_class': 'C_OP_RenderSprites', 'm_bRefract': False})
    assert not is_screen_refraction({'_class': 'C_OP_RenderSprites', 'm_bRefract': 1})
    assert not is_screen_refraction(None)


def test_importer_reports_the_pass_instead_of_drawing_it():
    # Both the per-particle sprite loop and the rope loop skip the pass with the recorded note.
    source = (Path(__file__).resolve().parents[1] / 'agr' / 'blender_simulated_effects.py').read_text(encoding='utf-8')
    assert source.count("if is_screen_refraction(renderer):") == 2
    assert source.count("omissions.append(resource+': '+REFRACTION_NOTE)") == 2
    assert 'refraction' in NOTE
