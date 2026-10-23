"""Flat controls drawn on canvases: buttons, fields, cards, switches and checks.

Tk canvases do not anti-alias on Windows, so every curved edge comes from a
small corner image whose pixels are pre-blended against the known background
(computed once per radius/color combination and cached). Straight edges stay
plain canvas rectangles, so redrawing on resize is cheap.

Every control keeps plain Tk behavior: normal and disabled states, hover/press
feedback, keyboard activation and the ``configure(state=...)`` API that the
shared busy-state controller expects.

Size arguments are design pixels (the Figma mockup at 100%); each control
converts them with ``px()`` so it stays proportional on scaled displays.
"""
from __future__ import annotations

import base64
import math
import random
import struct
import sys
import time
import tkinter as tk
import zlib
from tkinter import font as tkfont

from . import gui_theme
from .gui_theme import px

_SUPERSAMPLE = 4
_CORNER_CACHE: dict[tuple, tk.PhotoImage] = {}
_ROW_CACHE: dict[tuple, list[list[str]]] = {}


def _parent_bg(parent, fallback):
    try:
        return parent.cget("background")
    except tk.TclError:
        return fallback


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def _corner_rows(radius, fill, outline, bg):
    """Pixel rows for the top-left corner of a rounded rectangle."""
    key = (radius, fill, outline, bg)
    if key in _ROW_CACHE:
        return _ROW_CACHE[key]
    f, b = _rgb(fill), _rgb(bg)
    o = _rgb(outline) if outline else None
    n = _SUPERSAMPLE * _SUPERSAMPLE
    rows = []
    for y in range(radius):
        row = []
        for x in range(radius):
            inner = ring = 0
            for sy in range(_SUPERSAMPLE):
                py = y + (sy + 0.5) / _SUPERSAMPLE
                for sx in range(_SUPERSAMPLE):
                    px = x + (sx + 0.5) / _SUPERSAMPLE
                    d = math.hypot(radius - px, radius - py)
                    if o is not None and d <= radius - 1:
                        inner += 1
                    elif d <= radius:
                        if o is None:
                            inner += 1
                        else:
                            ring += 1
            cf, co = inner / n, ring / n
            row.append(_hex([b[i] * (1 - cf - co) + f[i] * cf + (o[i] * co if o else 0) for i in range(3)]))
        rows.append(row)
    _ROW_CACHE[key] = rows
    return rows


def _corner(master, radius, fill, outline, bg, which):
    """Corner image cached per Tk interpreter; ``which`` is nw, ne, sw or se."""
    # Keep the interpreter itself: numeric ids can be reused after root teardown.
    key = (master.tk, radius, fill, outline, bg, which)
    image = _CORNER_CACHE.get(key)
    if image is None:
        rows = _corner_rows(radius, fill, outline, bg)
        if which in ("ne", "se"):
            rows = [list(reversed(row)) for row in rows]
        if which in ("sw", "se"):
            rows = list(reversed(rows))
        image = tk.PhotoImage(master=master, width=radius, height=radius)
        image.put(" ".join("{" + " ".join(row) + "}" for row in rows))
        _CORNER_CACHE[key] = image
    return image


def draw_rounded(canvas, x0, y0, x1, y1, radius, fill, outline=None, bg=None, tag="shape",
                 corners=("nw", "ne", "sw", "se")):
    """Draw an anti-aliased rounded rectangle on ``canvas`` (integer pixel box)."""
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    bg = bg or canvas.cget("background")
    r = int(max(0, min(radius, (x1 - x0) // 2, (y1 - y0) // 2)))
    if r < 2:
        canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline or "", width=1 if outline else 0, tags=tag)
        return
    canvas.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, width=0, tags=tag)
    canvas.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, width=0, tags=tag)
    if outline:
        for box in ((x0 + r, y0, x1 - r, y0 + 1), (x0 + r, y1 - 1, x1 - r, y1),
                    (x0, y0 + r, x0 + 1, y1 - r), (x1 - 1, y0 + r, x1, y1 - r)):
            canvas.create_rectangle(*box, fill=outline, width=0, tags=tag)
    spots = {"nw": (x0, y0), "ne": (x1 - r, y0), "sw": (x0, y1 - r), "se": (x1 - r, y1 - r)}
    for which, (cx, cy) in spots.items():
        if which in corners:
            canvas.create_image(cx, cy, image=_corner(canvas, r, fill, outline, bg, which), anchor="nw", tags=tag)
        else:
            canvas.create_rectangle(cx, cy, cx + r, cy + r, fill=fill, width=0, tags=tag)


def gradient_color(stops, t):
    """Color at position ``t`` along gradient ``stops`` [(position 0-1, "#rrggbb"), ...]."""
    t = min(1.0, max(0.0, t))
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            k = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            a, b = _rgb(c0), _rgb(c1)
            return _hex([a[i] + (b[i] - a[i]) * k for i in range(3)])
    return stops[-1][1]


def bind_tree(widget, sequence, callback, add="+"):
    widget.bind(sequence, callback, add=add)
    for child in widget.winfo_children():
        bind_tree(child, sequence, callback, add=add)


class _StateMixin:
    """Shared ``configure``/``cget`` handling for canvas-drawn controls."""

    _handled = ("state",)

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        for key in self._handled:
            if key in kw:
                self._set_option(key, kw.pop(key))
        if kw:
            tk.Canvas.configure(self, **kw)

    config = configure

    def _set_option(self, key, value):
        if key == "state" and value in ("normal", "disabled", "readonly", "!disabled"):
            # "readonly" is what a combobox would be given; treat it as enabled.
            self._state = "disabled" if value == "disabled" else "normal"
            if self._state == "disabled":
                self._hover = self._pressed = False
            # ``focus_script`` (set by a scrolling page) replaces plain takefocus while enabled, so
            # Tab can reach the control and scroll it into view even when it is out of sight.
            enabled = self._state != "disabled"
            tk.Canvas.configure(self, cursor="hand2" if enabled else "arrow",
                                takefocus=(getattr(self, "focus_script", None) or 1) if enabled else 0)
            self._draw()

    def cget(self, key):
        if key == "state":
            return self._state
        return tk.Canvas.cget(self, key)

    def __getitem__(self, key):
        return self.cget(key)


