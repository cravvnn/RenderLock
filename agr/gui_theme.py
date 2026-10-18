"""Desktop colors, type and ttk styling for Deadlock 2 Blender.

Each skin is a restrained tool palette: one neutral surface scale plus a single
accent used for primary actions, selection and focus. No marketing styling; the
interface should sit naturally next to Blender. ``D2B_UI_SKIN`` may select an
alternative skin for comparison builds; normal use always takes the default.
"""
from __future__ import annotations

import os
import sys
from tkinter import font as tkfont
from tkinter import ttk

SKINS = {
    # Blender-adjacent neutral gray with a soft blue accent.
    "workbench": dict(
        BG="#2c2c2c", SURFACE="#333333", HEADER="#262626", FIELD="#212121",
        RAIL_BG="#262626", RAIL_SEL="#3a3a3a", RAIL_HOVER="#313131",
        TEXT="#e8e8e8", MUTED="#a6a6a6", DIM="#7d7d7d",
        EDGE="#424242", EDGE_SOFT="#373737",
        ACCENT="#5f8fd8", ACCENT_HOVER="#74a1e4", ACCENT_PRESSED="#4f7cc0",
        ACCENT_TEXT="#101820",
        BUTTON="#3b3b3b", BUTTON_HOVER="#474747", BUTTON_PRESSED="#323232", BUTTON_TEXT="#e8e8e8",
        DOT_READY="#8a8a8a", DOT_BUSY="#5f8fd8", DOT_ERROR="#d06a5a",
        CARD="#303030", CARD_EDGE="#3a3a3a", RAIL_EDGE_TOP="#3a3a3a", RAIL_EDGE_BOTTOM="#363636",
        WINDOW_EDGE="#424242", SELECTION="#474747", SEGMENT_SEL="#3b3b3b", BRAND="#e8e8e8", NAV_BAR_IDLE="#262626",
        BACKDROP="#2a2a2a", SIDEBAR=(("#262626", 1.0), ("#242424", 1.0), ("#202020", 1.0)),
        NAV_SEL_FROM="#3a3a3a", NAV_SEL_TO="#3a3a3a",
    ),
    # Warm near-black, cream text and a muted brass accent, over an animated background.
    "bone": dict(
        BG="#0f0e0d", SURFACE="#12100f", HEADER="#0f0e0d", FIELD="#1d1c1b",
        RAIL_BG="#121110", RAIL_SEL="#201e1a", RAIL_HOVER="#1d1b19",
        TEXT="#ede6d8", MUTED="#a99e8d", DIM="#7f7668",
        EDGE="#3f3a34", EDGE_SOFT="#332f2a",
        ACCENT="#c6a15b", ACCENT_HOVER="#d6b273", ACCENT_PRESSED="#b3914e",
        ACCENT_TEXT="#241b0d",
        BUTTON="#34302b", BUTTON_HOVER="#3f3a34", BUTTON_PRESSED="#2b2824", BUTTON_TEXT="#ede6d8",
        DOT_READY="#9c9184", DOT_BUSY="#c6a15b", DOT_ERROR="#cf6f5b",
        # Section cards: one step above the background, with a 1 px edge.
        CARD="#12100f", CARD_EDGE="#161616",
        # Sidebar outline (right edge): linear gradient from the Figma design, top to bottom.
        RAIL_EDGE_TOP="#2e2e2e", RAIL_EDGE_BOTTOM="#141312",
        # 1 px outline around the whole window.
        WINDOW_EDGE="#332f2a",
        SELECTION="#3f3a34", SEGMENT_SEL="#312f2c", BRAND="#f0dfbe", NAV_BAR_IDLE="#211f1d",
        # Average color of the animated background, for corners and areas it has not reached yet.
        BACKDROP="#0d0c0b",
        # Sidebar fill: top, 68 % and bottom stops of the design's gradient (color, opacity).
        SIDEBAR=(("#121110", 1.0), ("#100f0e", 0.91), ("#070707", 0.8)),
        # Selected nav item: left-to-right fade (the right end is fully transparent).
        NAV_SEL_FROM="#201e1a", NAV_SEL_TO="#34302b",
    ),
    # Original graphite with a mint accent, kept for comparison.
    "mint": dict(
        BG="#151a1e", SURFACE="#1d242a", HEADER="#11161a", FIELD="#11171b",
        RAIL_BG="#11161a", RAIL_SEL="#242e35", RAIL_HOVER="#1a2126",
        TEXT="#dce2e5", MUTED="#a2adb4", DIM="#75828b",
        EDGE="#303b43", EDGE_SOFT="#232c33",
        ACCENT="#9abfb4", ACCENT_HOVER="#b0cfc6", ACCENT_PRESSED="#89aca2",
        ACCENT_TEXT="#12221d",
        BUTTON="#29343c", BUTTON_HOVER="#35434c", BUTTON_PRESSED="#1f2a30", BUTTON_TEXT="#dce2e5",
        DOT_READY="#93a0a8", DOT_BUSY="#9abfb4", DOT_ERROR="#d07a6a",
        CARD="#1a2026", CARD_EDGE="#28323a", RAIL_EDGE_TOP="#28323a", RAIL_EDGE_BOTTOM="#242d34",
        WINDOW_EDGE="#303b43", SELECTION="#2f3a42", SEGMENT_SEL="#29343c", BRAND="#dce2e5", NAV_BAR_IDLE="#11161a",
        BACKDROP="#141a1e", SIDEBAR=(("#11161a", 1.0), ("#10151a", 1.0), ("#0c1013", 1.0)),
        NAV_SEL_FROM="#242e35", NAV_SEL_TO="#242e35",
    ),
}

