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

import re
import sys
import tkinter as tk
import webbrowser
from tkinter import font as tkfont

from . import gui_theme
from .gui_theme import hairline, px
from .gui_widgets import rgba_image

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

# Links at the left end of the bar, from the design: YouTube and Discord icons, a divider and the
# credit line. They are quiet until hovered. Positions and sizes are design pixels.
LINK_INK = "#1d1c1b"      # icons and divider
CREDIT_INK = "#332f28"    # "Made by Cravvnn"
LINK_ICON = 18
LINK_REACH = 5            # extra click room on each side of an icon
LINKS = (("youtube", 15, "https://www.youtube.com/results?search_query=cravvnn"),
         ("discord", 43, "https://discord.gg/XTefeBUqX5"))
DIVIDER_X, DIVIDER_HEIGHT = 71, 14.5
CREDIT_X, CREDIT_SIZE = 81, 11
CREDIT_PREFIX, CREDIT_NAME = "Made by ", "Cravvnn"
CREDIT_URL = "https://github.com/cravvnn"
# The icons' outlines (SVG path data in an 18 x 18 box, even-odd fill), drawn at the display scale.
LINK_PATHS = {
    "youtube": "M7.18959 11.3275V6.27687C8.98258 7.12056 10.3712 7.93558 12.0136 8.81427C10.659 9.56551 "
               "8.98258 10.4084 7.18959 11.3275ZM17.1819 3.76496C16.8726 3.35747 16.3455 3.04028 15.7843 "
               "2.93527C14.1347 2.62202 3.84389 2.62113 2.19524 2.93527C1.7452 3.01963 1.34445 3.22356 1.0002 "
               "3.5404C-0.450326 4.88671 0.00419956 12.1065 0.353832 13.276C0.500856 13.7823 0.690921 14.1473 "
               "0.930285 14.387C1.23868 14.7038 1.66092 14.922 2.14592 15.0198C3.50411 15.3007 10.5012 15.4578 "
               "15.7556 15.062C16.2397 14.9776 16.6682 14.7524 17.0062 14.4221C18.3473 13.0812 18.2559 5.45592 "
               "17.1819 3.76496Z",
    "discord": "M15.2477 3.17085C14.0825 2.6257 12.8367 2.2295 11.5342 2.00391C11.3742 2.29311 11.1873 2.68211 "
               "11.0585 2.99155C9.67387 2.78332 8.30201 2.78332 6.94287 2.99155C6.81404 2.68211 6.62292 2.29311 "
               "6.46152 2.00391C5.15761 2.2295 3.91032 2.62716 2.74514 3.17374C0.394982 6.72514 -0.242108 "
               "10.1883 0.0764375 13.6024C1.63519 14.7664 3.14581 15.4735 4.63093 15.9362C4.99762 15.4316 "
               "5.32465 14.8951 5.60638 14.3297C5.06981 14.1258 4.5559 13.8742 4.07031 13.5821C4.19913 13.4867 "
               "4.32514 13.3869 4.44689 13.2842C7.40865 14.6695 10.6267 14.6695 13.5531 13.2842C13.6762 13.3869 "
               "13.8022 13.4867 13.9296 13.5821C13.4426 13.8756 12.9273 14.1273 12.3907 14.3312C12.6724 14.8951 "
               "12.9981 15.433 13.3662 15.9377C14.8527 15.4749 16.3647 14.7678 17.9235 13.6024C18.2973 9.64464 "
               "17.285 6.21325 15.2477 3.17085ZM6.00988 11.5028C5.12079 11.5028 4.39166 10.6727 4.39166 9.66198C"
               "4.39166 8.65122 5.10522 7.81977 6.00988 7.81977C6.91457 7.81977 7.64367 8.64977 7.6281 9.66198C"
               "7.62951 10.6727 6.91457 11.5028 6.00988 11.5028ZM11.9901 11.5028C11.101 11.5028 10.3718 10.6727 "
               "10.3718 9.66198C10.3718 8.65122 11.0854 7.81977 11.9901 7.81977C12.8947 7.81977 13.6238 8.64977 "
               "13.6083 9.66198C13.6083 10.6727 12.8947 11.5028 11.9901 11.5028Z",
}

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


