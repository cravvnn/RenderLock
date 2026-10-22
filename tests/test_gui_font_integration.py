"""A failed font load must not inherit another window's display-face selection."""
from agr import gui_theme as theme


def test_only_verified_available_faces_are_selected_and_scaled(monkeypatch):
    monkeypatch.setattr(theme.tkfont, 'families', lambda root: (
        'Segoe UI', 'VALVE Oracle Medium', 'VALVE Oracle SemiBold', 'VALVE Pulp Bold'))
    monkeypatch.setattr(theme, 'SCALE', 1.5)
    for name in theme.DISPLAY_FONTS:
        monkeypatch.setattr(theme, name, theme.DISPLAY_FALLBACKS[name])
    theme._resolve_display_fonts(object(), ['VALVE Oracle Medium'])
    assert theme.FONT_WORDMARK == ('VALVE Oracle Medium', -32)
    assert theme.FONT_TITLE == ('VALVE Oracle Medium', -32)
    assert theme.FONT_SECTION == theme.DISPLAY_FALLBACKS['FONT_SECTION']
    assert theme.FONT_STICKER == theme.DISPLAY_FALLBACKS['FONT_STICKER']


def test_later_fallback_root_does_not_keep_previous_display_faces(monkeypatch):
    monkeypatch.setattr(theme.tkfont, 'families', lambda root: ('VALVE Oracle Medium',))
    monkeypatch.setattr(theme, 'SCALE', 1)
    for name in theme.DISPLAY_FONTS:
        monkeypatch.setattr(theme, name, theme.DISPLAY_FALLBACKS[name])
    theme._resolve_display_fonts(object(), ['VALVE Oracle Medium'])
    assert theme.FONT_WORDMARK[0] == 'VALVE Oracle Medium'
    theme._resolve_display_fonts(object(), [])
    for name, fallback in theme.DISPLAY_FALLBACKS.items():
        assert getattr(theme, name) == fallback