DEFAULT_SKIN = "bone"

FONT = ("Segoe UI", 9)          # 12 px at 100 %, like the design's text
FONT_SMALL = ("Segoe UI", 9)
FONT_TINY = ("Segoe UI", 8)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_SECTION = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 16, "bold")
FONT_WORDMARK = ("Segoe UI", 16, "bold")
FONT_LOG = ("Consolas", 9)
FONT_STICKER = ("Segoe UI", 10, "bold")

# Display faces from the design. They are used only when installed on this machine
# (they are not bundled); otherwise the Segoe UI fallbacks above stay in effect.
# Sizes are design pixels and are converted with px() in initialize().
DISPLAY_FONTS = {
    "FONT_WORDMARK": ("VALVE Oracle Medium", 21),
    "FONT_TITLE": ("VALVE Oracle Medium", 21),
    "FONT_SECTION": ("VALVE Oracle SemiBold", 15),
    "FONT_STICKER": ("VALVE Pulp Bold", 14),
}
# These faces report a shallow descent to Windows (VALVE Oracle: 0.09 em) while their
# glyphs reach much lower (0.30 em), so Tk would crop "p", "y", ... Real depth per family:
TRUE_DESCENT_EM = {"VALVE Oracle SemiBold": 0.30, "VALVE Oracle Medium": 0.30, "VALVE Pulp Bold": 0.40}


def descent_shortfall(font):
    """Extra pixels below the reported descent that a font's glyphs actually need."""
    em = TRUE_DESCENT_EM.get(font[0])
    if not em:
        return 0
    measured = tkfont.Font(font=font)
    size_px = -font[1] if font[1] < 0 else measured.metrics("ascent")  # display fonts use pixel sizes
    return max(0, int(em * size_px + 0.999) - measured.metrics("descent"))

ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")

