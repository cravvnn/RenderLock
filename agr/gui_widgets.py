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

import math
import tkinter as tk
from functools import lru_cache
from tkinter import font as tkfont

from .gui_theme import px

_SUPERSAMPLE = 4
_CORNER_CACHE: dict[tuple, tk.PhotoImage] = {}
_ROW_CACHE: dict[tuple, list[list[str]]] = {}


def _parent_bg(parent, fallback):
    try:
        return parent.cget("background")
    except tk.TclError:
        return fallback


@lru_cache(maxsize=256)
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
    """Cached corner image; ``which`` is one of nw, ne, sw, se."""
    key = (radius, fill, outline, bg, which)
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


def gradient_color(stops, t):
    """Color at position ``t`` along gradient ``stops`` [(position 0-1, "#rrggbb"), ...]."""
    t = min(1.0, max(0.0, t))
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            k = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            a, b = _rgb(c0), _rgb(c1)
            return _hex([a[i] + (b[i] - a[i]) * k for i in range(3)])
    return stops[-1][1]


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
            tk.Canvas.configure(self, cursor="arrow" if self._state == "disabled" else "hand2",
                                takefocus=self._state != "disabled")
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
        self._font = tkfont.Font(family="Segoe UI", size=10, weight="bold" if bold else "normal")
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
                              readonlybackground=t["FIELD"], selectbackground=t["RAIL_SEL"],
                              selectforeground=t["TEXT"], font=font)
        self._window = self.create_window(self._pad_x, height // 2, window=self.entry, anchor="w")
        # Small shapes are cheaper to paint in the same layout pass. Only the
        # expensive rail gradient is deferred; avoid an extra paint pass here.
        ResizeRedraw(self, lambda _w, _h: self._draw(), idle=False)
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

    The rounded shape is drawn on a canvas placed behind an ordinary padded
    frame, so normal geometry propagation sizes the card from its content.
    """

    def __init__(self, parent, *, theme, fill=None, edge=None, radius=15, pad_x=20, pad_y=15,
                 background=None):
        self._fill = fill or theme["CARD"]
        self._edge = edge
        self._radius = px(radius)
        self._bg = background or _parent_bg(parent, theme["BG"])
        super().__init__(parent, background=self._bg)
        self._shape = tk.Canvas(self, background=self._bg, highlightthickness=0, bd=0)
        self._shape.place(x=0, y=0, relwidth=1, relheight=1)
        ResizeRedraw(self._shape, self._redraw, idle=False)
        self.body = tk.Frame(self, background=self._fill)
        self.body.pack(fill="both", expand=True, padx=px(pad_x), pady=px(pad_y))
        tk.Misc.lower(self._shape)  # Canvas.lower is tag_lower; this lowers the widget itself.

    def _redraw(self, width, height):
        self._shape.delete("shape")
        draw_rounded(self._shape, 0, 0, width, height, self._radius, self._fill, self._edge, self._bg)


class SegmentedControl(_StateMixin, tk.Canvas):
    """Two-or-more option switch bound to a StringVar."""

    _handled = ("state", "command")

    def __init__(self, parent, *, options, variable, command=None, theme,
                 height=34, radius=12, pad_x=10, background=None):
        self._seg_defs = list(options)
        self._variable = variable
        self._command = command
        self._theme = dict(theme)
        self._state = "normal"
        self._hover = self._pressed = self._focused = False
        self._hover_index = None
        self._font = tkfont.Font(family="Segoe UI", size=10)
        self._inset = px(4)
        self._seg_widths = [self._font.measure(label) + 2 * px(pad_x) for label, _ in self._seg_defs]
        self._gap = px(2)
        self._cw = sum(self._seg_widths) + self._gap * (len(self._seg_widths) - 1) + 2 * self._inset
        self._ch = px(height)
        self._radius = px(radius)
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
        inner = max(2, self._radius - self._inset - 1)
        for index, (label, value, x0, x1) in enumerate(self._segments()):
            selected = value == current
            if selected:
                face = t["EDGE_SOFT"] if disabled else t["BUTTON"]
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
        self._font = tkfont.Font(family="Segoe UI", size=10)
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
