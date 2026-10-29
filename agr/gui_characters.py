"""Replay character selection, backed by the existing explicit hero-ID filter.

The roster is a convenience for choosing IDs, not a promise that every track
appears or is compatible throughout a selected clip.
"""
from __future__ import annotations

import base64
import math
import random
import tkinter as tk
import zlib

try:
    import numpy as np
except ImportError:  # the portraits need numpy; without it heroes show their initials
    np = None

from . import gui_assets, gui_theme
from .gui_theme import px
from .gui_widgets import (BoilClock, RoundedButton, close_bubble, draw_rounded, open_bubble, rgba_image,
                          wrap_to_width)
from .replay import hero_display_name


def _hero_labels(metadata):
    """Resolve only recorded positive IDs; display labels never supply identity."""
    rows=metadata.get('heroes')
    if not isinstance(rows,list) or not rows:
        rows=metadata.get('roster',[])
    grouped={}
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict):continue
        identity=row.get('hero_id')
        if type(identity) is not int or identity<=0:continue
        name=row.get('hero_name')
        name=' '.join(name.split()) if isinstance(name,str) else ''
        if name==f'Hero {identity}':name=''
        if identity not in grouped or (name and not grouped[identity]):
            grouped[identity]=name
    return {identity:grouped[identity] or hero_display_name(identity) for identity in grouped}


# Standing explanations, shown in info icons beside the Characters title and the Follow camera label
# (gui_layout); the notes under the controls show only what needs attention.
CHARACTERS_INFO = "Choose characters to export. Each choice includes all matching tracks; availability may vary by clip."
FOLLOW_INFO = "Include this character in the Characters selection above."


class FollowHeroPicker(tk.Frame):
    """Named follow-camera menu backed by the existing shot_hero setting."""

    _OVERVIEW='overview'

    def __init__(self,parent,*,hero:tk.StringVar):
        background=parent.cget('background')
        super().__init__(parent,background=background)
        self._hero=hero
        self._names={}
        self._busy=False
        self._disposed=False
        self._choice=tk.StringVar(master=self)
        self._notice=tk.StringVar(master=self)
        theme=gui_theme.palette()
        self.button=RoundedButton(self,text='',command=self._open,theme=theme,background=background)
        self.button.pack(anchor='w')
        self.menu=tk.Menu(self,tearoff=False,font=gui_theme.FONT,
            background=theme['BUTTON'],foreground=theme['TEXT'],
            activebackground=theme['ACCENT'],activeforeground=theme['ACCENT_TEXT'],
            selectcolor=theme['TEXT'],borderwidth=0,relief='flat')
        self.note=wrap_to_width(tk.Label(self,textvariable=self._notice,background=background,
            foreground=gui_theme.MUTED,font=gui_theme.FONT_SMALL,
            justify='left',anchor='w',wraplength=gui_theme.px(430)))
        self.note.pack(fill='x',pady=(gui_theme.px(6),0))
        self._trace=hero.trace_add('write',self._sync)
        self.button.bind('<Down>',lambda _event:self._open(),add='+')
        self.button.bind('<Alt-Down>',lambda _event:self._open(),add='+')
        self.bind('<Destroy>',self._destroyed,add='+')
        self.clear()

    @property
    def hero_ids(self):
        return set(self._names)

    def load(self,metadata):
        self.menu.unpost()
        self._names=_hero_labels(metadata)
        self._rebuild()
        self._sync()

    def clear(self,*,reset_selection=False):
        self.menu.unpost()
        self._names={}
        if reset_selection:self._hero.set('')
        self._rebuild()
        self._sync()

    def _rebuild(self):
        self.menu.delete(0,'end')
        self.menu.add_radiobutton(label='Overview (no follow)',variable=self._choice,
            value=self._OVERVIEW,command=lambda:self._select(''))
        if self._names:self.menu.add_separator()
        for identity in sorted(self._names,key=lambda i:(self._names[i].casefold(),i)):
            self.menu.add_radiobutton(label=self._names[identity],variable=self._choice,
                value=str(identity),command=lambda value=identity:self._select(str(value)))

    def _select(self,value):
        if self._busy or (value and value not in {str(identity) for identity in self._names}):
            self._sync()
            return
        self._hero.set(value)

    def _open(self):
        if self._busy:return
        try:
            self.menu.tk_popup(self.button.winfo_rootx(),self.button.winfo_rooty()+self.button.winfo_height())
        finally:
            self.menu.grab_release()

    def set_busy(self,busy):
        self._busy=bool(busy)
        if busy:self.menu.unpost()
        self.button.configure(state='disabled' if busy else 'normal')

    def _sync(self,*_args):
        if self._disposed:return
        raw=self._hero.get().strip()
        try:identity=int(raw)
        except ValueError:identity=None
        self._choice.set(str(identity) if raw else self._OVERVIEW)
        if not raw:
            text='Overview (no follow)'
            note=('Choose a hero for a follow camera.' if self._names else
                  'Inspect the recording to choose a follow hero.')
        elif identity in self._names:
            text=self._names[identity]
            note=''  # all is well: FOLLOW_INFO is in the info icon beside the label
        else:
            text='Unavailable follow target' if self._names else 'Saved follow target'
            note=('The saved target is not in this recording. Choose a listed hero or Overview.' if self._names else
                  'Inspect the recording to confirm the saved camera target, or choose Overview.')
        self.button.configure(text=text+' \u25be')
        self._notice.set(note)
        if note and not self.note.winfo_manager():
            self.note.pack(fill='x',pady=(gui_theme.px(6),0))
        elif not note and self.note.winfo_manager():
            self.note.pack_forget()  # no empty line under the menu

    def _destroyed(self,event):
        if event.widget is self and not self._disposed:
            self._disposed=True
            try:self._hero.trace_remove('write',self._trace)
            except tk.TclError:pass


