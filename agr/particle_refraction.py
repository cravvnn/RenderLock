"""Screen-refraction sprite and rope passes (m_bRefract).

In the game these passes bend the picture behind them and add no colour of their own; their texture is a normal
map. Screen refraction is not reproduced, so the pass is left out and reported rather than drawn as a colour layer.
"""

NOTE = 'screen-refraction pass omitted; its normal texture is not a colour layer'


def is_screen_refraction(renderer):
    return isinstance(renderer, dict) and renderer.get('m_bRefract') is True
