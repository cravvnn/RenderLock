"""Desktop workspace: title bar, sidebar, page content and the shared job controller.

Every page shares the launcher's controller (``AGRApp``); this module only
builds widgets and the small navigation/selection behaviors. Layout targets a
compact tool window that sits naturally next to Blender: the design's own title
bar, a sidebar for pages, one card per section, and a footer with the job
buttons and the status line, all over an animated background.

Tk widgets are opaque, so everything that sits directly on the background (page
title, footer links, status, sticker buttons, the gaps between cards) is drawn
on canvases that also carry the background (see ``gui_backdrop``). Cards and
their controls are ordinary widgets on top.

All sizes are design pixels (the Figma mockup at 100%) passed through ``px()``,
so the layout keeps its proportions on scaled displays. The window resizes
freely; the background stretches with it so a maximized window looks like the
default one, while controls keep their size.
"""
from __future__ import annotations

import base64
from pathlib import Path
import tkinter as tk
import zlib
from tkinter import ttk

from . import __version__, gui_assets, gui_fade, gui_theme
from .gui_backdrop import BASE_COLOR, Backdrop
from .gui_chrome import BAR_HEIGHT, EdgeResizer, TitleBar, WindowChrome
from .gui_theme import hairline, px
from .gui_widgets import (Card, CanvasDot, CanvasFocus, CanvasLink, CanvasProgress, CanvasSticker, CanvasText,
                          CheckBox, CollapsibleNote, CopyButton, DisplayText, InfoIcon, RoundedButton, RoundedEntry,
                          SegmentedControl, copy_text, gradient_color, rgba_image, wrap_to_width)
from .release_profile import character_beta
from .gui_characters import CHARACTERS_INFO, FOLLOW_INFO, TRAY_GAP, CharacterChecklist, FollowHeroPicker

PAGE_EXPORT = "Export"
PAGE_SETUP = "Setup"
PAGE_ADVANCED = "Advanced"
PAGE_LIGHTING = "Lighting lab"
PAGE_MAP = "Map"
PAGE_UPDATES = "Updates"

PAGE_DETAILS = {
    PAGE_EXPORT: "Choose a recording, inspect it, then export a Blender scene.",
    PAGE_SETUP: "Point RenderLock at Blender and your game files. Conversion never launches Deadlock.",
    PAGE_ADVANCED: "Experimental character options and output policy.",
    PAGE_LIGHTING: "Partial Source lighting and Elagrimm character look.",
    PAGE_MAP: "Append the downloaded map; keep lighting setup separate.",
    PAGE_UPDATES: "Review release notes and install RenderLock updates.",
}

WORDMARK = "RenderLock"
WINDOW_W, WINDOW_H = 1040, 760   # content below the title bar
MIN_W, MIN_H = 900, 660
RAIL_WIDTH = 181
NAV_TOP, NAV_HEIGHT = 121, 40
CONTENT_PAD_X = 40
HEADER_TOP = 30                # page title line (26 px tall); the subtitle line starts 29 px lower
PAGE_TOP = 107                 # first card
PAGE_BOTTOM = 128              # last card ends this far above the window bottom
ACTIONS_BOTTOM = 102           # footer links row (36 px) starts this far above the bottom
STATUS_BOTTOM = 66             # status row (36 px)
STICKER_CENTER_BOTTOM = 57     # sticker labels' centre line
LOG_GAP = 12
# Spacing rhythm inside the pages (design px). The closer two things belong together, the
# smaller the gap: blocks of a card are 15 apart, field rows 7, checkbox options 5.
CARD_GAP = 10              # between cards
SECTION_TITLE_LINE = 18    # card title line height
SECTION_GAP = 15           # between a card's blocks: title -> content, fields -> their explanation,
                           # one sub-section -> the next, status -> its buttons
ROW_GAP = 7                # between field rows (and between two short notes that read as one)
CHECK_GAP = 5              # after a checkbox: to the next option, or to the fields it switches on
LABEL_GAP = 10             # space between a row label and its field
TITLE_GAP = 4              # title -> subtitle inside cards
TEXT_TOP = 1               # a 12 px Segoe UI line's letters start about this far below the line's top
LONG_NOTE = 220            # characters: longer notes start collapsed to one line ending in "…"
LABEL_COLUMN = 0           # Export rows: each label hugs its field ("File", "Save as")
WIDE_LABEL_COLUMN = 150    # Setup/Advanced rows keep their fields aligned
TEXT_WRAP = 700            # first-layout wrap width; notes then wrap at their card's width


def _pad(value):
    """Scale a padding value or (before, after) pair from design pixels."""
    return tuple(px(v) for v in value) if isinstance(value, tuple) else px(value)


def _copy_log(app):
    """The log drawer's copy button: the whole log to the clipboard. True when copied."""
    text = app.log.get("1.0", "end-1c")
    if not text.strip():
        return False
    if copy_text(app.log, text):
        return True
    # The clipboard was unavailable: select the log and focus it, so Ctrl+C copies it.
    app.log.tag_add("sel", "1.0", "end-1c")
    app.log.mark_set("insert", "1.0")
    app.log.focus_set()
    return False


def _display_text(parent, text, font, color):
    """A title in a display face, with room for its real descenders.

    Returns the widget and the extra room (px) it adds below the line, so callers can
    take that room back out of the gap that follows and keep the layout unchanged.
    """
    room = gui_theme.descent_shortfall(font)
    return DisplayText(parent, text=text, font=font, foreground=color, room=room), room


def _blend(stops, t):
    """Color and opacity at ``t`` on a gradient of ((position, "#rrggbb", alpha), ...)."""
    t = min(1.0, max(0.0, t))
    for (p0, c0, a0), (p1, c1, a1) in zip(stops, stops[1:]):
        if t <= p1:
            k = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            rgb0 = [int(c0[i:i + 2], 16) for i in (1, 3, 5)]
            rgb1 = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
            return [a + (b - a) * k for a, b in zip(rgb0, rgb1)], a0 + (a1 - a0) * k
    last = stops[-1]
    return [int(last[1][i:i + 2], 16) for i in (1, 3, 5)], last[2]


def _over(rgb, alpha):
    """``rgb`` (screen colors) at ``alpha`` over the background's typical color."""
    base = gui_theme.screen_rgb(BASE_COLOR)
    return "#%02x%02x%02x" % tuple(int(b + (c - b) * alpha + 0.5) for c, b in zip(rgb, base))


def _asset_image(master, width, height, blob):
    """A Tk image from embedded raw RGBA, converted for this screen's colors."""
    raw = zlib.decompress(base64.b64decode(blob))
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        pixels = np.frombuffer(raw, np.uint8).reshape(height, width, 4).copy()
        pixels[:, :, :3] = gui_theme.screen_pixels(pixels[:, :, :3])
        rows = [pixels[y].tobytes() for y in range(height)]
    else:
        rows = [raw[y * width * 4:(y + 1) * width * 4] for y in range(height)]
    return rgba_image(master, width, height, rows)


def set_log_visible(app, visible: bool) -> None:
    was_visible = getattr(app, "log_visible", False)
    app.log_visible = visible
    app.log_button.configure(text="Hide log" if visible else "Show log")
    if not visible:
        # Keyboard focus must not stay inside the hidden drawer (Return/Space on its copy button).
        drawer = str(app.log_frame)
        focused = str(app.root.tk.call("focus", "-lastfor", app.root))  # also when another app is active
        if focused == drawer or focused.startswith(drawer + "."):
            app.root.focus_set()
    app.layout_stage()
    if visible != was_visible:
        _resize_for_log(app, visible)