# Fallback values for module attributes until initialize() runs.
BG = SKINS[DEFAULT_SKIN]["BG"]
SURFACE = SKINS[DEFAULT_SKIN]["SURFACE"]
HEADER = SKINS[DEFAULT_SKIN]["HEADER"]
FIELD = SKINS[DEFAULT_SKIN]["FIELD"]
RAIL_BG = SKINS[DEFAULT_SKIN]["RAIL_BG"]
CARD = SKINS[DEFAULT_SKIN]["CARD"]
CARD_EDGE = SKINS[DEFAULT_SKIN]["CARD_EDGE"]
RAIL_EDGE_TOP = SKINS[DEFAULT_SKIN]["RAIL_EDGE_TOP"]
RAIL_EDGE_BOTTOM = SKINS[DEFAULT_SKIN]["RAIL_EDGE_BOTTOM"]
RAIL_SEL = SKINS[DEFAULT_SKIN]["RAIL_SEL"]
RAIL_HOVER = SKINS[DEFAULT_SKIN]["RAIL_HOVER"]
TEXT = SKINS[DEFAULT_SKIN]["TEXT"]
MUTED = SKINS[DEFAULT_SKIN]["MUTED"]
DIM = SKINS[DEFAULT_SKIN]["DIM"]
EDGE = SKINS[DEFAULT_SKIN]["EDGE"]
EDGE_SOFT = SKINS[DEFAULT_SKIN]["EDGE_SOFT"]
ACCENT = SKINS[DEFAULT_SKIN]["ACCENT"]
ACCENT_HOVER = SKINS[DEFAULT_SKIN]["ACCENT_HOVER"]
ACCENT_PRESSED = SKINS[DEFAULT_SKIN]["ACCENT_PRESSED"]
ACCENT_TEXT = SKINS[DEFAULT_SKIN]["ACCENT_TEXT"]
BUTTON = SKINS[DEFAULT_SKIN]["BUTTON"]
BUTTON_HOVER = SKINS[DEFAULT_SKIN]["BUTTON_HOVER"]
BUTTON_PRESSED = SKINS[DEFAULT_SKIN]["BUTTON_PRESSED"]
BUTTON_TEXT = SKINS[DEFAULT_SKIN]["BUTTON_TEXT"]
DOT_READY = SKINS[DEFAULT_SKIN]["DOT_READY"]
DOT_BUSY = SKINS[DEFAULT_SKIN]["DOT_BUSY"]
DOT_ERROR = SKINS[DEFAULT_SKIN]["DOT_ERROR"]
SELECTION = SKINS[DEFAULT_SKIN]["SELECTION"]
WINDOW_EDGE = SKINS[DEFAULT_SKIN]["WINDOW_EDGE"]
SEGMENT_SEL = SKINS[DEFAULT_SKIN]["SEGMENT_SEL"]
BRAND = SKINS[DEFAULT_SKIN]["BRAND"]
NAV_BAR_IDLE = SKINS[DEFAULT_SKIN]["NAV_BAR_IDLE"]
BACKDROP = SKINS[DEFAULT_SKIN]["BACKDROP"]
SIDEBAR = SKINS[DEFAULT_SKIN]["SIDEBAR"]
NAV_SEL_FROM = SKINS[DEFAULT_SKIN]["NAV_SEL_FROM"]
NAV_SEL_TO = SKINS[DEFAULT_SKIN]["NAV_SEL_TO"]
SKIN_NAME = DEFAULT_SKIN

# Display scale: 1.0 at 100% Windows scaling, 1.5 at 150%, ... Set by initialize().
# Layout values are written in design pixels (the Figma mockup at 100%) and pass
# through px(); font sizes are in points, which Tk already scales.
SCALE = 1.0


def enable_dpi_awareness():
    """Draw at the screen's real resolution instead of being stretched by Windows.

    Must run before the first Tk window is created. Without it, Windows renders the
    window at 96 DPI and enlarges the bitmap on scaled displays, which blurs text.
    Set D2B_UI_DPI_AWARE=0 to opt out.
    """
    if sys.platform != "win32" or os.environ.get("D2B_UI_DPI_AWARE", "1") == "0":
        return False
    import ctypes
    try:
        # System aware (1): sharp at the primary display's scale. Tk 8.6 does not
        # re-layout on per-monitor DPI changes, so per-monitor mode is not used.
        return ctypes.windll.shcore.SetProcessDpiAwareness(1) == 0
    except (AttributeError, OSError):
        try:
            return bool(ctypes.windll.user32.SetProcessDPIAware())
        except (AttributeError, OSError):
            return False


