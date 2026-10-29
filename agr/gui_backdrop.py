"""Animated window background: the design's "BG - LAYER 1" shapes over its "BG - LAYER 2" picture.

Tk widgets cannot be see-through, so the background is painted on every canvas that shows the
window color (the content stage and each page), each at its own offset, and it stays fixed to the
window while a page scrolls. Everything snaps on the shared 500 ms boil clock that also drives the
sticker buttons: poses are held, never tweened ("posterized time").

Layer 2 (photo, ink wash and graffiti) is a picture: five renders from the design with the animated
photo at 100, 80, 60, 40 and 20 % opacity, stretched to the window with numpy and stepped through
one per snap (100 -> 20 -> 100). They are kept at half the screen resolution (the picture is soft)
and copied into the one displayed image when the step changes, which keeps memory low.

Layer 1 is vector: the main shape (a two-color gradient, drawn as the solid bands an 8-bit screen
shows), the halftone rim peeking out from under it, and two dust shapes fading along their
gradient. Halftone and dust are 1-bit dot patterns
(Tk stipples) made from the design's own textures. There are eight hand-cut variants of every
shape; each snap shows another one and shifts the dust, like the sticker buttons' poses.

Drawing cost: Tk fills a stippled polygon through an off-screen copy of its bounding box, so shapes
are clipped to the parts of each canvas the background is actually seen through (gaps between
cards, margins, header and footer), and the polygons are reused from snap to snap. While the
window is being moved or resized the background holds still and catches up once it is released.

Without numpy the picture layer stays the flat window color; the vector layer still animates.
"""
from __future__ import annotations

import atexit
import base64
import math
import os
import random
import shutil
import tempfile
import tkinter as tk
import zlib

from . import gui_assets, gui_theme

try:
    import numpy as np
except ImportError:  # the picture layer is optional
    np = None

DESIGN_W, DESIGN_H = 1040, 760   # the background frame, in design pixels
POSES = 8
PHOTO_CYCLE = (100, 80, 60, 40, 20, 40, 60, 80)   # the animated photo's opacity, one step per snap
SETTLE_MS = 250                  # after a size change, the background waits this long before redrawing
BASE_COLOR = (13, 12, 11)        # layer 2's typical color, for blending the dot colors

# Shapes in background-frame design pixels (from the Figma vectors).
MAIN = ((308.5, 845.5), (627.5, 733.5), (627.5, 652), (951, 703), (1102.5, 62.5), (1283.5, 970))
HALFTONE_SHIFT = (-9.5, -13.5)   # the halftone is the main shape moved up and left
# Dust shapes: anchors, each with the bezier controls of the segment to the next anchor (or None).
NOISE_BOTTOM = (((474, 893.53), None), ((674.2, 648.88), None),
                ((755.95, 744.18), ((800.25, 685.05), (889.93, 564.56))),
                ((894.3, 555.59), ((898.67, 546.62), (875.43, 434.79))),
                ((863.26, 380), None), ((1171, 413.64), None), ((1078.11, 917.75), None))
NOISE_TOP = (((587, -188.78), None), ((386.8, 55.88), None),
             ((305.05, -39.43), ((260.75, 19.7), (171.07, 140.2))),
             ((166.7, 149.17), ((162.33, 158.14), (185.57, 269.96))),
             ((197.74, 324.75), None), ((-110, 291.12), None), ((-17.11, -213), None))
# The dust texture only exists inside its (rotated) tile rectangle.
DUST_BOTTOM_BOX = (565, 139, 1386, 960)
DUST_TOP_BOX = (-325, -255.25, 496, 565.75)
DUST_COLOR = (164, 110, 52)
DUST_BANDS = 4
DUST_STRENGTH = 0.7              # matches the speck brightness measured in the Figma render
MAIN_COLORS = ((18, 16, 14), (14, 13, 12))


def _linear(row, origin, size, flip=False):
    """Figma linear gradient position t = a*x + b*y + c in frame coordinates.

    ``row`` is the gradient transform's first row; ``origin``/``size`` the painted box. A box rotated
    by 180 degrees (``flip``) maps its local (u, v) to (origin - u*size).
    """
    ru, rv, rc = row
    (x0, y0), (w, h) = origin, size
    sign = -1.0 if flip else 1.0
    a, b = sign * ru / w, sign * rv / h
    return a, b, rc - a * x0 - b * y0