def _work_area(root):
    """Usable rectangle (left, top, right, bottom) of the monitor the window is on, without the
    taskbar. Before the window is shown: the primary monitor's."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        if root.winfo_ismapped():
            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
            user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user32.MonitorFromWindow.restype = wintypes.HMONITOR
            user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            monitor = user32.MonitorFromWindow(wintypes.HWND(int(root.wm_frame(), 16)), 2)  # NEAREST
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.addressof(info)):
                work = info.rcWork
                return work.left, work.top, work.right, work.bottom
        rect = wintypes.RECT()
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError, ValueError, tk.TclError):
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _resize_for_log(app, visible):
    """Grow the window by the log drawer's height when it opens, and give it back when it closes.

    The pages keep their space instead of being squeezed. If the taller window would run
    off the bottom of its monitor it moves up first, and it never grows past the monitor.
    A maximized window keeps its size; a drawer closed while maximized gives its height back
    when the window is restored (see _restored).
    """
    root = app.root
    if not root.winfo_ismapped() or root.state() == "zoomed":
        if visible:
            # Opened while maximized: nothing grows now. If a shrink was still owed (the drawer was
            # closed while maximized), the reopened drawer simply uses that height again.
            if not getattr(app, "log_shrink_pending", False):
                app.log_growth = 0
            app.log_shrink_pending = False
        elif getattr(app, "log_growth", 0):
            app.log_shrink_pending = True
        return
    root.update_idletasks()
    size, x, y = root.geometry().split("+")[0], root.winfo_x(), root.winfo_y()
    width, height = (int(v) for v in size.split("x"))
    if visible:
        wanted = app.log_frame.winfo_reqheight() + px(LOG_GAP)
        _left, top, _right, bottom = _work_area(root)
        chrome = (root.winfo_rooty() - y) + (root.winfo_rootx() - x)  # native frame, if any
        growth = min(wanted, max(0, (bottom - top) - chrome - height))
        app.log_growth = growth
        if growth > 0:
            new_y = max(top, min(y, bottom - chrome - (height + growth)))
            root.geometry(f"{width}x{height + growth}+{x}+{new_y}")
    else:
        growth, app.log_growth = getattr(app, "log_growth", 0), 0
        app.log_shrink_pending = False
        if growth:
            root.geometry(f"{width}x{max(app.min_height, height - growth)}+{x}+{y}")


def _restored(app, event):
    """After un-maximizing, give back the height of a log drawer that was closed while maximized."""
    root = app.root
    if (event.widget is root and getattr(app, "log_shrink_pending", False) and not app.log_visible
            and root.winfo_ismapped() and root.state() == "normal"):
        app.log_shrink_pending = False
        root.after_idle(lambda: _resize_for_log(app, False))


# --------------------------------------------------------------------------
# Small builders shared by every page. Section content lives on cards, so
# every helper takes the card body as ``parent`` and paints with CARD.


def _label(parent, text, font=None, color=None, **kw):
    # No border or padding: the line is exactly the font's height, like the design's 16 px text lines.
    return tk.Label(parent, text=text, background=parent.cget("background"), bd=0, padx=0, pady=0,
                    foreground=color or gui_theme.MUTED, font=font or gui_theme.FONT_SMALL, **kw)


def _entry(parent, variable, width=120):
    return RoundedEntry(parent, variable=variable, theme=gui_theme.palette(), font=gui_theme.FONT,
                        width=width)


def _card(app, page, row, title, detail="", label_column=LABEL_COLUMN, info=""):
    """Add a section card to ``page`` and return its body and the next page row.

    ``detail`` is a line that stays visible under the title (e.g. the installed version);
    ``info`` is the card's description, shown when hovering the info icon beside the title.
    """
    # Padding counts the 1 px edge, as in the design (content starts 16 px down, 21 px in).
    card = Card(page.canvas, theme=gui_theme.palette(), background=gui_theme.BACKDROP, pad_x=21, pad_y=16)
    page.add(card, gap=0 if row == 0 else CARD_GAP)
    body = card.body
    body.columnconfigure(0, minsize=px(label_column))
    body.columnconfigure(1, weight=1)
    holder = tk.Frame(body, background=gui_theme.CARD)
    heading, room = _display_text(holder, title, gui_theme.FONT_SECTION, gui_theme.TEXT)
    heading.pack(side="left")
    if info:
        InfoIcon(holder, text=info, theme=gui_theme.palette()).pack(side="left", padx=(px(6), 0))
    # The title's 18 px line plus a 15 px gap: content starts 33 px below the title's top.
    gap = px(TITLE_GAP) - room if detail else px(SECTION_TITLE_LINE + SECTION_GAP) - int(heading.cget("height"))
    holder.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, max(0, gap)))
    if detail:
        wrap_to_width(_label(body, detail, justify="left", anchor="w", wraplength=px(TEXT_WRAP))).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=_pad((0, SECTION_GAP)))
    return body, row + 1


def _next_row(body):
    return body.grid_size()[1]


def _note(parent, text, pady=(12, 0)):
    if len(text) > LONG_NOTE:
        note = CollapsibleNote(parent, text=text, theme=gui_theme.palette())
    else:
        note = wrap_to_width(_label(parent, text, justify="left", anchor="w", wraplength=px(TEXT_WRAP)))
    note.grid(row=_next_row(parent), column=0, columnspan=3, sticky="ew", pady=_pad(pady))


def _path_row(app, parent, label, key, command, button=True):
    row = _next_row(parent)
    gap = _pad((0, ROW_GAP))
    _label(parent, label).grid(row=row, column=0, sticky="w", padx=_pad((0, LABEL_GAP)), pady=gap)
    entry = _entry(parent, app.values[key])
    entry.grid(row=row, column=1, sticky="ew", pady=gap)
    app.mutable_widgets.append(entry)
    if button:
        browse = RoundedButton(parent, text="Browse", command=command, theme=gui_theme.palette(), height=34,
                               pad_x=13)
        browse.grid(row=row, column=2, padx=_pad((5, 0)), pady=gap)
        app.mutable_widgets.append(browse)


def _check(app, parent, text, variable, onvalue="1", offvalue="0", pady=(0, 1), *, installed_only=False, info=""):
    """A checkbox row; ``info`` adds an info icon whose hover text describes the option."""
    holder = parent
    if info:
        holder = tk.Frame(parent, background=parent.cget("background"))
    check = CheckBox(holder, text=text, variable=variable, theme=gui_theme.palette(),
                     onvalue=onvalue, offvalue=offvalue)
    if info:
        check.pack(side="left")
        InfoIcon(holder, text=info, theme=gui_theme.palette(), size=12).pack(side="left", padx=(px(2), 0))
    (holder if info else check).grid(row=_next_row(parent), column=0, columnspan=3, sticky="w", pady=_pad(pady))
    app.mutable_widgets.append(check)
    if installed_only:
        if not hasattr(app, "installed_only_widgets"):
            app.installed_only_widgets = []
        app.installed_only_widgets.append(check)
    return check


def refresh_asset_source_controls(app):
    """Reflect preparation dependencies without changing remembered preferences."""
    installed = app.values["asset_source"].get() == "installed"
    state = "normal" if installed and not getattr(app, "busy", False) else "disabled"
    for widget in getattr(app, "installed_only_widgets", ()):
        widget.configure(state=state)
    refresh_character_look(app)


def refresh_character_look(app, *, changed=False):
    """Describe the selected bundle within the existing Lighting lab card."""
    if not hasattr(app, 'look_description'):
        return
    mode = app.values['character_look'].get()
    source = mode in {'source', 'elagrimm_source'}
    custom = mode == 'custom'
    if changed:
        app.lighting_consent.set(False)
        app.look_tools_expanded = False
    app.look_custom_fields.grid() if custom else app.look_custom_fields.grid_remove()
    app.look_consent_fields.grid() if source or custom else app.look_consent_fields.grid_remove()
    app.look_shader_fields.grid() if mode == 'elagrimm_source' or custom else app.look_shader_fields.grid_remove()
    app.look_tools_fields.grid() if source or custom else app.look_tools_fields.grid_remove()
    expanded = getattr(app, 'look_tools_expanded', False) and (source or custom)
    app.look_tool_overrides.grid() if expanded else app.look_tool_overrides.grid_remove()
    app.look_tools_toggle.configure(text='Hide advanced tool overrides' if expanded else 'Advanced tool overrides')
    try:
        found = all(Path(app.values[key].get()).is_file() for key in
                    ('probe_atlas_decoder', 'cubemap_decoder', 'lighting_dotnet'))
    except (OSError, ValueError):
        found = False
    app.look_tools_status.set('Lighting tools detected; used automatically for indirect light and reflections.' if found else
                             'Lighting tools not found. Packaged RenderLock includes them; advanced overrides are for custom installations.')
    descriptions = {
        'standard': 'Converted game materials. Source lighting is off; saved shader and tool locations are kept.',
        'source': 'Prepares character diffuse and probe reflections together, including installed-map lighting data. No separate Map checkbox is needed.',
        'elagrimm_source': 'Prepares Source lighting, probes and NPR inputs together. Select the supported shader below and a follow hero on Export. No separate Map checkbox is needed.',
        'custom': 'Manual lighting options are shown below for existing or advanced settings. Their individual requirements still apply.',
    }
    text = descriptions.get(mode, 'Choose a supported character look.')
    if source and app.values['asset_source'].get() != 'installed':
        text = 'This look needs Installed game on Setup. Standard materials can use prepared assets without new Source lighting.'
    app.look_description.set(text)
    if hasattr(app, 'map_prepare_check'):
        app.map_prepare_check.grid_remove() if source else app.map_prepare_check.grid()
        app.map_look_note.set('Lighting data is prepared automatically by the selected character look in Lighting lab.' if source else
                              'Prepare a region manually only for custom lighting or supported map work.')


def toggle_look_tool_overrides(app):
    if getattr(app, 'busy', False):
        return
    app.look_tools_expanded = not getattr(app, 'look_tools_expanded', False)
    refresh_character_look(app)


# --------------------------------------------------------------------------
# Pages.


class _Page:
    """Scrollable page: cards stacked on a canvas, so the background shows in the gaps.

    The scrollbar appears only when the cards do not fit; the background stays fixed to the
    window while the cards scroll over it. While the user drags the window's size (``held``),
    cards keep their width: reflowing every field on every mouse move is what makes Tk resize
    sluggishly, so they reflow once when the drag ends.
    """

    held = False
    pages: list = []
    on_view = None  # on_view(page) after a page scrolls or is laid out; on_view(None) while held (edge fades)

    def __init__(self, stage, backdrop):
        self.backdrop = backdrop
        self.shell = tk.Frame(stage, background=gui_theme.BACKDROP)
        self.shell.columnconfigure(0, weight=1)
        self.shell.rowconfigure(0, weight=1)
        self.canvas = canvas = tk.Canvas(self.shell, background=gui_theme.BACKDROP, highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self.shell, orient="vertical", command=canvas.yview)
        canvas.grid(row=0, column=0, sticky="nsew")
        canvas.configure(yscrollcommand=self._scrolled)
        backdrop.attach(canvas, areas=self._open_areas)
        self.cards = []
        self.gaps = []     # (top, bottom) canvas rows between and below the cards
        self._job = None
        self.bar_shown = False
        self.on_bar = None  # called when the scrollbar appears or goes (the log drawer follows the cards)
        _Page.pages.append(self)
        canvas.bind("<Configure>", lambda _e: self._schedule(), add="+")
        self._root = stage.winfo_toplevel()
        self._wheel_binding = self._root.bind("<MouseWheel>", self._wheel, add="+")
        self.shell.bind("<Destroy>", self._destroyed, add="+")

    def _destroyed(self, event):
        if event.widget is not self.shell:
            return
        if self._job is not None:
            self.canvas.after_cancel(self._job)
            self._job = None
        if self in _Page.pages:
            _Page.pages.remove(self)
        self._root.unbind("<MouseWheel>", self._wheel_binding)

    def add(self, card, gap):
        item = self.canvas.create_window(0, 0, window=card, anchor="nw")
        self.cards.append((card, item, px(gap)))
        card.bind("<Configure>", lambda _e: self._schedule(), add="+")

    def _schedule(self):
        if self._job is None and not _Page.held:
            self._job = self.canvas.after_idle(self.layout)

    @classmethod
    def hold(cls, held):
        """Hold card layout while the window is being dragged; lay every page out once afterwards."""
        cls.held = held
        if held and cls.on_view:
            cls.on_view(None)
        if not held:
            for page in cls.pages:
                page._schedule()

    def layout(self):
        self._job = None
        if _Page.held:
            return
        canvas = self.canvas
        width = canvas.winfo_width()
        y = 0
        self.gaps = []
        for card, item, gap in self.cards:
            if gap:
                self.gaps.append((y, y + gap))
            y += gap
            canvas.coords(item, 0, y)
            canvas.itemconfigure(item, width=width)
            y += card.winfo_reqheight()
        self.gaps.append((y, y + 100000))
        canvas.configure(scrollregion=(0, 0, width, y))
        shown = y > canvas.winfo_height() + 1
        if shown:
            self.bar.grid(row=0, column=1, sticky="ns", padx=_pad((8, 0)))
        else:
            self.bar.grid_remove()
            canvas.yview_moveto(0)
        self.backdrop.sync(canvas, redraw=True)
        if shown != self.bar_shown:
            self.bar_shown = shown
            if self.on_bar:
                self.on_bar()
        if _Page.on_view:
            _Page.on_view(self)

    def bar_room(self):
        """Width the scrollbar takes from the cards (0 while the page fits)."""
        return self.bar.winfo_reqwidth() + px(8) if self.bar_shown else 0

    def _open_areas(self):
        """Where the background shows on this page: the gaps between cards and below the last one."""
        canvas = self.canvas
        top, width, height = canvas.canvasy(0), canvas.winfo_width(), canvas.winfo_height()
        return [(0, max(0, y0 - top), width, min(height, y1 - top)) for y0, y1 in self.gaps
                if y1 - top > 0 and y0 - top < height]

    def watch_focus(self):
        """Let Tab reach every field on this page and scroll it into view first.

        Tk skips widgets it cannot see when tabbing, and canvas cards scrolled out of view are
        not visible to it at all. Each focusable control instead asks ``_reveal`` (a takefocus
        script), which scrolls the field into view and says yes.
        """
        script = self.canvas.register(self._reveal)
        pending = list(self.canvas.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, tk.Entry):
                widget.configure(takefocus=script)
            elif hasattr(widget, "_set_option") and str(widget.cget("takefocus")) in ("1", "True"):
                widget.focus_script = script
                tk.Canvas.configure(widget, takefocus=script)

    def _reveal(self, path):
        widget = self.canvas.nametowidget(path)
        if not self.shell.winfo_ismapped():
            return 0  # a page that is not shown is not part of the Tab order
        if isinstance(widget, tk.Entry) and str(widget.cget("state")) == "disabled":
            return 0
        canvas = self.canvas
        card = widget
        while card is not None and card.master is not canvas:
            card = card.master
        item = next((item for c, item, _gap in self.cards if c is card), None)
        if item is None:
            return 1
        top = canvas.coords(item)[1] + widget.winfo_rooty() - card.winfo_rooty()
        bottom = top + widget.winfo_height()
        view_top, view_height = canvas.canvasy(0), canvas.winfo_height()
        region = str(canvas.cget("scrollregion")).split()
        total = max(1.0, float(region[-1])) if region else 1.0  # not laid out yet: nothing to scroll
        margin = px(12)
        if top < view_top:
            canvas.yview_moveto(max(0.0, top - margin) / total)
        elif bottom > view_top + view_height:
            canvas.yview_moveto(max(0.0, bottom + margin - view_height) / total)
        canvas.update_idletasks()  # map the card before Tk gives the field focus
        return 1

    def _scrolled(self, first, last):
        self.bar.set(first, last)
        self.backdrop.sync(self.canvas)  # keep the background fixed while the page scrolls
        if _Page.on_view:
            _Page.on_view(self)          # the faded edges follow what is cut

    def _wheel(self, event):
        # Scroll only the page under the pointer; logs keep their native wheel.
        widget = event.widget
        while widget is not None:
            if widget == self.shell and self.canvas.yview() != (0.0, 1.0):
                self.canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
                return
            widget = getattr(widget, "master", None)


def show_page(app, name):
    app.page = name
    app.stage.itemconfigure(app.page_item, window=app.page_frames[name])
    _nav_select(app, name)
    app.page_title.configure(text=name)
    app.page_subtitle.configure(text=PAGE_DETAILS.get(name, ""))
    if getattr(app, "log_visible", False):
        app.layout_stage()  # the open log matches this page's card width (with or without scrollbar)
    if _Page.on_view:
        _Page.on_view(app.page_layouts[name])


# --------------------------------------------------------------------------
# Sidebar: one canvas with the gradient, the outline, the tree art, the brand and the pages.


def _nav_paint(app, name):
    rail, parts, t = app.rail, app.nav_items[name], gui_theme
    selected, hover = app.page == name, app.nav_hover == name
    rail.itemconfigure(parts["bar"], fill=t.ACCENT if selected else t.NAV_BAR_IDLE)
    if parts["icon"] is not None:
        rail.itemconfigure(parts["icon"], fill=t.ACCENT if selected else (t.TEXT if hover else t.MUTED))
    rail.itemconfigure(parts["text"], fill=t.TEXT if (selected or hover) else t.MUTED)


def _nav_select(app, name):
    rail = app.rail
    top = app.nav_items[name]["top"]
    rail.coords(app.nav_selected, 0, top)
    rail.itemconfigure(app.nav_selected, state="normal")
    _nav_hover(app, app.nav_hover)
    for key in app.nav_items:
        _nav_paint(app, key)


def _nav_hover(app, name):
    app.nav_hover = name
    rail = app.rail
    if name is None or name == app.page:
        rail.itemconfigure(app.nav_hovered, state="hidden")
    else:
        rail.coords(app.nav_hovered, 0, app.nav_items[name]["top"])
        rail.itemconfigure(app.nav_hovered, state="normal")
    for key in app.nav_items:
        _nav_paint(app, key)


def _nav_at(app, y):
    for name, parts in app.nav_items.items():
        if parts["top"] <= y < parts["top"] + px(NAV_HEIGHT):
            return name
    return None


def _fade_image(master, width, height, start, end, strength):
    """Horizontal fade from ``start`` (opaque) to ``end`` (transparent), as RGBA."""
    a = [int(start[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
    row = bytearray()
    for x in range(width):
        k = (x + 0.5) / width
        row += bytes([int(a[i] + (b[i] - a[i]) * k + 0.5) for i in range(3)] + [int(255 * (1 - k) * strength + 0.5)])
    return rgba_image(master, width, height, [row] * height)


def _paint_sidebar(app, rail):
    """Rebuild the sidebar's gradient fill and right-edge outline for the current height."""
    width, height = px(RAIL_WIDTH), max(1, rail.winfo_height())
    if getattr(app, "rail_height", None) == height:
        return
    app.rail_height = height
    stops = tuple((pos, color, alpha) for pos, (color, alpha) in zip((0.0, 0.68, 1.0), gui_theme.SIDEBAR))
    edge = hairline()
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        # Fast path (this runs on every step of a window resize).
        rgb = lambda color: [int(color[i:i + 2], 16) for i in (1, 3, 5)]  # noqa: E731
        t = 0.98 * (np.arange(height) + 0.5) / height + 0.02
        positions = [stop[0] for stop in stops]
        alpha = np.interp(t, positions, [stop[2] for stop in stops])[:, None]
        fill = np.stack([np.interp(t, positions, [rgb(stop[1])[i] for stop in stops]) for i in range(3)], 1)
        base = np.array(gui_theme.screen_rgb(BASE_COLOR), float)
        column = base + (fill - base) * alpha
        t = np.clip(1.1 * (np.arange(height) + 0.5) / height + 0.21, 0, 1)[:, None]
        top, bottom = np.array(rgb(gui_theme.RAIL_EDGE_TOP), float), np.array(rgb(gui_theme.RAIL_EDGE_BOTTOM), float)
        pixels = np.repeat((column + 0.5).astype(np.uint8)[:, None, :], width, axis=1)
        pixels[:, width - edge:] = (top + (bottom - top) * t + 0.5).astype(np.uint8)[:, None, :]
        image = tk.PhotoImage(master=rail, format="ppm", data=b"P6 %d %d 255 " % (width, height) + pixels.tobytes())
    else:
        column = []
        for y in range(height):
            color, alpha = _blend(stops, 0.98 * (y + 0.5) / height + 0.02)
            column.append("{" + _over(color, alpha) + "}")
        image = tk.PhotoImage(master=rail, width=width, height=height)
        image.put(" ".join(column), to=(0, 0, width, height))
        outline = ["{" + gradient_color(((0.0, gui_theme.RAIL_EDGE_TOP), (1.0, gui_theme.RAIL_EDGE_BOTTOM)),
                                        1.1 * (y + 0.5) / height + 0.21) + "}" for y in range(height)]
        image.put(" ".join(outline), to=(width - edge, 0, width, height))
    app.rail_image = image
    rail.itemconfigure(app.rail_fill, image=image)
    if app.rail_art is not None:
        if getattr(app, "rail_art_job", None) is not None:
            rail.after_cancel(app.rail_art_job)
            app.rail_art_job = None
        if app.chrome.sizing:
            # Mid-drag: keep the current tree (just moved along) and resize it once the drag ends.
            _place_rail_art(app)
            app.rail_art_job = rail.after(300, lambda: _scale_rail_art(app))
        else:
            _scale_rail_art(app)  # a jump in size (maximize, restore): resize in the same step
            _place_rail_art(app)


