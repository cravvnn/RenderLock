import pytest
from agr.installed_effects import UnsupportedEffect
from agr.particle_texture_channels import texture_channel_mode,texture_alpha,RGBA,RGB_ALPHA

def test_existing_default_rgba_alpha_is_preserved():
 assert texture_channel_mode({})==RGBA
 assert texture_alpha(RGBA,(.7,.3,.1,.25))==.25

def test_real_bat_core_selector_uses_rgb_not_opaque_alpha():
 mode=texture_channel_mode({'m_nTextureChannels':RGB_ALPHA})
 assert texture_alpha(mode,(0,0,0,1))==0
 assert texture_alpha(mode,(1,1,1,0))==pytest.approx(1)
 assert texture_alpha(mode,(1,0,0,1))==pytest.approx(.299)
 assert texture_alpha(mode,(0,1,0,1))==pytest.approx(.587)
 assert texture_alpha(mode,(0,0,1,1))==pytest.approx(.114)

@pytest.mark.parametrize('mode',['future',None,42])
def test_unqualified_selector_is_not_silently_rgba(mode):
 with pytest.raises(UnsupportedEffect):texture_channel_mode({'m_nTextureChannels':mode})
