"""Replay character selection, backed by the existing explicit hero-ID filter.

The roster is a convenience for choosing IDs, not a promise that every track
appears or is compatible throughout a selected clip.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

from . import gui_theme
from .gui_widgets import CheckBox, RoundedButton
from .replay import hero_display_name


class CharacterChecklist(tk.Frame):
    """Choose all, a subset, or explicitly none without losing raw ID choices.

    ``heroes`` keeps the CLI's comma-separated positive IDs; an empty string
    means all. ``empty == '1'`` separately records the user's explicit None
    choice so it can never accidentally become an all-character export.
    """

    def __init__(self, parent, *, heroes: tk.StringVar, empty: tk.StringVar):
        self._theme = gui_theme.palette()
        background = parent.cget("background")
        super().__init__(parent, background=background)
        self._heroes = heroes
        self._empty = empty
        self._busy = False
        self._updating = False
        self._disposed = False
        self._rows: dict[int, dict] = {}
        self._row_traces = []
        self._warning = ""
        self._label_font = tkfont.Font(root=self, family="Segoe UI", size=10)

        controls = tk.Frame(self, background=background)
        controls.pack(fill="x")
        self.all_button = RoundedButton(
            controls, text="All", command=self._select_all, theme=self._theme,
            height=30, pad_x=12, background=background)
        self.all_button.pack(side="left")
        self.none_button = RoundedButton(
            controls, text="None", command=self._select_none, theme=self._theme,
            height=30, pad_x=12, background=background)
        self.none_button.pack(side="left", padx=(gui_theme.px(7), 0))
        self._count = tk.StringVar(master=self)
        tk.Label(controls, textvariable=self._count, background=background,
                 foreground=gui_theme.MUTED, font=gui_theme.FONT_SMALL,
                 anchor="e").pack(side="right")
        self._list = tk.Frame(self, background=background)
        self._list.pack(fill="x", pady=(gui_theme.px(8), 0))
        for column in range(2):
            self._list.columnconfigure(column, weight=1, uniform="characters")
        self._notice = tk.StringVar(master=self)
        self.notice_label = tk.Label(
            self, textvariable=self._notice, background=background,
            foreground=gui_theme.MUTED, font=gui_theme.FONT_SMALL,
            justify="left", anchor="w", wraplength=gui_theme.px(490))
        self.notice_label.pack(fill="x", pady=(gui_theme.px(7), 0))
        self._heroes_trace = heroes.trace_add("write", self._external_heroes)
        self._empty_trace = empty.trace_add("write", self._external_empty)
        self.bind("<Destroy>", self._destroyed, add="+")
        self._sync()

    @property
    def has_roster(self) -> bool:
        return bool(self._rows)

    @property
    def hero_ids(self) -> set[int]:
        """Return inspected IDs for caller validation, independent of selection."""
        return set(self._rows)

    @staticmethod
    def _positive_id(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None

    def _raw_selection(self):
        """Return (explicit IDs, invalid); never rewrite a caller's raw filter."""
        raw = self._heroes.get().strip()
        if not raw:
            return set(), False
        try:
            parts = [part.strip() for part in raw.split(",")]
            ids = {int(part) for part in parts}
            if not ids or min(ids) <= 0:
                return set(), True
            return ids, False
        except ValueError:
            return set(), True

    def _short_label(self, label):
        limit = gui_theme.px(190)
        if self._label_font.measure(label) <= limit:
            return label
        while label and self._label_font.measure(label + "…") > limit:
            label = label[:-1]
        return label + "…"

    def load(self, metadata: dict):
        """Load recorded hero types, preserving all/none/raw selections."""
        self._clear_rows()
        self._warning = str(metadata.get("roster_warning") or "").strip()
        rows = metadata.get("heroes")
        if not isinstance(rows, list) or not rows:
            rows = metadata.get("roster", [])
        grouped = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            hero_id = self._positive_id(row.get("hero_id"))
            if hero_id is None:
                continue
            name = row.get("hero_name")
            name = " ".join(name.split()) if isinstance(name, str) else ""
            if name == f"Hero {hero_id}":
                name = ""
            if hero_id not in grouped or (name and not grouped[hero_id]):
                grouped[hero_id] = name
        ordered = sorted(grouped, key=lambda hero_id: (
            (grouped[hero_id] or hero_display_name(hero_id)).casefold(), hero_id))
        for index, hero_id in enumerate(ordered):
            label = grouped[hero_id] or hero_display_name(hero_id)
            variable = tk.BooleanVar(master=self, value=False)
            checkbox = CheckBox(
                self._list, text=self._short_label(label), variable=variable,
                theme=self._theme, background=self.cget("background"))
            checkbox.grid(row=index // 2, column=index % 2, sticky="ew",
                          padx=(0, gui_theme.px(12) if index % 2 == 0 else 0),
                          pady=(0, gui_theme.px(3)))
            trace = variable.trace_add("write", self._checked)
            # CheckBox owns a drawing trace; remove both traces before a row is
            # destroyed rather than leaving a callback targeting a dead canvas.
            self._row_traces.append((variable, trace, checkbox._trace))
            self._rows[hero_id] = {"variable": variable, "checkbox": checkbox, "name": label}
        self._sync()

    def clear(self, *, reset_selection=False):
        """Forget an inspected roster; changing input may also reset selection."""
        self._clear_rows()
        self._warning = ""
        if reset_selection:
            self._write("", "0")
        else:
            self._sync()

    def set_busy(self, busy: bool):
        self._busy = bool(busy)
        self._set_states()

    def _set_states(self):
        self.all_button.configure(state="disabled" if self._busy else "normal")
        self.none_button.configure(state="disabled" if self._busy or not self.has_roster else "normal")
        for row in self._rows.values():
            row["checkbox"].configure(state="disabled" if self._busy else "normal")

    def _select_all(self):
        if not self._busy:
            self._write("", "0")

    def _select_none(self):
        if not self._busy and self.has_roster:
            self._write("", "1")

    def _write(self, heroes, empty):
        self._updating = True
        try:
            self._heroes.set(heroes)
            self._empty.set(empty)
        finally:
            self._updating = False
        self._sync()

    def _external_heroes(self, *_args):
        if self._updating or self._disposed:
            return
        self._updating = True
        try:
            self._empty.set("0")
        finally:
            self._updating = False
        self._sync()

    def _external_empty(self, *_args):
        if not self._updating and not self._disposed:
            self._sync()

    def _checked(self, *_args):
        if self._updating or self._disposed:
            return
        explicit, invalid = self._raw_selection()
        if self._busy or invalid:
            self._sync()
            return
        selected = {hero_id for hero_id, row in self._rows.items() if row["variable"].get()}
        # Explicit IDs outside the inspected list remain part of the filter.
        # The caller validates them; a convenience widget must not discard them.
        selected |= explicit - self._rows.keys()
        if not selected:
            self._write("", "1")
        elif selected == self._rows.keys():
            self._write("", "0")
        else:
            self._write(",".join(str(hero_id) for hero_id in sorted(selected)), "0")

    def _sync(self):
        explicit, invalid = self._raw_selection()
        none = self._empty.get() == "1"
        all_selected = not self._heroes.get().strip() and not none
        self._updating = True
        try:
            for hero_id, row in self._rows.items():
                selected = not none and not invalid and (all_selected or hero_id in explicit)
                if row["variable"].get() != selected:
                    row["variable"].set(selected)
        finally:
            self._updating = False
        if self.has_roster:
            count = sum(row["variable"].get() for row in self._rows.values())
            self._count.set(f"{count} of {len(self._rows)} selected")
            note = "Choose characters to export. Each choice includes all matching tracks; availability may vary by clip."
        else:
            self._count.set("No characters selected" if none else (
                "All characters" if all_selected else f"{len(explicit)} selected IDs"))
            note = "Inspect the replay or saved package to list its characters."
        messages = [note]
        if self._warning:
            messages.append(self._warning)
        if invalid:
            messages.append("Hero IDs must be positive numbers separated by commas. Choose All to reset.")
        elif explicit and self.has_roster and not none:
            missing = sorted(explicit - self._rows.keys())
            if missing:
                messages.append("Selected IDs outside this list (kept): " + ", ".join(map(str, missing)) + ".")
        self._notice.set("\n".join(messages))
        self._set_states()

    @staticmethod
    def _remove_trace(variable, trace):
        try:
            variable.trace_remove("write", trace)
        except tk.TclError:
            pass

    def _clear_rows(self):
        for variable, trace, drawing_trace in self._row_traces:
            self._remove_trace(variable, trace)
            self._remove_trace(variable, drawing_trace)
        self._row_traces.clear()
        for child in self._list.winfo_children():
            child.destroy()
        self._rows.clear()

    def _destroyed(self, event):
        if event.widget is self and not self._disposed:
            self._disposed = True
            self._remove_trace(self._heroes, self._heroes_trace)
            self._remove_trace(self._empty, self._empty_trace)
            for variable, trace, drawing_trace in self._row_traces:
                self._remove_trace(variable, trace)
                self._remove_trace(variable, drawing_trace)
            self._row_traces.clear()
