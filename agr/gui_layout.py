"""Desktop workspace: navigation rail, page content, shared job controller.

Every page shares the launcher's controller (``AGRApp``); this module only
builds widgets and the small navigation/selection behaviors. Layout targets a
compact tool window that sits naturally next to Blender: a raised left rail for
pages, one rounded card per section, and a pinned action footer with the job
status on its own line.

All sizes are design pixels (the Figma mockup at 100%) passed through ``px()``,
so the layout keeps its proportions on scaled displays.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import __version__, gui_assets, gui_theme
from .gui_theme import hairline, px
from .gui_widgets import (Card, CheckBox, IdleUpdate, ProgressLine, ResizeRedraw, RoundedButton, RoundedEntry, SegmentedControl,
                          StatusDot, bind_tree, gradient_color, outline_right_rounded)
from .release_profile import character_beta
from .gui_characters import CharacterChecklist

PAGE_EXPORT = "Export"
PAGE_SETUP = "Setup"
PAGE_ADVANCED = "Advanced"
PAGE_MAP = "Map"
PAGE_LIGHTING = "Lighting lab"
PAGE_UPDATES = "Updates"

PAGE_DETAILS = {
    PAGE_EXPORT: "Choose a recording, inspect it, then export a Blender scene.",
    PAGE_SETUP: "Point RenderLock at Blender and your game files. Conversion never launches Deadlock.",
    PAGE_ADVANCED: "Experimental character options and output policy.",
    PAGE_MAP: "Prepare the installed map region used by the lighting tools. Map geometry is not exported in this beta.",
    PAGE_LIGHTING: "Experimental character look: partial Source lighting, probe reflections and the Elagrimm shader.",
    PAGE_UPDATES: "Review what changed, download an update, then restart when you are ready.",
}

RAIL_WIDTH = 181
RAIL_RADIUS = 18
CONTENT_PAD_X = 40
CARD_GAP = 10
ROW_GAP = 7                # vertical space between field rows inside a card
LABEL_GAP = 10             # space between a row label and its field
HEADER_TOP = 30            # rail brand and page header start on the same line
TITLE_GAP = 4              # title -> subtitle, same in the rail and the page header
LABEL_COLUMN = 0           # Export rows: each label hugs its field ("File", "Save as")
WIDE_LABEL_COLUMN = 150    # Setup/Advanced rows keep their fields aligned
TEXT_WRAP = 700


def _pad(value):
    """Scale a padding value or (before, after) pair from design pixels."""
    return tuple(px(v) for v in value) if isinstance(value, tuple) else px(value)


def _rail_edge_color(u, v):
    """Rail outline color at normalized rail coordinates (Figma linear gradient, mostly top to bottom)."""
    t = -0.0269 * u + 0.6237 * v + 0.0134
    return gradient_color(((0.0, gui_theme.RAIL_EDGE_TOP), (1.0, gui_theme.RAIL_EDGE_BOTTOM)), t)


def set_log_visible(app, visible: bool) -> None:
    was_visible = bool(app.log_frame.winfo_manager())
    if visible:
        app.log_frame.grid()
    else:
        app.log_frame.grid_remove()
    app.log_button.configure(text="Hide log" if visible else "Show log")
    if visible != was_visible:
        _resize_for_log(app, visible)


def _work_area(root):
    """Usable screen rectangle (left, top, right, bottom) excluding the taskbar."""
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError):
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _resize_for_log(app, visible):
    """Grow the window by the log drawer's height when it opens, and give it back when it closes.

    The pages keep their space instead of being squeezed. If the taller window would run
    off the bottom of the screen it moves up first, and it never grows past the screen.
    A maximized window is left alone.
    """
    root = app.root
    if not root.winfo_ismapped() or root.state() == "zoomed":
        app.log_growth = 0
        return
    root.update_idletasks()
    size, x, y = root.geometry().split("+")[0], root.winfo_x(), root.winfo_y()
    width, height = (int(v) for v in size.split("x"))
    if visible:
        wanted = app.log_frame.winfo_reqheight() + px(12)  # drawer plus its top gap
        _left, top, _right, bottom = _work_area(root)
        chrome = (root.winfo_rooty() - y) + (root.winfo_rootx() - x)  # title bar + bottom border
        growth = min(wanted, max(0, (bottom - top) - chrome - height))
        app.log_growth = growth
        if growth > 0:
            new_y = max(top, min(y, bottom - chrome - (height + growth)))
            root.geometry(f"{width}x{height + growth}+{x}+{new_y}")
    else:
        growth, app.log_growth = getattr(app, "log_growth", 0), 0
        if growth:
            root.geometry(f"{width}x{max(px(660), height - growth)}+{x}+{y}")


# --------------------------------------------------------------------------
# Small builders shared by every page. Section content lives on cards, so
# every helper takes the card body as ``parent`` and paints with CARD.


def _label(parent, text, font=None, color=None, **kw):
    return tk.Label(parent, text=text, background=parent.cget("background"),
                    foreground=color or gui_theme.MUTED, font=font or gui_theme.FONT_SMALL, **kw)


def _entry(parent, variable, width=120):
    return RoundedEntry(parent, variable=variable, theme=gui_theme.palette(), font=gui_theme.FONT,
                        width=width)


def _card(app, page, row, title, detail="", label_column=LABEL_COLUMN):
    """Add a section card to ``page`` and return its body and the next page row."""
    card = Card(page, theme=gui_theme.palette())
    card.grid(row=row, column=0, sticky="ew", pady=_pad((0 if row == 0 else CARD_GAP, 0)))
    body = card.body
    body.columnconfigure(0, minsize=px(label_column))
    body.columnconfigure(1, weight=1)
    _label(body, title, font=gui_theme.FONT_SECTION, color=gui_theme.TEXT).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=_pad((0, 4 if detail else 9)))
    if detail:
        _label(body, detail, justify="left", anchor="w", wraplength=px(TEXT_WRAP)).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=_pad((0, 11)))
    return body, row + 1


def _next_row(body):
    return body.grid_size()[1]


def _note(parent, text, pady=(12, 0)):
    _label(parent, text, justify="left", anchor="w", wraplength=px(TEXT_WRAP)).grid(
        row=_next_row(parent), column=0, columnspan=3, sticky="w", pady=_pad(pady))


def _path_row(app, parent, label, key, command, button=True):
    row = _next_row(parent)
    gap = _pad((0, ROW_GAP))
    _label(parent, label).grid(row=row, column=0, sticky="w", padx=_pad((0, LABEL_GAP)), pady=gap)
    entry = _entry(parent, app.values[key])
    entry.grid(row=row, column=1, sticky="ew", pady=gap)
    app.mutable_widgets.append(entry)
    if button:
        browse = RoundedButton(parent, text="Browse", command=command, theme=gui_theme.palette(), height=34)
        browse.grid(row=row, column=2, padx=_pad((5, 0)), pady=gap)
        app.mutable_widgets.append(browse)


def _check(app, parent, text, variable, onvalue="1", offvalue="0", pady=(0, 1)):
    check = CheckBox(parent, text=text, variable=variable, theme=gui_theme.palette(),
                     onvalue=onvalue, offvalue=offvalue)
    check.grid(row=_next_row(parent), column=0, columnspan=3, sticky="w", pady=_pad(pady))
    app.mutable_widgets.append(check)


# --------------------------------------------------------------------------
# Pages.


def _page(container):
    """Scrollable page body; the scrollbar appears only when it is needed."""
    shell = tk.Frame(container, background=gui_theme.BG)
    shell.columnconfigure(0, weight=1)
    shell.rowconfigure(0, weight=1)
    canvas = tk.Canvas(shell, background=gui_theme.BG, highlightthickness=0, bd=0)
    bar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
    canvas.grid(row=0, column=0, sticky="nsew")
    canvas.configure(yscrollcommand=bar.set)
    content = tk.Frame(canvas, background=gui_theme.BG)
    content.columnconfigure(0, weight=1)
    window = canvas.create_window((0, 0), window=content, anchor="nw")

    last_width = last_region = None
    scrollbar_visible = False

    def resize():
        nonlocal last_width, last_region, scrollbar_visible
        width = canvas.winfo_width()
        if width != last_width:
            canvas.itemconfigure(window, width=width)
            last_width = width
        region = canvas.bbox("all")
        if region != last_region:
            canvas.configure(scrollregion=region)
            last_region = region
        overflow = content.winfo_reqheight() > canvas.winfo_height() + 1
        if overflow != scrollbar_visible:
            if overflow:
                bar.grid(row=0, column=1, sticky="ns", padx=_pad((8, 0)))
            else:
                bar.grid_remove()
                canvas.yview_moveto(0)
            scrollbar_visible = overflow

    update = IdleUpdate(shell, resize)
    content.bind("<Configure>", update)
    canvas.bind("<Configure>", update)

    def wheel(event):
        # Scroll only the page under the pointer; logs keep their native wheel.
        widget = event.widget
        while widget is not None:
            if widget == shell and canvas.yview() != (0.0, 1.0):
                canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
                return
            widget = getattr(widget, "master", None)

    container.winfo_toplevel().bind("<MouseWheel>", wheel, add="+")
    return shell, content


def show_page(app, name):
    app.page = name
    for key, frame in app.page_frames.items():
        if key == name:
            frame.grid(row=0, column=0, sticky="nsew")
        else:
            frame.grid_remove()
    _nav_select(app, name)
    app.page_title.configure(text=name)
    app.page_subtitle.configure(text=PAGE_DETAILS.get(name, ""))


# --------------------------------------------------------------------------
# Navigation rail.


def _nav_hover(app, name, active):
    parts = app.nav_items[name]
    if app.page == name:
        return
    bg = gui_theme.RAIL_HOVER if active else gui_theme.RAIL_BG
    fg = gui_theme.TEXT if active else gui_theme.MUTED
    for key in ("frame", "inner", "bar"):
        parts[key].configure(background=bg)
    if parts.get("icon") is not None:
        parts["icon"].configure(background=bg, foreground=fg)
    parts["text"].configure(background=bg, foreground=fg)


def _nav_select(app, name):
    for key, parts in app.nav_items.items():
        selected = key == name
        bg = gui_theme.RAIL_SEL if selected else gui_theme.RAIL_BG
        parts["frame"].configure(background=bg)
        parts["inner"].configure(background=bg)
        parts["bar"].configure(background=gui_theme.ACCENT if selected else bg)
        if parts.get("icon") is not None:
            parts["icon"].configure(background=bg,
                                    foreground=gui_theme.ACCENT if selected else gui_theme.MUTED)
        parts["text"].configure(background=bg, foreground=gui_theme.TEXT if selected else gui_theme.MUTED)


def _nav_item(app, parent, name, glyph, icon_family, label):
    bg = gui_theme.RAIL_BG
    item = tk.Frame(parent, background=bg, cursor="hand2")
    item.pack(fill="x")
    bar = tk.Frame(item, background=bg, width=px(3))
    bar.pack(side="left", fill="y")
    inner = tk.Frame(item, background=bg)
    inner.pack(side="left", fill="x", expand=True, padx=_pad((13, 8)), pady=px(10))
    icon = None
    if glyph and icon_family:
        icon = tk.Label(inner, text=glyph, font=(icon_family, 11),
                        background=bg, foreground=gui_theme.MUTED)
        icon.pack(side="left", padx=_pad((0, 10)))
    text = tk.Label(inner, text=label, font=gui_theme.FONT, background=bg, foreground=gui_theme.MUTED)
    text.pack(side="left")
    app.nav_items[name] = {"frame": item, "bar": bar, "inner": inner, "icon": icon, "text": text}
    bind_tree(item, "<Button-1>", lambda _event, n=name: show_page(app, n))
    bind_tree(item, "<Enter>", lambda _event, n=name: _nav_hover(app, n, True))
    bind_tree(item, "<Leave>", lambda _event, n=name: _nav_hover(app, n, False))


def _build_rail(app, parent, beta):
    rail = tk.Frame(parent, background=gui_theme.RAIL_BG, width=px(RAIL_WIDTH))
    rail.grid(row=0, column=0, sticky="ns")
    rail.grid_propagate(False)
    rail.pack_propagate(False)
    # Faint emblem anchored to the bottom of the rail, below the navigation. The image is
    # pre-blended for the bone colors and exported per display scale; use the closest one.
    if gui_theme.SKIN_NAME == "bone":
        scale = min(gui_assets.RAIL_WATERMARK_PNG, key=lambda s: abs(s - gui_theme.SCALE))
        app.rail_watermark = tk.PhotoImage(master=rail, data=gui_assets.RAIL_WATERMARK_PNG[scale])
        watermark = tk.Label(rail, image=app.rail_watermark, bd=0, highlightthickness=0,
                             background=gui_theme.RAIL_BG)
        watermark.place(x=0, rely=1.0, anchor="sw")
        tk.Misc.lower(watermark)
    brand = tk.Frame(rail, background=gui_theme.RAIL_BG)
    brand.pack(fill="x", padx=px(17), pady=_pad((HEADER_TOP, 31)))
    app.rail_logo = app.app_icon.subsample(max(1, round(app.app_icon.width() / px(40))))
    tk.Label(brand, image=app.rail_logo, background=gui_theme.RAIL_BG, bd=0).pack(
        anchor="w", pady=_pad((0, 8)))
    tk.Label(brand, text="RenderLock", font=gui_theme.FONT_WORDMARK, background=gui_theme.RAIL_BG,
             foreground=gui_theme.ACCENT).pack(anchor="w")
    tk.Label(brand, text=f"{__version__}  ·  " + ("Character beta" if beta else "Development"),
             font=gui_theme.FONT_TINY, background=gui_theme.RAIL_BG,
             foreground=gui_theme.DIM).pack(anchor="w", pady=_pad((1, 0)))
    icon_family = gui_theme.icon_font(app.root)
    items = [(PAGE_EXPORT, "\ue896", "Export"),
             (PAGE_SETUP, "\ue713", "Setup"),
             (PAGE_ADVANCED, "\ue9e9", "Advanced"),
             (PAGE_MAP, "\ue826", "Map"),
             (PAGE_LIGHTING, "\ue945", "Lighting lab"),
             (PAGE_UPDATES, "\ue895", "Updates")]
    app.nav_items = {}
    for name, glyph, label in items:
        _nav_item(app, rail, name, glyph, icon_family, label)
    # The rail is a raised panel: rounded corners and a 1 px gradient outline facing the content.
    outline_right_rounded(rail, px(RAIL_RADIUS), gui_theme.RAIL_BG, gui_theme.BG, _rail_edge_color,
                          width=hairline(), linear_gradient=True)
    return rail


# --------------------------------------------------------------------------
# Export page content.


def _build_export(app, page):
    body, row = _card(app, page, 0, "Replay")
    _path_row(app, body, "File", "input", app._browse_input)
    modes = tk.Frame(body, background=gui_theme.CARD)
    modes.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew")
    app.mode_switch = SegmentedControl(
        modes, options=(("Replay .dem", "replay"), ("Saved package .json", "segment")),
        variable=app.values["mode"], command=app._mode_changed, theme=gui_theme.palette())
    app.mode_switch.pack(side="left")
    app.mutable_widgets.append(app.mode_switch)
    app.inspect_button = RoundedButton(modes, text="Inspect replay", command=app._inspect,
                                       theme=gui_theme.palette(), height=34, pad_x=15)
    app.inspect_button.pack(side="right")
    app.mutable_widgets.append(app.inspect_button)
    app.input_summary = tk.StringVar(value="Inspect to read the replay information.")
    tk.Label(body, textvariable=app.input_summary, background=gui_theme.CARD, foreground=gui_theme.MUTED,
             font=gui_theme.FONT_SMALL, justify="left", anchor="w", wraplength=px(TEXT_WRAP)).grid(
        row=_next_row(body), column=0, columnspan=3, sticky="w", pady=_pad((11, 0)))

    body, row = _card(app, page, row, "Characters")
    app.character_picker = CharacterChecklist(body, heroes=app.values["heroes"], empty=app.values["heroes_empty"])
    app.character_picker.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew")

    body, row = _card(app, page, row, "Clip")
    fields = tk.Frame(body, background=gui_theme.CARD)
    fields.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew")
    # Character selection lives in the named checklist below the clip settings.
    for col, (key, label, weight) in enumerate((("start", "First tick", 3), ("end", "Last tick", 3),
                                                ("fps", "FPS", 3))):
        fields.columnconfigure(col, weight=weight, uniform="clip")
        group = tk.Frame(fields, background=gui_theme.CARD)
        group.grid(row=0, column=col, sticky="ew", padx=_pad((0, 13 if col < 2 else 0)))
        _label(group, label).pack(anchor="w")
        entry = _entry(group, app.values[key])
        entry.pack(fill="x", pady=_pad((5, 0)))
        app.mutable_widgets.append(entry)
    camera = tk.Frame(body, background=gui_theme.CARD)
    camera.grid(row=_next_row(body), column=0, columnspan=3, sticky="w", pady=_pad((ROW_GAP, 0)))
    _label(camera, "Follow hero ID", font=gui_theme.FONT, color=gui_theme.TEXT).pack(side="left", padx=_pad((0, 12)))
    entry = _entry(camera, app.values["shot_hero"], width=70)
    entry.pack(side="left")
    app.mutable_widgets.append(entry)
    _label(camera, "Empty uses the overview camera").pack(side="left", padx=_pad((12, 0)))

    body, row = _card(app, page, row, "Output")
    _path_row(app, body, "Save as", "output", app._browse_output)
    _check(app, body, "Include supported particle effects", app.values["effects_enabled"], pady=(0, 0))
    _note(body, "Missing effects are listed in the coverage report. Add maps separately in Blender.", pady=(9, 0))
    return row


# --------------------------------------------------------------------------
# Setup page content.


def _build_setup(app, page):
    body, row = _card(app, page, 0, "Local tools",
                      "Choose these once. Conversion reads installed files without launching Deadlock.",
                      label_column=WIDE_LABEL_COLUMN)
    _path_row(app, body, "Blender executable", "blender", app._browse_blender)

    source_row = _next_row(body)
    _label(body, "Asset source").grid(row=source_row, column=0, sticky="w", pady=_pad((0, ROW_GAP)))
    app.asset_source_label = tk.StringVar(
        value="Installed game" if app.values["asset_source"].get() == "installed" else "Prepared assets")
    # A switch instead of a dropdown: only two choices, both always visible.
    app.asset_source_combo = SegmentedControl(
        body, options=(("Installed game", "Installed game"), ("Prepared assets", "Prepared assets")),
        variable=app.asset_source_label, command=app._asset_source_changed, theme=gui_theme.palette())
    app.asset_source_combo.grid(row=source_row, column=1, columnspan=2, sticky="w", pady=_pad((0, ROW_GAP)))
    app.mutable_widgets.append(app.asset_source_combo)

    fields_row = _next_row(body)
    app.installed_fields = tk.Frame(body, background=gui_theme.CARD)
    app.installed_fields.grid(row=fields_row, column=0, columnspan=3, sticky="ew")
    app.installed_fields.columnconfigure(0, minsize=px(WIDE_LABEL_COLUMN))
    app.installed_fields.columnconfigure(1, weight=1)
    for label, key, command in (("Deadlock folder", "game_install", app._browse_game),
                                ("Source 2 Viewer CLI", "source2viewer", app._browse_source2viewer),
                                ("Generated asset cache", "asset_cache", app._browse_cache)):
        _path_row(app, app.installed_fields, label, key, command)
    _note(app.installed_fields, "Source 2 Viewer 20.1 is included with this beta; browse only to override it.",
          pady=(8, 0))
    app.prepared_fields = tk.Frame(body, background=gui_theme.CARD)
    app.prepared_fields.grid(row=fields_row, column=0, columnspan=3, sticky="ew")
    app.prepared_fields.columnconfigure(0, minsize=px(WIDE_LABEL_COLUMN))
    app.prepared_fields.columnconfigure(1, weight=1)
    _path_row(app, app.prepared_fields, "Prepared asset folder", "assets", app._browse_assets)
    app._asset_source_changed()
    _note(body, "Blender and Source 2 Viewer are separate tools. "
                "Keep generated files outside the game installation.", pady=(ROW_GAP, 0))
    return row


# --------------------------------------------------------------------------
# Advanced page content.


def _build_advanced(app, page):
    body, row = _card(app, page, 0, "Character selection")
    _path_row(app, body, "Hero IDs", "heroes", None, button=False)
    _note(body, "Use the Export checklist after inspecting a recording. Manual IDs are available here if its character list is unavailable; empty selects all.", pady=(6, 0))

    body, row = _card(app, page, row, "Character options",
                      "Experimental features depend on the selected hero and recorded data. "
                      "Check coverage after export.")
    for text, key in (("Cloth / secondary motion (experimental)", "cloth_enabled"),
                      ("Model bone rules (experimental)", "model_constraints"),
                      ("Material repairs / approximate glass (experimental)", "material_repairs")):
        _check(app, body, text, app.values[key])
        if key == "model_constraints":
            _note(body, "Applies supported model rules for how bones aim at, follow or twist with other bones.\n"
                        "This can refine the recorded pose. It does not simulate cloth or physics.",
                  pady=(0, ROW_GAP))

    body, row = _card(app, page, row, "Particle simulation",
                      "Optional event simulation. Unsupported events are reported and omitted.",
                      label_column=WIDE_LABEL_COLUMN)
    _check(app, body, "Simulate recorded particle events", app.values["simulate_effects"], pady=(0, ROW_GAP))
    _path_row(app, body, "Particle simulator", "particle_probe", app._browse_particle_probe)
    _path_row(app, body, ".NET (DLL only)", "particle_dotnet", app._browse_particle_dotnet)
    _path_row(app, body, "Earlier history (ticks)", "particle_history_ticks", None, button=False)
    _note(body, "0 keeps the selected range. Effects follow the export camera; "
                "use AGR_Retarget_Effects.py after changing cameras.", pady=(8, 0))

    body, row = _card(app, page, row, "Output policy")
    _check(app, body, "Allow partial export / movement placeholders", app.partial, True, False)
    _check(app, body, "Overwrite existing scene and companion package", app.overwrite, True, False)
    return row


# --------------------------------------------------------------------------
# Development-only lighting page.


def _build_map(app, page):
    body, row = _card(app, page, 0, "Map region",
                      "The lighting tools read one prepared region of the installed map. "
                      "The region is decoded once per map and reused; map geometry is not exported in this beta.",
                      label_column=WIDE_LABEL_COLUMN)
    _check(app, body, "Prepare installed map region", app.values["map_enabled"])
    _path_row(app, body, "Installed map", "map_name", None, button=False)
    _path_row(app, body, "Extra margin (metres)", "map_margin", None, button=False)
    _path_row(app, body, "Regional exporter", "world_region_probe", app._browse_world_region_probe)
    _path_row(app, body, ".NET executable", "lighting_dotnet", app._browse_lighting_dotnet)
    _check(app, body, "Characters only; omit prepared map geometry", app.values["character_only"])
    _note(body, "Source lighting on the Lighting lab page needs this region. "
                "Add maps separately in Blender.")
    return row


def _development_lighting(app, page):
    body, row = _card(app, page, 0, "Lighting lab",
                      "Experimental character look. Partial and artistic by design; the coverage report lists what was and was not applied.",
                      label_column=WIDE_LABEL_COLUMN)
    for text, key in (("Partial static diffuse lighting", "source_lighting"),
                      ("Partial hero probe lighting / reflections", "source_hero_lighting"),
                      ("Partial NPR indirect (requires partial export and follow camera)", "source_npr_lighting")):
        _check(app, body, text, app.values[key])
    for label, key, command in (
        ("Probe atlas decoder", "probe_atlas_decoder",
         lambda: app._browse_hero_decoder("probe_atlas_decoder", "ProbeAtlasDecode.dll")),
        ("Cubemap decoder", "cubemap_decoder",
         lambda: app._browse_hero_decoder("cubemap_decoder", "CubemapDecode.dll")),
        ("Elagrimm shader .blend", "character_shader", app._browse_character_shader)):
        _path_row(app, body, label, key, command)
    _note(body, "Prepare the map region on the Map tab first. The optional "
                "Elagrimm's BLENDER-Deadlock-Shader.blend uses Eevee, a partial bridge and artistic rim. "
                "Other shader libraries are not supported.")
    return row


# --------------------------------------------------------------------------
# Top-level assembly.


def _build_updates(app, page):
    body, row = _card(app, page, 0, "RenderLock updates", f"Installed version: {__version__}")
    app.update_status = tk.StringVar(value="Check for new RenderLock releases.")
    _label(body, "Checks contact GitHub. Downloads and installation start only when you click.",
           wraplength=px(TEXT_WRAP), justify="left").grid(row=_next_row(body), column=0, columnspan=3, sticky="w")
    _check(app, body, "Check for updates when RenderLock opens", app.values["check_updates"], pady=(12, 8))
    status = _label(body, "", wraplength=px(TEXT_WRAP), justify="left")
    status.configure(textvariable=app.update_status)
    status.grid(row=_next_row(body), column=0, columnspan=3, sticky="w", pady=_pad((0, 10)))
    buttons = tk.Frame(body, background=gui_theme.CARD)
    buttons.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew")
    app.update_check = RoundedButton(buttons, text="Check for updates", command=lambda: app.update_ui.check(),
                                    theme=gui_theme.palette(), height=34)
    app.update_check.pack(side="left", padx=_pad((0, 8)))
    app.update_action = RoundedButton(buttons, text="Download update", command=lambda: app.update_ui.action(),
                                     style="primary", theme=gui_theme.palette(), height=34)
    app.update_action.pack(side="left")
    body, row = _card(app, page, row, "What's new")
    notes_frame = tk.Frame(body, background=gui_theme.FIELD)
    notes_frame.grid(row=_next_row(body), column=0, columnspan=3, sticky="ew")
    notes_frame.columnconfigure(0, weight=1)
    app.update_notes = tk.Text(notes_frame, height=12, wrap="word", state="disabled", bd=0,
        padx=px(12), pady=px(12), background=gui_theme.FIELD, foreground=gui_theme.TEXT,
        font=gui_theme.FONT, highlightthickness=0)
    app.update_notes.grid(row=0, column=0, sticky="ew")
    scroll = ttk.Scrollbar(notes_frame, orient="vertical", command=app.update_notes.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    app.update_notes.configure(yscrollcommand=scroll.set)
    app.update_notes_link = RoundedButton(body, text="Open full release on GitHub", style="quiet",
        command=lambda: app.update_ui.open_notes(), theme=gui_theme.palette())
    app.update_notes_link.grid(row=_next_row(body), column=0, columnspan=3, sticky="w", pady=_pad((8, 0)))


def build(app):
    beta = character_beta()
    root = app.root
    gui_theme.initialize(root)
    root.configure(background=gui_theme.BG)
    root.title(f"RenderLock | {__version__}" + (" · Character beta" if beta else " · Development"))
    app.app_icon = gui_theme.brand_window(root)
    root.geometry(f"{px(1040)}x{px(760)}")
    root.minsize(px(900), px(660))

    shell = tk.Frame(root, background=gui_theme.BG)
    shell.pack(fill="both", expand=True)
    shell.columnconfigure(1, weight=1)
    shell.rowconfigure(0, weight=1)
    _build_rail(app, shell, beta)

    # Content sits directly on the window color; cards provide the raised surfaces.
    main = tk.Frame(shell, background=gui_theme.BG)
    main.grid(row=0, column=1, sticky="nsew", padx=px(CONTENT_PAD_X), pady=_pad((HEADER_TOP, 21)))
    main.columnconfigure(0, weight=1)
    main.rowconfigure(1, weight=1)

    header = tk.Frame(main, background=gui_theme.BG)
    header.grid(row=0, column=0, sticky="ew", pady=_pad((0, 26)))
    app.page_title = tk.Label(header, text=PAGE_EXPORT, font=gui_theme.FONT_TITLE,
                              background=gui_theme.BG, foreground=gui_theme.TEXT)
    app.page_title.pack(anchor="w")
    app.page_subtitle = tk.Label(header, text=PAGE_DETAILS[PAGE_EXPORT], font=gui_theme.FONT_SMALL,
                                 background=gui_theme.BG, foreground=gui_theme.MUTED)
    app.page_subtitle.pack(anchor="w", pady=_pad((TITLE_GAP, 0)))

    body = tk.Frame(main, background=gui_theme.BG)
    body.grid(row=1, column=0, sticky="nsew")
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)

    app.page_frames = {}
    for name, builder in ((PAGE_EXPORT, _build_export), (PAGE_SETUP, _build_setup),
                          (PAGE_ADVANCED, _build_advanced), (PAGE_MAP, _build_map),
                          (PAGE_LIGHTING, _development_lighting), (PAGE_UPDATES, _build_updates)):
        page_shell, page = _page(body)
        app.page_frames[name] = page_shell
        builder(app, page)

    # Log drawer, hidden by default: a field-colored card above the footer.
    app.log_frame = Card(main, theme=gui_theme.palette(), fill=gui_theme.FIELD, radius=9, pad_x=12, pad_y=10)
    app.log_frame.grid(row=2, column=0, sticky="ew", pady=_pad((12, 0)))
    log_body = app.log_frame.body
    log_body.columnconfigure(0, weight=1)
    app.log = tk.Text(log_body, wrap="word", state="disabled", height=6, font=gui_theme.FONT_LOG,
                      background=gui_theme.FIELD, foreground=gui_theme.MUTED, insertbackground=gui_theme.TEXT,
                      relief="flat", bd=0, padx=0, pady=0, highlightthickness=0)
    app.log.grid(row=0, column=0, sticky="ew")
    log_bar = ttk.Scrollbar(log_body, orient="vertical", command=app.log.yview)

    def log_scrolled(first, last):
        # Show the log scrollbar only once the text overflows the drawer.
        if float(first) <= 0.0 and float(last) >= 1.0:
            log_bar.grid_remove()
        else:
            log_bar.grid(row=0, column=1, sticky="ns", padx=_pad((8, 0)))
        log_bar.set(first, last)

    app.log.configure(yscrollcommand=log_scrolled)

    # Footer: text links on the left, the job buttons on the right.
    actions = tk.Frame(main, background=gui_theme.BG)
    actions.grid(row=3, column=0, sticky="ew", pady=_pad((14, 0)))
    RoundedButton(actions, text="Open output folder", style="quiet", pad_x=0, command=app._open_output,
                  theme=gui_theme.palette()).pack(side="left", padx=_pad((0, 20)))
    app.log_button = RoundedButton(actions, text="Show log", style="quiet", pad_x=0,
                                   command=lambda: set_log_visible(app, not bool(app.log_frame.winfo_manager())),
                                   theme=gui_theme.palette())
    app.log_button.pack(side="left")
    app.convert_button = RoundedButton(actions, text="Export scene", style="primary", pad_x=13,
                                       command=app._convert, theme=gui_theme.palette())
    app.convert_button.pack(side="right")
    app.mutable_widgets.append(app.convert_button)
    app.cancel_button = RoundedButton(actions, text="Cancel", pad_x=13, command=app._cancel,
                                      theme=gui_theme.palette())
    app.cancel_button.configure(state="disabled")
    app.cancel_button.pack(side="right", padx=_pad((0, 5)))

    # Status on its own line, pinned to the bottom of the window.
    status_row = tk.Frame(main, background=gui_theme.BG, height=px(36))
    status_row.grid(row=4, column=0, sticky="ew")
    status_row.pack_propagate(False)
    app.status_dot = StatusDot(status_row, color=gui_theme.DOT_READY, background=gui_theme.BG, size=6)
    app.status_dot.pack(side="left", padx=_pad((1, 7)))
    status_label = tk.Label(status_row, textvariable=app.status, background=gui_theme.BG,
                            foreground=gui_theme.MUTED, font=gui_theme.FONT, justify="left", anchor="w")
    status_label.pack(side="left", fill="x", expand=True)
    ResizeRedraw(status_label, lambda width, _height: status_label.configure(wraplength=max(px(120), width)), idle=False)

    # Progress line in a fixed-height slot, so starting a job never moves anything.
    slot = tk.Frame(main, background=gui_theme.BG, height=px(3))
    slot.grid(row=5, column=0, sticky="ew", pady=_pad((6, 0)))
    slot.grid_propagate(False)
    slot.columnconfigure(0, weight=1)
    slot.rowconfigure(0, weight=1)
    app.progress = ProgressLine(slot, theme=gui_theme.palette(), height=3, background=gui_theme.BG)
    app.progress.grid(row=0, column=0, sticky="nsew")
    app.progress.grid_remove()

    set_log_visible(app, False)
    show_page(app, PAGE_EXPORT)
    app._mode_changed()
    app._append_log("Choose a recording, check Setup, then inspect a short range.\n" + app.discovery_note)