class RoundedButton(_StateMixin, tk.Canvas):
    """Flat button with smooth rounded corners.

    ``style`` chooses the palette: ``primary`` (accent fill), ``secondary``
    (raised neutral fill) or ``quiet`` (text only, background-less).
    """

    _handled = ("state", "text", "command")

    def __init__(self, parent, *, text="", command=None, theme, style="secondary",
                 height=36, radius=9, pad_x=14, bold=False, background=None):
        self._theme = dict(theme)
        self._style = style
        self._text = text
        self._command = command
        self._state = "normal"
        self._hover = self._pressed = self._focused = False
        self._pad_x = px(pad_x)
        self._radius = px(radius)
        self._ch = px(height)
        self._font = tkfont.Font(font=gui_theme.FONT_BOLD if bold else gui_theme.FONT)
        self._bg = background or _parent_bg(parent, theme["BG"])
        self._cw = self._font.measure(text) + 2 * self._pad_x
        tk.Canvas.__init__(self, parent, width=self._cw, height=self._ch, background=self._bg,
                           highlightthickness=0, bd=0, takefocus=True, cursor="hand2")
        self._draw()
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda _e: self._invoke())
        self.bind("<space>", lambda _e: self._invoke())
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))

    def _colors(self):
        t = self._theme
        disabled = self._state == "disabled"
        if self._style == "primary":
            if disabled:
                return t["EDGE_SOFT"], None, t["DIM"]
            face = t["ACCENT_PRESSED"] if self._pressed else (t["ACCENT_HOVER"] if self._hover else t["ACCENT"])
            return face, None, t["ACCENT_TEXT"]
        if self._style == "quiet":
            if disabled:
                return None, None, t["DIM"]
            if self._pressed or self._hover:
                # Text-only links (no side padding) brighten instead of gaining a pill.
                return (None if self._pad_x < 4 else t["RAIL_HOVER"]), None, t["TEXT"]
            return None, None, t["MUTED"]
        if disabled:
            return t["BUTTON"], t["EDGE_SOFT"], t["DIM"]
        face = t["BUTTON_PRESSED"] if self._pressed else (t["BUTTON_HOVER"] if self._hover else t["BUTTON"])
        return face, t["EDGE_SOFT"], t["BUTTON_TEXT"]

    def _draw(self):
        self.delete("all")
        face, outline, text = self._colors()
        if self._focused and self._state != "disabled":
            # Keyboard focus ring; clicks do not take focus, so this appears on Tab only.
            outline = self._theme["TEXT"] if self._style == "primary" else self._theme["ACCENT"]
            face = face or self._bg
        if face is not None:
            draw_rounded(self, 0, 0, self._cw, self._ch, self._radius, face, outline, self._bg)
        self.create_text(self._cw / 2, self._ch / 2, text=self._text, fill=text, font=self._font)

    def _set_hover(self, hover):
        self._hover = hover and self._state != "disabled"
        if not hover:
            self._pressed = False
        self._draw()

    def _set_focus(self, focused):
        self._focused = focused
        self._draw()

    def _on_press(self, _event):
        if self._state != "disabled":
            self._pressed = True
            self._draw()

    def _on_release(self, event):
        was = self._pressed
        self._pressed = False
        self._draw()
        if was and 0 <= event.x <= self._cw and 0 <= event.y <= self._ch:
            self._invoke()

    def _invoke(self):
        if self._state != "disabled" and self._command is not None:
            self._command()

    def _set_option(self, key, value):
        if key == "text" and value is not None:
            self._text = value
            width = self._font.measure(value) + 2 * self._pad_x
            if width != self._cw:
                self._cw = width
                tk.Canvas.configure(self, width=width)
            self._draw()
        elif key == "command":
            self._command = value
        else:
            super()._set_option(key, value)

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "command":
            return self._command
        return super().cget(key)