def _path_polygons(path, scale, steps=12):
    """SVG path data (M, L, H, V, C, Z; absolute or relative) -> closed polygons, scaled."""
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", path)
    polygons, points = [], []
    x = y = start_x = start_y = 0.0
    command, i = "", 0
    while i < len(tokens):
        if tokens[i].isalpha():
            command, i = tokens[i], i + 1
            if command in "Zz":
                if len(points) > 2:
                    polygons.append(points)
                points, x, y = [], start_x, start_y
            continue
        relative = command.islower()
        ox, oy = (x, y) if relative else (0.0, 0.0)
        kind = command.upper()
        count = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6}[kind]
        v = [float(t) for t in tokens[i:i + count]]
        i += count
        if kind == "M":
            if len(points) > 2:
                polygons.append(points)
            x, y = start_x, start_y = ox + v[0], oy + v[1]
            points = [(x, y)]
            command = "l" if relative else "L"  # further pairs after a move are lines
        elif kind == "L":
            x, y = ox + v[0], oy + v[1]
            points.append((x, y))
        elif kind == "H":
            x = ox + v[0]
            points.append((x, y))
        elif kind == "V":
            y = oy + v[0]
            points.append((x, y))
        else:
            (x1, y1), (x2, y2), (x3, y3) = (ox + v[0], oy + v[1]), (ox + v[2], oy + v[3]), (ox + v[4], oy + v[5])
            for k in range(1, steps + 1):
                t = k / steps
                a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                points.append((a * x + b * x1 + c * x2 + d * x3, a * y + b * y1 + c * y2 + d * y3))
            x, y = x3, y3
    if len(points) > 2:
        polygons.append(points)
    return [[(px_ * scale, py_ * scale) for px_, py_ in points] for points in polygons]


