"""The same RenderLock branding throughout install, recovery and failure UI.

The window body is one canvas: the designer's textured paper card (rendered from the Figma pop-ups,
``gui_assets.UPDATE_PAPER``) with the wordmark, texts, progress line and diagnostic box drawn on it,
so the paper shows behind the text. Above it sits the main window's custom title bar (gui_chrome)
with minimize and close only. gui_theme, gui_widgets and gui_assets are imported directly;
gui_chrome is imported guarded (the native title bar stays without it). All four are in the
updater's list of required files.
"""
from __future__ import annotations

import base64
import math
import random
import time
import tkinter as tk
import zlib

from . import gui_assets
from . import gui_theme as theme
from .gui_widgets import BOIL_INTERVAL_MS, COPY_SIZE, CopyButton, copy_text, draw_rounded, rgba_image, _ProgressMotion

# Layout from the Figma pop-ups, in design px: window client size and where the paper card sits.
LAYOUT = {"progress": ((560, 270), (14.7, 16.5)), "details": ((560, 369), (14.5, 19.0))}
PAD_X, PAD_Y = 24, 22      # card padding around its content
CONTENT_W = 472
ROW_H = 26                 # emblem + wordmark row
SUBTITLE_Y = 30            # the row, then 4
STATUS_Y = 146
LINE_GAP = 10              # status -> progress line or details box
DETAILS_H = 116
DETAILS_RADIUS = 4
TEXT_INSET = 11            # diagnostic text from the box edge (10 padding + 1 px edge)
COPY_INSET = 5             # copy button, top right inside the details box
# The paper card boils like the sticker buttons, but calmer: every other beat of the shared 500 ms
# boil clock it snaps to another pose, turning only slightly (it is large) and never moving up or
# down. The text on it stays still.
PAPER_POSES = (0.0, -0.2, 0.2, -0.12, 0.12)   # degrees; the first is the design at rest
PAPER_BEAT_MS = 2 * BOIL_INTERVAL_MS
TITLE_BAR = 32             # the main window's custom title bar, at the pop-ups' bar height


def _custom_chrome(root):
    """The main window's frameless chrome and title bar class for this window, or None (the native
    title bar stays). Imported here, guarded, so a worker that runs this window without
    gui_chrome still gets a working window."""
    try:
        from .gui_chrome import TitleBar, WindowChrome
    except ImportError:
        return None
    chrome = WindowChrome(root, resizable=False)
    return (chrome, TitleBar) if chrome.prepare() else None


def _weights(np, size_in, size_out):
    """Tent-filter resampling weights (size_out x size_in), widened when shrinking so it averages."""
    factor = size_out / size_in
    support = max(1.0, 1.0 / factor)
    centres = (np.arange(size_out) + 0.5) / factor - 0.5
    weights = np.maximum(0.0, 1.0 - np.abs(np.arange(size_in)[None, :] - centres[:, None]) / support)
    weights = weights.astype(np.float32)
    return weights / weights.sum(axis=1, keepdims=True)


def _resize(np, pixels, width, height):
    """Resample ``pixels`` (h x w x channels) to width x height: two plain 2-D matrix products."""
    in_h, in_w, channels = pixels.shape
    rows = _weights(np, in_h, height) @ pixels.reshape(in_h, in_w * channels).astype(np.float32)
    columns = np.ascontiguousarray(rows.reshape(height, in_w, channels).transpose(1, 0, 2)).reshape(in_w, -1)
    out = (_weights(np, in_w, width) @ columns).reshape(width, height, channels).transpose(1, 0, 2)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def _paper_source(name):
    """The paper card at this screen's scale, in design colors (float RGB), or None (other skins,
    no numpy)."""
    if getattr(theme, "SKIN_NAME", "") != "bone":
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    width, height, blob = gui_assets.UPDATE_PAPER[name]
    pixels = np.frombuffer(zlib.decompress(base64.b64decode(blob)), np.uint8).reshape(height, width, 3)
    factor = theme.SCALE / gui_assets.UPDATE_PAPER_SCALE
    out_w, out_h = max(1, int(width * factor + 0.5)), max(1, int(height * factor + 0.5))
    if (out_w, out_h) != (width, height):
        pixels = _resize(np, pixels, out_w, out_h)
    return pixels.astype(np.float32)