class RoundedEntry(_StateMixin, tk.Canvas):
    """Single-line text field on a rounded, borderless background.

    The border appears on hover and turns to the accent color while the
    field has keyboard focus. ``entry`` is the underlying ``tk.Entry``.
    """

    def __init__(self, parent, *, variable, theme, font, width=120, height=34, radius=9,
                 pad_x=10, background=None):
        self._theme = dict(theme)
        self._state = "normal"
        self._hover = self._pressed = self._focus = False
        self._radius = px(radius)
        self._pad_x = px(pad_x)
        height = px(height)
        self._bg = background or _parent_bg(parent, theme["BG"])
        tk.Canvas.__init__(self, parent, width=px(width), height=height, background=self._bg,
                           highlightthickness=0, bd=0, takefocus=False, cursor="xterm")
        t = self._theme
        self.entry = tk.Entry(self, textvariable=variable, relief="flat", bd=0, highlightthickness=0,
                              background=t["FIELD"], foreground=t["TEXT"], insertbackground=t["TEXT"],
                              disabledbackground=t["FIELD"], disabledforeground=t["DIM"],
                              readonlybackground=t["FIELD"], selectbackground=t["SELECTION"],
                              selectforeground=t["TEXT"], font=font)
        self._window = self.create_window(self._pad_x, height // 2, window=self.entry, anchor="w")
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", lambda _e: self.entry.focus_set())
        for widget in (self, self.entry):
            widget.bind("<Enter>", lambda _e: self._set_hover(True), add="+")
            widget.bind("<Leave>", lambda _e: self._set_hover(False), add="+")
        self.entry.bind("<FocusIn>", lambda _e: self._set_focus(True), add="+")
        self.entry.bind("<FocusOut>", lambda _e: self._set_focus(False), add="+")

    def _set_hover(self, hover):
        self._hover = hover and self._state != "disabled"
        self._draw()

    def _set_focus(self, focused):
        self._focus = focused
        self._draw()

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            w, h = int(tk.Canvas.cget(self, "width")), int(tk.Canvas.cget(self, "height"))
        t = self._theme
        outline = None
        if self._focus and self._state != "disabled":
            outline = t["ACCENT"]
        elif self._hover:
            outline = t["EDGE"]
        self.delete("shape")
        draw_rounded(self, 0, 0, w, h, self._radius, t["FIELD"], outline, self._bg)
        self.tag_lower("shape")
        self.coords(self._window, self._pad_x, h // 2)
        self.itemconfigure(self._window, width=max(10, w - 2 * self._pad_x))

    def _set_option(self, key, value):
        if key == "state" and value in ("normal", "disabled", "readonly"):
            self._state = "disabled" if value == "disabled" else "normal"
            self.entry.configure(state=value)
            if self._state == "disabled":
                self._hover = False
            self._draw()


class Card(tk.Frame):
    """Rounded section card. Put content in ``card.body``; the card grows to fit it.

    The rounded shape (with its 1 px edge) is drawn on a canvas placed behind an
    ordinary padded frame, so normal geometry propagation sizes the card from its content.
    """

    def __init__(self, parent, *, theme, fill=None, edge="card", radius=6, pad_x=20, pad_y=15,
                 background=None):
        self._fill = fill or theme["CARD"]
        self._edge = theme["CARD_EDGE"] if edge == "card" else edge
        self._radius = px(radius)
        self._bg = background or _parent_bg(parent, theme["BG"])
        super().__init__(parent, background=self._bg)
        self._shape = tk.Canvas(self, background=self._bg, highlightthickness=0, bd=0)
        self._shape.place(x=0, y=0, relwidth=1, relheight=1)
        self._shape.bind("<Configure>", self._redraw)
        self.body = tk.Frame(self, background=self._fill)
        self.body.pack(fill="both", expand=True, padx=px(pad_x), pady=px(pad_y))
        tk.Misc.lower(self._shape)  # Canvas.lower is tag_lower; this lowers the widget itself.

    def _redraw(self, event):
        self._shape.delete("shape")
        draw_rounded(self._shape, 0, 0, event.width, event.height, self._radius, self._fill, self._edge, self._bg)


class SegmentedControl(_StateMixin, tk.Canvas):
    """Two-or-more option switch bound to a StringVar."""

    _handled = ("state", "command")

    def __init__(self, parent, *, options, variable, command=None, theme,
                 height=34, radius=9, inner_radius=5, pad_x=10, background=None):
        self._seg_defs = list(options)
        self._variable = variable
        self._command = command
        self._theme = dict(theme)
        self._state = "normal"
        self._hover = self._pressed = self._focused = False
        self._hover_index = None
        self._font = tkfont.Font(font=gui_theme.FONT)
        self._inset = px(4)
        self._seg_widths = [self._font.measure(label) + 2 * px(pad_x) for label, _ in self._seg_defs]
        self._gap = px(2)
        self._cw = sum(self._seg_widths) + self._gap * (len(self._seg_widths) - 1) + 2 * self._inset
        self._ch = px(height)
        self._radius = px(radius)
        self._inner_radius = px(inner_radius)
        self._bg = background or _parent_bg(parent, theme["BG"])
        tk.Canvas.__init__(self, parent, width=self._cw, height=self._ch, background=self._bg,
                           highlightthickness=0, bd=0, takefocus=True, cursor="hand2")
        self._trace = variable.trace_add("write", lambda *args: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Left>", lambda _e: self._step(-1))
        self.bind("<Right>", lambda _e: self._step(1))
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self._draw()

    def _segments(self):
        left = self._inset
        for (label, value), width in zip(self._seg_defs, self._seg_widths):
            yield label, value, left, left + width
            left += width + self._gap

    def _draw(self):
        self.delete("all")
        t = self._theme
        disabled = self._state == "disabled"
        edge = t["ACCENT"] if self._focused and not disabled else None
        draw_rounded(self, 0, 0, self._cw, self._ch, self._radius, t["FIELD"], edge, self._bg)
        current = self._variable.get()
        inner = self._inner_radius
        for index, (label, value, x0, x1) in enumerate(self._segments()):
            selected = value == current
            if selected:
                face = t["EDGE_SOFT"] if disabled else t["SEGMENT_SEL"]
                draw_rounded(self, x0, self._inset, x1, self._ch - self._inset, inner, face, None, t["FIELD"])
            if disabled:
                fg = t["DIM"]
            elif selected or self._hover_index == index:
                fg = t["TEXT"]
            else:
                fg = t["MUTED"]
            self.create_text((x0 + x1) / 2, self._ch / 2, text=label, fill=fg, font=self._font)

    def _set_focus(self, focused):
        self._focused = focused
        self._draw()

    def _step(self, delta):
        if self._state == "disabled":
            return
        values = [value for _label, value in self._seg_defs]
        try:
            index = values.index(self._variable.get())
        except ValueError:
            index = 0
        target = values[max(0, min(len(values) - 1, index + delta))]
        if target != self._variable.get():
            self._variable.set(target)
            if self._command is not None:
                self._command()

    def _index_at(self, x):
        for index, (_label, _value, x0, x1) in enumerate(self._segments()):
            if x0 <= x < x1:
                return index
        return None

    def _on_motion(self, event):
        if self._state == "disabled":
            return
        index = self._index_at(event.x)
        if index != self._hover_index:
            self._hover_index = index
            self._draw()

    def _on_leave(self, _event):
        if self._hover_index is not None:
            self._hover_index = None
            self._draw()

    def _on_click(self, event):
        if self._state == "disabled":
            return
        index = self._index_at(event.x)
        if index is None:
            return
        _label, value = self._seg_defs[index]
        if value != self._variable.get():
            self._variable.set(value)
        if self._command is not None:
            self._command()

    def _set_option(self, key, value):
        if key == "command":
            self._command = value
        else:
            super()._set_option(key, value)

    def cget(self, key):
        if key == "command":
            return self._command
        return super().cget(key)


class CheckBox(_StateMixin, tk.Canvas):
    """Checkbox with a rounded box and a drawn tick, bound to a Tk variable.

    Works with the StringVar "1"/"0" values and with BooleanVar. The whole row
    (box and label) is clickable; Space toggles it from the keyboard.
    """

    BOX = 16  # design pixels

    def __init__(self, parent, *, text, variable, theme, onvalue=True, offvalue=False, background=None):
        self._theme = dict(theme)
        self._text = text
        self._variable = variable
        self._onvalue, self._offvalue = onvalue, offvalue
        self._state = "normal"
        self._hover = self._pressed = self._focused = False
        self._font = tkfont.Font(font=gui_theme.FONT)
        self._bg = background or _parent_bg(parent, theme["BG"])
        self._box = px(self.BOX)
        self._ch = px(26)
        self._cw = self._box + px(10) + self._font.measure(text) + px(4)
        tk.Canvas.__init__(self, parent, width=self._cw, height=self._ch, background=self._bg,
                           highlightthickness=0, bd=0, takefocus=True, cursor="hand2")
        self._trace = variable.trace_add("write", lambda *args: self._draw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", lambda _e: self._toggle())
        self.bind("<space>", lambda _e: self._toggle())
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self._draw()

    def _checked(self):
        try:
            return self._variable.get() == self._onvalue
        except tk.TclError:
            return False

    def _set_hover(self, hover):
        self._hover = hover and self._state != "disabled"
        self._draw()

    def _set_focus(self, focused):
        self._focused = focused
        self._draw()

    def _toggle(self):
        if self._state != "disabled":
            self._variable.set(self._offvalue if self._checked() else self._onvalue)

    def _draw(self):
        self.delete("all")
        t = self._theme
        disabled = self._state == "disabled"
        checked = self._checked()
        box = self._box
        top = (self._ch - box) // 2
        if checked:
            fill = t["EDGE"] if disabled else (t["ACCENT_HOVER"] if self._hover else t["ACCENT"])
            outline = None
        else:
            fill = t["FIELD"]
            outline = t["EDGE_SOFT"] if disabled else (t["MUTED"] if self._hover else t["EDGE"])
        if self._focused and not disabled:
            outline = t["TEXT"] if checked else t["ACCENT"]
        draw_rounded(self, 0, top, box, top + box, px(4), fill, outline, self._bg)
        if checked:
            tick = t["DIM"] if disabled else t["ACCENT_TEXT"]
            # Tick drawn on the 16 px design box, scaled to the actual box size.
            k = box / self.BOX
            self.create_line(4 * k, top + 8 * k, 7 * k, top + 11 * k, 12 * k, top + 5 * k, fill=tick,
                             width=px(2), capstyle="round", joinstyle="round")
        fg = t["DIM"] if disabled else t["TEXT"]
        self.create_text(box + px(10), self._ch / 2, text=self._text, anchor="w", fill=fg, font=self._font)


class DisplayText(tk.Canvas):
    """One line of text that never crops its descenders.

    Some display faces report a shallower descent than their glyphs use, so a
    tk.Label cuts off "p", "y", ... ``room`` adds that many pixels below the line.
    Supports ``configure(text=..., foreground=...)`` like a label.
    """

    def __init__(self, parent, *, text, font, foreground, room=0, background=None):
        self._font = tkfont.Font(font=font)
        self._text = text
        self._room = room
        super().__init__(parent, width=1, height=1, highlightthickness=0, bd=0,
                         background=background or _parent_bg(parent, "#000000"))
        self._item = self.create_text(0, 0, anchor="nw", text=text, font=self._font, fill=foreground)
        self._fit()

    def _fit(self):
        tk.Canvas.configure(self, width=self._font.measure(self._text) + px(2),
                            height=self._font.metrics("linespace") + self._room)

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if "text" in kw:
            self._text = kw.pop("text")
            self.itemconfigure(self._item, text=self._text)
            self._fit()
        color = kw.pop("foreground", None) or kw.pop("fg", None)
        if color:
            self.itemconfigure(self._item, fill=color)
        if kw:
            super().configure(**kw)

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        return super().cget(key)


# --------------------------------------------------------------------------
# Controls drawn straight onto a host canvas (the content stage), so the animated
# background shows around them: Tk widgets are always opaque rectangles, canvas
# items are not. Images carry real alpha, which Tk blends over whatever is below.


def _png(width, height, rows):
    """Base64 PNG (8-bit RGBA) from ``rows`` of width*4 bytes, for tk.PhotoImage(data=...)."""
    raw = b"".join(b"\0" + bytes(row) for row in rows)

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    return base64.b64encode(data).decode("ascii")


def rgba_image(master, width, height, rows):
    return tk.PhotoImage(master=master, data=_png(width, height, rows))


_ALPHA_CACHE: dict[tuple, tk.PhotoImage] = {}


def _cached(key, make):
    image = _ALPHA_CACHE.get(key)
    if image is None:
        if len(_ALPHA_CACHE) > 256:
            _ALPHA_CACHE.clear()
        image = _ALPHA_CACHE[key] = make()
    return image


def dot_image(master, size, color):
    """Anti-aliased filled circle with a transparent surround."""
    def make():
        r, g, b = _rgb(color)
        n = _SUPERSAMPLE
        rows = []
        for y in range(size):
            row = bytearray()
            for x in range(size):
                inside = sum(1 for sy in range(n) for sx in range(n)
                             if math.hypot(x + (sx + 0.5) / n - size / 2, y + (sy + 0.5) / n - size / 2) <= size / 2)
                row += bytes((r, g, b, round(255 * inside / (n * n))))
            rows.append(row)
        return rgba_image(master, size, size, rows)
    return _cached(("dot", size, color), make)


def paper_image(master, size, rect, angle, dx, dy, fill):
    """Anti-aliased rotated rectangle ``rect`` (w, h), centred in ``size`` and moved by (dx, dy)."""
    def make():
        cw, ch = size
        half_w, half_h = rect[0] / 2.0, rect[1] / 2.0
        a = math.radians(angle)
        cos_a, sin_a = math.cos(a), math.sin(a)
        cx, cy = cw / 2.0 + dx, ch / 2.0 + dy
        r, g, b = _rgb(fill)
        rows = []
        for y in range(ch):
            py = y + 0.5 - cy
            row = bytearray()
            for x in range(cw):
                qx = x + 0.5 - cx
                # Screen -> paper coordinates (counter-clockwise visual rotation in y-down space).
                u = qx * cos_a - py * sin_a
                v = qx * sin_a + py * cos_a
                cover = min(1.0, max(0.0, half_w - abs(u) + 0.5)) * min(1.0, max(0.0, half_h - abs(v) + 0.5))
                row += bytes((r, g, b, int(cover * 255 + 0.5)))
            rows.append(row)
        return rgba_image(master, cw, ch, rows)
    return _cached(("paper", size, rect, round(angle, 3), round(dx, 2), round(dy, 2), fill), make)


def wrap_to_width(label):
    """Wrap a label's text at the label's own width instead of a fixed column.

    Grid it with ``sticky="ew"`` (or pack with ``fill="x"``) so it spans its card: the text
    then uses the card's full width on big windows and never runs past the edge on small ones.
    """
    def wrap(event):
        if event.width > 1 and getattr(label, "_wrap_width", None) != event.width:
            label._wrap_width = event.width
            label.configure(wraplength=event.width)
    label.bind("<Configure>", wrap, add="+")
    return label


INFO_GLYPH = ""  # "Info" in Segoe Fluent Icons and Segoe MDL2 Assets
BUBBLE_STYLE = "rounded"   # "rounded": soft rounded bubble without a border; "plain": the text alone
BUBBLE_RADIUS = 7


def _bubble_widget(canvas, text, theme, width):
    """The bubble itself: a small canvas with a rounded, borderless fill behind the text."""
    t = theme
    under = t["CARD"]  # what the bubble's rounded corners blend into (cards and trays are this dark)
    font = tkfont.Font(font=gui_theme.FONT_SMALL)
    if BUBBLE_STYLE == "plain":
        return tk.Label(canvas, text=text, background=under, foreground=t["TEXT"], font=font, justify="left",
                        anchor="w", wraplength=px(width), bd=0, padx=0, pady=0)
    pad_x, pad_y = px(10), px(6)
    tip = tk.Canvas(canvas, background=under, highlightthickness=0, bd=0)
    label = tip.create_text(pad_x, pad_y, text=text, anchor="nw", fill=t["TEXT"], font=font, width=px(width))
    x0, y0, x1, y1 = tip.bbox(label)
    w, h = (x1 - x0) + 2 * pad_x, (y1 - y0) + 2 * pad_y
    tip.configure(width=w, height=h)
    draw_rounded(tip, 0, 0, w, h, px(BUBBLE_RADIUS), t["FIELD"], None, under)
    tip.tag_raise(label)
    return tip


def open_bubble(widget, text, *, theme, box=None, width=320, center=False):
    """Show ``text`` in a small bubble just below ``box`` of ``widget`` and return a handle.

    ``box`` is (x0, y0, x1, y1) in the widget's own pixels (default: the whole widget). The
    bubble is a window item on the nearest canvas *around* the widget (the page), above the
    cards, so it never takes focus and is never clipped by the widget itself. It opens upwards
    when there is no room below. Close it with ``close_bubble(handle)``.
    """
    canvas = widget.master
    while canvas is not None and not isinstance(canvas, tk.Canvas):
        canvas = canvas.master
    if canvas is None or not widget.winfo_viewable():
        return None
    tip = _bubble_widget(canvas, text, dict(theme), width)
    tip.update_idletasks()
    tip_w, tip_h = tip.winfo_reqwidth(), tip.winfo_reqheight()
    x0, y0, x1, y1 = box or (0, 0, widget.winfo_width(), widget.winfo_height())
    left, top = canvas.canvasx(0), canvas.canvasy(0)
    ox = left + widget.winfo_rootx() - canvas.winfo_rootx()
    oy = top + widget.winfo_rooty() - canvas.winfo_rooty()
    x = ox + ((x0 + x1 - tip_w) / 2 if center else x0 - px(6))
    y = oy + y1 + px(6)
    x = max(left, min(x, left + canvas.winfo_width() - tip_w - 1))
    # While the page continues below, its bottom edge is faded (gui_fade): that strip is no room either.
    from .gui_fade import FADE_HEIGHT
    faded = min(px(FADE_HEIGHT), canvas.winfo_height() // 3) if canvas.yview()[1] < 0.9995 else 0
    if y + tip_h > top + canvas.winfo_height() - faded:  # no room below: open upwards
        y = oy + y0 - tip_h - px(6)
    item = canvas.create_window(x, y, window=tip, anchor="nw")
    tk.Misc.lift(tip)  # the widget above the cards (Canvas.lift would mean raising a canvas item)
    return canvas, tip, item


def close_bubble(handle):
    if handle is not None:
        canvas, tip, item = handle
        try:
            canvas.delete(item)
            tip.destroy()
        except tk.TclError:
            pass


class InfoIcon(tk.Label):
    """A small "i" beside a title or option; hovering or focusing it shows ``text`` in a bubble.

    The bubble is drawn inside the page canvas the icon sits on (as a window item above the
    cards), not as a separate window, so it never takes focus from the main window.
    """

    def __init__(self, parent, *, text, theme, size=13, width=320):
        self._theme = dict(theme)
        self.text = text
        self._width = width
        self._tip = None
        self._job = None
        family = gui_theme.icon_font(parent)
        glyph, font = (INFO_GLYPH, (family, -px(size))) if family else ("ⓘ", gui_theme.FONT)
        super().__init__(parent, text=glyph, font=font, background=parent.cget("background"),
                         foreground=self._theme["DIM"], bd=0, padx=0, pady=0, highlightthickness=0,
                         takefocus=1, cursor="question_arrow")
        self.bind("<Enter>", lambda _e: self._later(), add="+")
        self.bind("<Leave>", lambda _e: self._hide(), add="+")
        self.bind("<FocusIn>", lambda _e: self._show(), add="+")
        self.bind("<FocusOut>", lambda _e: self._hide(), add="+")
        self.bind("<Destroy>", lambda _e: self._hide(), add="+")

    def _later(self):
        self.configure(foreground=self._theme["ACCENT"])
        if self._job is None:
            self._job = self.after(150, self._show)

    def _show(self):
        self._job = None
        self.configure(foreground=self._theme["ACCENT"])
        if self._tip is None:
            self._tip = open_bubble(self, self.text, theme=self._theme, width=self._width)

    def _hide(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        try:
            self.configure(foreground=self._theme["DIM"])
        except tk.TclError:
            pass  # being destroyed
        close_bubble(self._tip)
        self._tip = None


class CollapsibleNote(tk.Frame):
    """A long note shown as one line that ends in a clickable "…"; clicking shows all of it.

    Grid it with ``sticky="ew"``: the line is cut to the note's width, at a word boundary.
    """

    def __init__(self, parent, *, text, theme, font=None):
        background = parent.cget("background")
        super().__init__(parent, background=background)
        t = dict(theme)
        self.text = " ".join(text.split())
        self.expanded = False
        self._font = tkfont.Font(font=font or gui_theme.FONT_SMALL)
        common = dict(background=background, font=self._font, bd=0, padx=0, pady=0, anchor="w", justify="left")
        self._line = tk.Label(self, foreground=t["MUTED"], cursor="hand2", **common)
        self._more = tk.Label(self, text="…", foreground=t["ACCENT"], cursor="hand2", takefocus=1, **common)
        self._full = wrap_to_width(tk.Label(self, text=self.text, foreground=t["MUTED"],
                                            wraplength=px(700), **common))
        self._less = tk.Label(self, text="Show less", foreground=t["ACCENT"], cursor="hand2", takefocus=1, **common)
        self._focus_font = self._font.copy()
        self._focus_font.configure(underline=True)
        self.columnconfigure(2, weight=1)
        for widget, action in ((self._line, self.expand), (self._more, self.expand), (self._less, self.collapse)):
            widget.bind("<Button-1>", lambda _e, act=action: act(), add="+")
            widget.bind("<Return>", lambda _e, act=action: act(), add="+")
            widget.bind("<space>", lambda _e, act=action: act(), add="+")
        for link in (self._more, self._less):  # keyboard focus: brighter and underlined
            link.bind("<FocusIn>", lambda _e, w=link: w.configure(foreground=t["TEXT"], font=self._focus_font), add="+")
            link.bind("<FocusOut>", lambda _e, w=link: w.configure(foreground=t["ACCENT"], font=self._font), add="+")
        self.bind("<Configure>", lambda event: self._fit(event.width), add="+")
        self._place()

    def _fit(self, width):
        """Cut the text to one line of ``width`` pixels, leaving room for the "…"."""
        if self.expanded or width <= 1:
            return
        room = width - self._font.measure(" …") - px(2)
        if self._font.measure(self.text) <= width:
            self._line.configure(text=self.text)
            self._more.grid_remove()
            return
        low, high = 0, len(self.text)
        while low < high:  # longest prefix that fits
            middle = (low + high + 1) // 2
            if self._font.measure(self.text[:middle]) <= room:
                low = middle
            else:
                high = middle - 1
        cut = self.text[:low]
        if " " in cut and low < len(self.text) and self.text[low] != " ":
            cut = cut[:cut.rfind(" ")]
        self._line.configure(text=cut.rstrip(" ,;:.") + " ")
        self._more.grid(row=0, column=1, sticky="w")

    def _place(self):
        if self.expanded:
            self._line.grid_remove()
            self._more.grid_remove()
            self._full.grid(row=0, column=0, columnspan=3, sticky="ew")
            self._less.grid(row=1, column=0, sticky="w", pady=(px(4), 0))
        else:
            self._full.grid_remove()
            self._less.grid_remove()
            self._line.grid(row=0, column=0, sticky="w")
            self._fit(self.winfo_width())

    def expand(self):
        had_focus = self.focus_get() is self._more
        self.expanded = True
        self._place()
        if had_focus:
            self._less.focus_set()  # keep the keyboard on the note's link

    def collapse(self):
        had_focus = self.focus_get() is self._less
        self.expanded = False
        self._place()
        if had_focus:
            self._more.focus_set()


class CanvasText:
    """A text item that behaves like a label for ``configure(text=, foreground=)``."""

    def __init__(self, canvas, *, text, font, fill, anchor="w", width=0):
        self.canvas = canvas
        self.item = canvas.create_text(0, 0, text=text, font=font, fill=fill, anchor=anchor, width=width)

    def place(self, x, y):
        self.canvas.coords(self.item, x, y)

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        options = {}
        if "text" in kw:
            options["text"] = kw.pop("text")
        color = kw.pop("foreground", None) or kw.pop("fg", None)
        if color:
            options["fill"] = color
        if "wraplength" in kw:
            options["width"] = kw.pop("wraplength")
        if options:
            self.canvas.itemconfigure(self.item, **options)

    config = configure

    def cget(self, key):
        return self.canvas.itemcget(self.item, "text" if key == "text" else key)


class _CanvasControl:
    """Shared state, hover, press and focus handling for controls drawn on a host canvas."""

    _cursor = "hand2"

    def __init__(self, canvas, *, command, theme):
        self.canvas = canvas
        self._theme = dict(theme)
        self._command = command
        self._state = "normal"
        self._hover = self._pressed = self._focused = False
        self.tag = "control%d" % id(self)

    def _bind(self):
        c, tag = self.canvas, self.tag
        c.tag_bind(tag, "<Enter>", lambda _e: self._set_hover(True))
        c.tag_bind(tag, "<Leave>", lambda _e: self._set_hover(False))
        c.tag_bind(tag, "<ButtonPress-1>", self._on_press)
        c.tag_bind(tag, "<ButtonRelease-1>", self._on_release)

    def _set_hover(self, hover):
        hover = hover and self._state != "disabled"
        self._entered(hover and not self._hover)
        self._hover = hover
        if not hover:
            self._pressed = False
        try:
            self.canvas.configure(cursor=self._cursor if hover else "")
        except tk.TclError:
            pass
        self._draw()

    def _entered(self, fresh):
        pass

    def _on_press(self, _event):
        if self._state != "disabled":
            self._pressed = True
            self._draw()

    def _on_release(self, event):
        was, self._pressed = self._pressed, False
        self._draw()
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        x0, y0, x1, y1 = self.canvas.bbox(self.tag) or (0, 0, 0, 0)
        if was and x0 <= x <= x1 and y0 <= y <= y1:
            self.invoke()

    def invoke(self):
        if self._state != "disabled" and self._command is not None:
            self._command()

    def focusable(self):
        return self._state != "disabled"

    def set_focus(self, focused):
        self._focused = focused
        self._draw()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if "command" in kw:
            self._command = kw.pop("command")
        if "text" in kw and kw["text"] is not None:
            self._set_text(kw.pop("text"))
        if "state" in kw:
            value = kw.pop("state")
            if value in ("normal", "disabled", "readonly", "!disabled"):
                self._state = "disabled" if value == "disabled" else "normal"
                if self._state == "disabled":
                    self._hover = self._pressed = False
                self._draw()

    config = configure

    def cget(self, key):
        return {"state": self._state, "command": self._command}.get(key, getattr(self, "_text", None))

    def __getitem__(self, key):
        return self.cget(key)

    def winfo_exists(self):
        return self.canvas.winfo_exists()

    def winfo_toplevel(self):
        return self.canvas.winfo_toplevel()


class CanvasLink(_CanvasControl):
    """Text-only action (e.g. "Show log"): muted, brightens on hover."""

    def __init__(self, canvas, *, text, command=None, theme, font):
        super().__init__(canvas, command=command, theme=theme)
        self._text = text
        self._font = tkfont.Font(font=font)
        self.item = canvas.create_text(0, 0, text=text, font=self._font, anchor="w", tags=(self.tag,))
        self.ring = canvas.create_rectangle(0, 0, 0, 0, outline="", width=1, state="hidden")
        self._bind()
        self._draw()

    def width(self):
        return self._font.measure(self._text)

    def place(self, x, y):
        self.canvas.coords(self.item, x, y)
        self._draw()

    def _set_text(self, text):
        self._text = text
        self.canvas.itemconfigure(self.item, text=text)

    def _draw(self):
        t = self._theme
        if self._state == "disabled":
            color = t["DIM"]
        else:
            color = t["TEXT"] if (self._hover or self._pressed) else t["MUTED"]
        self.canvas.itemconfigure(self.item, fill=color)
        box = self.canvas.bbox(self.item)
        if self._focused and box:
            pad = px(3)
            self.canvas.coords(self.ring, box[0] - pad, box[1] - px(1), box[2] + pad, box[3] + px(1))
            self.canvas.itemconfigure(self.ring, outline=t["ACCENT"], state="normal")
        else:
            self.canvas.itemconfigure(self.ring, state="hidden")


class CanvasSticker(_CanvasControl):
    """Uppercase label on a slightly tilted paper rectangle that snaps between poses.

    Like Deadlock's hero tags: every ~500 ms all stickers snap to a new pose at the same moment
    (no tweening). Only the paper moves; the label stays put. ``style`` is ``primary`` (accent
    paper) or ``secondary``; ``tilt`` is the resting angle in degrees (positive = counter-clockwise),
    ``paper`` its size and ``paper_offset`` where its centre sits relative to the label (design px).
    """

    POSES = 6

    def __init__(self, canvas, *, text, command=None, theme, font, style="primary", tilt=0.0,
                 paper=(80.75, 29.35), paper_offset=(0.0, 0.0), seed=0):
        super().__init__(canvas, command=command, theme=theme)
        self._style = style
        self._text = text
        self._font = tkfont.Font(font=font)
        self._tilt = tilt
        self._paper_design = paper
        self._offset_design = paper_offset
        self.scale = 1.0
        self._font_size = self._font.cget("size")   # the theme's size; grows with set_scale
        self._rng = random.Random(seed)
        self._pose_index = 0
        self._x = self._y = 0
        self._layout()
        self.paper = canvas.create_image(0, 0, anchor="center", tags=(self.tag,))
        self.ring = canvas.create_polygon(0, 0, 0, 0, 0, 0, fill="", outline="", width=1, state="hidden")
        # The label ignores the pointer, so moving over it does not count as leaving the paper.
        self.label = canvas.create_text(0, 0, text=text.upper(), font=self._font, state="disabled")
        self._bind()
        self._draw()
        BoilClock.register(self)

    def set_scale(self, scale):
        """Draw the sticker ``scale`` times its design size (paper and label); for big windows."""
        if abs(scale - self.scale) < 0.01:
            return
        self.scale = scale
        self._font.configure(size=int(round(self._font_size * scale)))
        self._layout()
        self.canvas.itemconfigure(self.label, font=self._font)
        self._draw()

    def _layout(self):
        s = self.scale
        self._offset = (self._offset_design[0] * gui_theme.SCALE * s, self._offset_design[1] * gui_theme.SCALE * s)
        self._paper_w = max(px(self._paper_design[0] * s), self._font.measure(self._text.upper()) + px(16 * s))
        self._paper_h = px(self._paper_design[1] * s)
        reach = math.sin(math.radians(abs(self._tilt) + 2.0))
        margin = px(4) + 2 * max(abs(v) for v in self._offset)
        self._cw = int(self._paper_w + self._paper_h * reach + margin) + 2
        self._ch = int(self._paper_h + self._paper_w * reach + margin) + 2
        unit = max(1, px(1))
        self._poses = [(0.0, 0, 0)]  # the first pose is the design at rest
        for _ in range(self.POSES - 1):
            self._poses.append((self._rng.uniform(-1.1, 1.1), self._rng.choice((-unit, 0, unit)),
                                self._rng.choice((-unit, 0, unit))))

    def size(self):
        return self._paper_w, self._paper_h

    def place(self, x, y):
        """Put the label's centre at (x, y)."""
        self._x, self._y = x, y
        self._draw()

    def advance(self):
        """Snap to another pose (never the same one twice in a row)."""
        choices = [i for i in range(len(self._poses)) if i != self._pose_index]
        self._pose_index = self._rng.choice(choices)
        self._draw()

    def _entered(self, fresh):
        if fresh:
            self.advance()  # a fresh pose under the pointer, like a tag being nudged

    def _set_text(self, text):
        self._text = text
        self._layout()
        self.canvas.itemconfigure(self.label, text=text.upper())

    def _colors(self):
        t = self._theme
        if self._state == "disabled":
            return t["EDGE_SOFT"], t["DIM"]
        if self._style == "primary":
            face = t["ACCENT_PRESSED"] if self._pressed else (t["ACCENT_HOVER"] if self._hover else t["ACCENT"])
            return face, t["ACCENT_TEXT"]
        face = t["BUTTON_PRESSED"] if self._pressed else (t["BUTTON_HOVER"] if self._hover else t["EDGE"])
        return face, t["BUTTON_TEXT"]

    def _draw(self):
        paper_tilt, dx, dy = self._poses[self._pose_index]
        if self._pressed:
            dy += max(1, px(1))  # the paper dips when pressed; the label does not move
        face, ink = self._colors()
        angle = self._tilt + paper_tilt
        dx, dy = dx + self._offset[0], dy + self._offset[1]
        image = paper_image(self.canvas, (self._cw, self._ch), (self._paper_w, self._paper_h), angle, dx, dy, face)
        c = self.canvas
        c.itemconfigure(self.paper, image=image)
        c.coords(self.paper, self._x, self._y)
        c.coords(self.label, self._x, self._y)
        c.itemconfigure(self.label, fill=ink, disabledfill=ink)
        if self._focused and self._state != "disabled":
            a = math.radians(angle)
            cos_a, sin_a = math.cos(a), math.sin(a)
            cx, cy = self._x + dx, self._y + dy
            hw, hh = self._paper_w / 2 + px(2), self._paper_h / 2 + px(2)
            points = []
            for u, v in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
                points += [cx + u * cos_a + v * sin_a, cy - u * sin_a + v * cos_a]
            c.coords(self.ring, *points)
            color = self._theme["TEXT"] if self._style == "primary" else self._theme["ACCENT"]
            c.itemconfigure(self.ring, outline=color, state="normal")
        else:
            c.itemconfigure(self.ring, state="hidden")


class CanvasDot:
    """A smooth status dot; ``configure(foreground=...)`` recolors it."""

    def __init__(self, canvas, *, color, size=5):
        self.canvas = canvas
        self._size = max(3, px(size))
        self._color = color
        self.item = canvas.create_image(0, 0, anchor="center", image=dot_image(canvas, self._size, color))

    def place(self, x, y):
        self.canvas.coords(self.item, x, y)

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        color = kw.pop("foreground", None) or kw.pop("fg", None)
        if color:
            self._color = color
            self.canvas.itemconfigure(self.item, image=dot_image(self.canvas, self._size, color))

    config = configure


class CanvasProgress:
    """Thin indeterminate progress line with the ttk ``start``/``stop`` and grid show/hide API.

    A short accent segment glides across the slot set by ``place``. Showing or hiding it never
    moves anything else.
    """

    def __init__(self, canvas, *, theme, height=3):
        self.canvas = canvas
        self._theme = dict(theme)
        self._height = max(1, px(height))
        self._job = None
        self._pos = 0.0
        self._interval = 12
        self._visible = False
        self._box = (0, 0, 1)
        self.item = canvas.create_rectangle(0, 0, 0, 0, fill=self._theme["ACCENT"], width=0, state="hidden")

    def place(self, x0, x1, y):
        self._box = (x0, x1, y)

    def grid(self, *_args, **_kw):
        self._visible = True

    def grid_remove(self):
        self._visible = False
        self.canvas.itemconfigure(self.item, state="hidden")

    def start(self, interval=12):
        self._interval = max(8, int(interval))
        if self._job is None:
            self._tick()

    def stop(self):
        if self._job is not None:
            self.canvas.after_cancel(self._job)
            self._job = None
        self.canvas.itemconfigure(self.item, state="hidden")

    def _tick(self):
        x0, x1, y = self._box
        width = max(1, x1 - x0)
        span = max(px(60), width * 0.22)
        self._pos = (self._pos + width / 140.0) % (width + span)
        self.canvas.coords(self.item, x0 + max(0, self._pos - span), y, x0 + min(width, self._pos), y + self._height)
        self.canvas.itemconfigure(self.item, state="normal" if self._visible else "hidden")
        self._job = self.canvas.after(self._interval, self._tick)


class CanvasFocus:
    """Keyboard focus for controls drawn on one canvas: Tab/Shift+Tab move between them
    (and on to the next widget after the last), Return/Space activate the focused one.

    Focus sits on a 1 px stand-in widget on the canvas, not on the canvas itself: a canvas that
    holds other widgets (the pages) also hears their focus changes, which would light the ring
    while the user types in a field. Created after the canvas's other widgets, the stand-in comes
    after them in Tab order; arriving with Shift+Tab starts at the last control.
    """

    def __init__(self, canvas):
        self.canvas = canvas
        self.controls = []
        self.index = None
        self.backward = False
        self.proxy = tk.Frame(canvas, width=1, height=1, background=canvas.cget("background"),
                              highlightthickness=0, bd=0, takefocus=1)
        canvas.create_window(0, 0, window=self.proxy, anchor="nw")
        self.proxy.bind("<FocusIn>", self._on_focus_in)
        self.proxy.bind("<FocusOut>", lambda _e: self._show(None))
        self.proxy.bind("<Tab>", lambda _e: self._move(1))
        self.proxy.bind("<Shift-Tab>", lambda _e: self._move(-1))
        for key in ("<Return>", "<space>"):
            self.proxy.bind(key, self._activate)
        # Remember which way the user is tabbing, anywhere in the window.
        top = canvas.winfo_toplevel()
        top.bind("<Tab>", lambda _e: setattr(self, "backward", False), add="+")
        top.bind("<Shift-Tab>", lambda _e: setattr(self, "backward", True), add="+")

    def add(self, control):
        self.controls.append(control)
        return control

    def _show(self, index):
        for i, control in enumerate(self.controls):
            control.set_focus(i == index)

    def _on_focus_in(self, _event):
        order = range(len(self.controls) - 1, -1, -1) if self.backward else range(len(self.controls))
        self.index = next((i for i in order if self.controls[i].focusable()), None)
        self._show(self.index)

    def _move(self, step):
        start = self.index if self.index is not None else (-1 if step > 0 else len(self.controls))
        i = start + step
        while 0 <= i < len(self.controls):
            if self.controls[i].focusable():
                self.index = i
                self._show(i)
                return "break"
            i += step
        self.index = None
        self._show(None)
        return None  # past the last control: let Tk move on to the next widget

    def _activate(self, _event):
        if self.index is not None:
            self.controls[self.index].invoke()
        return "break"


# --------------------------------------------------------------------------
# The shared "boil" clock (Deadlock's hero tags snap every ~500 ms, all at once).

BOIL_INTERVAL_MS = 500


class BoilClock:
    """Single timer that advances every registered animation together.

    Anything with ``advance()``, ``winfo_exists()`` and ``winfo_toplevel()`` can register.
    Nothing moves when Windows animation effects are off (see ``gui_theme.motion_enabled``).
    """

    members: list = []
    job = None
    root = None

    @classmethod
    def register(cls, member):
        cls.members.append(member)
        if cls.job is None and gui_theme.motion_enabled():
            cls.root = member.winfo_toplevel()
            cls.job = cls.root.after(BOIL_INTERVAL_MS, cls.tick)

    @classmethod
    def tick(cls):
        alive = []
        for member in cls.members:
            try:
                if member.winfo_exists():
                    member.advance()
                    alive.append(member)
            except tk.TclError:
                pass
        cls.members = alive
        cls.job = cls.root.after(BOIL_INTERVAL_MS, cls.tick) if alive else None


# Compatibility controls retained for updater windows and resize consumers.
def round_frame_corners(frame, radius, fill, bg, corners=("nw", "sw")):
    """Overlay anti-aliased corners on a plain frame (e.g. the content panel)."""
    labels = []
    for which in corners:
        label = tk.Label(frame, image=_corner(frame, radius, fill, None, bg, which), bd=0,
                         highlightthickness=0, background=fill)
        relx = 1.0 if which in ("ne", "se") else 0.0
        rely = 1.0 if which in ("sw", "se") else 0.0
        label.place(relx=relx, rely=rely, anchor=which)
        labels.append(label)
    return labels

class IdleUpdate:
    """Combine a burst of layout notifications into one update after layout settles."""

    def __init__(self, widget, callback):
        self.widget = widget
        self.callback = callback
        self._job = None
        widget.bind("<Destroy>", self._destroy, add="+")

    def __call__(self, _event=None):
        if self._job is None:
            self._job = self.widget.after_idle(self._flush)

    def _flush(self):
        self._job = None
        self.callback()

    def _destroy(self, event):
        if event.widget is self.widget and self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None

class ResizeRedraw(IdleUpdate):
    """Redraw once per settled size; moving a widget does not repaint its shape."""

    def __init__(self, widget, callback, *, idle=True):
        self._size = None
        self._redraw = callback
        self._idle = idle
        super().__init__(widget, self._resize)
        widget.bind("<Configure>", self, add="+")

    def __call__(self, event=None):
        if event is None or (event.widget is self.widget and
                             (event.width, event.height) != self._size):
            if self._idle:
                super().__call__(event)
            else:
                self._resize()

    def _resize(self):
        size = self.widget.winfo_width(), self.widget.winfo_height()
        if size != self._size:
            self._size = size
            self._redraw(*size)

def _strip_colors(length, color_at, linear):
    """Sample a strip; a linear RGB gradient has constant-color runs after rounding.

    Equal endpoint colors imply an equal interior only for a linear gradient.
    Splitting at color changes preserves the exact pixel colors without running
    interpolation and hex formatting for every pixel of the rail on every resize.
    """
    if not linear:
        return [color_at(index) for index in range(length)]
    colors = [None] * length

    def fill(first, last, left, right):
        if left == right or first == last:
            colors[first:last + 1] = [left] * (last - first + 1)
        elif last - first == 1:
            colors[first], colors[last] = left, right
        else:
            middle = (first + last) // 2
            center = color_at(middle)
            fill(first, middle, left, center)
            fill(middle, last, center, right)

    if length:
        fill(0, length - 1, color_at(0), color_at(length - 1))
    return colors

def outline_right_rounded(frame, radius, fill, bg, color_at, width=1, linear_gradient=False):
    """Gradient outline on the top, right and bottom edges of a frame with rounded right corners.

    ``color_at(u, v)`` gives the outline color at normalized frame coordinates
    (0-1 across, 0-1 down), so any linear gradient from the design can be used.
    Set ``linear_gradient`` only for a single linear RGB gradient, allowing exact
    constant-color runs to be sampled together during resize.
    The rounded corners themselves (fill, outline ring and outside color) are
    drawn here too. Call after the frame's content is built so the pieces sit on top.
    """
    pieces = {name: tk.Label(frame, bd=0, highlightthickness=0, background=fill)
              for name in ("top", "bottom", "right", "ne", "se")}
    pieces["top"].place(x=0, y=0, relwidth=1.0, width=-radius, height=width)
    pieces["bottom"].place(x=0, rely=1.0, y=-width, relwidth=1.0, width=-radius, height=width)
    pieces["ne"].place(relx=1.0, y=0, anchor="ne")
    pieces["se"].place(relx=1.0, rely=1.0, anchor="se")
    pieces["right"].place(relx=1.0, x=-width, y=radius, width=width, relheight=1.0, height=-2 * radius)

    def strip(colors, horizontal):
        image = tk.PhotoImage(master=frame, width=len(colors) if horizontal else width,
                              height=width if horizontal else len(colors))
        if horizontal:
            image.put(" ".join(["{" + " ".join(colors) + "}"] * width))
        else:
            image.put(" ".join("{" + " ".join([c] * width) + "}" for c in colors))
        return image

    last_size = None

    def redraw(w, h):
        nonlocal last_size
        if w <= radius or h <= 2 * radius:
            return
        run = w - radius
        images = {}
        # The rail has a fixed width. A height-only resize needs only its right
        # strip; re-creating the top/bottom images adds work to every drag event.
        if last_size is None or w != last_size[0]:
            images.update(
                top=strip(_strip_colors(run, lambda x: color_at(x / w, 0.0), linear_gradient), True),
                bottom=strip(_strip_colors(run, lambda x: color_at(x / w, 1.0), linear_gradient), True))
        if last_size is None or h != last_size[1]:
            images["right"] = strip(_strip_colors(h - 2 * radius,
                lambda y: color_at(1.0, (radius + y) / h), linear_gradient), False)
        if last_size is None:
            images.update(ne=_corner(frame, radius, fill, color_at(1.0, 0.0), bg, "ne"),
                          se=_corner(frame, radius, fill, color_at(1.0, 1.0), bg, "se"))
        for name, image in images.items():
            pieces[name].configure(image=image)
            pieces[name].image = image  # keep a reference; Tk does not
        last_size = w, h

    ResizeRedraw(frame, redraw)()
    for piece in pieces.values():
        piece.lift()
    return pieces

class StatusDot(tk.Canvas):
    """A smooth status dot; ``configure(foreground=...)`` recolors it."""

    def __init__(self, parent, *, color, background, size=6):
        self._size = size = px(size)
        self._color = color
        self._bg = background
        super().__init__(parent, width=size, height=size, background=background, highlightthickness=0, bd=0)
        self._draw()

    def _draw(self):
        self.delete("all")
        draw_rounded(self, 0, 0, self._size, self._size, self._size // 2, self._color, None, self._bg)

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        color = kw.pop("foreground", None) or kw.pop("fg", None)
        if color:
            self._color = color
            self._draw()
        if kw:
            super().configure(**kw)

    config = configure

class ProgressLine(tk.Canvas):
    """Thin indeterminate progress line with the ttk ``start``/``stop`` API.

    A short accent segment glides across the full width. Place it in a
    fixed-height container so showing and hiding it never moves the layout.
    """

    def __init__(self, parent, *, theme, height=3, background=None):
        self._theme = dict(theme)
        self._job = None
        self._pos = 0.0
        self._interval = 12
        super().__init__(parent, height=px(height), background=background or theme["FIELD"],
                         highlightthickness=0, bd=0)

    def start(self, interval=12):
        self._interval = max(8, int(interval))
        if self._job is None:
            self._tick()

    def stop(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self.delete("all")

    def _tick(self):
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        span = max(px(60), width * 0.22)
        self._pos = (self._pos + width / 140.0) % (width + span)
        self.delete("all")
        self.create_rectangle(max(0, self._pos - span), 0, min(width, self._pos), height,
                              fill=self._theme["ACCENT"], width=0)
        self._job = self.after(self._interval, self._tick)


# --------------------------------------------------------------------------
# Copy to the clipboard, and the small copy button.

COPY_SIZE = 26
COPY_GLYPH, DONE_GLYPH = "", ""   # Segoe Fluent Icons / MDL2 Assets: Copy, CheckMark
COPIED_MS = 1500


def _win32_copy(widget, text):
    """Hand ``text`` to the Windows clipboard directly. Tk renders clipboard text on request from its
    own window; this way the copy no longer depends on that window (the update window closes
    soon after a copy, and its process ends)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    try:
        hwnd = widget.winfo_id()  # the clipboard needs an owner window of this process
    except tk.TclError:
        return False
    buffer = ctypes.create_unicode_buffer(text.replace("\r\n", "\n").replace("\n", "\r\n"))
    size = ctypes.sizeof(buffer)
    handle = kernel32.GlobalAlloc(0x0002, size)  # GMEM_MOVEABLE
    if not handle:
        return False
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        return False
    ctypes.memmove(pointer, buffer, size)
    kernel32.GlobalUnlock(handle)
    for _ in range(10):  # another program (a clipboard manager) may hold it for a moment
        if user32.OpenClipboard(hwnd):
            break
        time.sleep(0.02)
    else:
        kernel32.GlobalFree(handle)
        return False
    try:
        if not user32.EmptyClipboard() or not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
            kernel32.GlobalFree(handle)
            return False
    finally:
        user32.CloseClipboard()
    return True


def copy_text(widget, text):
    """Put ``text`` on the clipboard; True when it worked."""
    if sys.platform == "win32":
        try:
            return _win32_copy(widget, text)
        except (OSError, AttributeError):
            pass  # no usable user32/kernel32: Tk's clipboard below
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
    except tk.TclError:
        return False
    return True


class CopyButton(tk.Canvas):
    """Small icon button that copies text (the update window's diagnostic box, the log drawer).

    ``command`` copies and returns True on success; the button then shows a check mark for
    ``COPIED_MS``. It sits on a FIELD background."""

    def __init__(self, parent, *, palette, command):
        self._t = palette
        self._command = command
        self._size = gui_theme.px(COPY_SIZE)
        self._hover = self._pressed = self._focused = self._done = False
        self._job = None
        super().__init__(parent, width=self._size, height=self._size, background=palette["FIELD"],
                         highlightthickness=0, bd=0, takefocus=True, cursor="hand2")
        family = gui_theme.icon_font(self)
        self._font = (family, -gui_theme.px(13)) if family else None
        self.bind("<Enter>", lambda _e: self._set("_hover", True))
        self.bind("<Leave>", lambda _e: (self._set("_pressed", False), self._set("_hover", False)))
        self.bind("<ButtonPress-1>", lambda _e: self._set("_pressed", True))
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda _e: self.invoke())
        self.bind("<space>", lambda _e: self.invoke())
        self.bind("<FocusIn>", lambda _e: self._set("_focused", True))
        self.bind("<FocusOut>", lambda _e: self._set("_focused", False))
        self.bind("<Destroy>", lambda e: e.widget is self and self._job is not None and self.after_cancel(self._job))
        self._draw()

    def _set(self, name, value):
        setattr(self, name, value)
        self._draw()

    def _on_release(self, event):
        was, self._pressed = self._pressed, False
        self._draw()
        if was and 0 <= event.x < self._size and 0 <= event.y < self._size:
            self.invoke()

    def invoke(self):
        if self._command():
            self._done = True
            self._draw()
            if self._job is not None:
                self.after_cancel(self._job)
            self._job = self.after(COPIED_MS, self._reset)

    def _reset(self):
        self._job = None
        self._set("_done", False)

    def _draw(self):
        self.delete("all")
        t, s = self._t, self._size
        face = t["BUTTON_PRESSED"] if self._pressed else (t["BUTTON"] if self._hover else None)
        outline = t["ACCENT"] if self._focused else None
        if face or outline:
            draw_rounded(self, 0, 0, s, s, gui_theme.px(5), face or t["FIELD"], outline, t["FIELD"])
        ink = t["ACCENT"] if self._done else (t["TEXT"] if self._hover or self._focused else t["MUTED"])
        c = s / 2
        if self._font:
            self.create_text(c, c, text=DONE_GLYPH if self._done else COPY_GLYPH, font=self._font, fill=ink)
        elif self._done:
            k = gui_theme.px(4)
            self.create_line(c - k, c, c - k / 3, c + k * 0.7, c + k, c - k * 0.7, fill=ink, width=gui_theme.hairline())
        else:  # two overlapping sheets
            k = gui_theme.px(4)
            self.create_rectangle(c - k + gui_theme.px(2), c - k - gui_theme.px(1), c + k + gui_theme.px(1), c + k - gui_theme.px(2),
                                  outline=ink, width=gui_theme.hairline())
            self.create_rectangle(c - k - gui_theme.px(1), c - k + gui_theme.px(2), c + k - gui_theme.px(2), c + k + gui_theme.px(1),
                                  outline=ink, fill=t["FIELD"], width=gui_theme.hairline())
