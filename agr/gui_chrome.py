"""Window chrome: the design's own title bar on a frameless window that still behaves like Windows.

Tk draws everything inside the window, including the title bar and its caption buttons. Windows
keeps the native behavior: dragging the bar moves the window with snapping, the edges resize it,
double-clicking or dragging to the top of the screen maximizes it, and the taskbar button, Alt+F4,
Win+arrow keys, minimize/restore animations and the window shadow all keep working.

How: Tk is told the window has no frame (override-redirect), so Tk's idea of the client area is
always exactly the window. The frame *styles* are then put back with ctypes and a small window
procedure tells Windows the frame has zero size. Moving and resizing start Windows' own modal
loops (WM_NCLBUTTONDOWN with a hit-test code), so they feel native.

Windows only. If any step is unavailable, ``install`` returns False and the caller keeps the
normal Windows title bar.
"""
from __future__ import annotations

import sys
import tkinter as tk

from . import gui_theme
from .gui_theme import hairline, px

BAR_HEIGHT = 35           # design pixels
BUTTON_WIDTH = 46         # caption button width, as in Windows
EDGE = 5                  # resize zone inside each window edge
CORNER = 14               # corner zones are longer, so diagonal resizing is easy to hit

# Title bar paint from the design: a horizontal gradient and a 1 px bottom line that fades in and out.
BAR_STOPS = ((0.0, "#131111"), (0.41, "#0d0d0c"), (0.67, "#0d0d0c"), (1.0, "#131110"))
LINE_STOPS = ((0.0, "#252424", 0.0), (0.15, "#1e1c1c", 1.0), (0.76, "#252424", 0.11), (1.0, "#252424", 0.0))

# Segoe Fluent Icons / MDL2 caption glyphs (the ones Windows itself uses).
GLYPHS = {"minimize": "", "maximize": "", "restore": "", "close": ""}
CLOSE_HOVER = "#8f2f24"
CLOSE_PRESSED = "#7a281f"

_GWL_STYLE, _GWL_EXSTYLE, _GWLP_WNDPROC = -16, -20, -4
_WS_CAPTION, _WS_THICKFRAME, _WS_SYSMENU = 0x00C00000, 0x00040000, 0x00080000
_WS_MINIMIZEBOX, _WS_MAXIMIZEBOX = 0x00020000, 0x00010000
_WS_EX_TOOLWINDOW, _WS_EX_APPWINDOW = 0x00000080, 0x00040000
_WM_WINDOWPOSCHANGING, _WM_NCCALCSIZE, _WM_NCLBUTTONDOWN = 0x0046, 0x0083, 0x00A1
_WM_SYSCOMMAND, _WM_CLOSE = 0x0112, 0x0010
_WM_ENTERSIZEMOVE, _WM_EXITSIZEMOVE = 0x0231, 0x0232
_SC_RESTORE, _SC_MAXIMIZE, _SC_MINIMIZE = 0xF120, 0xF030, 0xF020
_HT = {"caption": 2, "w": 10, "e": 11, "n": 12, "nw": 13, "ne": 14, "s": 15, "sw": 16, "se": 17}
_CURSORS = {"w": "size_we", "e": "size_we", "n": "size_ns", "s": "size_ns",
            "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw"}


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _mix(stops, t):
    t = min(1.0, max(0.0, t))
    for (p0, *c0), (p1, *c1) in zip(stops, stops[1:]):
        if t <= p1:
            k = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            return [a + (b - a) * k for a, b in zip(_flat(c0), _flat(c1))]
    return _flat(stops[-1][1:])


def _flat(values):
    """("#rrggbb"[, alpha]) -> [r, g, b(, alpha)] as floats."""
    out = list(_rgb(values[0]))
    return out + list(values[1:])


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c + 0.5))) for c in rgb)