def _paper_margin(source):
    """Border around the paper image so the most-turned pose is never cropped."""
    height, width = source.shape[:2]
    turn = math.sin(math.radians(max(abs(a) for a in PAPER_POSES)))
    return int(math.hypot(width, height) / 2 * turn) + 2


def _edge(source):
    """The color all around the rendered card (its whole outer ring is one color), used for the
    border the poses turn on and for the window background, so no seam can show."""
    return source[0, 0].copy()


def _padded(np, source):
    """``source`` with a 1 px border of its edge color, for bilinear taps past its edge."""
    padded = np.empty((source.shape[0] + 2, source.shape[1] + 2, 3), np.float32)
    padded[:] = _edge(source)
    padded[1:-1, 1:-1] = source
    return padded


class _PaperPose:
    """One pose of the paper: ``source`` turned by ``angle`` degrees about its centre (bilinear) on a
    ``margin`` px border of its edge color, in this screen's colors. It is computed a band of rows
    at a time (``build_some``), so building it never holds up the window for long."""

    BAND_PIXELS = 40_000   # about 20 ms of work per band

    def __init__(self, source, angle, margin, padded=None):
        import numpy as np
        self._np = np
        height, width = source.shape[:2]
        self.width, self.height = width + 2 * margin, height + 2 * margin
        self._source, self._angle, self._margin = source, angle, margin
        self._padded = padded if padded is not None else _padded(np, source)
        self._rows = max(4, self.BAND_PIXELS // self.width) if angle else self.height
        self._bands = []
        self._done_rows = 0

    @property
    def done(self):
        return self._done_rows >= self.height

    def build_some(self):
        """Compute the next band of rows; True once the whole pose is ready."""
        np, src_h, src_w = self._np, *self._source.shape[:2]
        r0 = self._done_rows
        r1 = min(self.height, r0 + self._rows)
        if not self._angle:  # at rest: the paper on its border, in one go
            band = np.empty((self.height, self.width, 3), np.float32)
            band[:] = _edge(self._source)
            m = self._margin
            band[m:m + src_h, m:m + src_w] = self._source
        else:
            a = math.radians(self._angle)
            cos_a, sin_a = math.cos(a), math.sin(a)
            xs = (np.arange(self.width, dtype=np.float32) + 0.5 - self.width / 2)[None, :]
            ys = (np.arange(r0, r1, dtype=np.float32) + 0.5 - self.height / 2)[:, None]
            u = xs * cos_a + ys * sin_a + src_w / 2 - 0.5
            v = ys * cos_a - xs * sin_a + src_h / 2 - 0.5
            x0, y0 = np.floor(u).astype(np.int32), np.floor(v).astype(np.int32)
            fx, fy = (u - x0)[..., None], (v - y0)[..., None]
            padded = self._padded

            def tap(x, y):
                return padded[np.clip(y + 1, 0, src_h + 1), np.clip(x + 1, 0, src_w + 1)]

            band = (tap(x0, y0) * (1 - fx) * (1 - fy) + tap(x0 + 1, y0) * fx * (1 - fy)
                    + tap(x0, y0 + 1) * (1 - fx) * fy + tap(x0 + 1, y0 + 1) * fx * fy)
        self._bands.append(theme.screen_pixels(np.clip(band + 0.5, 0, 255).astype(np.uint8)).tobytes())
        self._done_rows = r1
        return self.done

    def photo(self, master):
        return tk.PhotoImage(master=master, format="ppm",
                             data=b"P6 %d %d 255 " % (self.width, self.height) + b"".join(self._bands))


def _paper_photo(master, source, angle, margin):
    """A whole pose at once (the first one shown, and tests)."""
    pose = _PaperPose(source, angle, margin)
    while not pose.build_some():
        pass
    return pose.photo(master)


def _paper_background(source):
    """The window background exactly as the paper image's border comes out (same conversion), so
    the image's edge never shows."""
    import numpy as np
    pixel = np.clip(_edge(source) + 0.5, 0, 255).astype(np.uint8)[None, None, :]
    r, g, b = theme.screen_pixels(pixel)[0, 0]
    return "#%02x%02x%02x" % (int(r), int(g), int(b))


def _emblem_photo(master):
    """The 16 px brand emblem from the sidebar (bone skin), or None. Stored per display scale up to
    200 %; past that the largest one is scaled up."""
    if getattr(theme, "SKIN_NAME", "") != "bone":
        return None
    width, height, blob = gui_assets.EMBLEM[min(gui_assets.EMBLEM, key=lambda s: abs(s - theme.SCALE))]
    raw = zlib.decompress(base64.b64decode(blob))
    try:
        import numpy as np
    except ImportError:
        return rgba_image(master, width, height, [raw[y * width * 4:(y + 1) * width * 4] for y in range(height)])
    pixels = np.frombuffer(raw, np.uint8).reshape(height, width, 4).copy()
    size = theme.px(16)
    if abs(size - width) > 1:
        largest = gui_assets.EMBLEM[max(gui_assets.EMBLEM)]
        source = np.frombuffer(zlib.decompress(base64.b64decode(largest[2])), np.uint8)
        source = source.reshape(largest[1], largest[0], 4).astype(np.float32)
        alpha = source[:, :, 3:4] / 255.0
        premultiplied = np.concatenate([source[:, :, :3] * alpha, source[:, :, 3:4]], axis=2)
        scaled = _resize(np, premultiplied, size, size).astype(np.float32)
        cover = scaled[:, :, 3:4]
        scaled[:, :, :3] = np.where(cover > 0, scaled[:, :, :3] * 255.0 / np.maximum(cover, 1.0), 0.0)
        pixels = np.clip(scaled + 0.5, 0, 255).astype(np.uint8)
        width = height = size
    pixels[:, :, :3] = theme.screen_pixels(np.ascontiguousarray(pixels[:, :, :3]))
    return rgba_image(master, width, height, [pixels[y].tobytes() for y in range(height)])


class _CanvasText:
    """A text item on the window canvas that answers ``configure``/``cget`` like the Label it
    replaced, so callers can keep setting ``title`` and ``status`` text."""

    def __init__(self, canvas, item, on_change=None):
        self.canvas, self.item, self._on_change = canvas, item, on_change

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        options = {}
        if "text" in kw:
            options["text"] = kw["text"]
        for key in ("foreground", "fg"):
            if key in kw:
                options["fill"] = kw[key]
        if "font" in kw:
            options["font"] = kw["font"]
        if options:
            self.canvas.itemconfigure(self.item, **options)
            if self._on_change is not None:
                self._on_change()

    config = configure

    def cget(self, key):
        return self.canvas.itemcget(self.item, {"foreground": "fill", "fg": "fill"}.get(key, key))

    def __getitem__(self, key):
        return self.cget(key)


class _PaperProgress(_ProgressMotion):
    """The thin indeterminate progress line (ProgressLine's motion and ``start``/``stop``), drawn on
    the window canvas so the paper shows around the moving segment."""

    # The line moves by elapsed time, at the speed ProgressLine has with Windows' default timer
    # (after(12) fires every 15.6 ms), so other work on the window (the paper's poses being built)
    # never slows it down; a longer hold-up is capped so the line never jumps far.
    TICK_MS = 15.625

    def __init__(self, canvas, width, height):
        self._canvas, self._width, self._height = canvas, width, height
        self._x = self._y = 0
        self._job = None
        self._pos = 0.0
        self._interval = 12
        self._last = None
        self._init_motion(canvas)

    def place(self, x, y):
        self._x, self._y = x, y
        self._progress_resized()

    def _clear_progress(self):
        try:
            self._canvas.delete("progress")
        except tk.TclError:  # the window is already being destroyed
            pass

    grid_remove = _ProgressMotion.stop  # it used to be a gridded widget

    def _draw_progress(self, animated):
        now = time.perf_counter()
        steps = 1.0 if self._last is None else min(4.0, (now - self._last) * 1000.0 / self.TICK_MS)
        self._last = now
        span = max(theme.px(60), self._width * 0.22)
        if animated:
            self._pos = (self._pos + self._width / 140.0 * steps) % (self._width + span)
        self._canvas.delete("progress")
        left, right = ((max(0, self._pos - span), min(self._width, self._pos))
                       if animated else (0, self._width))
        if right > left:
            self._canvas.create_rectangle(self._x + left, self._y, self._x + right, self._y + self._height,
                                          fill=theme.ACCENT, width=0, tags="progress")


class UpdateWindow:
    def __init__(self, *, recover=False, root=None):
        if root is None:
            theme.enable_dpi_awareness()
            root = tk.Tk()
        self.root = root
        theme.initialize(root)
        self.icon = theme.brand_window(root)
        self._emblem = _emblem_photo(root)
        title = "RenderLock | " + ("Recovery" if recover else "Updating")
        root.title(title)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", lambda: None)
        # The main window's own title bar, with minimize and close only (the size is fixed).
        self.chrome = self.title_bar = None
        custom = _custom_chrome(root)
        if custom is not None:
            self.chrome, title_bar = custom
            self.title_bar = title_bar(root, self.chrome, theme=theme.palette(), buttons=("minimize", "close"),
                                       links=False, height=TITLE_BAR, title=title, icon=self._emblem)
            self.title_bar.pack(side="top", fill="x")
            self._mapped = False
            root.bind("<Map>", self._first_map, add="+")
        self._mode = "progress"
        self._size(*LAYOUT[self._mode][0])
        self.canvas = c = tk.Canvas(root, background=theme.BG, highlightthickness=0, bd=0)
        c.pack(fill="both", expand=True)
        c.bind("<Destroy>", lambda e: e.widget is c and self._stop_motion(), add="+")
        self._sources = {}      # mode -> paper pixels at this scale (or None)
        self._papers = {}       # (mode, pose) -> PhotoImage
        self._pose = 0
        self._rng = random.Random()
        self._boil_job = self._build_job = None
        self._queue = []        # [((mode, pose), _PaperPose)] still being built
        self._paper = c.create_image(0, 0, anchor="nw")
        self._emblem_item = c.create_image(0, 0, anchor="w", image=self._emblem) if self._emblem else None
        self.wordmark = _CanvasText(c, c.create_text(0, 0, anchor="w", text="RenderLock",
                                                     font=theme.FONT_WORDMARK, fill=theme.BRAND))
        self.title = _CanvasText(c, c.create_text(
            0, 0, anchor="nw", width=theme.px(CONTENT_W), font=theme.FONT, fill=theme.TEXT,
            text="Recovering your previous version" if recover else "Installing your update"), self._place)
        self.status = _CanvasText(c, c.create_text(
            0, 0, anchor="nw", width=theme.px(CONTENT_W), font=theme.FONT, fill=theme.MUTED,
            text="Restoring the saved application files..." if recover else
            "Verifying and installing RenderLock. The app will reopen when it is ready."), self._place)
        self.progress = _PaperProgress(c, theme.px(CONTENT_W), theme.px(3))
        self.details = self.copy_button = None
        self._place()
        self.progress.start()
        theme.watch_motion(self)
        self.motion_changed()

    def motion_changed(self):
        self._queue = []
        for name in ("_boil_job", "_build_job"):
            job = getattr(self, name)
            if job is not None:
                self.canvas.after_cancel(job)
                setattr(self, name, None)
        if theme.motion_enabled() and self._sources.get(self._mode) is not None:
            self._build_poses()
            self._boil_job = self.canvas.after(PAPER_BEAT_MS, self._boil)

    def _paper_image(self):
        """The paper for the current mode and pose (the pose at rest is built right away)."""
        if self._mode not in self._sources:
            self._sources[self._mode] = _paper_source(self._mode)
        source = self._sources[self._mode]
        if source is None:
            return None, 0
        margin = _paper_margin(source)
        key = (self._mode, self._pose)
        if key not in self._papers:
            self._papers[key] = _paper_photo(self.canvas, source, PAPER_POSES[self._pose], margin)
        return self._papers[key], margin

    def _build_poses(self):
        """Queue the turned poses of the current mode; they are built between frames."""
        source = self._sources.get(self._mode)
        if source is None:
            return
        import numpy as np  # a paper source exists, so numpy does
        margin, padded = _paper_margin(source), _padded(np, source)
        self._queue = [((self._mode, i), _PaperPose(source, angle, margin, padded))
                       for i, angle in enumerate(PAPER_POSES) if i and (self._mode, i) not in self._papers]
        if self._queue and self._build_job is None:
            self._build_job = self.canvas.after(1, self._build_step)

    def _build_step(self):
        self._build_job = None
        if not self._queue:
            return
        key, pose = self._queue[0]
        if pose.build_some():
            self._papers[key] = pose.photo(self.canvas)
            self._queue.pop(0)
        if self._queue:
            self._build_job = self.canvas.after(15, self._build_step)  # room for the progress line

    def _boil(self):
        """Snap the paper to another ready pose (never the same one twice in a row)."""
        self._boil_job = None
        if not theme.motion_enabled():
            return
        ready = [i for i in range(len(PAPER_POSES)) if i != self._pose and (self._mode, i) in self._papers]
        if ready:
            self._pose = self._rng.choice(ready)
            self.canvas.itemconfigure(self._paper, image=self._papers[(self._mode, self._pose)])
        self._boil_job = self.canvas.after(PAPER_BEAT_MS, self._boil)

    def _stop_motion(self):
        self.progress.stop()
        self._queue = []
        for name in ("_boil_job", "_build_job"):
            job = getattr(self, name)
            if job is not None:
                try:
                    self.canvas.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, name, None)

    def _size(self, width, height):
        """Size the window for a body of width x height design px (plus the custom title bar)."""
        root = self.root
        width, height = theme.px(width), theme.px(height)
        if self.title_bar is not None:
            height += theme.px(TITLE_BAR)
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")

    def _first_map(self, event):
        """Once the window is first shown: switch to the custom frame, or fall back to the native one."""
        if event.widget is not self.root or self._mapped:
            return
        self._mapped = True
        if not self.chrome.finish():
            self.title_bar.pack_forget()
            self.title_bar = self.chrome = None
            self._size(*LAYOUT[self._mode][0])
        elif not self.chrome.native_border:
            # Windows 10 draws no outline around a frameless window: a 1 px one, as in the design.
            self.root.configure(highlightthickness=theme.hairline(), highlightbackground=theme.WINDOW_EDGE,
                                highlightcolor=theme.WINDOW_EDGE)

    def _set_title(self, title):
        self.root.title(title)
        if self.title_bar is not None:
            self.title_bar.set_title(title)

    def _place(self):
        """Position everything for the current mode (also after a text change, since the status
        line may wrap)."""
        c, px = self.canvas, theme.px
        x0, y0 = (px(v) for v in LAYOUT[self._mode][1])
        image, margin = self._paper_image()
        c.delete("card")
        if image is not None:
            c.itemconfigure(self._paper, image=image)
            c.coords(self._paper, x0 - margin, y0 - margin)
            background = _paper_background(self._sources[self._mode])
            if c.cget("background") != background:
                c.configure(background=background)
        else:  # plain card where the paper is unavailable
            width, height = (v / gui_assets.UPDATE_PAPER_SCALE for v in gui_assets.UPDATE_PAPER[self._mode][:2])
            draw_rounded(c, x0, y0, x0 + px(width), y0 + px(height), px(6), theme.CARD, theme.CARD_EDGE,
                         theme.BG, tag="card")
            c.tag_lower("card")
        left, top = x0 + px(PAD_X), y0 + px(PAD_Y)
        row = top + px(ROW_H) / 2
        if self._emblem_item is not None:
            c.coords(self._emblem_item, left, row)
        # Emblem, 5 px, wordmark (measured from the emblem bitmap actually used).
        c.coords(self.wordmark.item, left + (self._emblem.width() + px(5) if self._emblem_item is not None else 0), row)
        c.coords(self.title.item, left, top + px(SUBTITLE_Y))
        c.coords(self.status.item, left, top + px(STATUS_Y))
        below = (c.bbox(self.status.item) or (0, 0, 0, top + px(STATUS_Y)))[3] + px(LINE_GAP)
        self.progress.place(left, below)
        if self.details is not None:
            self._place_details(left, below)

    def _place_details(self, left, top):
        c, px = self.canvas, theme.px
        right, bottom = left + px(CONTENT_W), top + px(DETAILS_H)
        c.delete("details")
        draw_rounded(c, left, top, right, bottom, px(DETAILS_RADIUS), theme.FIELD, theme.EDGE, theme.CARD,
                     tag="details")
        c.tag_raise("details", self._paper)
        inset = px(DETAILS_RADIUS)  # the text widget stays clear of the rounded corners
        gutter = px(COPY_INSET + COPY_SIZE + 4)
        c.coords(self._details_item, left + inset, top + inset)
        c.itemconfigure(self._details_item, width=right - left - inset - gutter, height=bottom - top - 2 * inset)
        c.coords(self._copy_item, right - px(COPY_INSET), top + px(COPY_INSET))

    def show_error(self, detail, close):
        self.progress.stop()
        self.progress.grid_remove()
        self._set_title("RenderLock | Update needs attention")
        self._mode = "details"
        self._pose = 0  # the taller paper starts at rest; its turned poses are built below
        self._size(*LAYOUT[self._mode][0])
        c, px = self.canvas, theme.px
        pad = px(TEXT_INSET) - px(DETAILS_RADIUS)
        self.details = tk.Text(c, width=1, height=1, wrap="word", bd=0, highlightthickness=0,
            background=theme.FIELD, foreground=theme.TEXT, font=theme.FONT_SMALL, padx=pad, pady=pad,
            selectbackground=theme.SELECTION, inactiveselectbackground=theme.SELECTION,
            selectforeground=theme.TEXT)
        self.details.insert("1.0", detail)
        self.details.configure(state="disabled")
        self._details_item = c.create_window(0, 0, anchor="nw", window=self.details)
        self.copy_button = CopyButton(c, palette=theme.palette(), command=lambda: self._copy(detail))
        self._copy_item = c.create_window(0, 0, anchor="ne", window=self.copy_button)
        self.title.configure(text="The update could not finish")
        self.status.configure(text="Your backup and recovery files have been retained. Review the details below.")
        self.root.protocol("WM_DELETE_WINDOW", close)
        self.root.bind("<Escape>", lambda _e: close())
        if self._boil_job is not None:
            self._build_poses()

    def _copy(self, detail):
        if copy_text(self.canvas, detail):
            return True
        # The clipboard was unavailable: select the message and give the box keyboard focus, so
        # Ctrl+C (the Text's own copy) works on it right away.
        self.details.tag_add("sel", "1.0", "end-1c")
        self.details.mark_set("insert", "1.0")
        self.details.focus_set()
        return False
