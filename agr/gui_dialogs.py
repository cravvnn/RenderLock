"""RenderLock message dialogs using the application's existing branded widgets.

An existing Tk window owns every dialog. Omitting ``parent`` uses Tk's current
default root; this module never creates a second interpreter or a native messagebox.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont, ttk

from . import gui_theme as theme
from .gui_chrome import TitleBar, WindowChrome
from .gui_widgets import Card, DisplayText, RoundedButton


def _exists(widget):
    try:
        return widget is not None and bool(widget.winfo_exists())
    except tk.TclError:
        return False


def _work_area(parent, chrome):
    """Use the existing chrome's monitor API, including secondary monitor origins."""
    try:
        api = chrome.api
        if api is not None:
            area = api.maximized_area(api.wintypes.HWND(int(parent.wm_frame(), 16)))
            if area is not None:
                left, top, width, height = area
                if width > 0 and height > 0:
                    return left, top, left + width, top + height
    except (AttributeError, OSError, ValueError, tk.TclError):
        pass
    left, top = parent.winfo_vrootx(), parent.winfo_vrooty()
    return left, top, left + parent.winfo_vrootwidth(), top + parent.winfo_vrootheight()


class _MessageDialog:
    def __init__(self, parent, title, message, *, question):
        self.parent = parent
        self.result = False
        self.finished = False
        self.window = window = tk.Toplevel(parent)
        window.withdraw()
        caption = f'RenderLock | {title}'
        window.title(caption)
        window.configure(background=theme.BACKDROP)
        # A transient of a withdrawn owner cannot become visible on some Tk builds.
        if parent.winfo_viewable():
            window.transient(parent)
        window.resizable(False, False)
        window.icon = theme.brand_window(window)
        window.logo = logo = theme.brand_emblem(window)
        window.chrome = chrome = WindowChrome(window, resizable=False)
        if chrome.prepare():
            window.title_bar = TitleBar(window, chrome, theme=theme.palette(), buttons=('close',),
                                        links=False, title=caption, icon=logo)
            window.title_bar.pack(side='top', fill='x')
            mapped = [False]

            def first_map(event):
                if event.widget is not window or mapped[0]:
                    return
                mapped[0] = True
                if not chrome.finish():
                    window.title_bar.pack_forget()
                elif not chrome.native_border:
                    window.configure(highlightthickness=theme.hairline(),
                                     highlightbackground=theme.WINDOW_EDGE)

            window.bind('<Map>', first_map, add='+')

        self.work_area = left, top, right, bottom = _work_area(parent, chrome)
        max_width = max(1, right - left - theme.px(48))
        max_height = max(1, bottom - top - theme.px(80))
        width = min(theme.px(620), max_width)
        wrap_width = max(1, width - theme.px(100))
        card = Card(window, theme=theme.palette(), pad_x=22, pad_y=20, background=theme.BACKDROP)
        card.pack(fill='both', expand=True, padx=theme.px(12), pady=theme.px(12))
        body = card.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        header = tk.Frame(body, background=theme.CARD)
        header.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, theme.px(14)))
        tk.Label(header, image=logo, background=theme.CARD).pack(side='left', padx=(0, theme.px(5)))
        DisplayText(header, text='RenderLock', font=theme.FONT_WORDMARK, foreground=theme.BRAND,
                    room=theme.descent_shortfall(theme.FONT_WORDMARK), background=theme.CARD).pack(side='left')
        tk.Label(body, text=str(title), font=theme.FONT_SECTION, background=theme.CARD,
                 foreground=theme.TEXT, anchor='w', justify='left', wraplength=wrap_width,
                 pady=theme.descent_shortfall(theme.FONT_SECTION)).grid(
                     row=1, column=0, columnspan=2, sticky='ew')
        message = str(message)
        font = tkfont.Font(root=window, font=theme.FONT)
        lines = sum(max(1, (font.measure(line) + wrap_width - 1) // wrap_width)
                    for line in message.splitlines())
        detail = tk.Text(body, width=1, height=min(12, max(3, lines)), wrap='word', font=theme.FONT,
                         background=theme.FIELD, foreground=theme.TEXT, relief='flat',
                         selectbackground=theme.SELECTION, selectforeground=theme.TEXT,
                         padx=theme.px(10), pady=theme.px(8))
        detail.grid(row=2, column=0, sticky='nsew', pady=(theme.px(10), theme.px(12)))
        detail.insert('1.0', message)
        detail.configure(state='disabled')
        scrollbar = ttk.Scrollbar(body, orient='vertical', command=detail.yview)
        scrollbar.grid(row=2, column=1, sticky='ns', pady=(theme.px(10), theme.px(12)))
        detail.configure(yscrollcommand=scrollbar.set)
        actions = tk.Frame(body, background=theme.CARD)
        actions.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(theme.px(4), 0))
        if question:
            self.initial_focus = RoundedButton(actions, text='No', command=self.cancel,
                                               theme=theme.palette())
            self.initial_focus.pack(side='right')
            RoundedButton(actions, text='Yes', command=lambda: self.choose(True),
                          theme=theme.palette()).pack(side='right', padx=(0, theme.px(8)))
        else:
            self.initial_focus = RoundedButton(actions, text='OK', command=self.cancel,
                                               theme=theme.palette())
            self.initial_focus.pack(side='right')
        window.protocol('WM_DELETE_WINDOW', self.cancel)
        window.bind('<Escape>', self.cancel)
        self.width, self.max_height = width, max_height

    def _position(self):
        window, parent = self.window, self.parent
        width = self.width
        window.update_idletasks()
        height = min(self.max_height, max(theme.px(280), window.winfo_reqheight()))
        left, top, right, bottom = self.work_area
        if parent.winfo_viewable():
            x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        else:
            x = left + (right - left - width) // 2
            y = top + (bottom - top - height) // 2
        x = max(left, min(x, right - width))
        y = max(top, min(y, bottom - height))
        # Keep the leading '+' for absolute negative coordinates. A lone '-' in
        # Tk geometry means an offset from the screen's right/bottom edge.
        window.geometry(f'{width}x{height}+{x}+{y}')

    def choose(self, value):
        if not self.finished:
            self.finished = True
            self.result = value is True
            if _exists(self.window):
                self.window.destroy()
        return 'break'

    def cancel(self, _event=None):
        return self.choose(False)

    def run(self):
        window = self.window
        try:
            self._position()
            window.deiconify()
            window.wait_visibility()
            if _exists(window):
                window.grab_set()
                self.initial_focus.focus_set()
                self.parent.wait_window(window)
        except tk.TclError:
            # Closing the dialog or its owner while Tk is mapping/waiting is cancellation.
            if _exists(window) and _exists(self.parent):
                raise
        finally:
            if _exists(window):
                window.destroy()
        return self.result