def _place_rail_art(app):
    """The tree hugs the bottom of the sidebar as in the design, and never climbs into the pages."""
    x, from_bottom = app.rail_art_place
    top = max(px(NAV_TOP + 3 * NAV_HEIGHT + 20), app.rail.winfo_height() - from_bottom)
    app.rail.coords(app.rail_art, x, top)


def _scale_rail_art(app):
    """Size the tree in proportion to the sidebar's height (bigger in a maximized window).

    The design size uses the art exported for this display scale; other heights resample the
    largest export, so the tree stays sharp when it grows."""
    app.rail_art_job = None
    rail = app.rail
    if app.chrome.sizing:  # still being dragged: try again once it settles
        app.rail_art_job = rail.after(300, lambda: _scale_rail_art(app))
        return
    grow = max(0.8, min(2.5, rail.winfo_height() / (WINDOW_H * gui_theme.SCALE)))
    if abs(grow - getattr(app, "rail_art_grow", 1.0)) < 0.02:
        return
    art = _rail_art_for(app, grow)
    if art is None:
        return
    app.rail_art_grow = grow
    app.rail_art_image, app.rail_art_place = art
    rail.itemconfigure(app.rail_art, image=art[0])
    _place_rail_art(app)


def _rail_art_for(app, grow):
    """(image, place) of the tree drawn ``grow`` times its design size; cached, so sizes seen
    before (maximize <-> restore) come back instantly."""
    if abs(grow - 1.0) < 0.03:
        return app.rail_art_native
    cache = app.__dict__.setdefault("rail_art_sizes", {})
    key = round(grow, 2)
    if key in cache:
        return cache[key]
    try:
        import numpy as np
    except ImportError:
        return None
    from .gui_backdrop import resample
    source_scale = max(gui_assets.RAIL_ART)
    x0, y0, width, height, data = gui_assets.RAIL_ART[source_scale]
    factor = gui_theme.SCALE * key / source_scale
    pixels = np.frombuffer(zlib.decompress(base64.b64decode(data)), np.uint8).reshape(height, width, 4)
    out_w, out_h = max(1, int(width * factor + 0.5)), max(1, int(height * factor + 0.5))
    scaled = resample(pixels, factor, factor, np.arange(out_w) + 0.5, np.arange(out_h) + 0.5).copy()
    scaled[:, :, :3] = gui_theme.screen_pixels(scaled[:, :, :3])
    image = rgba_image(app.rail, out_w, out_h, [scaled[y].tobytes() for y in range(out_h)])
    if len(cache) > 6:
        cache.clear()
    cache[key] = (image, (int(x0 * factor), int((WINDOW_H * source_scale - y0) * factor)))
    return cache[key]


