"""Source dependencies stay visible and survive busy-state restoration."""
from types import SimpleNamespace
import tkinter as tk

import pytest

from agr import gui_layout, gui_theme


def test_preparation_controls_follow_source_without_changing_user_choices():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    try:
        gui_theme.initialize(root)
        frame = tk.Frame(root, background=gui_theme.CARD)
        app = SimpleNamespace(values={"asset_source": tk.StringVar(root, value="installed")},
                              busy=False, mutable_widgets=[])
        cloth = tk.StringVar(root, value="1")
        effects = tk.StringVar(root, value="1")
        preparation = gui_layout._check(app, frame, "Prepare cloth", cloth, installed_only=True)
        inclusion = gui_layout._check(app, frame, "Include saved effects", effects)
        gui_layout.refresh_asset_source_controls(app)
        assert preparation.cget("state") == inclusion.cget("state") == "normal"
        app.values["asset_source"].set("prepared")
        gui_layout.refresh_asset_source_controls(app)
        preparation._toggle()
        assert preparation.cget("state") == "disabled" and cloth.get() == "1"
        assert inclusion.cget("state") == "normal"
        inclusion._toggle()
        assert effects.get() == "0"
        app.values["asset_source"].set("installed")
        app.busy = True
        gui_layout.refresh_asset_source_controls(app)
        assert preparation.cget("state") == "disabled"
        app.busy = False
        gui_layout.refresh_asset_source_controls(app)
        assert preparation.cget("state") == "normal" and cloth.get() == "1"
    finally:
        root.destroy()


def test_source_handler_can_run_before_later_pages_have_registered_controls():
    app = SimpleNamespace(values={"asset_source": SimpleNamespace(get=lambda: "prepared")})
    gui_layout.refresh_asset_source_controls(app)