def _coverage(polygons, size, rows=5):
    """Anti-aliased even-odd coverage (0..1) of ``polygons`` on a size x size grid.

    Exact across each pixel row, ``rows`` samples down it."""
    edges = [(a, b) for points in polygons for a, b in zip(points, points[1:] + points[:1]) if a[1] != b[1]]
    cover = [[0.0] * size for _ in range(size)]
    for row in range(size):
        line = cover[row]
        for s in range(rows):
            yy = row + (s + 0.5) / rows
            xs = sorted(x0 + (yy - y0) * (x1 - x0) / (y1 - y0)
                        for (x0, y0), (x1, y1) in edges if (y0 <= yy) != (y1 <= yy))
            for left, right in zip(xs[::2], xs[1::2]):
                left, right = max(0.0, left), min(float(size), right)
                column = int(left)
                while column < right:
                    line[column] += (min(right, column + 1) - max(left, column)) / rows
                    column += 1
    return cover


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
    """Frameless-but-native window handling for one Tk root. Use ``install(root)``.

    ``resizable=False`` (the update window) leaves out the resize frame and the maximize box, so
    Windows offers no resizing, snapping or maximizing for it; its size changes only from the app.
    """

    def __init__(self, root, *, resizable=True):
        self.root = root
        self.resizable = resizable
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
        # Called as follow(x, y, width, height, flags) whenever the window has moved or resized, from
        # inside the window procedure: Win32 calls only, never tkinter (see gui_fade).
        self.followers = []

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
        hidden = False
        try:
            hwnd = api.wintypes.HWND(int(root.wm_frame(), 16))
            user32.ShowWindow(hwnd, 0)  # SW_HIDE: the taskbar only notices the new style on re-show
            hidden = True
            # Tk rewrites the extended style when alpha changes, so finish with alpha before restyling.
            root.attributes("-alpha", 1.0)
            style = user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)
            style |= _WS_CAPTION | _WS_SYSMENU | _WS_MINIMIZEBOX
            if self.resizable:
                style |= _WS_THICKFRAME | _WS_MAXIMIZEBOX
            else:
                style &= ~(_WS_THICKFRAME | _WS_MAXIMIZEBOX)
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
            if hidden:  # Tk took the hide as "withdrawn" and rebuilt the native frame that way: show it
                try:
                    root.deiconify()
                except tk.TclError:
                    pass
            return False
        root._frameless_chrome = True  # gui_theme.brand_window leaves the outline color alone from now on
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
                    # (A fixed-size window only changes size from its own code, which lays out at once.)
                    if jump and not self.sizing and not iconic and self.resizable:
                        self._hide_content()
                        pos.flags |= 0x0100  # SWP_NOCOPYBITS: no old pixels carried into the new size
            if msg == 0x0047 and self.followers:  # WM_WINDOWPOSCHANGED: owned overlays keep up, even mid-drag
                pos = api.WINDOWPOS.from_address(lparam)
                for follow in self.followers:
                    follow(pos.x, pos.y, pos.cx, pos.cy, pos.flags)
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
    """The custom title bar: gradient bar, fading bottom line, the YouTube / Discord / credit links
    and minimize/maximize/close buttons.

    Other windows (the update window) choose their ``buttons`` and show ``icon`` + ``title`` at
    the left instead of the links (``links=False``); ``height`` is in design px."""

    def __init__(self, parent, chrome, *, theme, buttons=("minimize", "maximize", "close"), links=True,
                 height=BAR_HEIGHT, title=None, icon=None):
        self._chrome = chrome
        self._theme = dict(theme)
        self._names = tuple(buttons)
        self._links_on = links
        self._title, self._title_icon = title, icon
        self._title_icon_fit = self._title_icon_fit_key = None
        self._height = px(height)
        self._button_w = px(BUTTON_WIDTH)
        self._hover = self._pressed = None
        self._link_hover = self._link_pressed = None
        self._link_boxes = []      # (name, left, right) click areas, set by _draw_links
        self._icons = {}           # (name, color) -> PhotoImage
        self._active = True
        self._image = None
        super().__init__(parent, height=self._height, background=gui_theme.screen(BAR_STOPS[0][1]),
                         highlightthickness=0, bd=0)
        family = gui_theme.icon_font(self)
        self._glyph_font = (family, -px(10)) if family else None
        self._credit_font = tkfont.Font(self, family=gui_theme.FONT[0], size=-px(CREDIT_SIZE))
        self._credit_link_font = tkfont.Font(self, family=gui_theme.FONT[0], size=-px(CREDIT_SIZE), underline=True)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self.drop_hover)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<ButtonRelease-3>", lambda e: self._button_at(e.x) is None and self._link_at(e.x, e.y) is None
                  and self._chrome.system_menu(e.x_root, e.y_root))
        top = self.winfo_toplevel()
        top.bind("<Activate>", lambda e: e.widget is top and self._set_active(True), add="+")
        top.bind("<Deactivate>", lambda e: e.widget is top and self._set_active(False), add="+")
        top.bind("<Configure>", lambda e: e.widget is top and self._draw_buttons(), add="+")

    def _buttons(self):
        width = self.winfo_width()
        names = self._names
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
        if self._links_on:
            self._draw_links()
        else:
            self._draw_title()

    def _draw_title(self):
        """Icon and window title at the left (for windows without the links)."""
        self.delete("caption")
        cy = (self._height - hairline()) / 2.0
        x = px(12)
        right = self.winfo_width() - len(self._names) * self._button_w - px(12)
        if right <= x:
            return
        if self._title_icon is not None:
            # Keep the caller's larger body logo intact; only this bar owns the fitted copy.
            limit = min(px(20), self._height - hairline() - 2 * px(6), right - x)
            if limit > 0:
                icon = self._fit_title_icon(limit)
                self.create_image(x, cy, anchor="w", image=icon, tags="caption")
                x += icon.width() + px(8)
        if self._title and x < right:
            ink = self._theme["TEXT"] if self._active else self._theme["MUTED"]
            title = str(self._title).replace("\r", " ").replace("\n", " ").replace("\t", " ")
            item = self.create_text(x, cy, anchor="w", text=title, font=gui_theme.FONT,
                                    fill=ink, tags="caption")
            # Canvas text bounds include a small overhang beyond Font.measure(). Use the
            # actual item bounds so neither that overhang nor a long title enters a button.
            def text_width(text):
                self.itemconfigure(item, text=text)
                left, _top, end, _bottom = self.bbox(item)
                return end - left

            available = right - x
            if text_width(title) > available:
                ellipsis = "\u2026"
                if text_width(ellipsis) > available:
                    self.delete(item)
                    return
                low, high = 0, len(title)
                while low < high:
                    middle = (low + high + 1) // 2
                    if text_width(title[:middle] + ellipsis) <= available:
                        low = middle
                    else:
                        high = middle - 1
                self.itemconfigure(item, text=title[:low] + ellipsis)
            left = self.bbox(item)[0]
            self.move(item, x - left, 0)

    def _fit_title_icon(self, limit):
        """Fit a PhotoImage proportionally, retaining alpha without a large zoom buffer."""
        source = self._title_icon
        width, height = source.width(), source.height()
        scale = min(1.0, limit / max(width, height))
        fitted_w, fitted_h = max(1, round(width * scale)), max(1, round(height * scale))
        key = (str(source), width, height, fitted_w, fitted_h)
        if key != self._title_icon_fit_key:
            if (fitted_w, fitted_h) == (width, height):
                fitted = source
            else:
                fitted = tk.PhotoImage(master=self, width=fitted_w, height=fitted_h)
                # Tk's copy keeps PNG alpha. Sampling straight into the small destination
                # bounds memory even for a large source or a fractional display scale.
                for y in range(fitted_h):
                    sy = min(height - 1, int((y + 0.5) * height / fitted_h))
                    for x in range(fitted_w):
                        sx = min(width - 1, int((x + 0.5) * width / fitted_w))
                        self.tk.call(str(fitted), "copy", str(source),
                                     "-from", sx, sy, sx + 1, sy + 1, "-to", x, y)
            self._title_icon_fit, self._title_icon_fit_key = fitted, key
        return self._title_icon_fit

    def set_title(self, title):
        self._title = title
        if not self._links_on:
            self._draw_title()

    def _link_icon(self, name, color):
        image = self._icons.get((name, color))
        if image is None:
            size = px(LINK_ICON)
            cover = _coverage(_path_polygons(LINK_PATHS[name], size / LINK_ICON), size)
            r, g, b = _rgb(color)
            rows = [b"".join(bytes((r, g, b, min(255, int(255 * c + 0.5)))) for c in line) for line in cover]
            image = self._icons[(name, color)] = rgba_image(self, size, size, rows)
        return image

    def _link_ink(self, name, rest):
        if self._link_hover == name:
            return self._theme["TEXT"] if self._link_pressed == name else self._theme["MUTED"]
        return gui_theme.screen(rest)

    def _draw_links(self):
        self.delete("link")
        cy = (self._height - hairline()) / 2.0
        size, reach = px(LINK_ICON), px(LINK_REACH)
        boxes = []
        for name, left, _url in LINKS:
            left = px(left)
            self.create_image(left, int(cy - size / 2.0 + 0.5), anchor="nw", tags="link",
                              image=self._link_icon(name, self._link_ink(name, LINK_INK)))
            boxes.append((name, left - reach, left + size + reach))
        x, half = px(DIVIDER_X), px(DIVIDER_HEIGHT) / 2.0
        self.create_rectangle(x, int(cy - half + 0.5), x + hairline(), int(cy + half + 0.5),
                              fill=gui_theme.screen(LINK_INK), width=0, tags="link")
        x = px(CREDIT_X)
        self.create_text(x, cy, text=CREDIT_PREFIX, anchor="w", font=self._credit_font,
                         fill=gui_theme.screen(CREDIT_INK), tags="link")
        x += self._credit_font.measure(CREDIT_PREFIX)
        hover = self._link_hover == "github"
        self.create_text(x, cy, text=CREDIT_NAME, anchor="w", tags="link",
                         font=self._credit_link_font if hover else self._credit_font,
                         fill=self._link_ink("github", CREDIT_INK))
        boxes.append(("github", x - px(3), x + self._credit_font.measure(CREDIT_NAME) + px(3)))
        self._link_boxes = boxes

    def _link_at(self, x, y):
        if not 0 <= y < self._height:
            return None
        return next((name for name, left, right in self._link_boxes if left <= x < right), None)

    def _set_link_hover(self, name):
        cursor = "hand2" if name else ""
        if self.cget("cursor") != cursor:
            self.configure(cursor=cursor)
        if name != self._link_hover:
            self._link_hover = name
            if name is None:
                self._link_pressed = None
            self._draw_links()

    def _open_link(self, name):
        url = CREDIT_URL if name == "github" else next(u for n, _left, u in LINKS if n == name)
        try:
            webbrowser.open(url)
        except (webbrowser.Error, OSError):
            pass

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
        if not self._links_on:
            self._draw_title()

    def _on_motion(self, event):
        self._set_hover(self._button_at(event.x))
        self._set_link_hover(self._link_at(event.x, event.y))

    def drop_hover(self, _event=None):
        """Forget hover and press looks: on <Leave>, and from EdgeResizer when the pointer enters a
        resize zone (the zone then owns the motion events, so _on_motion would not clear them)."""
        self._set_hover(None)
        self._set_link_hover(None)

    def _on_press(self, event):
        link = self._link_at(event.x, event.y)
        if link is not None:  # a link, not the bar: no window drag
            self._link_hover = self._link_pressed = link
            self._draw_links()
            return
        name = self._button_at(event.x)
        if name is None:
            self._chrome.start_move()
            return
        self._pressed = name
        self._draw_buttons()

    def _on_release(self, event):
        link, self._link_pressed = self._link_pressed, None
        if link is not None:
            self._draw_links()
            if self._link_at(event.x, event.y) == link:
                self._open_link(link)
            return
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
        # The second click of a double-click on a link does nothing (the first one opened it).
        if ("maximize" in self._names and self._button_at(event.x) is None
                and self._link_at(event.x, event.y) is None):
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
                    drop = getattr(widget, "drop_hover", None)
                    if drop is not None:
                        drop()  # e.g. a title bar link: no hover look under a resize cursor
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
