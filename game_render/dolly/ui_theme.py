"""Single source of truth for Dolly's UI palette.

Two palettes live here, one per surface:

- :data:`NATIVE_TOKENS` feeds the in-game ImGui panel
  (``native/src/dolly_overlay_win.cpp``) through
  ``native/src/dolly_ui_tokens_generated.hpp``, written by
  ``tools/generate_ui_tokens.py``. It is Dolly's cool teal/graphite identity,
  tuned to neutral, low-chroma tones, and it is frozen: a change here changes
  the in-game editor at the next native build.
- :data:`TOKENS` is the desktop Tk palette (:mod:`dolly.gui`,
  :mod:`dolly.gui_theme` and the canvas-drawn shell). It carries every native
  key, overridden with Kara's "Dolly Blue" redesign, plus desktop-only keys.

Write Dolly UI colours here and regenerate the native headers with
``tools/generate_ui_tokens.py`` after changing native tokens.
"""
from __future__ import annotations

# Semantic tokens. Keys are referenced verbatim by the desktop theme and the
# generated native header, so rename with both in mind.
NATIVE_TOKENS: dict[str, str] = {
    # Surfaces
    "bg": "#11171b",            # opaque graphite window background
    "panel": "#1b252b",         # cards and popovers
    "panel_alt": "#263139",     # nested rows, headers
    "field": "#0e1418",         # recessed fields and graph interiors
    "edge": "#2c373e",          # quiet outlines and separators
    # Text
    "text": "#c8ced1",          # primary text
    "muted": "#9ba7ad",         # secondary text
    "strong": "#e6ecee",        # titles
    # Accent (primary action, selection, focus): Dolly's neutral mint/teal.
    "accent": "#8bc1b8",
    "accent_hover": "#a6d6cd",
    "accent_active": "#74a89e",
    "accent_ink": "#122a28",    # text on an accent fill
    # Interactive states
    "button": "#253039",        # secondary button at rest
    "button_hover": "#34434b",
    "button_active": "#2c3a3e",
    "selected": "#304a48",      # selected row / axis
    "selected_ink": "#e0f0ec",
    "selected_edge": "#607f7b", # selected axis outline
    "selected_blue": "#357cbb",     # selected Lite playback speed
    "disabled_bg": "#1b232a",
    "disabled_fg": "#66747e",
    "track": "#27313a",         # slider / progress trough
    "scroll_hover": "#43525c",
    # In-game panel: dimmed (background) tabs
    "tab_dim": "#181d21",
    "tab_dim_selected": "#243033",
    "tab_dim_overline": "#3d5f5a",
    # Curve identities stay consistent in single and stacked views.
    "curve_x": "#d8aaa0",
    "curve_y": "#a9c3a4",
    "curve_z": "#96b9d6",
    "curve_rotation": "#d7c27f",
    "curve_before": "#b8c5cb",
    # Status
    "warn": "#d9bd76",
    "warn_bg": "#332f22",
    "danger": "#cf8c80",
    # Key-cap chips
    "keycap": "#a2adb4",
    "keycap_ink": "#12161a",
}