# --------------------------------------------------------------------------
# Hero portraits: one rounded tray per team, icons that wiggle on the shared boil clock,
# grow a little under the pointer and lose their color when deselected.

TRAY_FILL = "#0f0d0c"      # design color of a team's tray
ICON = 46                  # design px
ICON_GAP = 10
TRAY_PAD_X, TRAY_PAD_Y = 15, 10
TRAY_PAD_X_MIN = 8         # side padding may shrink to this before a team's row wraps
TRAY_RADIUS = 7
TRAY_GAP = 20              # between two teams' trays, and above / below the trays
HOVER_SCALE = 1.13
PRESS_SCALE = 0.9          # a click dips the icon for PRESS_MS, then it springs back (tactile feedback)
PRESS_MS = 110
# The wiggle only turns the portraits, a little; each tick about half of them move, so the tray never
# jumps as a whole. The first pose is the icon at rest.
POSE_ANGLES = (0.0, -1.8, 1.8, -1.0, 1.0)
WIGGLE_SHARE = 0.5


def _hero_teams(metadata):
    """{hero_id: team} for heroes the inspection could place on one team (others are left out)."""
    teams = {}
    rows = metadata.get("heroes")
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            identity, team = row.get("hero_id"), row.get("team")
            if type(identity) is int and identity > 0 and type(team) is int:
                teams[identity] = team
    return teams


_SOURCES = {}   # (hero_id, size) -> premultiplied float RGBA at that size, or None
_IMAGES = {}    # (hero_id, size, angle, selected) -> PhotoImage


def _box_weights(n_in, n_out):
    weights = np.zeros((n_out, n_in))
    scale = n_in / n_out
    for o in range(n_out):
        start, end = o * scale, (o + 1) * scale
        for i in range(int(start), min(n_in, int(math.ceil(end)))):
            weights[o, i] = max(0.0, min(end, i + 1) - max(start, i))
        weights[o] /= weights[o].sum()
    return weights