def _prepare_maximized_art(app):
    """Build the tree for the maximized window ahead of time, so the first maximize has it ready."""
    chrome = app.chrome
    if app.rail_art is None or not chrome.active:
        return
    area = chrome.api.maximized_area(chrome.hwnd)
    if area:
        rail_height = area[3] - (app.root.winfo_height() - app.rail.winfo_height())
        _rail_art_for(app, max(0.8, min(2.5, rail_height / (WINDOW_H * gui_theme.SCALE))))


def _build_rail(app, parent, beta):
    t = gui_theme
    rail = tk.Canvas(parent, width=px(RAIL_WIDTH), background=t.SIDEBAR[0][0], highlightthickness=0, bd=0)
    rail.grid(row=0, column=0, sticky="ns")
    app.rail = rail
    app.rail_fill = rail.create_image(0, 0, anchor="nw")
    app.rail_art = None
    if t.SKIN_NAME == "bone":
        scale = min(gui_assets.RAIL_ART, key=lambda s: abs(s - t.SCALE))
        x0, y0, art_w, art_h, data = gui_assets.RAIL_ART[scale]
        app.rail_art_image = _asset_image(rail, art_w, art_h, data)
        # Position as (x, distance of the top from the sidebar's bottom at the design height).
        app.rail_art_place = (x0, int(WINDOW_H * t.SCALE + 0.5) - y0)
        app.rail_art_native = (app.rail_art_image, app.rail_art_place)
        app.rail_art = rail.create_image(x0, y0, anchor="nw", image=app.rail_art_image)
    rail.bind("<Configure>", lambda _e: _paint_sidebar(app, rail), add="+")

    # Brand: emblem and wordmark on one line, then the app name and version.
    if t.SKIN_NAME == "bone":
        scale = min(gui_assets.EMBLEM, key=lambda s: abs(s - t.SCALE))
        app.emblem_image = _asset_image(rail, *gui_assets.EMBLEM[scale])
        rail.create_image(px(17), px(35), anchor="nw", image=app.emblem_image)
        wordmark_x = 38
    else:
        wordmark_x = 17
    rail.create_text(px(wordmark_x), px(43), anchor="w", text=WORDMARK, font=t.FONT_WORDMARK, fill=t.BRAND)
    rail.create_text(px(17), px(67), anchor="w", text="Deadlock to Blender tool", font=t.FONT_SMALL, fill=t.TEXT)
    rail.create_text(px(17), px(83), anchor="w", font=t.FONT_TINY, fill=t.DIM,
                     text=f"{__version__}  ·  " + ("Character beta" if beta else "Development"))

    # Pages.
    nav_w, nav_h = px(RAIL_WIDTH - 1), px(NAV_HEIGHT)
    app.nav_selected_image = _fade_image(rail, nav_w, nav_h, t.NAV_SEL_FROM, t.NAV_SEL_TO, 1.0)
    app.nav_hover_image = _fade_image(rail, nav_w, nav_h, t.NAV_SEL_FROM, t.NAV_SEL_TO, 0.5)
    app.nav_selected = rail.create_image(0, 0, anchor="nw", image=app.nav_selected_image, state="hidden")
    app.nav_hovered = rail.create_image(0, 0, anchor="nw", image=app.nav_hover_image, state="hidden")
    icon_family = t.icon_font(app.root)
    items = [(PAGE_EXPORT, "", "Export"),
             (PAGE_SETUP, "", "Setup"),
             (PAGE_ADVANCED, "", "Advanced")]
    items.extend(((PAGE_MAP, "", "Map"), (PAGE_LIGHTING, "", "Lighting lab"),
                  (PAGE_UPDATES, "", "Updates")))
    app.nav_items = {}
    app.nav_hover = None
    for index, (name, glyph, label) in enumerate(items):
        top = px(NAV_TOP) + index * nav_h
        middle = top + nav_h // 2
        bar = rail.create_rectangle(0, top, px(3), top + nav_h, fill=t.NAV_BAR_IDLE, width=0)
        icon = None
        if icon_family:
            icon = rail.create_text(px(24), middle, text=glyph, font=(icon_family, 11), fill=t.MUTED)
        text = rail.create_text(px(42), middle, anchor="w", text=label, font=t.FONT_SMALL, fill=t.MUTED)
        app.nav_items[name] = {"top": top, "bar": bar, "icon": icon, "text": text}

    def motion(event):
        name = _nav_at(app, event.y)
        rail.configure(cursor="hand2" if name else "")
        if name != app.nav_hover:
            _nav_hover(app, name)

    def click(event):
        name = _nav_at(app, event.y)
        if name:
            show_page(app, name)

    rail.bind("<Motion>", motion)
    rail.bind("<Leave>", lambda _e: _nav_hover(app, None))
    rail.bind("<Button-1>", click)
    return rail


