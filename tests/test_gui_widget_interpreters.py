"""Rounded recovery controls use images owned by their actual Tk interpreter."""
from types import SimpleNamespace
import tkinter as tk

import pytest

from agr import gui_theme, gui_widgets


@pytest.fixture
def roots(monkeypatch):
    live = []
    # Isolate tests only: never clear the cache between roots within a test.
    gui_widgets._CORNER_CACHE.clear()
    monkeypatch.setattr(gui_theme, "SCALE", 1.0)

    def create():
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            pytest.skip(f"Tk display unavailable: {exc}")
        root.withdraw()
        root.configure(background=gui_theme.palette()["BG"])
        live.append(root)
        return root

    yield create
    for root in reversed(live):
        try:
            exists = root.winfo_exists()
        except tk.TclError:  # This test already destroyed the first interpreter.
            continue
        if exists:
            root.destroy()
    gui_widgets._CORNER_CACHE.clear()


def control(parent, kind, monkeypatch, *, theme=None, **options):
    theme = theme or gui_theme.palette()
    if kind == "Card":
        widget = gui_widgets.Card(parent, theme=theme, **options)
    elif kind == "RoundedButton":
        # Its implicit Tk font root is separate from corner-image ownership.
        with monkeypatch.context() as context:
            context.setattr(tk, "_default_root", parent._root())
            widget = gui_widgets.RoundedButton(parent, text="Continue", theme=theme, **options)
    else:
        value = tk.StringVar(parent, value="Owned diagnostic")
        widget = gui_widgets.RoundedEntry(parent, variable=value, theme=theme,
                                         font=gui_theme.FONT, **options)
        widget._test_value = value
        widget.configure(state="readonly")
    redraw(widget)
    return widget


def redraw(widget):
    # Withdrawn windows do not receive mapped geometry. Exercise the controls'
    # real drawing paths at explicit dimensions without showing/focusing them.
    if isinstance(widget, gui_widgets.Card):
        widget._redraw(SimpleNamespace(width=240, height=100))
    else:
        widget._draw()


def corners(widget):
    canvas = widget._shape if isinstance(widget, gui_widgets.Card) else widget
    items = [item for item in canvas.find_all() if canvas.type(item) == "image"]
    assert len(items) == 4
    names = [canvas.itemcget(item, "image") for item in items]
    assert len(set(names)) == 4
    for item, name in zip(items, names):
        # These Tcl calls require an actual photo in this canvas's interpreter;
        # a Python cache entry or another root's photo cannot satisfy them.
        assert canvas.tk.call("image", "type", name) == "photo"
        assert canvas.tk.getint(canvas.tk.call("image", "width", name)) == widget._radius
        assert canvas.tk.getint(canvas.tk.call("image", "height", name)) == widget._radius
        assert canvas.itemcget(item, "anchor") == "nw"
    return names


@pytest.mark.parametrize("kind", ["Card", "RoundedButton", "RoundedEntry"])
@pytest.mark.parametrize("destroy_first", [False, True], ids=["simultaneous", "sequential"])
def test_recovery_controls_construct_in_independent_interpreters(roots, monkeypatch, kind,
                                                                destroy_first):
    first = roots()
    first_widget = control(first, kind, monkeypatch)
    first_names = corners(first_widget)
    if destroy_first:
        first.destroy()
    second = roots()
    assert second.tk is not first.tk
    assert set(first_names).isdisjoint(second.tk.call("image", "names"))
    second_widget = control(second, kind, monkeypatch)
    second_names = corners(second_widget)
    assert set(first_names).isdisjoint(second_names)
    redraw(second_widget)
    assert corners(second_widget) == second_names
    if not destroy_first:
        # Going back to the first root must keep its own images, too.
        redraw(first_widget)
        assert corners(first_widget) == first_names
        assert first.state() == "withdrawn"
    assert second.state() == "withdrawn"


@pytest.mark.parametrize("kind", ["Card", "RoundedButton", "RoundedEntry"])
def test_same_interpreter_reuses_photos_across_frames_and_toplevels(roots, monkeypatch, kind):
    root = roots()
    first_parent = tk.Frame(root, background=gui_theme.palette()["BG"])
    second_parent = tk.Frame(root, background=gui_theme.palette()["BG"])
    window = tk.Toplevel(root)
    window.withdraw()
    window.configure(background=gui_theme.palette()["BG"])
    first = control(first_parent, kind, monkeypatch)
    names = corners(first)
    second = control(second_parent, kind, monkeypatch)
    third = control(window, kind, monkeypatch)
    assert first.tk is second.tk is third.tk
    assert corners(second) == corners(third) == names
    first.destroy()
    redraw(second)
    redraw(third)
    assert corners(second) == corners(third) == names
    assert root.state() == window.state() == "withdrawn"


@pytest.mark.parametrize("theme", gui_theme.SKINS.values(), ids=gui_theme.SKINS)
def test_readonly_entry_keeps_value_colors_and_geometry(roots, monkeypatch, theme):
    root = roots()
    field = control(root, "RoundedEntry", monkeypatch, theme=theme, background=theme["BG"])
    assert field.entry.cget("state") == "readonly"
    assert field.entry.cget("readonlybackground") == theme["FIELD"]
    assert field.entry.cget("foreground") == theme["TEXT"]
    assert field.entry.get() == "Owned diagnostic"
    field.entry.insert(0, "must not edit")
    assert field.entry.get() == "Owned diagnostic"
    assert field.coords(field._window) == [10.0, 17.0]
    assert float(field.itemcget(field._window, "width")) == 100.0
    names = corners(field)
    assert [field.coords(item) for item in field.find_withtag("shape")
            if field.type(item) == "image"] == [[0.0, 0.0], [111.0, 0.0],
                                               [0.0, 25.0], [111.0, 25.0]]
    field.configure(state="disabled")
    assert field.entry.cget("state") == "disabled"
    field.configure(state="readonly")
    assert field.entry.cget("state") == "readonly"
    assert corners(field) == names


@pytest.mark.parametrize("theme", gui_theme.SKINS.values(), ids=gui_theme.SKINS)
@pytest.mark.parametrize("style", ["default", "no-edge", "custom"])
def test_card_styles_keep_corner_pixels_colors_and_geometry(roots, monkeypatch, theme, style):
    options = {"background": theme["BG"]}
    if style == "no-edge":
        options["edge"] = None
    elif style == "custom":
        options.update(fill=theme["SURFACE"], edge=theme["ACCENT"], radius=9)
    root = roots()
    card = control(root, "Card", monkeypatch, theme=theme, **options)
    fill = options.get("fill", theme["CARD"])
    edge = options.get("edge", theme["CARD_EDGE"])
    assert card.body.cget("background") == fill
    names = corners(card)
    rows = gui_widgets._corner_rows(card._radius, fill, edge, theme["BG"])
    r = card._radius
    assert [card._shape.coords(item) for item in card._shape.find_withtag("shape")
            if card._shape.type(item) == "image"] == [[0.0, 0.0], [240.0 - r, 0.0],
                                                     [0.0, 100.0 - r], [240.0 - r, 100.0 - r]]
    for name, horizontal, vertical in zip(names, (False, True, False, True),
                                         (False, False, True, True)):
        for y in range(r):
            for x in range(r):
                color = rows[r - 1 - y if vertical else y][r - 1 - x if horizontal else x]
                expected = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
                assert tuple(card.tk.call(name, "get", x, y)) == expected
    redraw(card)
    assert corners(card) == names
