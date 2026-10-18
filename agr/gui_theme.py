"""Desktop colors, type and ttk styling for RenderLock.

Each skin is a restrained tool palette: one neutral surface scale plus a single
accent used for primary actions, selection and focus. No marketing styling; the
interface should sit naturally next to Blender. ``RENDERLOCK_UI_SKIN`` may select an
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
    ),
    # Warm charcoal, cream text and a muted brass accent.
    "bone": dict(
        # The rail is the raised panel; content sits directly on the window color.
        BG="#211f1d", SURFACE="#2d2a27", HEADER="#211f1d", FIELD="#1b1917",
        RAIL_BG="#252321", RAIL_SEL="#34302b", RAIL_HOVER="#2c2a27",
        TEXT="#ede6d8", MUTED="#a99e8d", DIM="#7f7668",
        EDGE="#3f3a34", EDGE_SOFT="#332f2a",
        ACCENT="#c6a15b", ACCENT_HOVER="#d6b273", ACCENT_PRESSED="#b3914e",
        ACCENT_TEXT="#241b0d",
        BUTTON="#34302b", BUTTON_HOVER="#3f3a34", BUTTON_PRESSED="#2b2824", BUTTON_TEXT="#ede6d8",
        DOT_READY="#9c9184", DOT_BUSY="#c6a15b", DOT_ERROR="#cf6f5b",
        # Section cards sit one step above the content panel.
        CARD="#282624", CARD_EDGE="#313131",
        # Rail outline: linear gradient from the Figma design, top to bottom.
        RAIL_EDGE_TOP="#313131", RAIL_EDGE_BOTTOM="#2e2e2e",
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
    ),
}

DEFAULT_SKIN = "bone"

FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_TINY = ("Segoe UI", 8)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SECTION = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 16, "bold")
FONT_WORDMARK = ("Segoe UI", 16, "bold")
FONT_LOG = ("Consolas", 9)

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
SKIN_NAME = DEFAULT_SKIN

# Display scale: 1.0 at 100% Windows scaling, 1.5 at 150%, ... Set by initialize().
# Layout values are written in design pixels (the Figma mockup at 100%) and pass
# through px(); font sizes are in points, which Tk already scales.
SCALE = 1.0


def enable_dpi_awareness():
    """Draw at the screen's real resolution instead of being stretched by Windows.

    Must run before the first Tk window is created. Without it, Windows renders the
    window at 96 DPI and enlarges the bitmap on scaled displays, which blurs text.
    Set RENDERLOCK_UI_DPI_AWARE=0 to opt out.
    """
    if sys.platform != "win32" or os.environ.get("RENDERLOCK_UI_DPI_AWARE", "1") == "0":
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


def px(value):
    """Convert design pixels to screen pixels for the current display scale."""
    return int(value * SCALE + 0.5)


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


def hairline():
    """Width of 1-pixel lines: stays crisp (whole pixels) at fractional scales."""
    return max(1, int(SCALE))


def icon_font(root):
    """Return a system icon family, or None when icons must be omitted."""
    families = set(tkfont.families(root))
    return next((name for name in ICON_FONTS if name in families), None)


def palette():
    """Return the active skin mapping (for canvas-drawn controls)."""
    return dict(SKINS[globals().get("SKIN_NAME", DEFAULT_SKIN)])


def initialize(root):
    """Apply the selected skin and return its palette."""
    name = os.environ.get("RENDERLOCK_UI_SKIN", DEFAULT_SKIN)
    skin = dict(SKINS.get(name, SKINS[DEFAULT_SKIN]))
    globals().update(skin)
    globals()["SKIN_NAME"] = name
    globals()["SCALE"] = max(1.0, root.winfo_fpixels("1i") / 96.0)
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
    style.configure("Vertical.TScrollbar", background=skin["EDGE"], troughcolor=skin["BG"],
                    bordercolor=skin["BG"], lightcolor=skin["EDGE"], darkcolor=skin["EDGE"],
                    arrowcolor=skin["MUTED"], borderwidth=0, arrowsize=px(8), gripcount=0)
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": 1, "sticky": "nswe"})]})])
    style.configure("Horizontal.TProgressbar", background=skin["ACCENT"], troughcolor=skin["FIELD"],
                    borderwidth=0, bordercolor=skin["FIELD"], lightcolor=skin["ACCENT"],
                    darkcolor=skin["ACCENT"], thickness=3, troughrelief="flat", pbarrelief="flat")
    root.option_add("*TCombobox*Listbox.background", skin["FIELD"])
    root.option_add("*TCombobox*Listbox.foreground", skin["TEXT"])
    root.option_add("*TCombobox*Listbox.selectBackground", skin["RAIL_SEL"])
    root.option_add("*TCombobox*Listbox.selectForeground", skin["TEXT"])
    return skin