def style_title_bar(root):
    """Paint the native Windows title bar in the skin's colors.

    The frame stays the real Windows frame (snapping, resizing and the caption
    buttons keep working); DWM only recolors it. Caption/text/border colors need
    Windows 11; Windows 10 (20H1+) gets the dark title bar only. Call once the
    window is mapped, because the outer frame only exists from then on.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    from tkinter import TclError
    try:
        hwnd = wintypes.HWND(int(root.wm_frame(), 16))
        dwm = ctypes.windll.dwmapi
    except (AttributeError, OSError, ValueError, TclError):
        return False

    def set_attribute(attribute, value):
        data = ctypes.c_int(value)
        return dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)) == 0

    def colorref(color):  # "#rrggbb" -> 0x00bbggrr
        return int(color[1:3], 16) | int(color[3:5], 16) << 8 | int(color[5:7], 16) << 16

    dark = set_attribute(20, 1) or set_attribute(19, 1)          # DWMWA_USE_IMMERSIVE_DARK_MODE
    caption = set_attribute(35, colorref(BG))                     # DWMWA_CAPTION_COLOR
    set_attribute(36, colorref(TEXT))                             # DWMWA_TEXT_COLOR
    set_attribute(34, colorref(BG))                               # DWMWA_BORDER_COLOR
    return dark or caption


def motion_enabled():
    """False when Windows "Animation effects" are off or D2B_UI_MOTION=0; decorative motion then stays still."""
    if os.environ.get("D2B_UI_MOTION", "1") == "0":
        return False
    if sys.platform != "win32":
        return True
    import ctypes
    enabled = ctypes.c_int(1)
    try:
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):  # SPI_GETCLIENTAREAANIMATION
            return bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return True


def _resolve_display_fonts(root):
    """Switch titles, the wordmark and sticker labels to the design's display faces when installed."""
    families = set(tkfont.families(root))
    for name, (family, size) in DISPLAY_FONTS.items():
        if family in families:
            # Negative sizes are pixels; scale them like every other design-pixel value.
            globals()[name] = (family, -px(size))


def px(value):
    """Convert design pixels to screen pixels for the current display scale."""
    return int(value * SCALE + 0.5)


def hairline():
    """Width of 1-pixel lines: stays crisp (whole pixels) at fractional scales."""
    return max(1, int(SCALE))


def icon_font(root):
    """Return a system icon family, or None when icons must be omitted."""
    families = set(tkfont.families(root))
    return next((name for name in ICON_FONTS if name in families), None)


# --------------------------------------------------------------------------
# Color management, the way Figma does it.
#
# The design's colors are sRGB values. On a wide-gamut screen Figma converts them to the screen's
# own primaries before drawing (keeping the sRGB tone curve), so there they look slightly calmer
# than the raw values an ordinary Windows app sends. The app makes the same conversion, using the
# display's color profile, so it looks like the design does in Figma on the same screen. On an
# sRGB screen nothing changes. D2B_UI_COLOR=raw sends the design values unchanged.

_SRGB_TO_XYZ_D50 = ((0.4360747, 0.3850649, 0.1430804), (0.2225045, 0.7168786, 0.0606169),
                    (0.0139322, 0.0971045, 0.7141733))
_COLOR_MATRIX = None      # sRGB (linear) -> display (linear), or None for no conversion
_COLOR_CACHE: dict = {}
_ACTIVE: dict = {}


def _invert(m):
    a, b, c = m
    det = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])
    return (((b[1] * c[2] - b[2] * c[1]) / det, (a[2] * c[1] - a[1] * c[2]) / det, (a[1] * b[2] - a[2] * b[1]) / det),
            ((b[2] * c[0] - b[0] * c[2]) / det, (a[0] * c[2] - a[2] * c[0]) / det, (a[2] * b[0] - a[0] * b[2]) / det),
            ((b[0] * c[1] - b[1] * c[0]) / det, (a[1] * c[0] - a[0] * c[1]) / det, (a[0] * b[1] - a[1] * b[0]) / det))