# --------------------------------------------------------------------------
# Export page content.


def _build_export(app, page):
    body, row = _card(app, page, 0, "Replay")
    _path_row(app, body, "File", "input", app._browse_input)
    modes = tk.Frame(body, background=gui_theme.CARD)
    modes.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew")
    app.mode_switch = SegmentedControl(
        modes, options=(("Replay .dem", "replay"), ("Saved package .json", "segment")),
        variable=app.values["mode"], command=app._mode_changed, theme=gui_theme.palette())
    app.mode_switch.pack(side="left")
    app.mutable_widgets.append(app.mode_switch)
    app.inspect_button = RoundedButton(modes, text="Inspect replay", command=app._inspect,
                                       theme=gui_theme.palette(), height=34, pad_x=15)
    app.inspect_button.pack(side="right")
    app.mutable_widgets.append(app.inspect_button)
    app.input_summary = tk.StringVar(value="Inspect to read the replay information.")
    wrap_to_width(tk.Label(body, textvariable=app.input_summary, background=gui_theme.CARD,
                           foreground=gui_theme.MUTED, font=gui_theme.FONT_SMALL, justify="left", anchor="w",
                           wraplength=px(TEXT_WRAP))).grid(
        row=_next_row(body), column=0, columnspan=3, sticky="ew", pady=_pad((SECTION_GAP, 0)))

    body, row = _card(app, page, row, "Characters", info=CHARACTERS_INFO)
    app.character_picker = CharacterChecklist(body, heroes=app.values["heroes"], empty=app.values["heroes_empty"])
    app.character_picker.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew")
    # Follow camera sits right under the heroes it picks from, so it is in view without scrolling;
    # TRAY_GAP from the trays to the heading's letters, as above the trays.
    camera = tk.Frame(body, background=gui_theme.CARD)
    camera.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew", pady=_pad((TRAY_GAP - TEXT_TOP, 0)))
    heading = tk.Frame(camera, background=gui_theme.CARD)
    heading.pack(anchor="w", pady=_pad((0, 5)))
    _label(heading, "Follow camera", font=gui_theme.FONT, color=gui_theme.TEXT).pack(side="left")
    InfoIcon(heading, text=FOLLOW_INFO, theme=gui_theme.palette(), size=12).pack(side="left", padx=(px(2), 0))
    app.follow_picker = FollowHeroPicker(camera, hero=app.values["shot_hero"])
    app.follow_picker.pack(fill="x")

    body, row = _card(app, page, row, "Clip")
    fields = tk.Frame(body, background=gui_theme.CARD)
    fields.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew")
    for col, (key, label, weight) in enumerate((("start", "First tick", 3), ("end", "Last tick", 3),
                                                ("fps", "FPS", 3))):
        fields.columnconfigure(col, weight=weight, uniform="clip")
        group = tk.Frame(fields, background=gui_theme.CARD)
        group.grid(row=0, column=col, sticky="ew", padx=_pad((0, 13 if col < 2 else 0)))
        _label(group, label).pack(anchor="w")
        entry = _entry(group, app.values[key])
        entry.pack(fill="x", pady=_pad((5, 0)))
        app.mutable_widgets.append(entry)

    body, row = _card(app, page, row, "Output")
    _path_row(app, body, "Save as", "output", app._browse_output)
    _check(app, body, "Include supported particle effects", app.values["effects_enabled"], pady=(0, 0),
           info="Only some game effects are supported. Character and world effects may be missing; see the coverage report. Downloaded maps are selected separately on Map.")
    return row


# --------------------------------------------------------------------------
# Setup page content.


def _build_setup(app, page):
    body, row = _card(app, page, 0, "Local tools", label_column=WIDE_LABEL_COLUMN,
                      info="Choose these once. Conversion reads installed files without launching Deadlock.")
    _path_row(app, body, "Blender executable", "blender", app._browse_blender)

    source_row = _next_row(body)
    _label(body, "Asset source").grid(row=source_row, column=0, sticky="w", pady=_pad((0, ROW_GAP)))
    app.asset_source_label = tk.StringVar(
        value="Installed game" if app.values["asset_source"].get() == "installed" else "Prepared assets")
    # A switch instead of a dropdown: only two choices, both always visible.
    app.asset_source_combo = SegmentedControl(
        body, options=(("Installed game", "Installed game"), ("Prepared assets", "Prepared assets")),
        variable=app.asset_source_label, command=app._asset_source_changed, theme=gui_theme.palette())
    app.asset_source_combo.grid(row=source_row, column=1, columnspan=2, sticky="w", pady=_pad((0, ROW_GAP)))
    app.mutable_widgets.append(app.asset_source_combo)

    fields_row = _next_row(body)
    app.installed_fields = tk.Frame(body, background=gui_theme.CARD)
    app.installed_fields.grid(row=fields_row, column=0, columnspan=3, sticky="ew")
    app.installed_fields.columnconfigure(0, minsize=px(WIDE_LABEL_COLUMN))
    app.installed_fields.columnconfigure(1, weight=1)
    for label, key, command in (("Deadlock folder", "game_install", app._browse_game),
                                ("Source 2 Viewer CLI", "source2viewer", app._browse_source2viewer),
                                ("Generated asset cache", "asset_cache", app._browse_cache)):
        _path_row(app, app.installed_fields, label, key, command)
    _note(app.installed_fields, "Source 2 Viewer 20.1 is included with this beta; browse only to override it.",
          pady=(8, 0))
    app.prepared_fields = tk.Frame(body, background=gui_theme.CARD)
    app.prepared_fields.grid(row=fields_row, column=0, columnspan=3, sticky="ew")
    app.prepared_fields.columnconfigure(0, minsize=px(WIDE_LABEL_COLUMN))
    app.prepared_fields.columnconfigure(1, weight=1)
    _path_row(app, app.prepared_fields, "Prepared asset folder", "assets", app._browse_assets)
    app._asset_source_changed()
    _note(body, "Blender and Source 2 Viewer are separate tools. "
                "Keep generated files outside the game installation.", pady=(ROW_GAP, 0))
    return row


# --------------------------------------------------------------------------
# Advanced page content.


def _build_advanced(app, page):
    body, row = _card(app, page, 0, "Character selection")
    _path_row(app, body, "Hero IDs", "heroes", None, button=False)
    _note(body, "Use the Export checklist after inspecting a recording. Manual IDs are available here if its character list is unavailable; empty selects all.", pady=(SECTION_GAP - ROW_GAP, 0))

    body, row = _card(app, page, row, "Character options",
                      info="Experimental features depend on the selected hero and recorded data. "
                           "Check coverage after export.")
    for text, key, gap, info in (
            ("Cloth / secondary motion (experimental)", "cloth_enabled", CHECK_GAP, ""),
            ("Model bone rules (experimental)", "model_constraints", CHECK_GAP,
             "Applies supported model rules for how bones aim at, follow or twist with other bones. "
             "This can refine the recorded pose. It does not simulate cloth or physics."),
            ("Emission and glass repairs (experimental)", "material_repairs", 0,
             "Applies supported static emission and unlit corrections, plus an artistic glass approximation. "
             "This does not recover every glow, animated mask or game shader behavior.")):
        _check(app, body, text, app.values[key], pady=(0, gap), installed_only=key != "model_constraints", info=info)

    body, row = _card(app, page, row, "Particle simulation", label_column=WIDE_LABEL_COLUMN,
                      info="Optional event simulation. Unsupported events are reported and omitted.")
    _check(app, body, "Simulate recorded particle events", app.values["simulate_effects"], pady=(0, CHECK_GAP), installed_only=True)
    _path_row(app, body, "Particle simulator", "particle_probe", app._browse_particle_probe)
    _path_row(app, body, ".NET (DLL only)", "particle_dotnet", app._browse_particle_dotnet)
    _path_row(app, body, "Earlier history (ticks)", "particle_history_ticks", None, button=False)
    _note(body, "0 keeps the selected range. Effects follow the export camera; "
                "use AGR_Retarget_Effects.py after changing cameras.", pady=(SECTION_GAP - ROW_GAP, 0))
    _note(body, "New cloth, material repairs and particle simulation use Installed game on Setup. "
                "Prepared assets reuse any cloth definitions and effects already present in the input. "
                "Effects inclusion and model bone rules remain available with either asset source.",
          pady=(SECTION_GAP, 0))

    body, row = _card(app, page, row, "Output policy")
    _check(app, body, "Keep supported cloth if some cloth is unavailable", app.partial_cloth, True, False,
           pady=(0, CHECK_GAP), info="Explicitly accept omitted or failed cloth with a coverage warning. Character models and animation stay strict.")
    _check(app, body, "Allow partial export / movement placeholders", app.partial, True, False, pady=(0, CHECK_GAP))
    _check(app, body, "Overwrite existing scene and companion package", app.overwrite, True, False, pady=(0, 0))
    return row


