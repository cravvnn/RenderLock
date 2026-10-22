"""Measure real Tk canvas bounds; these checks do not qualify native Windows behavior."""
import json
from types import SimpleNamespace
import tkinter as tk

import pytest

from agr import gui_theme
from agr.gui_assets import APP_ICON_PNG
from agr.gui_chrome import TitleBar
from agr.gui_widgets import rgba_image


DPI_SCALES = (1.0, 1.25, 1.5, 2.0)
RECOVERY_TITLE = 'RenderLock | Export recovery'
WINDOW_BUTTONS = ('minimize', 'maximize', 'close')


class ChromeRecorder:
    def __init__(self):
        self.calls = []

    def zoomed(self):
        return False

    def start_move(self):
        self.calls.append('move')

    def minimize(self):
        self.calls.append('minimize')

    def toggle_maximize(self):
        self.calls.append('maximize')

    def close(self):
        self.calls.append('close')

    def system_menu(self, *position):
        self.calls.append('menu')


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    errors = []
    original_scaling = window.tk.call('tk', 'scaling')
    window.report_callback_exception = lambda *error: errors.append(error)
    window.geometry('700x180+20+20')
    window.update()
    try:
        yield window
    finally:
        window.tk.call('tk', 'scaling', original_scaling)
        window.destroy()
        assert not errors, errors


def scale_display(root, monkeypatch, scale):
    # Exercise Tk's point-sized fonts and RenderLock's design-pixel layout together.
    root.tk.call('tk', 'scaling', 96 * scale / 72)
    monkeypatch.setattr(gui_theme, 'SCALE', scale)


def titlebar(root, *, width=640, **options):
    chrome = ChromeRecorder()
    root.geometry(f'{gui_theme.px(width)}x{gui_theme.px(180)}+20+20')
    bar = TitleBar(root, chrome, theme=gui_theme.palette(), **options)
    bar.pack(fill='x')
    root.update()
    return bar, chrome


def caption_items(bar):
    items = bar.find_withtag('caption')
    return {bar.type(item): item for item in items}


def layout_bounds(bar, button_count):
    return {
        'widget': [bar.winfo_width(), bar.winfo_height()],
        'caption': {kind: list(bar.bbox(item)) for kind, item in caption_items(bar).items()},
        'buttons_left': bar.winfo_width() - button_count * gui_theme.px(46),
        'tk_scaling': float(bar.tk.call('tk', 'scaling')),
    }