class _Win32:
    """The handful of user32/dwmapi calls the chrome needs, with 64-bit-safe signatures."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32 = user32
        self.LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(self.LRESULT, wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM)
        for name, args, res in (
                ("GetWindowLongPtrW", (wintypes.HWND, ctypes.c_int), ctypes.c_ssize_t),
                ("SetWindowLongPtrW", (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t), ctypes.c_ssize_t),
                ("CallWindowProcW", (ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM), self.LRESULT),
                ("SetWindowPos", (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.UINT), wintypes.BOOL),
                ("ShowWindow", (wintypes.HWND, ctypes.c_int), wintypes.BOOL),
                ("IsZoomed", (wintypes.HWND,), wintypes.BOOL),
                ("IsIconic", (wintypes.HWND,), wintypes.BOOL),
                ("ReleaseCapture", (), wintypes.BOOL),
                ("SendMessageW", (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM), self.LRESULT),
                ("PostMessageW", (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM), wintypes.BOOL),
                ("MonitorFromWindow", (wintypes.HWND, wintypes.DWORD), wintypes.HMONITOR),
                ("GetMonitorInfoW", (wintypes.HMONITOR, ctypes.c_void_p), wintypes.BOOL),
                ("GetCursorPos", (ctypes.POINTER(wintypes.POINT),), wintypes.BOOL),
                ("GetSystemMenu", (wintypes.HWND, wintypes.BOOL), wintypes.HMENU),
                ("TrackPopupMenu", (wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.HWND, ctypes.c_void_p), wintypes.BOOL),
                ("EnableMenuItem", (wintypes.HMENU, wintypes.UINT, wintypes.UINT), wintypes.BOOL),
                ("SetMenuDefaultItem", (wintypes.HMENU, wintypes.UINT, wintypes.UINT), wintypes.BOOL),
                ("RedrawWindow", (wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT), wintypes.BOOL),
                ("GetWindowRect", (wintypes.HWND, ctypes.POINTER(wintypes.RECT)), wintypes.BOOL),
                ("GetClientRect", (wintypes.HWND, ctypes.POINTER(wintypes.RECT)), wintypes.BOOL),
                ("FillRect", (wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH), ctypes.c_int)):
            function = getattr(user32, name)
            function.argtypes, function.restype = args, res
        self.gdi32 = ctypes.WinDLL("gdi32")
        self.gdi32.CreateSolidBrush.argtypes = (wintypes.COLORREF,)
        self.gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        self.dwm = ctypes.WinDLL("dwmapi")

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        class WINDOWPOS(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND), ("x", ctypes.c_int),
                        ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]

        class APPBARDATA(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
                        ("uEdge", wintypes.UINT), ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]

        self.MONITORINFO, self.WINDOWPOS, self.APPBARDATA = MONITORINFO, WINDOWPOS, APPBARDATA
        self.shell32 = ctypes.WinDLL("shell32")
        self.shell32.SHAppBarMessage.argtypes = (wintypes.DWORD, ctypes.c_void_p)
        self.shell32.SHAppBarMessage.restype = ctypes.c_size_t

    def maximized_area(self, hwnd):
        """(x, y, width, height) a maximized window should cover on its monitor."""
        info = self.MONITORINFO()
        info.cbSize = self.ctypes.sizeof(info)
        monitor = self.user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor or not self.user32.GetMonitorInfoW(monitor, self.ctypes.addressof(info)):
            return None
        work, screen = info.rcWork, info.rcMonitor
        left, top, right, bottom = work.left, work.top, work.right, work.bottom
        if (left, top, right, bottom) == (screen.left, screen.top, screen.right, screen.bottom):
            # An auto-hidden taskbar only slides out if the window leaves it a pixel to sense the pointer.
            bar = self.APPBARDATA()
            bar.cbSize = self.ctypes.sizeof(bar)
            if self.shell32.SHAppBarMessage(4, self.ctypes.addressof(bar)) & 1:  # ABM_GETSTATE: ABS_AUTOHIDE
                edge = 3
                if self.shell32.SHAppBarMessage(5, self.ctypes.addressof(bar)):  # ABM_GETTASKBARPOS
                    edge = bar.uEdge
                if edge == 0:
                    left += 1
                elif edge == 1:
                    top += 1
                elif edge == 2:
                    right -= 1
                else:
                    bottom -= 1
        return left, top, right - left, bottom - top

    def set_dwm(self, hwnd, attribute, value):
        data = self.ctypes.c_int(value)
        return self.dwm.DwmSetWindowAttribute(hwnd, attribute, self.ctypes.byref(data),
                                              self.ctypes.sizeof(data)) == 0


class WindowChrome:
    """Frameless-but-native window handling for one Tk root. Use ``install(root)``."""

    def __init__(self, root):
        self.root = root
        self.api = None
        self.hwnd = None
        self._proc = None       # keep the callback alive for as long as the window exists
        self._old_proc = None
        self.native_border = False
        # True while Windows runs its move/size loop (the user is dragging the window). Only a
        # flag: the window procedure must never call back into Tk (see _window_proc).
        self.sizing = False
        # While the window jumps in size (maximize, restore, snap) its content does not paint;
        # repaint() shows it again, complete, once Tk has laid it out (see _hide_content).
        self.content = None
        self.redraw_off = False
        self.redraw_off_at = 0.0
        self._brush = None

    # -- setup ---------------------------------------------------------------------------------

    def prepare(self):
        """Call before the window is first shown. Returns False on platforms without the chrome."""
        if sys.platform != "win32":
            return False
        try:
            self.api = _Win32()
        except (OSError, AttributeError, ImportError):
            return False
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", 0.0)  # hidden until the frame styles are restored
        return True

    def finish(self):
        """Once mapped: restore the frame styles, hook the window procedure, show the window."""
        api, root = self.api, self.root
        user32 = api.user32
        try:
            hwnd = api.wintypes.HWND(int(root.wm_frame(), 16))
            user32.ShowWindow(hwnd, 0)  # SW_HIDE: the taskbar only notices the new style on re-show
            # Tk rewrites the extended style when alpha changes, so finish with alpha before restyling.
            root.attributes("-alpha", 1.0)
            style = user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)
            style |= _WS_CAPTION | _WS_THICKFRAME | _WS_SYSMENU | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX
            ex_style = user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
            ex_style = (ex_style & ~_WS_EX_TOOLWINDOW) | _WS_EX_APPWINDOW
            old = user32.GetWindowLongPtrW(hwnd, _GWLP_WNDPROC)
            if not old:
                raise OSError("no window procedure")
            self._old_proc = old & 0xFFFFFFFFFFFFFFFF
            self._proc = api.WNDPROC(self._window_proc)
            proc_address = api.ctypes.cast(self._proc, api.ctypes.c_void_p).value
            user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, style)
            user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, ex_style)
            user32.SetWindowLongPtrW(hwnd, _GWLP_WNDPROC, proc_address)
            self.hwnd = hwnd
            self.content = api.wintypes.HWND(root.winfo_id())     # Tk's window inside the frame
            self._brush = api.gdi32.CreateSolidBrush(_colorref(gui_theme.BACKDROP))
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
            api.set_dwm(hwnd, 20, 1)                         # dark mode for the system menu and shadow
            api.set_dwm(hwnd, 33, 2)                         # Windows 11: rounded corners
            api.set_dwm(hwnd, 3, 1)                          # no zoom animation: maximize and restore snap
            # Windows 11: a 1 px outline around the whole window (Windows 10 has no such option;
            # the caller draws one itself when this returns False).
            self.native_border = api.set_dwm(hwnd, 34, _colorref(gui_theme.WINDOW_EDGE))
            user32.ShowWindow(hwnd, 5)                       # SW_SHOW
        except (OSError, AttributeError, ValueError, tk.TclError):
            self._undo()
            root.attributes("-alpha", 1.0)
            return False
        return True

    def _undo(self):
        """Fall back to the normal Windows frame."""
        if self.hwnd is not None and self._old_proc:
            try:
                self.api.user32.SetWindowLongPtrW(self.hwnd, _GWLP_WNDPROC, self._old_proc)
            except OSError:
                pass
        self.hwnd = None
        self.root.overrideredirect(False)

    def _window_proc(self, hwnd, msg, wparam, lparam):
        # Runs inside Windows' message dispatch, possibly while Tk itself is mid-call: it must not
        # call tkinter (doing so corrupts tkinter's interpreter-lock bookkeeping and aborts Python).
        api = self.api
        try:
            if msg == _WM_NCCALCSIZE:
                return 0  # no frame: the client area is the whole window (as Tk already assumes)
            if msg in (_WM_ENTERSIZEMOVE, _WM_EXITSIZEMOVE):
                self.sizing = msg == _WM_ENTERSIZEMOVE
            if msg == 0x0014 and self.redraw_off and self._brush:  # WM_ERASEBKGND while content is hidden
                rect = api.wintypes.RECT()
                api.user32.GetClientRect(hwnd, api.ctypes.byref(rect))
                api.user32.FillRect(wparam, api.ctypes.byref(rect), self._brush)
                return 1
            if msg == _WM_WINDOWPOSCHANGING:
                pos = api.WINDOWPOS.from_address(lparam)
                if not pos.flags & 0x0001:  # SWP_NOSIZE: the size changes
                    if api.user32.IsZoomed(hwnd):
                        # A maximized framed window hangs over the screen edges by its (here invisible)
                        # frame; keep it on the work area instead, so nothing is cut off.
                        area = api.maximized_area(hwnd)
                        if area:
                            pos.x, pos.y, pos.cx, pos.cy = area
                            pos.flags &= ~0x0002  # SWP_NOMOVE
                    rect = api.wintypes.RECT()
                    api.user32.GetWindowRect(hwnd, api.ctypes.byref(rect))
                    jump = (pos.cx, pos.cy) != (rect.right - rect.left, rect.bottom - rect.top)
                    # Not for minimizing or coming back from the taskbar: no new layout to wait for.
                    iconic = api.user32.IsIconic(hwnd) or pos.x <= -32000
                    if jump and not self.sizing and not iconic:
                        self._hide_content()
                        pos.flags |= 0x0100  # SWP_NOCOPYBITS: no old pixels carried into the new size
        except Exception:  # never let a Python error break the window's message handling
            pass
        return api.user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)

    # -- actions -------------------------------------------------------------------------------

    @property
    def active(self):
        return self.hwnd is not None

    def zoomed(self):
        return bool(self.active and self.api.user32.IsZoomed(self.hwnd))

    def _native(self, hit):
        """Hand the current mouse press to Windows as a press on the frame (move or resize loop).

        Posted, not sent: Windows' drag loop must start from Tk's own event loop. Started from inside
        a Tk event handler, any Tk callback during the drag would abort Python.
        """
        api = self.api
        point = api.wintypes.POINT()
        api.user32.GetCursorPos(api.ctypes.byref(point))
        api.user32.ReleaseCapture()
        api.user32.PostMessageW(self.hwnd, _WM_NCLBUTTONDOWN, _HT[hit],
                                (point.x & 0xFFFF) | ((point.y & 0xFFFF) << 16))

    def start_move(self):
        if self.active:
            self._native("caption")

    def start_resize(self, edge):
        if self.active:
            self._native(edge)

    def minimize(self):
        if self.active:
            self.api.user32.PostMessageW(self.hwnd, _WM_SYSCOMMAND, _SC_MINIMIZE, 0)

    def toggle_maximize(self):
        if self.active:
            command = _SC_RESTORE if self.zoomed() else _SC_MAXIMIZE
            self.api.user32.PostMessageW(self.hwnd, _WM_SYSCOMMAND, command, 0)

    def close(self):
        if self.active:
            self.api.user32.PostMessageW(self.hwnd, _WM_CLOSE, 0, 0)  # runs the app's close handler

    def _hide_content(self):
        """Stop the window's content from painting while it jumps to a new size.

        Tk re-lays out many small windows after a size jump and paints them one by one; meanwhile
        Windows shows half-drawn pieces and old pixels (smears). With painting switched off
        (WM_SETREDRAW) the frame shows the flat window color instead, and repaint() brings the
        content back in one piece. Called from the window procedure: no Tk calls here."""
        if not self.redraw_off and self.content is not None:
            import time
            self.api.user32.SendMessageW(self.content, 0x000B, 0, 0)  # WM_SETREDRAW off
            self.redraw_off = True
            self.redraw_off_at = time.monotonic()

    def repaint(self):
        """Show the content again if it was hidden, and have every part of the window repaint.

        Only marks the window as needing paint; Tk paints it from its own event loop, so this is
        safe to call from Tk code."""
        if not self.active:
            return
        if self.redraw_off:
            self.api.user32.SendMessageW(self.content, 0x000B, 1, 0)  # WM_SETREDRAW on
            self.redraw_off = False
        # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_FRAME
        self.api.user32.RedrawWindow(self.hwnd, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0400)

    def check_hidden(self, limit=0.8):
        """Safety net: never leave the content hidden for long (e.g. if no resize event follows)."""
        import time
        if self.redraw_off and not self.sizing and time.monotonic() - self.redraw_off_at > limit:
            self.repaint()

    def system_menu(self, x_root, y_root):
        """The window menu (Restore, Move, Size, Minimize, Maximize, Close) at the pointer.

        Posted to Windows (WM_POPUPSYSTEMMENU), which shows it with the right items enabled; like the
        drag loop, the menu's own modal loop must not start from inside a Tk event handler.
        """
        if self.active:
            self.api.user32.PostMessageW(self.hwnd, 0x0313, 0, (x_root & 0xFFFF) | ((y_root & 0xFFFF) << 16))


def _colorref(color):
    r, g, b = _rgb(color)
    return r | g << 8 | b << 16


class TitleBar(tk.Canvas):
    """The custom title bar: gradient bar, fading bottom line and minimize/maximize/close buttons."""

    def __init__(self, parent, chrome, *, theme):
        self._chrome = chrome
        self._theme = dict(theme)
        self._height = px(BAR_HEIGHT)
        self._button_w = px(BUTTON_WIDTH)
        self._hover = self._pressed = None
        self._active = True
        self._image = None
        super().__init__(parent, height=self._height, background=gui_theme.screen(BAR_STOPS[0][1]),
                         highlightthickness=0, bd=0)
        family = gui_theme.icon_font(self)
        self._glyph_font = (family, -px(10)) if family else None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<ButtonRelease-3>", lambda e: self._button_at(e.x) is None
                  and self._chrome.system_menu(e.x_root, e.y_root))
        top = self.winfo_toplevel()
        top.bind("<Activate>", lambda e: e.widget is top and self._set_active(True), add="+")
        top.bind("<Deactivate>", lambda e: e.widget is top and self._set_active(False), add="+")
        top.bind("<Configure>", lambda e: e.widget is top and self._draw_buttons(), add="+")

    def _buttons(self):
        width = self.winfo_width()
        names = ("minimize", "maximize", "close")
        return [(name, width - (len(names) - i) * self._button_w) for i, name in enumerate(names)]

    def _button_at(self, x):
        for name, left in self._buttons():
            if left <= x < left + self._button_w:
                return name
        return None

    def _draw(self):
        width = max(1, self.winfo_width())
        height = self._height
        line = hairline()
        bar_stops = tuple((pos, gui_theme.screen(color)) for pos, color in BAR_STOPS)
        line_stops = tuple((pos, gui_theme.screen(color), alpha) for pos, color, alpha in LINE_STOPS)
        try:
            import numpy as np
        except ImportError:
            np = None
        if np is not None:
            # Fast path (this runs on every step of a window resize).
            t = (np.arange(width) + 0.5) / width
            positions = [stop[0] for stop in bar_stops]
            bar = np.stack([np.interp(t, positions, [_rgb(stop[1])[i] for stop in bar_stops]) for i in range(3)], 1)
            positions = [stop[0] for stop in line_stops]
            ink = np.stack([np.interp(t, positions, [_rgb(stop[1])[i] for stop in line_stops]) for i in range(3)], 1)
            alpha = np.interp(t, positions, [stop[2] for stop in line_stops])[:, None]
            pixels = np.empty((height, width, 3), np.uint8)
            pixels[:height - line] = (bar + 0.5).astype(np.uint8)
            pixels[height - line:] = (bar + (ink - bar) * alpha + 0.5).astype(np.uint8)
            image = tk.PhotoImage(master=self, format="ppm",
                                  data=b"P6 %d %d 255 " % (width, height) + pixels.tobytes())
        else:
            bar = [_mix(bar_stops, (x + 0.5) / width) for x in range(width)]
            row = "{" + " ".join(_hex(c) for c in bar) + "}"
            stroke = []
            for x, base in enumerate(bar):
                *rgb, a = _mix(line_stops, (x + 0.5) / width)
                stroke.append(_hex([b + (c - b) * a for b, c in zip(base, rgb)]))
            image = tk.PhotoImage(master=self, width=width, height=height)
            image.put(row, to=(0, 0, width, height - line))
            image.put("{" + " ".join(stroke) + "}", to=(0, height - line, width, height))
        self._image = image
        if not self.find_withtag("bar"):
            self.create_image(0, 0, anchor="nw", tags="bar")
        self.itemconfigure("bar", image=image)
        self.tag_lower("bar")
        self._draw_buttons()

    def _draw_buttons(self):
        self.delete("button")
        t = self._theme
        h = self._height - hairline()
        zoomed = self._chrome.zoomed()
        for name, left in self._buttons():
            hover, pressed = self._hover == name, self._pressed == name
            if name == "close" and (hover or pressed):
                face, ink = gui_theme.screen(CLOSE_PRESSED if pressed else CLOSE_HOVER), t["TEXT"]
            elif hover or pressed:
                face, ink = (t["BUTTON_PRESSED"] if pressed else t["RAIL_HOVER"]), t["TEXT"]
            else:
                face, ink = None, (t["MUTED"] if self._active else t["DIM"])
            if face:
                self.create_rectangle(left, 0, left + self._button_w, h, fill=face, width=0, tags="button")
            glyph = "restore" if (name == "maximize" and zoomed) else name
            cx, cy = left + self._button_w // 2, h // 2
            if self._glyph_font:
                self.create_text(cx, cy, text=GLYPHS[glyph], font=self._glyph_font, fill=ink, tags="button")
            else:
                self._draw_glyph(glyph, cx, cy, ink)

    def _draw_glyph(self, glyph, cx, cy, ink):
        """Line-drawn fallback when no icon font is installed."""
        s, w = px(5), hairline()
        if glyph == "minimize":
            self.create_line(cx - s, cy, cx + s, cy, fill=ink, width=w, tags="button")
        elif glyph == "close":
            self.create_line(cx - s, cy - s, cx + s, cy + s, fill=ink, width=w, tags="button")
            self.create_line(cx - s, cy + s, cx + s, cy - s, fill=ink, width=w, tags="button")
        else:
            inset = px(2) if glyph == "restore" else 0
            self.create_rectangle(cx - s, cy - s + inset, cx + s - inset, cy + s, outline=ink, width=w, tags="button")
            if inset:
                self.create_line(cx - s + inset, cy - s, cx + s, cy - s, cx + s, cy + s - inset,
                                 fill=ink, width=w, tags="button")

    def _set_hover(self, name):
        if name != self._hover:
            self._hover = name
            if name is None:
                self._pressed = None
            self._draw_buttons()

    def _set_active(self, active):
        self._active = active
        self._draw_buttons()

    def _on_motion(self, event):
        self._set_hover(self._button_at(event.x))

    def _on_press(self, event):
        name = self._button_at(event.x)
        if name is None:
            self._chrome.start_move()
            return
        self._pressed = name
        self._draw_buttons()

    def _on_release(self, event):
        name, self._pressed = self._pressed, None
        self._draw_buttons()
        if name is None or self._button_at(event.x) != name or not (0 <= event.y < self._height):
            return
        if name == "minimize":
            self._chrome.minimize()
        elif name == "maximize":
            self._chrome.toggle_maximize()
        else:
            self._chrome.close()

    def _on_double(self, event):
        if self._button_at(event.x) is None:
            self._chrome.toggle_maximize()


class EdgeResizer:
    """Resize zones along the inside of the window edges.

    Every widget gets a bind tag in front of its own, so a press in a zone starts Windows' resize
    loop and stops there, before the widget under the pointer sees the click.
    """

    TAG = "D2BWindowEdge"

    def __init__(self, root, chrome):
        self.root = root
        self.chrome = chrome
        self.cursors: dict = {}
        root.bind_class(self.TAG, "<Motion>", self._on_motion)
        root.bind_class(self.TAG, "<Leave>", self._on_leave)
        root.bind_class(self.TAG, "<ButtonPress-1>", self._on_press)

    def attach(self, widget=None):
        widget = widget or self.root
        tags = widget.bindtags()
        if self.TAG not in tags:
            widget.bindtags((self.TAG,) + tags)
        for child in widget.winfo_children():
            if child.winfo_toplevel() is self.root:
                self.attach(child)

    def _edge(self, event):
        if not self.chrome.active or self.chrome.zoomed():
            return None
        root = self.root
        x, y = event.x_root - root.winfo_rootx(), event.y_root - root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        edge, corner = max(3, px(EDGE)), px(CORNER)
        near = lambda value, size, reach: (value < reach, value >= size - reach)
        left, right = near(x, w, edge)
        top, bottom = near(y, h, edge)
        left_c, right_c = near(x, w, corner)
        top_c, bottom_c = near(y, h, corner)
        if (top and left_c) or (left and top_c):
            return "nw"
        if (top and right_c) or (right and top_c):
            return "ne"
        if (bottom and left_c) or (left and bottom_c):
            return "sw"
        if (bottom and right_c) or (right and bottom_c):
            return "se"
        return "n" if top else "s" if bottom else "w" if left else "e" if right else None

    def _on_motion(self, event):
        widget, edge = event.widget, self._edge(event)
        try:
            if edge:
                if widget not in self.cursors:
                    self.cursors[widget] = widget.cget("cursor")
                widget.configure(cursor=_CURSORS[edge])
                return "break"  # the widget's own hover handling must not replace the resize cursor
            if widget in self.cursors:
                widget.configure(cursor=self.cursors.pop(widget))
        except tk.TclError:
            self.cursors.pop(widget, None)
        return None

    def _on_leave(self, event):
        widget = event.widget
        if widget in self.cursors:
            try:
                widget.configure(cursor=self.cursors.pop(widget))
            except tk.TclError:
                self.cursors.pop(widget, None)

    def _on_press(self, event):
        edge = self._edge(event)
        if edge is None:
            return None
        self._on_leave(event)
        self.chrome.start_resize(edge)
        return "break"