# --------------------------------------------------------------------------
# Experimental lighting page.


def _development_lighting(app, page):
    body, row = _card(app, page, 0, "Lighting lab", label_column=WIDE_LABEL_COLUMN,
                      info="Experimental character look. Partial and artistic by design; the coverage report lists what was and was not applied.")
    _label(body, 'Character look', color=gui_theme.TEXT).grid(
        row=_next_row(body), column=0, columnspan=3, sticky='w', pady=_pad((0, ROW_GAP)))
    app.look_switch = SegmentedControl(body,
        options=(("Standard materials", "standard"), ("Source lighting", "source"),
                 ("Elagrimm + Source", "elagrimm_source"), ("Custom", "custom")),
        variable=app.values['character_look'], theme=gui_theme.palette())
    app.look_switch.grid(row=_next_row(body), column=0, columnspan=3, sticky='w', pady=_pad((0, ROW_GAP)))
    app.mutable_widgets.append(app.look_switch)
    app.look_description = tk.StringVar()
    wrap_to_width(_label(body, '', textvariable=app.look_description, justify='left', anchor='w', wraplength=px(TEXT_WRAP))).grid(
        row=_next_row(body), column=0, columnspan=3, sticky='ew', pady=_pad((0, SECTION_GAP)))
    app.look_custom_fields = custom = tk.Frame(body, background=gui_theme.CARD)
    custom.grid(row=_next_row(body), column=0, columnspan=3, sticky='ew')
    for text, key in ((("Partial Source character diffuse lighting" if character_beta() else "Partial static diffuse lighting"), "source_lighting"),
                      ("Partial hero probe lighting / reflections", "source_hero_lighting"),
                      ("Partial NPR indirect (requires follow camera)", "source_npr_lighting")):
        _check(app, custom, text, app.values[key], pady=(0, CHECK_GAP), installed_only=True)
    app.look_shader_fields = shader = tk.Frame(body, background=gui_theme.CARD)
    shader.grid(row=_next_row(body), column=0, columnspan=3, sticky='ew', pady=_pad((0, SECTION_GAP)))
    shader.columnconfigure(0, minsize=px(WIDE_LABEL_COLUMN))
    shader.columnconfigure(1, weight=1)
    _path_row(app, shader, 'Elagrimm shader .blend', 'character_shader', app._browse_character_shader)
    _note(shader, 'Select the separately downloaded Elagrimm library. Its materials are embedded in the exported .blend.', pady=(0, ROW_GAP))
    shader_download = RoundedButton(shader, text='Get supported Elagrimm shader', style='quiet',
        command=app._open_character_shader_download, theme=gui_theme.palette())
    shader_download.grid(row=_next_row(shader), column=0, columnspan=3, sticky='w')
    app.mutable_widgets.append(shader_download)
    app.look_consent_fields = consent = tk.Frame(body, background=gui_theme.CARD)
    consent.grid(row=_next_row(body), column=0, columnspan=3, sticky='ew')
    _note(consent, 'Source lighting is a partial, artistic approximation. Exposure, material response and reflections may differ from the game.', pady=(0, ROW_GAP))
    _check(app, consent, 'Accept partial lighting limits for this export', app.lighting_consent, True, False,
           pady=(0, SECTION_GAP))
    app.look_tools_fields = tools = tk.Frame(body, background=gui_theme.CARD)
    tools.grid(row=_next_row(body), column=0, columnspan=3, sticky='ew')
    tools.columnconfigure(0, weight=1)
    app.look_tools_status = tk.StringVar()
    wrap_to_width(_label(tools, '', textvariable=app.look_tools_status, justify='left', anchor='w', wraplength=px(TEXT_WRAP))).grid(
        row=_next_row(tools), column=0, columnspan=3, sticky='ew', pady=_pad((0, ROW_GAP)))
    app.look_tools_expanded = False
    app.look_tools_toggle = RoundedButton(tools, text='Advanced tool overrides', style='quiet',
        command=lambda: toggle_look_tool_overrides(app), theme=gui_theme.palette())
    app.look_tools_toggle.grid(row=_next_row(tools), column=0, columnspan=3, sticky='w')
    app.mutable_widgets.append(app.look_tools_toggle)
    app.look_tool_overrides = overrides = tk.Frame(tools, background=gui_theme.CARD)
    overrides.grid(row=_next_row(tools), column=0, columnspan=3, sticky='ew', pady=_pad((ROW_GAP, 0)))
    overrides.columnconfigure(0, minsize=px(WIDE_LABEL_COLUMN))
    overrides.columnconfigure(1, weight=1)
    for label, key, command in (
        ("Probe atlas decoder", "probe_atlas_decoder",
         lambda: app._browse_hero_decoder("probe_atlas_decoder", "ProbeAtlasDecode.dll")),
        ("Cubemap decoder", "cubemap_decoder",
         lambda: app._browse_hero_decoder("cubemap_decoder", "CubemapDecode.dll")),
        ('.NET executable', 'lighting_dotnet', app._browse_lighting_dotnet)):
        _path_row(app, overrides, label, key, command)
    _note(overrides, 'The probe atlas decoder reads baked indirect lighting; the cubemap decoder reads environment reflections. These are bundled programs, not shader or texture files you need to supply.', pady=(0, 0))
    app.values['character_look'].trace_add('write', lambda *_: refresh_character_look(app, changed=True))
    for key in ('character_shader', 'shot_hero', 'map_name', 'map_margin', 'input', 'mode',
                'start', 'end', 'fps', 'heroes', 'asset_source', 'game_install',
                'source_lighting', 'source_hero_lighting', 'source_npr_lighting',
                'probe_atlas_decoder', 'cubemap_decoder', 'lighting_dotnet'):
        app.values[key].trace_add('write', lambda *_: app.lighting_consent.set(False))
    for key in ('probe_atlas_decoder', 'cubemap_decoder', 'lighting_dotnet'):
        app.values[key].trace_add('write', lambda *_: refresh_character_look(app))
    refresh_character_look(app)
    return row


# --------------------------------------------------------------------------
# Content stage: page title, the page, the log drawer and the footer, drawn over the background.


