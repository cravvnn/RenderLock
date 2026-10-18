"""The same RenderLock branding throughout install, recovery and failure UI."""
from __future__ import annotations

import tkinter as tk

from . import gui_theme as theme
from .gui_widgets import Card, ProgressLine, RoundedButton


class UpdateWindow:
    def __init__(self, *, recover=False, root=None):
        if root is None:
            theme.enable_dpi_awareness()
            root = tk.Tk()
        self.root = root
        theme.initialize(root)
        self.icon = theme.brand_window(root)
        root.title("RenderLock | " + ("Recovery" if recover else "Updating"))
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", lambda: None)
        self._size(560, 270)
        self.card = Card(root, theme=theme.palette(), pad_x=24, pad_y=22)
        self.card.pack(fill="both", expand=True, padx=theme.px(18), pady=theme.px(18))
        body = self.card.body
        body.columnconfigure(1, weight=1)
        self.logo = self.icon.subsample(max(1, round(self.icon.width() / theme.px(72))))
        self.logo_label = tk.Label(body, image=self.logo, background=theme.CARD, bd=0)
        self.logo_label.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, theme.px(18)))
        self.wordmark = tk.Label(body, text="RenderLock", background=theme.CARD,
                                foreground=theme.ACCENT, font=theme.FONT_WORDMARK)
        self.wordmark.grid(row=0, column=1, sticky="w", pady=(theme.px(4), 0))
        self.title = tk.Label(body, text="Recovering your previous version" if recover else "Installing your update",
            background=theme.CARD, foreground=theme.TEXT, font=theme.FONT_BOLD,
            justify="left", wraplength=theme.px(340))
        self.title.grid(row=1, column=1, sticky="nw", pady=(theme.px(4), theme.px(14)))
        self.status = tk.Label(body, text="Restoring the saved application files..." if recover else
            "Verifying and installing RenderLock. The app will reopen when it is ready.",
            background=theme.CARD, foreground=theme.MUTED, font=theme.FONT,
            justify="left", anchor="w", wraplength=theme.px(460))
        self.status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(theme.px(12), theme.px(18)))
        self.progress = ProgressLine(body, theme=theme.palette(), background=theme.CARD, height=3)
        self.progress.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.progress.start()

    def _size(self, width, height):
        root = self.root
        width, height = theme.px(width), theme.px(height)
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")

    def show_error(self, detail, close):
        self.progress.stop()
        self.progress.grid_remove()
        self.root.title("RenderLock | Update needs attention")
        self._size(560, 430)
        self.title.configure(text="The update could not finish")
        self.status.configure(text="Your backup and recovery files have been retained. Review the details below.")
        body = self.card.body
        self.details = tk.Text(body, height=6, width=1, wrap="word", bd=0, highlightthickness=0,
            background=theme.FIELD, foreground=theme.TEXT, font=theme.FONT_SMALL,
            padx=theme.px(10), pady=theme.px(10))
        self.details.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.details.insert("1.0", detail)
        self.details.configure(state="disabled")
        # Keyboard scrolling works even when a long diagnostic exceeds the box.
        self.close_button = RoundedButton(body, text="Close", style="primary", command=close,
                                           theme=theme.palette())
        self.close_button.grid(row=5, column=0, columnspan=2, sticky="e", pady=(theme.px(14), 0))
        self.root.protocol("WM_DELETE_WINDOW", close)
