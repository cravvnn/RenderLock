import pytest

from agr.cloth_proxy_settings import proxy_pin_weights


def proxy(enable, strength=None):
    paint={'cloth_enable$0':enable}
    if strength is not None:paint['cloth_goal_strength_v2$0']=strength
    return {'vertices':[(0,0,0)]*len(enable),'paint':paint}


def test_default_keeps_existing_static_only_behavior():
    assert proxy_pin_weights(proxy([0,1,.5],[.2,.8,.4]))==[1,0,0]
    assert proxy_pin_weights(proxy([0,1]))==[1,0]


def test_explicit_mapping_retains_static_pins_and_authored_dynamic_strength():
    assert proxy_pin_weights(proxy([0,1,.5],[.2,.8,.4]),'authored_strength')==[1,.8,.4]


@pytest.mark.parametrize('strength',[None,[],[float('nan')],[float('inf')],[-.1],[1.1],[True]])
def test_invalid_goal_paint_is_rejected(strength):
    with pytest.raises(ValueError):proxy_pin_weights(proxy([1],strength),'authored_strength')


def test_unknown_mapping_is_rejected():
    with pytest.raises(ValueError):proxy_pin_weights(proxy([1],[.5]),'guess')
