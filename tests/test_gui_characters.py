"""Character choices retain the CLI's strict all/subset/explicit-none meaning."""
import tkinter as tk

import pytest

from agr import gui_theme, gui_widgets
from agr.gui_characters import CharacterChecklist


@pytest.fixture(scope="module")
def character_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    root.geometry("540x700+30000+30000")
    # Image objects belong to one Tk interpreter. Other GUI test modules may
    # have destroyed their interpreter while retaining the shared shape cache.
    gui_widgets._CORNER_CACHE.clear()
    gui_theme.initialize(root)
    root.deiconify()
    root.update()
    yield root
    root.destroy()
    gui_widgets._CORNER_CACHE.clear()


@pytest.fixture
def checklist(character_root):
    surface = tk.Frame(character_root, background=gui_theme.CARD)
    surface.pack(fill="both", expand=True)
    heroes = tk.StringVar(master=character_root, value="")
    empty = tk.StringVar(master=character_root, value="0")
    widget = CharacterChecklist(surface, heroes=heroes, empty=empty)
    widget.pack(fill="x")
    character_root.update()
    yield widget, heroes, empty
    surface.destroy()
    character_root.update()


def _roster():
    return {"heroes": [{"hero_id": 2, "hero_name": "Seven", "player_count": 1},
                       {"hero_id": 11, "hero_name": "Dynamo", "player_count": 1},
                       {"hero_id": 50, "hero_name": "Pocket", "player_count": 1}]}


def _selected(widget):
    return {hero_id for hero_id, row in widget._rows.items() if row["variable"].get()}


def _click(widget, hero_id):
    widget._rows[hero_id]["checkbox"]._toggle()


def test_inspect_hint_and_no_roster_all_reset(checklist):
    widget, heroes, empty = checklist
    assert not widget.has_roster
    assert "Inspect" in widget._notice.get()
    assert widget.none_button.cget("state") == "disabled"
    heroes.set("2")
    widget.all_button._invoke()
    assert (heroes.get(), empty.get()) == ("", "0")
    widget.none_button._invoke()
    assert empty.get() == "0"


def test_real_checks_transition_all_subset_none_and_back(checklist):
    widget, heroes, empty = checklist
    widget.load(_roster())
    assert widget.has_roster and _selected(widget) == {2, 11, 50}
    assert (heroes.get(), empty.get()) == ("", "0")
    _click(widget, 11)
    assert (heroes.get(), empty.get()) == ("2,50", "0")
    assert _selected(widget) == {2, 50}
    _click(widget, 50)
    _click(widget, 2)
    assert (heroes.get(), empty.get()) == ("", "1")
    assert _selected(widget) == set()
    _click(widget, 11)
    assert (heroes.get(), empty.get()) == ("11", "0")
    widget.all_button._invoke()
    assert (heroes.get(), empty.get()) == ("", "0")
    assert _selected(widget) == {2, 11, 50}
    widget.none_button._invoke()
    assert (heroes.get(), empty.get()) == ("", "1")


def test_saved_subset_and_none_survive_reinspect(checklist):
    widget, heroes, empty = checklist
    heroes.set("50,2")
    widget.load(_roster())
    widget.load(_roster())
    assert heroes.get() == "50,2"
    assert _selected(widget) == {2, 50}
    widget.none_button._invoke()
    widget.load(_roster())
    assert (heroes.get(), empty.get()) == ("", "1")
    assert not _selected(widget)


def test_external_raw_ids_sync_and_clear_explicit_none(checklist):
    widget, heroes, empty = checklist
    widget.load(_roster())
    widget.none_button._invoke()
    heroes.set("11,2,11")
    assert empty.get() == "0"
    assert heroes.get() == "11,2,11"
    assert _selected(widget) == {2, 11}
    heroes.set("")
    assert _selected(widget) == {2, 11, 50}
    empty.set("1")
    assert not _selected(widget)


def test_missing_explicit_id_remains_visible_and_is_not_dropped(checklist):
    widget, heroes, empty = checklist
    heroes.set("999,2")
    widget.load(_roster())
    assert heroes.get() == "999,2"
    assert _selected(widget) == {2}
    assert "outside this list (kept): 999" in widget._notice.get()
    _click(widget, 2)
    assert (heroes.get(), empty.get()) == ("999", "0")
    _click(widget, 11)
    assert heroes.get() == "11,999"
    widget.load(_roster())
    assert heroes.get() == "11,999"
    widget.all_button._invoke()
    assert heroes.get() == "" and "999" not in widget._notice.get()