@pytest.mark.parametrize('scale', DPI_SCALES)
@pytest.mark.parametrize('buttons', [('close',), WINDOW_BUTTONS, ()])
def test_recovery_logo_and_caption_have_clear_gap_and_do_not_cover_buttons(
        root, monkeypatch, record_property, scale, buttons):
    scale_display(root, monkeypatch, scale)
    original = tk.PhotoImage(master=root, data=APP_ICON_PNG)
    # Match the actual recovery caller, including its larger body logo.
    logo = original.subsample(max(1, (original.width() + gui_theme.px(32) - 1)
                                 // gui_theme.px(32)))
    original_size = (logo.width(), logo.height())
    body = tk.Label(root, image=logo)
    bar, _chrome = titlebar(root, links=False, title=RECOVERY_TITLE, icon=logo, buttons=buttons)
    bounds = layout_bounds(bar, len(buttons))
    image, text = (bounds['caption'][kind] for kind in ('image', 'text'))
    assert image[0] >= 0 and image[1] >= 0
    assert image[3] <= bar.winfo_height() - gui_theme.hairline()
    assert text[0] - image[2] >= gui_theme.px(8), bounds
    assert text[2] <= bounds['buttons_left'] - gui_theme.px(8), bounds
    assert bar.itemcget(caption_items(bar)['text'], 'text') == RECOVERY_TITLE
    # Fitting the title bar must not resize the caller's shared body image.
    assert (logo.width(), logo.height()) == original_size
    assert str(body.cget('image')) == str(logo)
    assert image[2] - image[0] < logo.width()
    record_property('layout', json.dumps(bounds))


@pytest.mark.parametrize('scale', DPI_SCALES)
@pytest.mark.parametrize('buttons', [('close',), WINDOW_BUTTONS, ()])
def test_long_title_is_single_line_ellipsized_and_refits_after_resize(
        root, monkeypatch, record_property, scale, buttons):
    scale_display(root, monkeypatch, scale)
    logo = tk.PhotoImage(master=root, width=gui_theme.px(32), height=gui_theme.px(32))
    logo.put('#aa8844', to=(0, 0, logo.width(), logo.height()))
    full_title = RECOVERY_TITLE + ' | ' + ('A long diagnostic caption \u03b1\u03b2\u03b3 ' * 40)
    bar, _chrome = titlebar(root, links=False, icon=logo, title=full_title, buttons=buttons)
    first_text = bar.itemcget(caption_items(bar)['text'], 'text')
    assert first_text.endswith('\u2026') and full_title.startswith(first_text[:-1])
    observations = []
    for width in (640, 260, 160, 64, 640):
        root.geometry(f'{gui_theme.px(width)}x{gui_theme.px(180)}+20+20')
        root.update()
        bounds = layout_bounds(bar, len(buttons))
        observations.append(bounds)
        for box in bounds['caption'].values():
            assert box[0] >= 0 and box[1] >= 0, bounds
            assert box[2] <= bounds['buttons_left'] - gui_theme.px(8), bounds
            assert box[3] <= bar.winfo_height() - gui_theme.hairline(), bounds
        if 'text' in caption_items(bar):
            item = caption_items(bar)['text']
            displayed = bar.itemcget(item, 'text')
            assert '\n' not in displayed and '\r' not in displayed
            assert displayed.endswith('\u2026')
    assert bar.itemcget(caption_items(bar)['text'], 'text') == first_text
    bar.set_title('RenderLock')
    assert bar.itemcget(caption_items(bar)['text'], 'text') == 'RenderLock'
    bar.set_title('First line\nSecond line\tFinal line')
    displayed = bar.itemcget(caption_items(bar)['text'], 'text')
    assert '\n' not in displayed and '\t' not in displayed
    record_property('resize_layouts', json.dumps(observations))


@pytest.mark.parametrize('scale', DPI_SCALES)
@pytest.mark.parametrize('size', [(96, 48), (48, 96)])
def test_non_square_icon_retains_aspect_content_and_transparency(root, monkeypatch, scale, size):
    scale_display(root, monkeypatch, scale)
    width, height = size
    logo = tk.PhotoImage(master=root, width=width, height=height)
    logo.put('#ff0000', to=(0, 0, width // 2, height // 2))
    logo.put('#00ff00', to=(width // 2, 0, width, height // 2))
    logo.put('#0000ff', to=(0, height // 2, width // 2, height))
    # The fourth quadrant stays transparent.
    bar, _chrome = titlebar(root, links=False, title=RECOVERY_TITLE, icon=logo, buttons=('close',))
    item = caption_items(bar)['image']
    left, top, right, bottom = bar.bbox(item)
    rendered_width, rendered_height = right - left, bottom - top
    assert top >= 0 and bottom <= bar.winfo_height() - gui_theme.hairline()
    assert abs(rendered_width * height - rendered_height * width) <= max(size)
    image = bar.itemcget(item, 'image')
    assert root.tk.call(image, 'get', 0, 0) == (255, 0, 0)
    assert root.tk.call(image, 'get', rendered_width - 1, 0) == (0, 255, 0)
    assert root.tk.call(image, 'get', 0, rendered_height - 1) == (0, 0, 255)
    assert root.tk.getboolean(root.tk.call(image, 'transparency', 'get',
                                         rendered_width - 1, rendered_height - 1))
    assert (logo.width(), logo.height()) == size


def test_fitted_icon_preserves_partial_alpha_against_light_and_dark_backgrounds(root, monkeypatch):
    scale_display(root, monkeypatch, 1.25)
    logo = rgba_image(root, 64, 64, [bytes((200, 120, 40, 128)) * 64 for _ in range(64)])
    bar, _chrome = titlebar(root, links=False, title=RECOVERY_TITLE, icon=logo, buttons=('close',))
    fitted = bar.itemcget(caption_items(bar)['image'], 'image')
    for background in ('#101010', '#f0f0f0'):
        composites = []
        for source in (str(logo), fitted):
            pixel = tk.PhotoImage(master=root, width=1, height=1)
            pixel.put(background, to=(0, 0, 1, 1))
            root.tk.call(str(pixel), 'copy', source, '-from', 0, 0, 1, 1, '-to', 0, 0)
            composites.append(pixel.get(0, 0))
        assert composites[0] == composites[1]
        assert composites[0] != (200, 120, 40), 'Partial alpha must not become opaque'


@pytest.mark.parametrize('scale', DPI_SCALES)
def test_title_without_icon_and_empty_title_update_cleanly(root, monkeypatch, scale):
    scale_display(root, monkeypatch, scale)
    bar, _chrome = titlebar(root, width=240, links=False, title=RECOVERY_TITLE,
                           buttons=WINDOW_BUTTONS)
    assert list(caption_items(bar)) == ['text']
    bounds = layout_bounds(bar, 3)
    assert bounds['caption']['text'][2] <= bounds['buttons_left'] - gui_theme.px(8)
    bar.set_title('')
    assert not caption_items(bar)
    bar.set_title('RenderLock')
    assert bar.itemcget(caption_items(bar)['text'], 'text') == 'RenderLock'
    bar._set_active(False)
    assert bar.itemcget(caption_items(bar)['text'], 'fill') == bar._theme['MUTED']


@pytest.mark.parametrize('scale', DPI_SCALES)
def test_canvas_events_keep_button_hit_areas_and_window_action_routing(root, monkeypatch, scale):
    scale_display(root, monkeypatch, scale)
    logo = tk.PhotoImage(master=root, width=gui_theme.px(32), height=gui_theme.px(32))
    bar, chrome = titlebar(root, links=False, icon=logo, title=RECOVERY_TITLE * 30)
    y = bar.winfo_height() // 2
    for index, name in enumerate(WINDOW_BUTTONS):
        left = bar.winfo_width() - (3 - index) * gui_theme.px(46)
        assert bar._button_at(left) == name
        assert bar._button_at(left + gui_theme.px(46) - 1) == name
        x = left + gui_theme.px(46) // 2
        bar.event_generate('<ButtonPress-1>', x=x, y=y)
        bar.event_generate('<ButtonRelease-1>', x=x, y=y)
    assert chrome.calls == list(WINDOW_BUTTONS)
    chrome.calls.clear()
    bar.event_generate('<ButtonPress-1>', x=gui_theme.px(20), y=y)
    bar.event_generate('<ButtonRelease-1>', x=gui_theme.px(20), y=y)
    assert chrome.calls == ['move']
    bar._on_double(SimpleNamespace(x=gui_theme.px(20), y=y))
    assert chrome.calls == ['move', 'maximize']
    bar.event_generate('<ButtonRelease-3>', x=gui_theme.px(20), y=y)
    assert chrome.calls[-1] == 'menu'
    chrome.calls.clear()
    bar.event_generate('<ButtonPress-1>', x=bar.winfo_width() - 2, y=y)
    bar.event_generate('<ButtonRelease-1>', x=gui_theme.px(20), y=y)
    assert not chrome.calls, 'Releasing outside Close must cancel the button press'


@pytest.mark.parametrize('scale', DPI_SCALES)
def test_link_mode_keeps_credits_clickable_and_separate_from_window_actions(root, monkeypatch, scale):
    scale_display(root, monkeypatch, scale)
    opened = []
    monkeypatch.setattr('agr.gui_chrome.webbrowser.open', opened.append)
    logo = tk.PhotoImage(master=root, width=256, height=256)
    bar, chrome = titlebar(root, links=True, title=RECOVERY_TITLE * 20, icon=logo)
    assert not caption_items(bar)
    assert any(bar.itemcget(item, 'text') == 'Made by ' for item in bar.find_withtag('link')
               if bar.type(item) == 'text')
    assert any(bar.itemcget(item, 'text') == 'Cravvnn' for item in bar.find_withtag('link')
               if bar.type(item) == 'text')
    for _name, left, right in bar._link_boxes:
        x, y = int((left + right) / 2), bar.winfo_height() // 2
        bar.event_generate('<Motion>', x=x, y=y)
        assert bar.cget('cursor') == 'hand2'
        bar.event_generate('<ButtonPress-1>', x=x, y=y)
        bar.event_generate('<ButtonRelease-1>', x=x, y=y)
        bar._on_double(SimpleNamespace(x=x, y=y))
    assert opened == ['https://www.youtube.com/results?search_query=cravvnn',
                      'https://discord.gg/XTefeBUqX5', 'https://github.com/cravvnn']
    assert not chrome.calls
    bar.set_title('Updated window title')
    assert not caption_items(bar)