# Desktop "Dolly Blue" (Figma [design reference], Bone collection mode [design reference]). Comments
# name the Figma variable each value resolves. Keys missing here (the in-game
# tabs, curves, warnings, key caps) keep their native value on the desktop.
_DOLLY_BLUE: dict[str, str] = {
    # Surfaces
    "bg": "#070809",            # bg/window
    "panel": "#0f1012",         # bg/surface: cards
    "panel_alt": "#17171a",     # bg/field
    "field": "#17171a",         # bg/field: fields, dropdowns, info bubble
    "edge": "#29292d",          # border/default
    # Text
    "text": "#ffffff",          # text/primary
    "muted": "#a6a6a6",         # text/muted: labels, values, links, status
    "strong": "#ffffff",        # text/primary
    # Accent: a fill colour (buttons, ticks, progress), too dark for text.
    "accent": "#2f5cb7",        # accent/default
    "accent_hover": "#3d6cc9",  # accent/hover
    "accent_active": "#284f9d", # accent/pressed
    "accent_ink": "#ffffff",    # text/on-accent
    # Interactive states
    "button": "#29292d",        # button/default
    "button_hover": "#34343a",  # button/hover
    "button_active": "#222226", # button/pressed
    "selected": "#29293f",      # bg/rail-selected
    "selected_ink": "#ffffff",  # text/primary
    "selected_edge": "#2f5cb7", # accent/default
    "selected_blue": "#2f5cb7", # accent/default
    "disabled_bg": "#17171a",   # kit: a disabled button is filled bg/field
    "disabled_fg": "#5d5d5d",   # text/dim
    "track": "#29292d",         # kit slider track
    "scroll_hover": "#34343a",  # button/hover
    "danger": "#e0705f",        # status/error
    # Desktop-only keys (the native header never carries these).
    "dim": "#5d5d5d",           # text/dim: placeholders, table header, info icon
    "card_edge": "#161616",     # border/soft: card stroke, table dividers
    "rail": "#0b0c0e",          # bg/rail
    "rail_hover": "#1e1e1e",    # bg/rail-hover
    "status_ready": "#a6a6a6",  # status/ready dot
    "status_busy": "#2f5cb7",   # status/busy dot
    "status_error": "#e0705f",  # status/error dot and text
    "sidebar_top": "#121a2a",   # sidebar gradient at y 0
    "sidebar_edge_top": "#2e2e2e",      # sidebar right edge, top
    "sidebar_edge_bottom": "#060709",   # sidebar right edge from y 591
    "stage_top": "#111827",     # screen background gradient at y 0
    "nav_selected_to": "#101626",       # selected nav strip, right end
    "nav_idle_to": "#000000",   # idle nav strip, right end
    "backdrop_shard": "#0a0c10",        # dark shard over the screen picture
    "backdrop_rim": "#111316",  # halftone rim beside the shard (12.5 % dots, as faint as Figma's 6 % tile)
    "backdrop": "#08090b",      # average backdrop behind the footer (pre-mixes)
    # The five ported pages (PLAN-5 2.2). Tk has no opacity: the kit's 50 % Disabled
    # faces are pre-mixed over bg/surface (``panel``), exactly mix(base, panel, .5).
    "card_edge_pinned": "#373737",      # Shot Playback card stroke ([design reference], unbound)
    "camera_marker_selected": "#e8c879", # selected in-game timeline camera
    "field_disabled": "#131416",        # bg/field at 50 %
    "muted_disabled": "#5b5b5c",        # text/muted at 50 %
    "text_disabled": "#878889",         # text/primary at 50 %
    "edge_disabled": "#1c1d20",         # border/default (and the slider track) at 50 %
    "accent_disabled": "#1f3665",       # accent/default at 50 %
    "curve_x": "#50aaff",               # Camera curve ([design reference]); the in-game panel keeps its own
    "curve_key": "#6496fb",             # Camera curve key stroke ([design reference])
    "notes_pane": "#151718",            # update notice: Release highlights pane ([design reference], unbound)
    # Dolly's own title bar and link bar (Frame 79 [design reference], USER-ADDITIONS UPDATE 20).
    "titlebar_left": "#101827",         # title bar gradient, left ([design reference])
    "titlebar_right": "#0a0e14",        # title bar gradient, right
    "titlebar_line": "#252525",         # the bars' fading 1 px lines
    "bar_line_clear": "#2c2d30",        # the lines' transparent first stop
    "linkbar_left": "#090a0b",          # link bar gradient, left ([design reference])
    "linkbar_right": "#050709",         # link bar gradient, right
    "linkbar_line": "#191919",          # link bar line at full strength
    "linkbar_ink": "#222222",           # link bar icons, divider and credit text
    # Caption buttons and menu plates: not in Figma, Windows 11's dark caption colours. Each plate
    # lays these over the bar right beneath it (window_bars.PLATE_HOVER and friends).
    "caption_tint": "#ffffff",          # hovered 6 %, pressed 4 % over the bar
    "caption_close": "#c42b1c",         # close button hovered (pressed: 90 % over the bar)
}