def _build_stage(app, parent, backdrop):
    t = gui_theme
    stage = tk.Canvas(parent, background=t.BACKDROP, highlightthickness=0, bd=0, width=1, height=1)
    stage.grid(row=0, column=1, sticky="nsew")
    app.stage = stage
    app.page_rect = (0, 0, 0, 0)

    def open_areas():
        # Everything around the page (header, margins, footer); the page draws its own background.
        width, height = stage.winfo_width(), stage.winfo_height()
        x0, y0, x1, y1 = app.page_rect
        return [(0, 0, width, y0), (0, y0, x0, y1), (x1, y0, width, y1), (0, y1, width, height)]

    backdrop.attach(stage, areas=open_areas)

    app.page_title = CanvasText(stage, text=PAGE_EXPORT, font=t.FONT_TITLE, fill=t.TEXT)
    app.page_subtitle = CanvasText(stage, text=PAGE_DETAILS[PAGE_EXPORT], font=t.FONT_SMALL, fill=t.MUTED)

    app.app_icon = gui_theme.brand_window(app.root)
    app.page_frames = {}
    app.page_layouts = {}
    for name, builder in ((PAGE_EXPORT, _build_export), (PAGE_SETUP, _build_setup),
                          (PAGE_ADVANCED, _build_advanced), (PAGE_MAP, _build_map),
                          (PAGE_LIGHTING, _development_lighting), (PAGE_UPDATES, _build_updates)):
        page = _Page(stage, backdrop)
        app.page_layouts[name] = page
        app.page_frames[name] = page.shell
        page.on_bar = lambda: getattr(app, "log_visible", False) and app.layout_stage()
        builder(app, page)
        page.watch_focus()
    app.page_item = stage.create_window(0, 0, anchor="nw", window=app.page_frames[PAGE_EXPORT])

    # Log drawer, hidden by default: a field-colored card above the footer.
    app.log_frame = Card(stage, theme=t.palette(), fill=t.FIELD, edge=None, radius=6, pad_x=12, pad_y=10,
                         background=t.BACKDROP)
    app.log_item = stage.create_window(0, 0, anchor="nw", window=app.log_frame, state="hidden")
    log_body = app.log_frame.body
    log_body.columnconfigure(0, weight=1)
    app.log = tk.Text(log_body, wrap="word", state="disabled", height=6, font=t.FONT_LOG,
                      background=t.FIELD, foreground=t.MUTED, insertbackground=t.TEXT,
                      relief="flat", bd=0, padx=0, pady=0, highlightthickness=0)
    app.log.grid(row=0, column=0, sticky="ew")
    log_bar = ttk.Scrollbar(log_body, orient="vertical", command=app.log.yview)

    def log_scrolled(first, last):
        # Show the log scrollbar only once the text overflows the drawer.
        if float(first) <= 0.0 and float(last) >= 1.0:
            log_bar.grid_remove()
        else:
            log_bar.grid(row=0, column=1, sticky="ns", padx=_pad((8, 0)))
        log_bar.set(first, last)

    app.log.configure(yscrollcommand=log_scrolled)
    # Copy button at the top right (past the scrollbar, so it never moves): copies the whole log.
    app.log_copy = CopyButton(log_body, palette=t.palette(), command=lambda: _copy_log(app))
    app.log_copy.grid(row=0, column=2, sticky="n", padx=_pad((8, 0)))
    app.log_visible = False

    # Footer: text links on the left, the job buttons as tilted paper stickers on the right
    # (they snap between poses like Deadlock's hero tags), the status line below the links.
    focus = CanvasFocus(stage)
    app.output_link = focus.add(CanvasLink(stage, text="Open output folder", command=app._open_output,
                                           theme=t.palette(), font=t.FONT))
    app.log_button = focus.add(CanvasLink(stage, text="Show log", theme=t.palette(), font=t.FONT,
                                          command=lambda: set_log_visible(app, not app.log_visible)))
    app.cancel_button = focus.add(CanvasSticker(stage, text="Cancel", style="secondary", tilt=-1.41, seed=3,
                                                paper_offset=(0.5, -1.34), font=t.FONT_STICKER,
                                                command=app._cancel, theme=t.palette()))
    app.cancel_button.configure(state="disabled")
    app.convert_button = focus.add(CanvasSticker(stage, text="Export", style="primary", tilt=2.28, seed=7,
                                                 paper_offset=(-1.27, 2.0), font=t.FONT_STICKER,
                                                 command=app._convert, theme=t.palette()))
    app.mutable_widgets.append(app.convert_button)
    app.status_dot = CanvasDot(stage, color=t.DOT_READY, size=5)
    status = CanvasText(stage, text=app.status.get(), font=t.FONT, fill=t.MUTED)
    linespace = int(stage.tk.call("font", "metrics", t.FONT, "-linespace"))
    status_at = [None]  # (text x, dot x, line y) from the last layout

    def place_status():
        # The message is centred on the status line; when it wraps, the dot stays beside its first line.
        if status_at[0] is None:
            return
        text_x, dot_x, line = status_at[0]
        status.place(text_x, line)
        box = stage.bbox(status.item)
        lines = max(1, round((box[3] - box[1]) / linespace)) if box else 1
        app.status_dot.place(dot_x, line - (lines - 1) * linespace / 2)

    app.status.trace_add("write", lambda *_a: (status.configure(text=app.status.get()), place_status()))
    app.progress = CanvasProgress(stage, theme=t.palette(), height=3)
    app.progress.grid_remove()

    def layout_stage(_event=None):
        width, height = stage.winfo_width(), stage.winfo_height()
        left, right = px(CONTENT_PAD_X), width - px(CONTENT_PAD_X)
        app.page_title.place(left, px(HEADER_TOP + 13))
        app.page_subtitle.place(left, px(HEADER_TOP + 29 + 8))
        page_top, page_bottom = px(PAGE_TOP), height - px(PAGE_BOTTOM)
        # Mid-drag the page and the log drawer (with all their fields) keep their size; only what
        # sits directly on the stage follows the window. They settle in one go when the drag ends.
        if not _Page.held:
            if app.log_visible:
                log_height = app.log_frame.winfo_reqheight()
                # Same right edge as the cards, which leave room for the page's scrollbar when it shows.
                page = app.page_layouts.get(getattr(app, "page", PAGE_EXPORT))
                log_right = right - (page.bar_room() if page else 0)
                stage.coords(app.log_item, left, page_bottom - log_height)
                stage.itemconfigure(app.log_item, width=max(1, log_right - left), state="normal")
                page_bottom -= log_height + px(LOG_GAP)
            else:
                stage.itemconfigure(app.log_item, state="hidden")
            stage.coords(app.page_item, left, page_top)
            stage.itemconfigure(app.page_item, width=max(1, right - left), height=max(1, page_bottom - page_top))
            if app.page_rect != (left, page_top, right, page_bottom):
                app.page_rect = (left, page_top, right, page_bottom)
                backdrop.sync(stage, redraw=True)
        links = height - px(ACTIONS_BOTTOM) + px(18)
        app.output_link.place(left, links)
        app.log_button.place(left + app.output_link.width() + px(20), links)
        if not _Page.held:
            # On bigger windows the stickers grow a little: half as fast as the window, at most 30 %.
            grow = min(width / px(WINDOW_W - RAIL_WIDTH), height / px(WINDOW_H))
            scale = round(min(1.3, max(1.0, 1 + (grow - 1) * 0.5)) * 20) / 20
            app.convert_button.set_scale(scale)
            app.cancel_button.set_scale(scale)
        scale = app.convert_button.scale
        # Anchored at the bottom-right corner of the footer; distances grow with the stickers.
        stickers = height - px(STICKER_CENTER_BOTTOM - 15) - px(15) * scale
        app.convert_button.place(right - px(41.76) * scale, stickers)
        app.cancel_button.place(right - px(135) * scale, stickers)
        line = height - px(STATUS_BOTTOM) + px(18)
        # Wrap long status messages before they reach the stickers on the right.
        status.configure(wraplength=max(px(120), right - left - px(230) * scale))
        status_at[0] = (left + px(10), left + px(2.5), line)
        place_status()
        app.progress.place(left, right, height - px(24))

    app.layout_stage = layout_stage
    stage.bind("<Configure>", layout_stage, add="+")
    app.log_frame.bind("<Configure>", lambda _e: app.log_visible and layout_stage(), add="+")
    return stage


def _window_edges(root, chrome):
    """A 1 px outline around the frameless window, for Windows versions that cannot draw one."""
    edge = hairline()
    lines = [tk.Frame(root, background=gui_theme.WINDOW_EDGE, bd=0, highlightthickness=0) for _ in range(4)]
    places = ({"x": 0, "y": 0, "relwidth": 1, "height": edge}, {"x": 0, "rely": 1, "y": -edge, "relwidth": 1, "height": edge},
              {"x": 0, "y": 0, "relheight": 1, "width": edge}, {"relx": 1, "x": -edge, "y": 0, "relheight": 1, "width": edge})

    def update(event=None):
        if event is not None and event.widget is not root:
            return
        for line, place in zip(lines, places):
            if chrome.zoomed():
                line.place_forget()   # a maximized window has no outline
            else:
                line.place(**place)
                line.lift()

    root.bind("<Configure>", update, add="+")
    update()


# --------------------------------------------------------------------------
# Top-level assembly.


