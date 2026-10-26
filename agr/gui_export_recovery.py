"""Branded, explicit export recovery; closing the window never starts a job."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

from . import gui_theme as theme
from .gui_chrome import TitleBar, WindowChrome
from .gui_widgets import Card, DisplayText, RoundedButton, RoundedEntry, copy_text


def failure_details(output):
    """Read only the diagnostic paths printed by this conversion."""
    failure, work, bundle = {}, None, None
    for line in output.splitlines():
        if line.startswith('RENDERLOCK_DIAGNOSTICS '):
            try:
                event = json.loads(line[len('RENDERLOCK_DIAGNOSTICS '):])
                if isinstance(event, dict):
                    if isinstance(event.get('failure'), dict):
                        failure = event['failure']
                    if isinstance(event.get('work_directory'), str):
                        work = Path(event['work_directory'])
                    if event.get('status') == 'saved' and isinstance(event.get('path'), str):
                        bundle = Path(event['path'])
            except ValueError:
                pass
        if line.startswith('Diagnostic files retained at: '):
            work = Path(line.partition(': ')[2].strip())
        for prefix in ('Diagnostic ZIP saved at: ', 'Diagnostic ZIP: ', 'Support ZIP: '):
            if line.startswith(prefix):
                bundle = Path(line[len(prefix):].strip())
    if work is not None and not failure:
        try:
            with (work / 'failure.json').open(encoding='utf-8') as stream:
                document = json.loads(stream.read(1024 * 1024))
            if isinstance(document, dict):
                failure = document
        except (OSError, ValueError):
            pass
    if not failure.get('error'):
        errors = [line[len('AGR error: '):] for line in output.splitlines()
                  if line.startswith('AGR error: ')]
        failure['error'] = errors[-1] if errors else 'See the export log for the last completed step.'
    return failure, work, bundle


def show_failure(app, command, output):
    from .export_recovery import recovery_choices
    failure, work, bundle = failure_details(output)
    choices = recovery_choices(command, failure, output)
    window = tk.Toplevel(app.root)
    try:
        return _show_failure_window(app, command, output, window, failure, work, bundle, choices)
    finally:
        # Destroying this modal also releases its grab, if display failed after
        # grab_set. Never inspect or change another window's grab or children.
        try:
            window.destroy()
        except tk.TclError:
            pass  # A choice, normal close, or root shutdown already destroyed it.


def _show_failure_window(app, command, output, window, failure, work, bundle, choices):
    from .export_recovery import recovery_summary
    window.withdraw()
    window.title('RenderLock | Export recovery')
    window.configure(background=theme.BACKDROP)
    window.transient(app.root)
    window.resizable(True, True)
    window.icon = theme.brand_window(window)
    window.minsize(theme.px(480), theme.px(400))
    logo = theme.brand_emblem(window)
    window.logo = logo
    # Unlike the main window, this small dialog has no deferred repaint loop.
    # Keep its native content visible and clickable when Windows resizes it.
    window.chrome = chrome = WindowChrome(window, defer_resize_redraw=False)
    if chrome.prepare():
        window.title_bar = TitleBar(window, chrome, theme=theme.palette(), buttons=('close',),
                                    links=False, title='RenderLock | Export recovery', icon=logo)
        window.title_bar.pack(side='top', fill='x')
        mapped = [False]

        def first_map(event):
            if event.widget is not window or mapped[0]:
                return
            mapped[0] = True
            if not chrome.finish():
                window.title_bar.pack_forget()
            elif not chrome.native_border:
                window.configure(highlightthickness=theme.hairline(), highlightbackground=theme.WINDOW_EDGE)

        window.bind('<Map>', first_map, add='+')
    card = Card(window, theme=theme.palette(), pad_x=22, pad_y=20, background=theme.BACKDROP)
    card.pack(fill='both', expand=True, padx=theme.px(12), pady=theme.px(12))
    body = card.body
    body.columnconfigure(0, weight=1)
    header = tk.Frame(body, background=theme.CARD)
    header.grid(row=0, column=0, sticky='ew', pady=(0, theme.px(14)))

    def label(parent, **options):
        options.setdefault('font', theme.FONT)
        options.setdefault('foreground', theme.TEXT)
        options.setdefault('pady', theme.descent_shortfall(options['font']))
        return tk.Label(parent, background=theme.CARD, anchor='w', justify='left', **options)

    label(header, image=logo).pack(side='left', padx=(0, theme.px(5)))
    DisplayText(header, text='RenderLock', font=theme.FONT_WORDMARK, foreground=theme.BRAND,
                room=theme.descent_shortfall(theme.FONT_WORDMARK), background=theme.CARD).pack(side='left')
    label(body, text='Choose how to retry this export' if choices else 'The export could not finish',
              font=theme.FONT_SECTION).grid(row=1, column=0, sticky='w')
    label(body, text=recovery_summary(command, failure, output), wraplength=theme.px(560),
              foreground=theme.MUTED).grid(row=2, column=0, sticky='ew', pady=(theme.px(8), 0))
    detail = tk.Text(body, height=5, wrap='word', font=theme.FONT, background=theme.FIELD,
                     foreground=theme.TEXT, relief='flat', padx=theme.px(10), pady=theme.px(8))
    detail.grid(row=3, column=0, sticky='nsew', pady=(theme.px(10), theme.px(12)))
    detail.insert('1.0', str(failure['error']))
    detail.configure(state='disabled')
    scrollbar = ttk.Scrollbar(body, orient='vertical', command=detail.yview)
    scrollbar.grid(row=3, column=1, sticky='ns', pady=(theme.px(10), theme.px(12)))
    detail.configure(yscrollcommand=scrollbar.set)
    body.rowconfigure(3, weight=1)
    selected = []

    def choose(choice):
        selected.append(choice)
        window.destroy()

    row = 4
    for choice in choices:
        RoundedButton(body, text=choice.title, command=lambda value=choice: choose(value),
                      theme=theme.palette()).grid(row=row, column=0, sticky='w', pady=(0, theme.px(5)))
        label(body, text=choice.description, wraplength=theme.px(560), foreground=theme.MUTED).grid(
            row=row + 1, column=0, sticky='ew', pady=(0, theme.px(12)))
        row += 2
    note = ('A retry may repeat preparation and scene creation, reusing verified cached assets. '
            'Review the final coverage report for anything omitted.' if choices else
            'Review your settings or share the diagnostic ZIP so this failure can be investigated.')
    label(body, text=note, wraplength=theme.px(560), foreground=theme.MUTED).grid(row=row, column=0, sticky='ew')
    row += 1
    available_bundle = bundle is not None and bundle.is_file()
    location = bundle if available_bundle else work
    if location is not None:
        label(body, text='Diagnostic ZIP' if available_bundle else 'Retained diagnostic folder',
                  font=theme.FONT_BOLD).grid(row=row, column=0, sticky='w', pady=(theme.px(14), theme.px(5)))
        value = tk.StringVar(master=window, value=str(location))
        path_field = RoundedEntry(body, variable=value, theme=theme.palette(), font=theme.FONT, width=540)
        path_field.configure(state='readonly')
        path_field.grid(row=row + 1, column=0, sticky='ew')
        row += 2
    actions = tk.Frame(body, background=theme.CARD)
    actions.grid(row=row, column=0, sticky='ew', pady=(theme.px(16), 0))

    def open_location():
        if location is None:
            return
        path = location.parent if available_bundle else location
        try:
            if sys.platform == 'win32':
                os.startfile(path)
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])
        except OSError as exc:
            app._append_log(f'Could not open diagnostics: {exc}\n')

    if location is not None:
        RoundedButton(actions, text='Open diagnostics', command=open_location,
                      theme=theme.palette()).pack(side='left', padx=(0, theme.px(8)))
        RoundedButton(actions, text='Copy path', command=lambda: copy_text(window, str(location)),
                      theme=theme.palette()).pack(side='left')
    review = RoundedButton(actions, text='Review settings', command=window.destroy, theme=theme.palette())
    review.pack(side='right')
    window.protocol('WM_DELETE_WINDOW', window.destroy)
    window.bind('<Escape>', lambda _event: window.destroy())
    window.update_idletasks()
    width = max(theme.px(620), window.winfo_reqwidth())
    height = min(window.winfo_screenheight() - theme.px(80), max(theme.px(400), window.winfo_reqheight()))
    x = max(0, app.root.winfo_rootx() + (app.root.winfo_width() - width) // 2)
    y = max(0, app.root.winfo_rooty() + (app.root.winfo_height() - height) // 2)
    window.geometry(f'{width}x{height}+{x}+{y}')
    window.deiconify()
    window.wait_visibility()
    window.grab_set()
    review.focus_set()
    app.root.wait_window(window)
    return selected[0] if selected else None