def test_saved_package_roster_deduplicates_and_retains_unknown_ids(checklist):
    widget, heroes, empty = checklist
    widget.load({"roster": [
        {"id": "91:1", "hero_id": 2, "hero_name": "Hero 2"},
        {"id": "91:2", "hero_id": 2},
        {"id": "95:4", "hero_id": 999}, {"hero_id": 0}, {"hero_id": True},
        {"hero_id": -2}, {"hero_id": "11"}, None]})
    assert set(widget._rows) == {2, 999}
    ids = widget.hero_ids
    ids.clear()
    assert widget.hero_ids == {2, 999}
    assert widget._rows[2]["name"] == "Seven"
    assert widget._rows[999]["name"] == "Hero 999"
    _click(widget, 2)
    assert (heroes.get(), empty.get()) == ("999", "0")


def test_grouped_rows_choose_available_name_without_duplicate_controls(checklist):
    widget, heroes, empty = checklist
    widget.load({"heroes": [{"hero_id": 2}, {"hero_id": 2, "hero_name": " Seven\n "},
                             {"hero_id": 2, "hero_name": "Other"}]})
    assert len(widget._rows) == 1
    assert widget._rows[2]["name"] == "Seven"
    assert _selected(widget) == {2}


def test_roster_warning_and_clip_limit_are_honest(checklist):
    widget, heroes, empty = checklist
    warning = "Two player rows have no valid hero ID; the list may be incomplete."
    widget.load(dict(_roster(), roster_warning=warning))
    assert warning in widget._notice.get()
    assert "availability may vary by clip" in widget._notice.get().casefold()
    widget.load({"heroes": [], "roster_warning": "Roster is unavailable."})
    assert not widget.has_roster
    assert "Roster is unavailable" in widget._notice.get()
    assert widget.none_button.cget("state") == "disabled"
    assert (heroes.get(), empty.get()) == ("", "0")


def test_busy_blocks_buttons_checks_and_direct_row_var_edits(checklist):
    widget, heroes, empty = checklist
    widget.load(_roster())
    heroes.set("2")
    widget.set_busy(True)
    assert widget.all_button.cget("state") == "disabled"
    assert widget.none_button.cget("state") == "disabled"
    assert all(row["checkbox"].cget("state") == "disabled" for row in widget._rows.values())
    widget.all_button._invoke()
    widget.none_button._invoke()
    _click(widget, 2)
    widget._rows[11]["variable"].set(True)
    assert (heroes.get(), empty.get()) == ("2", "0")
    assert _selected(widget) == {2}
    widget.load(_roster())
    assert all(row["checkbox"].cget("state") == "disabled" for row in widget._rows.values())
    widget.set_busy(False)
    _click(widget, 11)
    assert heroes.get() == "2,11"


def test_clear_and_new_input_reset_have_distinct_selection_behavior(checklist):
    widget, heroes, empty = checklist
    widget.load(_roster())
    heroes.set("2")
    widget.clear()
    assert not widget.has_roster and heroes.get() == "2"
    widget.load(_roster())
    assert _selected(widget) == {2}
    widget.clear(reset_selection=True)
    assert not widget.has_roster
    assert (heroes.get(), empty.get()) == ("", "0")
    widget.load(_roster())
    assert _selected(widget) == {2, 11, 50}


def test_invalid_raw_filter_is_reported_and_not_silently_rewritten(checklist):
    widget, heroes, empty = checklist
    widget.load(_roster())
    heroes.set("2,,oops")
    assert "positive numbers" in widget._notice.get()
    assert not _selected(widget)
    _click(widget, 2)
    assert heroes.get() == "2,,oops"
    widget.all_button._invoke()
    assert heroes.get() == ""


def test_rebuild_and_destroy_remove_owned_traces(checklist, character_root):
    widget, heroes, empty = checklist
    widget.load(_roster())
    old_variables = [row["variable"] for row in widget._rows.values()]
    widget.load(_roster())
    assert all(not variable.trace_info() for variable in old_variables)
    for variable in old_variables:
        variable.set(False)  # No callback may draw a destroyed CheckBox.
    row_variables = [row["variable"] for row in widget._rows.values()]
    widget.destroy()
    assert not heroes.trace_info() and not empty.trace_info()
    assert all(not variable.trace_info() for variable in row_variables)
    heroes.set("2")
    empty.set("1")
    character_root.update()


def test_long_names_fit_two_columns_at_minimum_content_width(checklist, character_root):
    widget, heroes, empty = checklist
    widget.load({"heroes": [{"hero_id": 2, "hero_name": "A" * 150},
                             {"hero_id": 999, "hero_name": "B" * 150}]})
    character_root.update()
    assert widget.winfo_reqwidth() <= 540 * gui_theme.SCALE
    assert all(row["checkbox"]._text.endswith("…") for row in widget._rows.values())