TOKENS: dict[str, str] = {**NATIVE_TOKENS, **_DOLLY_BLUE}


# Typography. Tk uses point sizes; the in-game ImGui panel loads its own font, so
# these apply to the desktop surfaces. 9 pt body is the design's 12 px at 96 dpi.
# Kara's UI face is Inter (Figma: Inter Regular for text, Inter Medium for values in
# fields, dropdowns, tables and keycaps). Dolly ships its static faces in
# assets/fonts/inter; :func:`dolly.ui.fonts.install` registers them privately and
# sets FONT_FAMILY / FONT_VALUE to what this process resolved. Until then, and when
# the files are missing, both stay the Segoe UI fallback.
UI_FACE = "Inter"
UI_FACE_VALUE = "Inter Medium"
FONT_FALLBACK = "Segoe UI"
FONT_FAMILY = FONT_FALLBACK
FONT_VALUE = FONT_FALLBACK
FONT_MONO = "Consolas"
FONT_SIZES = {"title": 18, "callout": 12, "card_title": 11, "section": 10, "body": 9,
              "small": 9, "tiny": 8}

# Named display faces for the redesigned shell, read by :mod:`dolly.ui.fonts`.
# Each key lists (family, design px, style) candidates in order of preference;
# the first family Tk lists wins. The VALVE faces are never shipped: they are
# used when installed or registered privately from the user's Deadlock install.
# Inter (shipped) stands in for them before the Segoe UI fallback.
DISPLAY_FONTS = {
    "wordmark": (("VALVE Oracle Medium", 21, ""), ("Inter SemiBold", 21, ""), ("Segoe UI Semibold", 21, ""),
                 ("Segoe UI", 21, "bold")),
    "page_title": (("VALVE Oracle Medium", 21, ""), ("Inter SemiBold", 21, ""), ("Segoe UI Semibold", 21, ""),
                   ("Segoe UI", 21, "bold")),
    # Every card title on every page, Shot Playback's too (Kara, UPDATE 25: "no make it 15px"):
    # 15 px on the 18 px title line.
    "card_title": (("VALVE Oracle SemiBold", 15, ""), ("Inter SemiBold", 15, ""), ("Segoe UI Semibold", 15, ""),
                   ("Segoe UI", 15, "bold")),
    "sticker": (("VALVE Pulp Bold", 16, ""), ("Inter", 16, "bold"), ("Segoe UI Black", 16, ""),
                ("Segoe UI", 16, "bold")),
    "body": (("Inter", 12, ""), ("Segoe UI", 12, "")),
    "small": (("Inter", 11, ""), ("Segoe UI", 11, "")),
    "icon": (("Segoe Fluent Icons", 13, ""), ("Segoe MDL2 Assets", 13, "")),
}
# These faces report a shallow descent to Windows while their glyphs reach much
# lower, so Tk would crop "p" and "y". Real depth per family, in em.
TRUE_DESCENT_EM = {"VALVE Oracle Medium": .30, "VALVE Oracle SemiBold": .30, "VALVE Pulp Bold": .40}


def rgb(hex_color: str) -> tuple[int, int, int]:
    """``"#rrggbb"`` -> ``(r, g, b)``; raises on anything else."""
    value = hex_color.strip()
    if len(value) != 7 or value[0] != "#":
        raise ValueError(f"not a #rrggbb colour: {hex_color!r}")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"not a #rrggbb colour: {hex_color!r}") from exc


def required_tokens() -> tuple[str, ...]:
    """Keys that both surfaces rely on; the native header must carry all of them."""
    return tuple(sorted(NATIVE_TOKENS))
