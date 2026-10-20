"""Portrait interaction and recovery UI contracts retained by the redesign."""
import tkinter as tk

from agr import gui_characters, gui_theme, gui_widgets, update_window


def test_portrait_keyboard_teams_and_unknown_hero_fallback():
    root = tk.Tk()
    root.geometry("540x700+30000+30000")
    gui_widgets._CORNER_CACHE.clear()
    gui_theme.initialize(root)
    heroes = tk.StringVar(root)
    empty = tk.StringVar(root, value="0")
    widget = gui_characters.CharacterChecklist(root, heroes=heroes, empty=empty)
    widget.pack(fill="both", expand=True)
    try:
        widget.load({"heroes": [dict(hero_id=2, team=2), dict(hero_id=11, team=3),
                                dict(hero_id=999, team=3)]})
        root.update()
        assert len(widget._groups) == 2
        group = next(g for g in widget._groups if len(g._heroes) == 2)
        group.focus_force()
        root.update()
        chosen = group._heroes[group._focus]["id"]
        group.event_generate("<space>")
        root.update()
        assert not widget._rows[chosen]["variable"].get()
        group.event_generate("<Right>")
        group.event_generate("<Return>")
        root.update()
        assert sum(row["variable"].get() for row in widget._rows.values()) == 1
        assert gui_characters.hero_icon(widget, 999, 46) is None
        widget.set_busy(True)
        previous = heroes.get()
        group.event_generate("<space>")
        assert heroes.get() == previous
        widget.clear()
        root.update()
        assert not widget._list.winfo_manager()
    finally:
        root.destroy()
        gui_widgets._CORNER_CACHE.clear()


def test_portrait_images_belong_to_each_tk_interpreter():
    for _ in range(2):
        root = tk.Tk()
        try:
            image = gui_characters.hero_icon(root, 2, 46)
            label = tk.Label(root, image=image)
            label.pack()
            root.update_idletasks()
            assert image.width() > 0
        finally:
            root.destroy()


def test_recovery_status_copy_fallback_and_close_contract(monkeypatch):
    root = tk.Tk()
    root.geometry("540x700+30000+30000")
    gui_widgets._CORNER_CACHE.clear()
    failures = []
    root.report_callback_exception = lambda *args: failures.append(args)
    try:
        window = update_window.UpdateWindow(root=root, recover=True)
        root.update()
        assert window.icon.width() > 0 and window._emblem.width() > 0
        assert window.wordmark.cget("text") == "RenderLock"
        window.status.configure(text="Bounded recovery status")
        assert window.status.cget("text") == "Bounded recovery status"
        window.progress.stop()
        window.progress.start()
        window.progress.grid_remove()
        detail = "Owned test diagnostic\nSecond line"
        window.show_error(detail, root.destroy)
        root.update()
        assert root.title() == "RenderLock | Update needs attention"
        assert window.details.get("1.0", "end-1c") == detail
        monkeypatch.setattr(update_window, "copy_text", lambda *args: False)
        window.copy_button.invoke()
        root.update()
        assert window.details.get("sel.first", "sel.last") == detail
        assert not window.copy_button._done
        copied = []
        monkeypatch.setattr(update_window, "copy_text", lambda _, text: copied.append(text) or True)
        window.copy_button.invoke()
        assert copied == [detail] and window.copy_button._done
        assert not failures
    finally:
        if root.winfo_exists():
            root.destroy()
        gui_widgets._CORNER_CACHE.clear()