MAIN_T = _linear((-1.777, 1.0786, 0.3344), (308.5, 62.5), (975, 907.5))
NOISE_BOTTOM_T = _linear((2.8189, -3.4179, 0.7457), (-683.5072, -593.3043), (2749.9709, 2372.0579))
NOISE_TOP_T = _linear((2.8189, -3.4179, 0.7457), (1744.5071, 1298.058), (2749.9709, 2372.0579), flip=True)


# --------------------------------------------------------------------------
# Geometry helpers.


def _flatten(anchors, steps=10):
    points = []
    count = len(anchors)
    for i, (start, controls) in enumerate(anchors):
        end = anchors[(i + 1) % count][0]
        points.append(start)
        if controls:
            (c1x, c1y), (c2x, c2y) = controls
            for k in range(1, steps):
                t = k / steps
                m = 1 - t
                points.append((m ** 3 * start[0] + 3 * m * m * t * c1x + 3 * m * t * t * c2x + t ** 3 * end[0],
                               m ** 3 * start[1] + 3 * m * m * t * c1y + 3 * m * t * t * c2y + t ** 3 * end[1]))
    return points


def _clip(poly, a, b, c):
    """Keep the part of ``poly`` where a*x + b*y + c >= 0 (Sutherland-Hodgman)."""
    out = []
    count = len(poly)
    for i in range(count):
        p, q = poly[i], poly[(i + 1) % count]
        fp, fq = a * p[0] + b * p[1] + c, a * q[0] + b * q[1] + c
        if fp >= 0:
            out.append(p)
        if (fp >= 0) != (fq >= 0):
            k = fp / (fp - fq)
            out.append((p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k))
    return out


def _clip_box(poly, box):
    x0, y0, x1, y1 = box
    for a, b, c in ((1, 0, -x0), (-1, 0, x1), (0, 1, -y0), (0, -1, y1)):
        poly = _clip(poly, a, b, c)
        if len(poly) < 3:
            return []
    return poly


def _bounds(poly):
    xs = [x for x, _y in poly]
    ys = [y for _x, y in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _band(poly, grad, t0, t1):
    """Part of ``poly`` where t0 <= t < t1 for the linear gradient ``grad`` (None = open end)."""
    a, b, c = grad
    if t0 is not None:
        poly = _clip(poly, a, b, c - t0)
    if t1 is not None and len(poly) >= 3:
        poly = _clip(poly, -a, -b, t1 - c)
    return poly if len(poly) >= 3 else []


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v + 0.5))) for v in rgb)


def _mix(base, color, alpha):
    return tuple(b + (c - b) * alpha for b, c in zip(base, color))


def _main_bands():
    """(t0, t1, color) runs where the 8-bit main-shape gradient keeps one color."""
    bands, start, current = [], None, None
    for k in range(0, 1001):
        t = k / 1000
        color = _hex(_mix(MAIN_COLORS[0], MAIN_COLORS[1], t))
        if color != current:
            if current is not None:
                bands.append((start, t, current))
            start, current = (None if k == 0 else t), color
    bands.append((start, None, current))
    return bands


# --------------------------------------------------------------------------
# Hand-cut shape variants.


def _main_variant(rng):
    """The main shape redrawn by hand: corners moved, the spike and notch shifted, long edges kinked."""
    points = [list(p) for p in MAIN]
    points[4][0] += rng.uniform(-18, 18)           # spike tip
    points[4][1] += rng.uniform(-12, 12)
    shift = rng.uniform(-14, 14)                   # notch
    points[1][0] += shift
    points[2][0] += shift
    points[2][1] += rng.uniform(-10, 10)
    points[3][1] += rng.uniform(-8, 8)             # the inner corner under the spike
    moved = [(x + rng.uniform(-5, 5), y + rng.uniform(-5, 5)) for x, y in points]
    out = []
    for i, a in enumerate(moved):
        b = moved[(i + 1) % len(moved)]
        out.append(a)
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        kinks = 2 if length > 450 else (1 if length > 200 else 0)
        for k in sorted(rng.uniform(0.2, 0.8) for _ in range(kinks)):
            kink = rng.uniform(-10, 10)
            out.append((a[0] + dx * k - dy / length * kink, a[1] + dy * k + dx / length * kink))
    return out