def _display_matrix():
    """sRGB -> display primaries from the main display's ICC profile (matrix profiles), or None."""
    if sys.platform != "win32" or os.environ.get("D2B_UI_COLOR", "") == "raw":
        return None
    import ctypes
    import struct
    from ctypes import wintypes
    try:
        user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        gdi32.GetICMProfileW.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.DWORD), ctypes.c_wchar_p]
        dc = user32.GetDC(None)
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(1024)
        found = gdi32.GetICMProfileW(dc, ctypes.byref(size), path)
        user32.ReleaseDC(None, dc)
        if not found:
            return None
        with open(path.value, "rb") as handle:
            data = handle.read()
        tags = {}
        for i in range(struct.unpack(">I", data[128:132])[0]):
            sig, offset, length = struct.unpack(">4sII", data[132 + 12 * i:144 + 12 * i])
            tags[sig] = data[offset:offset + length]
        columns = [[struct.unpack(">i", tags[tag][8 + 4 * k:12 + 4 * k])[0] / 65536.0 for k in range(3)]
                   for tag in (b"rXYZ", b"gXYZ", b"bXYZ")]
    except (OSError, KeyError, ValueError, struct.error, AttributeError):
        return None
    panel = tuple(tuple(columns[c][r] for c in range(3)) for r in range(3))
    try:
        inverse = _invert(panel)
    except ZeroDivisionError:
        return None
    matrix = tuple(tuple(sum(inverse[i][k] * _SRGB_TO_XYZ_D50[k][j] for k in range(3)) for j in range(3))
                   for i in range(3))
    if all(abs(matrix[i][j] - (1.0 if i == j else 0.0)) < 0.003 for i in range(3) for j in range(3)):
        return None  # an sRGB screen: nothing to convert
    return matrix


