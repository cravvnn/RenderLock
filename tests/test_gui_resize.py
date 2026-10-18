"""Resize work stays bounded and preserves the designer's exact border colors."""
import tkinter as tk
from tkinter import ttk

import pytest

from agr import gui_layout, gui_theme
from agr.gui_widgets import IdleUpdate, ResizeRedraw, _strip_colors, outline_right_rounded


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    root.geometry("600x400+30000+30000")
    root.deiconify()
    root.update()
    yield root
    root.destroy()


@pytest.fixture
def surface(tk_root):
    frame = tk.Frame(tk_root, width=300, height=250)
    frame.place(x=0, y=0, width=300, height=250)
    tk_root.update()
    yield frame
    frame.destroy()
    tk_root.update()


def test_resize_coalesces_burst_and_ignores_position_only_configure(surface):
    calls = []
    redraw = ResizeRedraw(surface, lambda w, h: calls.append((w, h)))
    # Tk may deliver a burst before the idle redraw; use the final actual size.
    for width in (100, 200, 300):
        surface.event_generate("<Configure>", width=width, height=250)
    assert calls == []
    surface.update()
    assert calls == [(300, 250)]
    surface.place_configure(x=4, y=7)
    surface.update()
    assert calls == [(300, 250)]
    surface.place_configure(width=320, height=270)
    surface.update()
    assert calls == [(300, 250), (320, 270)]


def test_destroy_cancels_pending_idle_callback(surface):
    calls = []
    child = tk.Frame(surface)
    update = IdleUpdate(child, lambda: calls.append(True))
    update()
    pending = update._job
    child.destroy()
    assert pending not in child.tk.call("after", "info")
    surface.update()
    assert calls == []


@pytest.mark.parametrize("skin", gui_theme.SKINS.values())
def test_linear_gradient_runs_match_every_original_pixel(skin):
    top, bottom = skin["RAIL_EDGE_TOP"], skin["RAIL_EDGE_BOTTOM"]
    for length in (1, 2, 17, 731, 1440):
        def color(index):
            t = -0.0269 + 0.6237 * index / length + 0.0134
            return gui_layout.gradient_color(((0.0, top), (1.0, bottom)), t)
        calls = []
        actual = _strip_colors(length, lambda i: (calls.append(i), color(i))[1], True)
        assert actual == [color(i) for i in range(length)]
        if length == 1440:
            assert len(calls) < length // 4


def test_height_resize_reuses_horizontal_border_images(surface):
    def color(u, v):
        return gui_layout.gradient_color(((0.0, "#313131"), (1.0, "#2e2e2e")), .6 * v + .02 * u)
    pieces = outline_right_rounded(surface, 8, "#252321", "#211f1d", color, linear_gradient=True)
    surface.update()
    original = {name: str(piece.image) for name, piece in pieces.items()}
    surface.place_configure(height=280)
    surface.update()
    for name in ("top", "bottom", "ne", "se"):
        assert str(pieces[name].image) == original[name]
    assert str(pieces["right"].image) != original["right"]
    image = pieces["right"].image
    for row in range(image.height()):
        expected = color(1.0, (row + 8) / 280)
        assert image.get(0, row) == tuple(int(expected[i:i + 2], 16) for i in (1, 3, 5))


def test_scrollbar_and_content_width_settle_after_overflow_changes(surface):
    shell, content = gui_layout._page(surface)
    shell.pack(fill="both", expand=True)
    block = tk.Frame(content, height=600, width=50)
    block.grid(row=0, column=0, sticky="ew")
    surface.update()
    canvas = next(w for w in shell.winfo_children() if isinstance(w, tk.Canvas))
    bar = next(w for w in shell.winfo_children() if isinstance(w, ttk.Scrollbar))
    assert bar.winfo_ismapped()
    assert content.winfo_width() == canvas.winfo_width()
    canvas.yview_moveto(0.5)
    assert canvas.yview()[0] > 0
    block.configure(height=40)
    surface.update()
    assert not bar.winfo_ismapped()
    assert canvas.yview()[0] == 0
    assert content.winfo_width() == canvas.winfo_width()