def _curve_variant(anchors, rng, reach):
    moves = [(rng.uniform(-reach, reach), rng.uniform(-reach, reach)) for _ in anchors]
    out = []
    for i, (point, controls) in enumerate(anchors):
        dx, dy = moves[i]
        nx, ny = moves[(i + 1) % len(anchors)]
        if controls:
            (c1x, c1y), (c2x, c2y) = controls
            controls = ((c1x + dx * 1.5, c1y + dy * 1.5), (c2x + nx * 1.5, c2y + ny * 1.5))
        out.append(((point[0] + dx, point[1] + dy), controls))
    return out


# --------------------------------------------------------------------------
# Dot patterns (Tk stipple bitmaps), made once per display scale from the design's textures.


class _Patterns:
    def __init__(self, scale):
        self.folder = tempfile.mkdtemp(prefix="d2b-ui-")
        atexit.register(shutil.rmtree, self.folder, True)
        self.files = {}
        self.alpha = {}
        self._halftone(scale)
        self._dust(scale)

    def _write(self, name, rows, width):
        """Save a 1-bit pattern (rows of 0/1) as an X bitmap file Tk can use as a stipple."""
        lines = [f"#define {name}_width {width}", f"#define {name}_height {len(rows)}",
                 f"static unsigned char {name}_bits[] = {{"]
        if np is not None:
            packed = np.packbits(np.asarray(rows, dtype=bool), axis=1, bitorder="little").ravel()
            data = ["0x%02x" % value for value in packed.tolist()]
        else:
            data = []
            for row in rows:
                for start in range(0, width, 8):
                    data.append("0x%02x" % sum(1 << bit for bit in range(8)
                                               if start + bit < width and row[start + bit]))
        lines.append(",".join(data) + "};")
        path = os.path.join(self.folder, name + ".xbm")
        with open(path, "w", encoding="ascii") as handle:
            handle.write("\n".join(lines) + "\n")
        self.files[name] = "@" + path.replace("\\", "/")

    def _halftone(self, scale):
        """A 16 x 16 dot block of the design's halftone texture (small tiles draw much faster)."""
        size = gui_assets.HALFTONE_SIZE
        dark = zlib.decompress(base64.b64decode(gui_assets.HALFTONE))
        cells = 16                                  # dot pitch: 4 design pixels
        pitch = max(3, int(4 * scale + 0.5))
        big = max(2, int(1.5 * scale + 0.5))
        tile = cells * pitch
        dots = [[0] * tile for _ in range(tile)]
        total = count = 0
        for cy in range(cells):
            for cx in range(cells):
                m = max(dark[(cy * 4 + y) * size + cx * 4 + x] for y in range(4) for x in range(4))
                if m < 20:
                    continue
                dot = big if m >= 90 else 1   # darker dots are bigger, as in a halftone
                total, count = total + m, count + 1
                for y in range(dot):
                    for x in range(dot):
                        dots[cy * pitch + y][cx * pitch + x] = 1
        self._write("halftone", dots, tile)
        self.alpha["halftone"] = (total / count / 255.0) if count else 0.0
        self.halftone_tile = tile

    def _dust(self, scale):
        """Two speck layers (soft and bright) from the dust block, averaged down to screen pixels
        the way Figma draws it at this scale."""
        size = gui_assets.DUST_SIZE
        raw = zlib.decompress(base64.b64decode(gui_assets.DUST))
        tile = max(8, int(gui_assets.DUST_DESIGN_SIZE * scale + 0.5))
        edges = [i * size // tile for i in range(tile + 1)]
        if np is not None:
            source = np.frombuffer(raw, np.uint8).reshape(size, size).astype(np.float32)
            cells = np.add.reduceat(np.add.reduceat(source, edges[:-1], axis=0), edges[:-1], axis=1)
            spans = np.diff(edges).astype(np.float32)
            alpha = (cells / (spans[:, None] * spans[None, :])).tolist()
        else:  # nearest sample
            alpha = [[raw[((edges[y] + edges[y + 1]) // 2) * size + (edges[x] + edges[x + 1]) // 2]
                      for x in range(tile)] for y in range(tile)]
        for name, (low, high) in {"dust_soft": (10, 64), "dust_bright": (64, 256)}.items():
            values = [a for row in alpha for a in row if low <= a < high]
            self._write(name, [[1 if low <= a < high else 0 for a in row] for row in alpha], tile)
            self.alpha[name] = (sum(values) / len(values) / 255.0) if values else 0.0
        self.dust_tile = tile



# --------------------------------------------------------------------------
# Layer 2 picture.


def resample(src, sx, sy, xs, ys):
    """Bilinear samples of ``src`` (height x width x channels, uint8) at target-pixel centres
    ``xs`` x ``ys``, the source being stretched by (sx, sy) target pixels per source pixel."""
    sh, sw = src.shape[:2]
    fx = np.clip(xs / sx - 0.5, 0, sw - 1)
    fy = np.clip(ys / sy - 0.5, 0, sh - 1)
    i0 = fx.astype(np.int32)
    i1 = np.minimum(i0 + 1, sw - 1)
    wx = (fx - i0).astype(np.float32)[None, :, None]
    out = np.empty((len(ys), len(xs), src.shape[2]), np.uint8)
    for start in range(0, len(ys), 192):
        rows = fy[start:start + 192]
        j0 = rows.astype(np.int32)
        j1 = np.minimum(j0 + 1, sh - 1)
        wy = (rows - j0).astype(np.float32)[:, None, None]
        top = src[j0][:, i0] * (1 - wx) + src[j0][:, i1] * wx
        bottom = src[j1][:, i0] * (1 - wx) + src[j1][:, i1] * wx
        out[start:start + len(rows)] = (top * (1 - wy) + bottom * wy + 0.5).astype(np.uint8)
    return out


def _photo(master, pixels):
    height, width = pixels.shape[:2]
    return tk.PhotoImage(master=master, data=b"P6 %d %d 255 " % (width, height) + pixels.tobytes(), format="ppm")


# --------------------------------------------------------------------------


class _Surface:
    """One canvas the background is painted on. ``areas()`` lists where it can be seen, as
    (x0, y0, x1, y1) in the canvas's on-screen pixels; None means everywhere."""

    __slots__ = ("canvas", "areas", "ox", "oy", "dirty", "items", "looks")

    def __init__(self, canvas, areas):
        self.canvas, self.areas = canvas, areas
        self.ox = self.oy = None
        self.dirty = True
        # Polygons are reused from snap to snap (moving an item is much cheaper than making one);
        # ``looks`` remembers each one's fill/pattern so unchanged options are not sent again.
        self.items = []
        self.looks = []


class Backdrop:
    """Paints and animates the background on the attached canvases.

    ``title_height`` is the custom title bar's height and ``left`` the sidebar width (screen pixels):
    the background frame starts below the bar, and the picture is only rendered right of the sidebar.
    """

    def __init__(self, root, *, title_height, left):
        self.root = root
        self.title_height = title_height
        self.left = left
        self.surfaces: list = []
        self.pose = 0
        self.step = 0
        self.frozen = False
        self.size = None
        self.display = None            # the one picture image shown on every canvas
        self.levels = {}               # photo opacity -> source image (reduced resolution when animating)
        self._zoom = 1
        self._queue = []
        self._render_job = None
        self._settle_job = None
        self._holds = set()
        self._rendered = None
        self.patterns = _Patterns(gui_theme.SCALE)
        self._build_poses()
        root.bind("<Configure>", self._on_root_configure, add="+")
        from .gui_widgets import BoilClock
        BoilClock.register(self)

    # -- the boil clock's interface ----------------------------------------------------------

    def winfo_exists(self):
        return self.root.winfo_exists()

    def winfo_toplevel(self):
        return self.root

    def advance(self):
        if self.frozen or not gui_theme.motion_enabled():
            return
        self.pose = self._rng.choice([i for i in range(POSES) if i != self.pose])
        self.step = (self.step + 1) % len(PHOTO_CYCLE)
        self._show_level()
        for surface in self.surfaces:
            if surface.canvas.winfo_ismapped():
                self._draw_vectors(surface)
            else:
                surface.dirty = True

    def motion_changed(self):
        """Discard unused opacity frames and repaint once in the new mode."""
        if self._render_job is not None:
            self.root.after_cancel(self._render_job)
            self._render_job = None
        self._queue = []
        if not self.frozen:
            self._schedule_render(0)

    def set_frozen(self, frozen, reason="drag"):
        """Hold the background still while the window is dragged or its size is settling; catch up
        afterwards. Several reasons can hold at once; it resumes when none does."""
        if frozen:
            self._holds.add(reason)
        else:
            self._holds.discard(reason)
        held = bool(self._holds)
        if held == self.frozen:
            return
        self.frozen = held
        frozen = held
        if frozen:
            # Shapes and picture drawn for the old size would only slow every step of the drag down
            # (Tk redraws them each time the window changes), so they step aside until the size
            # settles. The picture is within a few levels of the flat color it leaves behind.
            for surface in self.surfaces:
                surface.canvas.itemconfigure("bd_vec", state="hidden")
                surface.canvas.itemconfigure("bd_img", state="hidden")
                surface.looks = [None] * len(surface.looks)
                surface.dirty = True
        else:
            self.root.after_idle(self._catch_up)

    def _catch_up(self):
        if self.frozen:
            return
        size = (self.root.winfo_width(), self.root.winfo_height())
        if size != self._rendered:
            self._schedule_render(1)
        self.sync(redraw=True)

    # -- poses -------------------------------------------------------------------------------

    def _build_poses(self):
        self._rng = random.Random(1717)
        rng = random.Random(4242)
        bands = _main_bands()
        pat = self.patterns
        halftone_color = _hex(gui_theme.screen_rgb(_mix(BASE_COLOR, (0, 0, 0), pat.alpha["halftone"])))
        self.poses = []
        for index in range(POSES):
            rest = index == 0   # the first pose is the design itself
            main = list(MAIN) if rest else _main_variant(rng)
            # The halftone follows the main shape's variant (so its rim keeps its shape), with a
            # little play of its own that makes the rim breathe.
            play = 0 if rest else 2.5
            halftone = [(x + HALFTONE_SHIFT[0] + rng.uniform(-play, play), y + HALFTONE_SHIFT[1] + rng.uniform(-play, play))
                        for x, y in main]
            bottom = NOISE_BOTTOM if rest else _curve_variant(NOISE_BOTTOM, rng, 10)
            top = NOISE_TOP if rest else _curve_variant(NOISE_TOP, rng, 10)
            # Bottom to top, as in the design: dust, halftone, main shape, the second dust shape.
            shapes = self._dust_bands(_flatten(bottom), DUST_BOTTOM_BOX, NOISE_BOTTOM_T)
            # Only the rim the main shape leaves uncovered can be seen: the strips swept between
            # matching edges of the two outlines.
            count = len(main)
            rim = [[halftone[i], halftone[(i + 1) % count], main[(i + 1) % count], main[i]] for i in range(count)]
            shapes += [(quad, halftone_color, "halftone") for quad in rim]
            shapes += self._main_pieces(main, bands)
            shapes += self._dust_bands(_flatten(top), DUST_TOP_BOX, NOISE_TOP_T)
            offsets = {
                "dust": (0, 0) if rest else (rng.randrange(pat.dust_tile), rng.randrange(pat.dust_tile)),
                "halftone": (0, 0) if rest else (rng.choice((-1, 0, 1)), rng.choice((-1, 0, 1))),
            }
            self.poses.append(([(points, _bounds(points), color, pattern) for points, color, pattern in shapes],
                               offsets))

    @staticmethod
    def _main_pieces(main, bands):
        """The main shape as solid gradient bands (one 8-bit color each; Figma's one-level
        dithering grain is left out: it cannot be seen at normal brightness and costs drawing time)."""
        return [(piece, gui_theme.screen(color), None) for piece, color in
                ((_band(main, MAIN_T, t0, t1), color) for t0, t1, color in bands) if piece]

    def _dust_bands(self, outline, box, grad):
        """Dust inside ``outline``: alpha bands of the fading gradient, two speck layers each."""
        shape = _clip_box(outline, box)
        if not shape:
            return []
        pieces = []
        for k in range(DUST_BANDS + 1):
            t0 = None if k == 0 else (k - 1) / DUST_BANDS
            t1 = 0.0 if k == 0 else k / DUST_BANDS
            alpha = 1.0 if k == 0 else 1.0 - (k - 0.5) / DUST_BANDS
            piece = _band(shape, grad, t0, t1)
            if not piece:
                continue
            for layer in ("dust_soft", "dust_bright"):
                mixed = _mix(BASE_COLOR, DUST_COLOR, self.patterns.alpha[layer] * alpha * DUST_STRENGTH)
                color = _hex(gui_theme.screen_rgb(mixed))
                if color != _hex(gui_theme.screen_rgb(BASE_COLOR)):
                    pieces.append((piece, color, layer))
        return pieces

    # -- canvases ----------------------------------------------------------------------------

    def attach(self, canvas, areas=None):
        surface = _Surface(canvas, areas)
        self.surfaces.append(surface)
        canvas.create_image(0, 0, anchor="nw", tags=("bd", "bd_img"))
        canvas.bind("<Map>", lambda _e: self.sync(canvas, redraw=True), add="+")
        return canvas

    def _offset(self, canvas):
        """Canvas coordinates of the background frame's origin are (-ox, -oy)."""
        root = self.root
        ox = canvas.winfo_rootx() - root.winfo_rootx() - canvas.canvasx(0)
        oy = canvas.winfo_rooty() - root.winfo_rooty() - self.title_height - canvas.canvasy(0)
        return ox, oy

    def sync(self, canvas=None, redraw=False):
        """Keep the background aligned to the window after a canvas moved, scrolled or changed."""
        for surface in self.surfaces:
            if canvas is not None and surface.canvas is not canvas:
                continue
            if not surface.canvas.winfo_ismapped():
                surface.dirty = True
                continue
            ox, oy = self._offset(surface.canvas)
            if redraw or surface.dirty or (ox, oy) != (surface.ox, surface.oy):
                surface.ox, surface.oy = ox, oy
                self._place_image(surface)
                self._draw_vectors(surface)

    def _scale(self):
        width = max(1, self.root.winfo_width())
        height = max(1, self.root.winfo_height() - self.title_height)
        return width / DESIGN_W, height / DESIGN_H

    def _place_image(self, surface):
        canvas = surface.canvas
        canvas.itemconfigure("bd_img", image=self.display or "", state="hidden" if self.frozen else "normal")
        canvas.coords("bd_img", self.left - surface.ox, -surface.oy)
        canvas.tag_lower("bd_img")

    def _draw_vectors(self, surface):
        if self.frozen:
            surface.dirty = True
            return
        surface.dirty = False
        canvas, ox, oy = surface.canvas, surface.ox, surface.oy
        sx, sy = self._scale()
        shapes, offsets = self.poses[self.pose]
        files = self.patterns.files
        # Pattern origins are given relative to the window (#x,y), so every canvas lines up.
        origin = {}
        for kind, tile in (("dust", self.patterns.dust_tile), ("halftone", self.patterns.halftone_tile)):
            dx, dy = offsets[kind]
            origin[kind] = "#%d,%d" % (dx % tile, (self.title_height + dy) % tile)
        # Where this canvas shows the background, in background pixels (fixed to the window, so a
        # scrolled page only needs its gaps recomputed).
        left = canvas.winfo_rootx() - self.root.winfo_rootx()
        top = canvas.winfo_rooty() - self.root.winfo_rooty() - self.title_height
        rects = surface.areas() if surface.areas else [(0, 0, canvas.winfo_width(), canvas.winfo_height())]
        views = [(left + x0 - 1, top + y0 - 1, left + x1 + 1, top + y1 + 1)
                 for x0, y0, x1, y1 in rects if x1 > x0 and y1 > y0]
        items, looks = surface.items, surface.looks
        used = 0
        for points, (bx0, by0, bx1, by1), color, pattern in shapes:
            box = (bx0 * sx, by0 * sy, bx1 * sx, by1 * sy)
            look = (color, files[pattern] if pattern else "", origin[pattern.split("_")[0]] if pattern else "0,0")
            for view in views:
                if box[2] < view[0] or box[0] > view[2] or box[3] < view[1] or box[1] > view[3]:
                    continue
                poly = _clip_box([(x * sx, y * sy) for x, y in points], view)
                if not poly:
                    continue
                coords = []
                for x, y in poly:
                    coords += (x - ox, y - oy)
                if used < len(items):
                    canvas.coords(items[used], coords)
                    if looks[used] != look:
                        canvas.itemconfigure(items[used], fill=look[0], stipple=look[1], offset=look[2],
                                             state="normal")
                        looks[used] = look
                else:
                    items.append(canvas.create_polygon(coords, fill=look[0], stipple=look[1], offset=look[2],
                                                       outline="", tags=("bd", "bd_vec")))
                    looks.append(look)
                used += 1
        for index in range(used, len(items)):
            if looks[index] is not None:
                canvas.itemconfigure(items[index], state="hidden")
                looks[index] = None
        canvas.tag_lower("bd_vec")
        canvas.tag_lower("bd_img")

    # -- the picture --------------------------------------------------------------------------

    def _level(self):
        return PHOTO_CYCLE[self.step] if gui_theme.motion_enabled() else 100

    def _show_level(self):
        source = self.levels.get(self._level())
        if self.display is not None and source is not None:
            self.display.tk.call(self.display.name, "copy", source.name, "-zoom", self._zoom, self._zoom)

    def _on_root_configure(self, event):
        if event.widget is not self.root:
            return
        size = (event.width, event.height)
        if size == self.size:
            if not self.frozen:
                self.sync()
            return
        # Any size change (maximize, restore, snap, a drag): the background steps aside until the
        # size has been still for a moment, so the window's own controls repaint first instead of
        # waiting behind the background work (which is what leaves stale pixels on screen).
        self.size = size
        self.set_frozen(True, "settle")
        if self._settle_job is not None:
            self.root.after_cancel(self._settle_job)
        self._settle_job = self.root.after(SETTLE_MS, self._settled)

    def _settled(self):
        self._settle_job = None
        self.set_frozen(False, "settle")

    def _schedule_render(self, delay):
        if self._render_job is not None:
            self.root.after_cancel(self._render_job)
        self._render_job = self.root.after(delay, self._render)

    def _render(self):
        """New picture for the current window size: the visible opacity first, the others after."""
        self._render_job = None
        if np is None or self.frozen:
            return
        width = self.root.winfo_width()
        height = self.root.winfo_height() - self.title_height
        if width <= self.left or height <= 0:
            return
        self._rendered = (width, height + self.title_height)
        # The picture is soft and within a few levels of flat, so it is kept at half (large windows:
        # a third) of the screen resolution and enlarged when shown; that keeps each render short.
        area = (width - self.left) * height
        self._zoom = 1 if not gui_theme.motion_enabled() else (2 if area <= 1_600_000 else 3)
        self.display = tk.PhotoImage(master=self.root, width=width - self.left, height=height)
        for surface in self.surfaces:
            if surface.ox is not None:
                self._place_image(surface)
        self.levels = {}
        current = self._level()
        self._queue = [current] + [level for level in (100, 80, 60, 40, 20) if level != current]
        if self._zoom == 1:
            self._queue = [100]
        self._render_level()

    def _render_level(self):
        if not self._queue or self.display is None:
            return
        level = self._queue.pop(0)
        blob = gui_assets.L2_LEVELS[level]
        source_w, source_h = gui_assets.L2_SIZE
        source = np.frombuffer(zlib.decompress(base64.b64decode(blob)), np.uint8).reshape(source_h, source_w, 3)
        width = int(self.display.cget("width"))
        height = int(self.display.cget("height"))
        z = self._zoom
        sx, sy = self._scale()
        xs = self.left + np.arange((width + z - 1) // z) * z + z / 2.0
        ys = np.arange((height + z - 1) // z) * z + z / 2.0
        pixels = resample(source, sx / gui_assets.L2_SCALE, sy / gui_assets.L2_SCALE, xs, ys)
        self.levels[level] = _photo(self.root, gui_theme.screen_pixels(pixels))
        if level == self._level():
            self._show_level()
        if self._queue:
            self._render_job = self.root.after(30, self._render_level)