def _to_linear(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _from_linear(v):
    v = min(1.0, max(0.0, v))
    return 255.0 * (12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055)


def screen_rgb(rgb):
    """A design (sRGB) color as (r, g, b) floats 0-255 for this screen."""
    if _COLOR_MATRIX is None:
        return tuple(float(v) for v in rgb)
    lin = [_to_linear(float(v)) for v in rgb]
    return tuple(_from_linear(sum(_COLOR_MATRIX[r][k] * lin[k] for k in range(3))) for r in range(3))


def screen(color):
    """A design "#rrggbb" color as the "#rrggbb" to send to this screen."""
    if _COLOR_MATRIX is None or not isinstance(color, str) or not color.startswith("#") or len(color) != 7:
        return color
    cached = _COLOR_CACHE.get(color)
    if cached is None:
        rgb = screen_rgb([int(color[i:i + 2], 16) for i in (1, 3, 5)])
        cached = _COLOR_CACHE[color] = "#%02x%02x%02x" % tuple(int(v + 0.5) for v in rgb)
    return cached


def screen_pixels(pixels):
    """Convert a uint8 numpy array (..., 3) of design colors for this screen (returns a new array)."""
    if _COLOR_MATRIX is None:
        return pixels
    import numpy as np
    decode = np.array([_to_linear(float(v)) for v in range(256)], np.float32)
    lin = decode[pixels]
    out = np.einsum("...k,rk->...r", lin, np.array(_COLOR_MATRIX, np.float32))
    out = np.clip(out, 0.0, 1.0)
    out = np.where(out <= 0.0031308, 12.92 * out, 1.055 * np.power(out, 1 / 2.4) - 0.055)
    return (out * 255.0 + 0.5).astype(np.uint8)


def _screen_value(value):
    if isinstance(value, str):
        return screen(value)
    if isinstance(value, tuple):
        return tuple(_screen_value(v) for v in value)
    return value


def palette():
    """Return the active skin mapping (for canvas-drawn controls), in screen colors."""
    return dict(_ACTIVE or SKINS[globals().get("SKIN_NAME", DEFAULT_SKIN)])


def initialize(root):
    """Apply the selected skin and return its palette."""
    global _COLOR_MATRIX
    _COLOR_MATRIX = _display_matrix()
    _COLOR_CACHE.clear()
    name = os.environ.get("D2B_UI_SKIN", DEFAULT_SKIN)
    if name not in SKINS:  # an unknown or mistyped skin name falls back to the default
        name = DEFAULT_SKIN
    skin = {key: _screen_value(value) for key, value in SKINS[name].items()}
    _ACTIVE.clear()
    _ACTIVE.update(skin)
    globals().update(skin)
    globals()["SKIN_NAME"] = name
    globals()["SCALE"] = max(1.0, root.winfo_fpixels("1i") / 96.0)
    _resolve_display_fonts(root)
    root.configure(background=skin["BG"])

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=skin["BG"], foreground=skin["TEXT"], font=FONT)
    style.configure("TFrame", background=skin["BG"])
    style.configure("TLabel", background=skin["BG"], foreground=skin["TEXT"])
    style.configure("Muted.TLabel", foreground=skin["MUTED"])
    style.configure("TCombobox", fieldbackground=skin["FIELD"], background=skin["BUTTON"],
                    foreground=skin["TEXT"], arrowcolor=skin["MUTED"], bordercolor=skin["EDGE"],
                    lightcolor=skin["EDGE"], darkcolor=skin["EDGE"], padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", skin["FIELD"]), ("disabled", skin["BG"])],
              foreground=[("readonly", skin["TEXT"]), ("disabled", skin["DIM"])],
              arrowcolor=[("disabled", skin["EDGE_SOFT"])])
    for name_ in ("TCheckbutton", "TRadiobutton"):
        style.configure(name_, background=skin["BG"], foreground=skin["TEXT"], padding=(0, 4),
                        indicatorbackground=skin["FIELD"], indicatorforeground=skin["ACCENT_TEXT"],
                        upperbordercolor=skin["EDGE"], lowerbordercolor=skin["EDGE"],
                        indicatorsize=13, indicatormargin=(0, 3, 8, 3))
        style.map(name_, background=[("active", skin["BG"])],
                  foreground=[("disabled", skin["DIM"])])
    for name_ in ("TCheckbutton", "TRadiobutton"):
        style.map(name_, indicatorbackground=[("disabled", skin["EDGE_SOFT"]),
                                              ("selected", skin["ACCENT"])],
                  indicatorforeground=[("selected", skin["ACCENT_TEXT"])])
    style.configure("Vertical.TScrollbar", background=skin["EDGE"], troughcolor=skin["BACKDROP"],
                    bordercolor=skin["BACKDROP"], lightcolor=skin["EDGE"], darkcolor=skin["EDGE"],
                    arrowcolor=skin["MUTED"], borderwidth=0, arrowsize=px(8), gripcount=0)
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": 1, "sticky": "nswe"})]})])
    style.configure("Horizontal.TProgressbar", background=skin["ACCENT"], troughcolor=skin["FIELD"],
                    borderwidth=0, bordercolor=skin["FIELD"], lightcolor=skin["ACCENT"],
                    darkcolor=skin["ACCENT"], thickness=3, troughrelief="flat", pbarrelief="flat")
    root.option_add("*TCombobox*Listbox.background", skin["FIELD"])
    root.option_add("*TCombobox*Listbox.foreground", skin["TEXT"])
    root.option_add("*TCombobox*Listbox.selectBackground", skin["SELECTION"])
    root.option_add("*TCombobox*Listbox.selectForeground", skin["TEXT"])
    return skin


def brand_window(root):
    """Share the actual RenderLock icon and native frame across app windows."""
    import tkinter as tk
    from .gui_assets import APP_ICON_PNG
    root._renderlock_icon = tk.PhotoImage(master=root, data=APP_ICON_PNG)
    root.iconphoto(True, root._renderlock_icon)
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Cravvnn.RenderLock")
        except (AttributeError, OSError):
            pass
    def mapped(event):
        if event.widget is root:
            style_title_bar(root)
    root.bind("<Map>", mapped, add="+")
    return root._renderlock_icon