def build(app):
    beta = character_beta()
    app.beta = beta
    root = app.root
    gui_theme.initialize(root, game_install=app.values['game_install'].get())
    root.configure(background=gui_theme.BACKDROP)
    root.title(f"RenderLock | {__version__}" + (" · Character beta" if beta else " · Development"))

    # The design's own title bar on a frameless window that keeps native moving, snapping and
    # resizing; if that is not available the normal Windows title bar is used instead.
    chrome = WindowChrome(root)
    app.chrome = chrome
    custom = chrome.prepare()
    bar_height = px(BAR_HEIGHT) if custom else 0
    width, height = px(WINDOW_W), px(WINDOW_H) + bar_height
    app.min_height = px(MIN_H) + bar_height
    left, top, right, bottom = _work_area(root)
    x = left + max(0, (right - left - width) // 2)
    y = top + max(0, (bottom - top - height) // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.minsize(px(MIN_W), app.min_height)

    title_bar = None
    if custom:
        title_bar = TitleBar(root, chrome, theme=gui_theme.palette())
        title_bar.pack(side="top", fill="x")
    shell = tk.Frame(root, background=gui_theme.BACKDROP)
    shell.pack(fill="both", expand=True)
    shell.columnconfigure(1, weight=1)
    shell.rowconfigure(0, weight=1)

    backdrop = Backdrop(root, title_height=bar_height, left=px(RAIL_WIDTH))
    app.backdrop = backdrop

    def size_move(dragging):
        # While the window is dragged (moved or resized) the background and the card layout wait,
        # so every step of the drag stays quick; both catch up once the mouse is released.
        _Page.hold(dragging)
        backdrop.set_frozen(dragging)
        if not dragging:
            root.after_idle(app.layout_stage)

    # Windows reports its drag loop only through a flag (chrome.sizing); notice a size drag from Tk's
    # side and poll for its end. Moving the window without resizing it needs no pause.
    drag = {"active": False, "size": None}

    def drag_end():
        if chrome.sizing:
            root.after(80, drag_end)
        else:
            drag["active"] = False
            size_move(False)

    def watch_drag(event):
        if event.widget is not root:
            return
        size = (event.width, event.height)
        if chrome.sizing and not drag["active"] and drag["size"] not in (None, size):
            drag["active"] = True
            size_move(True)
            root.after(80, drag_end)
        if not chrome.sizing:
            drag["size"] = size

    root.bind("<Configure>", watch_drag, add="+")
    root.bind("<Configure>", lambda event: _restored(app, event), add="+")

    # After a size jump (maximize, restore, snap) Tk moves many small windows; Windows may carry
    # their old pixels along for a moment. Once the layout has settled, repaint everything once.
    repaint = {"job": None, "size": None}

    def repaint_now():
        repaint["job"] = None
        if chrome.sizing:
            repaint["job"] = root.after(150, repaint_now)
        else:
            root.update_idletasks()  # finish the new layout first, then show it in one piece
            chrome.repaint()

    def repaint_later(event):
        size = (event.width, event.height)
        if event.widget is not root or size == repaint["size"]:
            return
        repaint["size"] = size
        if repaint["job"] is not None:
            root.after_cancel(repaint["job"])
        repaint["job"] = root.after(40, repaint_now)

    def watchdog():
        chrome.check_hidden(0.5)
        root.after(250, watchdog)

    root.bind("<Configure>", repaint_later, add="+")
    root.after(250, watchdog)
    _build_rail(app, shell, beta)
    _build_stage(app, shell, backdrop)
    resizer = EdgeResizer(root, chrome)

    def first_map(event):
        if event.widget is not root or getattr(app, "window_ready", False):
            return
        app.window_ready = True
        if custom and not chrome.finish():
            title_bar.pack_forget()
            backdrop.title_height = 0
            app.min_height -= bar_height
            root.minsize(px(MIN_W), app.min_height)
        if not chrome.active:
            gui_theme.style_title_bar(root)  # recolor the native title bar instead
        elif not chrome.native_border:
            _window_edges(root, chrome)      # Windows 10: draw the 1 px window outline ourselves
        resizer.attach()
        backdrop.sync(redraw=True)
        # Cards fade out where a scrolling page cuts them, instead of ending in a hard line.
        app.edge_fade = gui_fade.install(root, chrome)
        if app.edge_fade:
            def page_view(page):
                if page is None:
                    app.edge_fade.hide()
                elif page.shell is app.page_frames.get(getattr(app, "page", None)):
                    app.edge_fade.request(page)
            _Page.on_view = page_view
            page_view(app.page_layouts[app.page])
        root.after(1500, lambda: _prepare_maximized_art(app))

    root.bind("<Map>", first_map, add="+")

    set_log_visible(app, False)
    show_page(app, PAGE_EXPORT)
    app._mode_changed()
    refresh_asset_source_controls(app)
    app._append_log("Choose a recording, check Setup, then inspect a short range.\n" + app.discovery_note)


def _build_map(app, page):
    body, row = _card(app, page, 0, "Downloaded map", label_column=WIDE_LABEL_COLUMN,
                     info='Download and extract the separate map once. RenderLock appends and aligns it; texture relinking and cutting stay manual.')
    _check(app, body, 'Include downloaded map', app.values['external_map_enabled'])
    _path_row(app, body, 'Map .blend', 'external_map', app._browse_external_map)
    buttons = tk.Frame(body, background=gui_theme.CARD)
    buttons.grid(row=_next_row(body), column=0, columnspan=3, sticky='w', pady=_pad((0, ROW_GAP)))
    RoundedButton(buttons, text='Download map', style='quiet', command=app._open_map_link,
                  theme=gui_theme.palette(), height=34).pack(side='left', padx=_pad((0, 8)))
    RoundedButton(buttons, text='Map Optimizer', style='quiet', command=lambda: app._open_map_link(True),
                  theme=gui_theme.palette(), height=34).pack(side='left')
    map_note = wrap_to_width(_label(body, 'Extract the ZIP and choose RenderLockMap.blend. Characters, cameras, lights and effects stay in RenderLockChar; '
               'the appended map is in RenderLockMap. In Blender, use File > External Data > Find Missing Files and select the '
               'downloaded Textures folder. Hide RenderLockChar while cutting with the separate Map Optimizer, then show it again. '
               'This map snapshot can differ from a later game or replay. Source lighting is optional and configured separately.',
               color=gui_theme.DIM, font=gui_theme.FONT_SMALL, justify='left', anchor='w', wraplength=px(TEXT_WRAP)))
    map_note.grid(row=_next_row(body), column=0, columnspan=3, sticky='ew', pady=_pad((SECTION_GAP, 0)))
    body, row = _card(app, page, row, "Installed lighting region", label_column=WIDE_LABEL_COLUMN,
                      info="The lighting tools read one prepared region of the installed map. "
                           "Prepared regions are cached by map content and export bounds. "
                           + ("This beta omits installed geometry conversion; downloaded maps are appended separately above." if character_beta()
                              else "Map geometry can be omitted from the Blender scene."))
    app.map_prepare_check = _check(app, body, "Prepare installed map region", app.values["map_enabled"], pady=(0, CHECK_GAP),
           installed_only=True)
    app.map_look_note = tk.StringVar()
    wrap_to_width(_label(body, '', textvariable=app.map_look_note, justify='left', anchor='w', wraplength=px(TEXT_WRAP))).grid(
        row=_next_row(body), column=0, columnspan=3, sticky='ew', pady=_pad((0, ROW_GAP)))
    _path_row(app, body, "Installed map", "map_name", None, button=False)
    _path_row(app, body, "Extra margin (metres)", "map_margin", None, button=False)
    _path_row(app, body, "Regional exporter", "world_region_probe", app._browse_world_region_probe)
    _path_row(app, body, ".NET executable", "lighting_dotnet", app._browse_lighting_dotnet)
    if character_beta():
        _note(body, "The beta omits converted installed geometry; the downloaded map above is appended separately.")
    else:
        _check(app, body, "Characters only; omit prepared map geometry", app.values["character_only"])
    _note(body, "Source/NPR lighting needs data from the installed map. Character-only exports retain "
                "that lighting data so you can add your own map separately in Blender. Preparing lighting "
                "does not require importing map geometry into your final scene. "
                "New map and lighting preparation uses Installed game on Setup.", pady=(SECTION_GAP, 0))
    refresh_character_look(app)
    return row


def _build_updates(app, page):
    body, row = _card(app, page, 0, "RenderLock updates", f"Installed version: {__version__}")
    app.update_status = tk.StringVar(value="Check for new RenderLock releases.")
    _check(app, body, "Check for updates when RenderLock opens", app.values["check_updates"], pady=(0, 8),
           info="Checks contact GitHub. Downloads and installation start only when you click.")
    status = wrap_to_width(_label(body, "", wraplength=px(TEXT_WRAP), justify="left", anchor="w"))
    status.configure(textvariable=app.update_status)
    status.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew", pady=_pad((0, SECTION_GAP)))
    buttons = tk.Frame(body, background=gui_theme.CARD)
    buttons.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew")
    app.update_check = RoundedButton(buttons, text="Check for updates", command=lambda: app.update_ui.check(),
                                    theme=gui_theme.palette(), height=34)
    app.update_check.pack(side="left", padx=_pad((0, 8)))
    app.update_action = RoundedButton(buttons, text="Download update", command=lambda: app.update_ui.action(),
                                     style="primary", theme=gui_theme.palette(), height=34)
    app.update_action.pack(side="left")
    body, row = _card(app, page, row, "What's new")
    # Rounded (4 px) like the design; the box's own 4 px inset plus the text's 8 px keep the text 12 px in.
    notes_box = Card(body, theme=gui_theme.palette(), fill=gui_theme.FIELD, edge=None, radius=4, pad_x=4, pad_y=4,
                     background=gui_theme.CARD)
    notes_box.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew")
    notes_frame = notes_box.body
    notes_frame.columnconfigure(0, weight=1)
    app.update_notes = tk.Text(notes_frame, height=12, wrap="word", state="disabled", bd=0,
        padx=px(8), pady=px(8), background=gui_theme.FIELD, foreground=gui_theme.TEXT,
        font=gui_theme.FONT, highlightthickness=0)
    app.update_notes.grid(row=0, column=0, sticky="ew")
    scroll = ttk.Scrollbar(notes_frame, orient="vertical", command=app.update_notes.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    app.update_notes.configure(yscrollcommand=scroll.set)
    app.update_notes_link = RoundedButton(body, text="Open full release on GitHub", style="quiet",
        command=lambda: app.update_ui.open_notes(), theme=gui_theme.palette())
    app.update_notes_link.grid(row=_next_row(body), column=0, columnspan=3, sticky="w", pady=_pad((8, 0)))