def _show(title, message, *, parent, question):
    if parent is None:
        parent = tk._default_root
    if parent is None:
        raise RuntimeError('RenderLock dialogs require an existing Tk window; pass parent=root.')
    if not _exists(parent):
        return False
    parent = parent.winfo_toplevel()
    previous_focus = parent.focus_get()
    previous_grab = parent.grab_current()
    previous_status = previous_grab.grab_status() if previous_grab is not None else None
    try:
        return _MessageDialog(parent, title, message, question=question).run()
    finally:
        try:
            if _exists(parent):
                current_grab = parent.grab_current()
                if _exists(previous_grab) and current_grab is None:
                    if previous_status == 'global':
                        previous_grab.grab_set_global()
                    else:
                        previous_grab.grab_set()
                if current_grab in (None, previous_grab) and _exists(previous_focus):
                    previous_focus.focus_set()
        except tk.TclError:
            # The previous owner may also have closed or become hidden while waiting.
            pass


def showerror(title, message, *, parent=None):
    """Show a branded error and wait until it is dismissed; return ``'ok'``."""
    _show(title, message, parent=parent, question=False)
    return 'ok'


def showinfo(title, message, *, parent=None):
    """Show branded information and wait until it is dismissed; return ``'ok'``."""
    _show(title, message, parent=parent, question=False)
    return 'ok'


def askyesno(title, message, *, parent=None) -> bool:
    """Return True only for an explicit Yes; No, Escape and close return False."""
    return _show(title, message, parent=parent, question=True) is True