def _source(hero_id, size):
    """The hero's portrait resized to ``size`` px (area-averaged), in this screen's colors."""
    key = (hero_id, size)
    if key not in _SOURCES:
        blob = getattr(gui_assets, "HERO_ICONS", {}).get(hero_id)
        if blob is None or np is None:
            _SOURCES[key] = None
        else:
            n = gui_assets.HERO_ICON_SIZE
            raw = np.frombuffer(zlib.decompress(base64.b64decode(blob)), np.uint8).reshape(n, n, 4).copy()
            raw[:, :, :3] = gui_theme.screen_pixels(raw[:, :, :3])
            rgba = raw.astype(np.float32) / 255.0
            rgba[:, :, :3] *= rgba[:, :, 3:4]
            weights = _box_weights(n, size).astype(np.float32)
            _SOURCES[key] = np.einsum("oi,ijc->ojc", weights, np.einsum("oj,ijc->ioc", weights, rgba))
    return _SOURCES[key]


def hero_icon(master, hero_id, size, angle=0.0, selected=True):
    """A PhotoImage of the hero's portrait, ``size`` px, turned by ``angle`` degrees; grey when not selected.

    The image is a little larger than ``size`` so a turned portrait is never cropped; draw it centred.
    Returns None when the hero has no portrait (or numpy is unavailable).
    """
    key = (master.tk, hero_id, size, round(angle, 2), selected)
    if key in _IMAGES:
        return _IMAGES[key]
    src = _source(hero_id, size)
    if src is None:
        return None
    side = size + 2 * (int(size * 0.06) + 1)
    a = math.radians(angle)
    cos_a, sin_a = math.cos(a), math.sin(a)
    yy, xx = np.mgrid[0:side, 0:side].astype(np.float32) + 0.5 - side / 2
    u = xx * cos_a - yy * sin_a + size / 2 - 0.5
    v = xx * sin_a + yy * cos_a + size / 2 - 0.5
    x0, y0 = np.floor(u).astype(int), np.floor(v).astype(int)
    fx, fy = (u - x0)[..., None], (v - y0)[..., None]
    padded = np.zeros((size + 2, size + 2, 4), np.float32)
    padded[1:-1, 1:-1] = src

    def tap(x, y):
        return padded[np.clip(y + 1, 0, size + 1), np.clip(x + 1, 0, size + 1)]

    out = (tap(x0, y0) * (1 - fx) * (1 - fy) + tap(x0 + 1, y0) * fx * (1 - fy)
           + tap(x0, y0 + 1) * (1 - fx) * fy + tap(x0 + 1, y0 + 1) * fx * fy)
    if not selected:
        grey = out[:, :, :3] @ np.array([0.299, 0.587, 0.114], np.float32) * 0.78
        out[:, :, :3] = grey[..., None]
    alpha = out[:, :, 3:4]
    rgb = np.where(alpha > 1e-4, out[:, :, :3] / np.maximum(alpha, 1e-4), 0.0)
    pixels = np.clip(np.concatenate([rgb, alpha], axis=2) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    image = rgba_image(master, side, side, [pixels[y].tobytes() for y in range(side)])
    _IMAGES[key] = image
    return image


class HeroGroup(tk.Canvas):
    """One team's tray of hero portraits; click (or Space) toggles a hero.

    ``heroes`` are (hero_id, name, BooleanVar) in display order. Arrow keys move between icons
    when the tray has keyboard focus; hovering or focusing an icon shows the hero's name.
    """

    def __init__(self, parent, *, theme, toggle):
        background = parent.cget("background")
        super().__init__(parent, background=background, highlightthickness=0, bd=0, width=1,
                         height=px(ICON + 2 * TRAY_PAD_Y), takefocus=1, cursor="hand2")
        self._theme = dict(theme)
        self._toggle = toggle
        self._fill = gui_theme.screen(TRAY_FILL)
        self._heroes = []
        self._cells = []
        self._hover = None
        self._pressed = None
        self._press_job = None
        self._focus = 0
        self._focused = False
        self._busy = False
        self._bubble = None
        self._rng = random.Random(7)
        self.bind("<Configure>", lambda _e: self._layout(), add="+")
        self.bind("<Motion>", self._motion, add="+")
        self.bind("<Leave>", lambda _e: self._point(None), add="+")
        self.bind("<Button-1>", self._click, add="+")
        self.bind("<FocusIn>", lambda _e: self._set_focus(True), add="+")
        self.bind("<FocusOut>", lambda _e: self._set_focus(False), add="+")
        for key, step in (("<Left>", -1), ("<Up>", -1), ("<Right>", 1), ("<Down>", 1)):
            self.bind(key, lambda _e, s=step: self._step(s), add="+")
        for key in ("<space>", "<Return>"):
            self.bind(key, lambda _e: self._activate(self._focus), add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        BoilClock.register(self)

    # -- content and size ----------------------------------------------------------------------

    def set_heroes(self, heroes):
        self._close_bubble()
        self._heroes = [{"id": hero_id, "name": name, "var": variable, "pose": 0} for hero_id, name, variable in heroes]
        self._focus = min(self._focus, max(0, len(self._heroes) - 1))
        self._layout()

    @staticmethod
    def row_width(count):
        """Screen pixels the icons of one row span, measured exactly as _layout places them."""
        return (count - 1) * px(ICON + ICON_GAP) + px(ICON) if count else 0

    @classmethod
    def natural_width(cls, count, pad=TRAY_PAD_X):
        """Screen width of a tray holding ``count`` icons in one row with ``pad`` (design px) at each side."""
        return 2 * px(pad) + cls.row_width(count)

    def _layout(self):
        width = self.winfo_width()
        count = len(self._heroes)
        step = px(ICON + ICON_GAP)
        row_width = self.row_width(count)
        if width <= 1:
            pad, columns = px(TRAY_PAD_X), max(1, count)
        elif width - 2 * px(TRAY_PAD_X_MIN) >= row_width:
            # One row: the design's 15 px side padding, or a little less when the tray is tight.
            pad, columns = max(px(TRAY_PAD_X_MIN), min(px(TRAY_PAD_X), (width - row_width) // 2)), max(1, count)
        else:
            pad = px(TRAY_PAD_X)
            columns = max(1, (width - 2 * pad + px(ICON_GAP)) // step)
        rows = max(1, math.ceil(count / columns))
        self._cells = []
        for index in range(count):
            row, col = divmod(index, columns)
            x0, y0 = pad + col * step, px(TRAY_PAD_Y) + row * step
            self._cells.append((x0, y0, x0 + px(ICON), y0 + px(ICON)))
        height = 2 * px(TRAY_PAD_Y) + rows * px(ICON) + (rows - 1) * px(ICON_GAP)
        if int(self.cget("height")) != height:
            self.configure(height=height)
        self.refresh()
        if self._bubble is not None:  # the tray moved or re-flowed: keep the name under its icon
            self._show_name(self._focus if self._focused else self._hover)

    # -- drawing ---------------------------------------------------------------------------------

    def refresh(self):
        self.delete("all")
        width, height = self.winfo_width(), int(self.cget("height"))
        if width > 1:
            draw_rounded(self, 0, 0, width, height, px(TRAY_RADIUS), self._fill, None, self.cget("background"))
        t = self._theme
        if self._focused and not self._busy and 0 <= self._focus < len(self._cells):
            # Keyboard focus: a soft rounded patch behind the icon (no border).
            x0, y0, x1, y1 = self._cells[self._focus]
            m = px(4)
            draw_rounded(self, x0 - m, y0 - m, x1 + m, y1 + m, px(9), t["FIELD"], None, self._fill, tag="focus")
        for index, (hero, (x0, y0, x1, y1)) in enumerate(zip(self._heroes, self._cells)):
            angle = POSE_ANGLES[hero["pose"]]
            selected = bool(hero["var"].get())
            if index == self._pressed:
                size = px(ICON * PRESS_SCALE)
            elif index == self._hover and not self._busy:
                size = px(ICON * HOVER_SCALE)
            else:
                size = px(ICON)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            image = hero_icon(self, hero["id"], size, angle, selected)
            if image is not None:
                self.create_image(cx, cy, image=image, anchor="center")
            else:  # no portrait for this hero: initials on a disc
                r = size / 2
                self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=t["FIELD"] if selected else self._fill,
                                 outline=t["EDGE"])
                initials = "".join(part[0] for part in hero["name"].split()[:2]).upper() or "?"
                self.create_text(cx, cy, text=initials, font=gui_theme.FONT_BOLD,
                                 fill=t["TEXT"] if selected else t["DIM"])

    def advance(self):
        """Boil clock: about half the portraits turn to another angle (never the same one twice in a row)."""
        if not self._heroes or not self.winfo_viewable():
            return
        for hero in self._heroes:
            if self._rng.random() < WIGGLE_SHARE:
                hero["pose"] = self._rng.choice([i for i in range(len(POSE_ANGLES)) if i != hero["pose"]])
        self.refresh()

    # -- pointer and keyboard ----------------------------------------------------------------------

    def _index_at(self, x, y):
        for index, (x0, y0, x1, y1) in enumerate(self._cells):
            if x0 <= x < x1 and y0 <= y < y1:
                return index
        return None

    def _motion(self, event):
        self._point(self._index_at(event.x, event.y))

    def _point(self, index):
        if index == self._hover:
            return
        self._hover = index
        self.configure(cursor="hand2" if index is not None and not self._busy else "")
        self._show_name(index)
        self.refresh()

    def _click(self, event):
        index = self._index_at(event.x, event.y)
        if index is not None:
            self._focus = index
            self._activate(index)

    def _activate(self, index):
        if not self._busy and 0 <= index < len(self._heroes):
            self._press(index)
            self._toggle(self._heroes[index]["id"])
        return "break"

    def _press(self, index):
        """Tactile feedback: the icon dips for a moment and springs back."""
        if not gui_theme.motion_enabled():
            return
        if self._press_job is not None:
            self.after_cancel(self._press_job)
        self._pressed = index
        self.refresh()

        def release():
            self._press_job = None
            try:
                if self._pressed == index and self.winfo_exists():
                    self._pressed = None
                    self.refresh()
            except tk.TclError:
                pass  # the tray was replaced (a new recording) in the meantime
        self._press_job = self.after(PRESS_MS, release)

    def motion_changed(self):
        if not gui_theme.motion_enabled() and self._press_job is not None:
            self.after_cancel(self._press_job)
            self._press_job = None
            self._pressed = None
            self.refresh()

    def _step(self, step):
        if self._heroes:
            self._focus = (self._focus + step) % len(self._heroes)
            self._show_name(self._focus)
            self.refresh()
        return "break"

    def _set_focus(self, focused):
        self._focused = focused
        if focused:
            self._reveal()
        self._show_name(self._focus if focused else self._hover)
        self.refresh()

    def _reveal(self):
        """Scroll the page so a tray reached with Tab is fully in view (clear of the faded edges)."""
        canvas = self.master
        while canvas is not None and not isinstance(canvas, tk.Canvas):
            canvas = canvas.master
        region = str(canvas.cget("scrollregion")).split() if canvas is not None else []
        total = float(region[-1]) if region else 0.0
        if total <= 0:
            return
        view_top, view_height = canvas.canvasy(0), canvas.winfo_height()
        top = view_top + self.winfo_rooty() - canvas.winfo_rooty()
        bottom = top + self.winfo_height()
        margin = px(48)
        if top < view_top + margin:
            canvas.yview_moveto(max(0.0, top - margin) / total)
        elif bottom > view_top + view_height - margin:
            canvas.yview_moveto(max(0.0, bottom + margin - view_height) / total)
        canvas.update_idletasks()

    def _show_name(self, index):
        self._close_bubble()
        if index is not None and 0 <= index < len(self._heroes):
            self._bubble = open_bubble(self, self._heroes[index]["name"], theme=self._theme,
                                       box=self._cells[index], center=True)

    def _close_bubble(self):
        close_bubble(self._bubble)
        self._bubble = None

    def set_busy(self, busy):
        self._busy = bool(busy)
        # While a job runs the trays are inert, so they also leave the Tab order (like disabled checkboxes).
        self.configure(cursor="" if busy else "hand2", takefocus=0 if busy else 1)
        if busy:
            self._close_bubble()
        self.refresh()

    def _destroyed(self, event):
        if event.widget is self:
            if self._press_job is not None:
                self.after_cancel(self._press_job)
                self._press_job = None
            self._close_bubble()
            if self in BoilClock.members:  # the clock only prunes members while it ticks (motion on)
                BoilClock.members.remove(self)


class CharacterChecklist(tk.Frame):
    """Choose all, a subset, or explicitly none without losing raw ID choices.

    ``heroes`` keeps the CLI's comma-separated positive IDs; an empty string
    means all. ``empty == '1'`` separately records the user's explicit None
    choice so it can never accidentally become an all-character export.
    Inspected heroes are shown as portraits, one tray per team when the
    inspection knows the teams.
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
        self._groups: list[HeroGroup] = []
        self._warning = ""

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
        self._new_list()  # the team trays; packed by _show_list once there is a roster
        self._notice = tk.StringVar(master=self)
        self.notice_label = wrap_to_width(tk.Label(
            self, textvariable=self._notice, background=background,
            foreground=gui_theme.DIM, font=gui_theme.FONT_SMALL,
            justify="left", anchor="w", wraplength=gui_theme.px(490)))
        self.notice_label.pack(fill="x", pady=(gui_theme.px(TRAY_GAP), 0))
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

    def load(self, metadata: dict):
        """Load recorded hero types, preserving all/none/raw selections."""
        self._clear_rows()
        self._warning = str(metadata.get("roster_warning") or "").strip()
        names = _hero_labels(metadata)
        teams = _hero_teams(metadata)
        for hero_id, name in names.items():
            variable = tk.BooleanVar(master=self, value=False)
            trace = variable.trace_add("write", self._checked)
            self._row_traces.append((variable, trace))
            self._rows[hero_id] = {"variable": variable, "name": name or hero_display_name(hero_id),
                                   "team": teams.get(hero_id)}
        # One tray per team when every hero's team is known; otherwise one tray for everyone.
        known = {row["team"] for row in self._rows.values()}
        trays = sorted(known) if self._rows and None not in known else [None]
        for team in trays:
            members = sorted((hero_id for hero_id, row in self._rows.items() if team is None or row["team"] == team),
                             key=lambda hero_id: (self._rows[hero_id]["name"].casefold(), hero_id))
            group = HeroGroup(self._list, theme=self._theme, toggle=self._toggle)
            group.set_heroes([(hero_id, self._rows[hero_id]["name"], self._rows[hero_id]["variable"])
                              for hero_id in members])
            self._groups.append(group)
        self._arrange(self._list.winfo_width())
        self._sync()

    def _arrange(self, width):
        """Teams side by side when both fit at their natural width, otherwise one above the other."""
        groups = self._groups
        if not groups:
            return
        gap = gui_theme.px(TRAY_GAP)
        natural = [HeroGroup.natural_width(len(g._heroes), TRAY_PAD_X_MIN) for g in groups]
        # Both trays get the same width, so the larger team must fit in one half.
        side_by_side = len(groups) == 2 and width > 1 and 2 * max(natural) + gap <= width
        for column in range(2):
            self._list.columnconfigure(column, weight=1 if side_by_side else 0, uniform="trays" if side_by_side else "")
        self._list.columnconfigure(0, weight=1)
        for index, group in enumerate(groups):
            if side_by_side:
                # The gap is split over both columns so the two (uniform) trays come out the same width.
                group.grid(row=0, column=index, columnspan=1, sticky="ew",
                           padx=(0, gap // 2) if index == 0 else (gap - gap // 2, 0), pady=0)
            else:
                group.grid(row=index, column=0, columnspan=2, sticky="ew", padx=0, pady=(gap if index else 0, 0))

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
        for group in self._groups:
            group.set_busy(self._busy)

    def _select_all(self):
        if not self._busy:
            self._write("", "0")

    def _select_none(self):
        if not self._busy and self.has_roster:
            self._write("", "1")

    def _toggle(self, hero_id):
        row = self._rows.get(hero_id)
        if row is not None and not self._busy:
            row["variable"].set(not row["variable"].get())

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
        messages = []
        if self.has_roster:
            count = sum(row["variable"].get() for row in self._rows.values())
            self._count.set(f"{count} of {len(self._rows)} selected")
            # The standing explanation (CHARACTERS_INFO) is in the info icon beside the card title.
        else:
            self._count.set("No characters selected" if none else (
                "All characters" if all_selected else f"{len(explicit)} selected IDs"))
            messages.append("Inspect the replay or saved package to list its characters.")
        if self._warning:
            messages.append(self._warning)
        if invalid:
            messages.append("Hero IDs must be positive numbers separated by commas. Choose All to reset.")
        elif explicit and self.has_roster and not none:
            missing = sorted(explicit - self._rows.keys())
            if missing:
                messages.append("Selected IDs outside this list (kept): " + ", ".join(map(str, missing)) + ".")
        self._show_notice("\n".join(messages))
        self._show_list(self.has_roster)
        for group in self._groups:
            group.refresh()
        self._set_states()

    def _show_notice(self, text):
        """The note under the trays, only while it has something to say (no empty gap otherwise)."""
        self._notice.set(text)
        if text and not self.notice_label.winfo_manager():
            self.notice_label.pack(fill="x", pady=(gui_theme.px(TRAY_GAP), 0))  # last, under the trays
        elif not text and self.notice_label.winfo_manager():
            self.notice_label.pack_forget()

    def _pack_list(self):
        above_notice = {"before": self.notice_label} if self.notice_label.winfo_manager() else {}
        self._list.pack(fill="x", pady=(gui_theme.px(TRAY_GAP), 0), **above_notice)

    def _show_list(self, visible):
        # An empty list takes no room (and no gap): without a roster the card is just the
        # buttons and the note, also after a previous recording's list was cleared.
        if visible and not self._list.winfo_manager():
            self._pack_list()
        elif not visible and self._list.winfo_manager():
            self._list.pack_forget()

    @staticmethod
    def _remove_trace(variable, trace):
        try:
            variable.trace_remove("write", trace)
        except tk.TclError:
            pass

    def _new_list(self):
        self._list = tk.Frame(self, background=self.cget("background"))
        self._list.bind("<Configure>", lambda event: self._arrange(event.width), add="+")

    def _clear_rows(self):
        for variable, trace in self._row_traces:
            self._remove_trace(variable, trace)
        self._row_traces.clear()
        # A fresh tray container for every roster: Tk's grid neither shrinks a frame whose last
        # child is gone nor places a new child that leaves the frame's size unchanged.
        was_packed = bool(self._list.winfo_manager())
        self._list.destroy()
        self._groups.clear()
        self._new_list()
        if was_packed:
            self._pack_list()
        self._rows.clear()

    def _destroyed(self, event):
        if event.widget is self and not self._disposed:
            self._disposed = True
            self._remove_trace(self._heroes, self._heroes_trace)
            self._remove_trace(self._empty, self._empty_trace)
            for variable, trace in self._row_traces:
                self._remove_trace(variable, trace)
            self._row_traces.clear()
