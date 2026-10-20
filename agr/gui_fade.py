"""Soft edges where a scrolling page cuts its cards.

Tk widgets are opaque and cannot be blended, so the fade is drawn by two small click-through
windows owned by the main window (one per edge of the page viewport), with per-pixel alpha via
UpdateLayeredWindow. Over the cards they go from clear to the background color, so content
dissolves at the edge instead of ending in a hard line; the gaps between cards stay untouched.

Windows only. Without the custom window chrome (or if anything here fails) pages keep their
plain edge. Like the chrome's window procedure, ``follow`` runs inside Windows' message
dispatch and must never call tkinter.
"""
from __future__ import annotations

import sys

from . import gui_theme
from .gui_theme import px

FADE_HEIGHT = 40          # design px, at most a third of the viewport
_CLASS = "RenderLockEdgeFade"
_WS_POPUP = 0x80000000
_WS_EX = 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000  # LAYERED, TRANSPARENT, TOOLWINDOW, NOACTIVATE
_SWP_FOLLOW = 0x0001 | 0x0004 | 0x0010                        # NOSIZE, NOZORDER, NOACTIVATE


class _Api:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        user32, gdi32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32")
        kernel32 = ctypes.WinDLL("kernel32")
        self.user32, self.gdi32 = user32, gdi32

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                        ("hIconSm", wintypes.HICON)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        self.WNDCLASSEXW, self.BLENDFUNCTION = WNDCLASSEXW, BLENDFUNCTION
        for lib, name, args, res in (
                (user32, "RegisterClassExW", (ctypes.c_void_p,), wintypes.ATOM),
                (user32, "CreateWindowExW", (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                             ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                             wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p), wintypes.HWND),
                (user32, "DestroyWindow", (wintypes.HWND,), wintypes.BOOL),
                (user32, "ShowWindow", (wintypes.HWND, ctypes.c_int), wintypes.BOOL),
                (user32, "SetWindowPos", (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, wintypes.UINT), wintypes.BOOL),
                (user32, "GetWindowRect", (wintypes.HWND, ctypes.POINTER(wintypes.RECT)), wintypes.BOOL),
                (user32, "UpdateLayeredWindow", (wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                                 ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                                 ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
                                                 ctypes.c_void_p, wintypes.DWORD), wintypes.BOOL),
                (user32, "GetDC", (wintypes.HWND,), wintypes.HDC),
                (user32, "ReleaseDC", (wintypes.HWND, wintypes.HDC), ctypes.c_int),
                (gdi32, "CreateCompatibleDC", (wintypes.HDC,), wintypes.HDC),
                (gdi32, "CreateDIBSection", (wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                             ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD),
                 wintypes.HBITMAP),
                (gdi32, "SelectObject", (wintypes.HDC, wintypes.HGDIOBJ), wintypes.HGDIOBJ),
                (gdi32, "DeleteObject", (wintypes.HGDIOBJ,), wintypes.BOOL),
                (gdi32, "DeleteDC", (wintypes.HDC,), wintypes.BOOL),
                (kernel32, "GetModuleHandleW", (wintypes.LPCWSTR,), wintypes.HMODULE)):
            function = getattr(lib, name)
            function.argtypes, function.restype = args, res
        self.instance = kernel32.GetModuleHandleW(None)
        # The windows only ever show a bitmap: Windows' own default procedure is all they need,
        # so no Python callback runs for them.
        window_class = WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
        window_class.lpfnWndProc = ctypes.cast(user32.DefWindowProcW, ctypes.c_void_p).value
        window_class.hInstance = self.instance
        window_class.lpszClassName = _CLASS
        if not user32.RegisterClassExW(ctypes.byref(window_class)) and ctypes.get_last_error() != 1410:
            raise OSError("cannot register the fade window class")  # 1410: already registered


class _Strip:
    """One faded edge: a layered window plus the offset it keeps from the main window."""

    def __init__(self, api, owner):
        self.api = api
        self.hwnd = api.user32.CreateWindowExW(_WS_EX, _CLASS, "", _WS_POPUP, 0, 0, 1, 1, owner, None,
                                               api.instance, None)
        if not self.hwnd:
            raise OSError("cannot create the fade window")
        self.offset = None      # (dx, dy) from the main window's top-left corner while shown
        self.shown = False

    def show(self, x, y, width, height, pixels, origin):
        api, ctypes, wintypes = self.api, self.api.ctypes, self.api.wintypes
        screen = api.user32.GetDC(None)
        memory = api.gdi32.CreateCompatibleDC(screen)
        header = (ctypes.c_uint32 * 10)(40, width, (-height) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bitmap = api.gdi32.CreateDIBSection(memory, header, 0, ctypes.byref(bits), None, 0)
        try:
            if not bitmap:
                return
            ctypes.memmove(bits, pixels, len(pixels))
            previous = api.gdi32.SelectObject(memory, bitmap)
            blend = api.BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, per-pixel (premultiplied) alpha
            api.user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wintypes.POINT(x, y)),
                                           ctypes.byref(wintypes.SIZE(width, height)), memory,
                                           ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2)
            api.gdi32.SelectObject(memory, previous)
        finally:
            if bitmap:
                api.gdi32.DeleteObject(bitmap)
            api.gdi32.DeleteDC(memory)
            api.user32.ReleaseDC(None, screen)
        self.offset = (x - origin[0], y - origin[1])
        if not self.shown:
            api.user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
            self.shown = True

    def hide(self):
        if self.shown:
            self.api.user32.ShowWindow(self.hwnd, 0)
            self.shown = False

    def move(self, x, y):
        if self.shown and self.offset:
            self.api.user32.SetWindowPos(self.hwnd, None, x + self.offset[0], y + self.offset[1], 0, 0, _SWP_FOLLOW)


class EdgeFade:
    """Faded top and bottom edges for the page that is showing (see module docstring)."""

    def __init__(self, root, owner):
        self.root = root
        self.api = _Api()
        self.owner = owner
        self.strips = {"top": _Strip(self.api, owner), "bottom": _Strip(self.api, owner)}
        self.size = None        # main window size the strips were laid out for
        self.page = None
        self._job = None
        self._rows = {}
        self.last = {}          # edge -> (x, y, width, height, BGRA bytes): what is on screen (for tests)
        rgb = [int(gui_theme.BACKDROP[i:i + 2], 16) for i in (1, 3, 5)]
        self.color = rgb

    # -- Tk side ------------------------------------------------------------------------------

    def request(self, page):
        """Lay the edges out for ``page`` once Tk is idle (after scrolling or layout)."""
        self.page = page
        if self._job is None:
            self._job = self.root.after_idle(self._update)

    def _row(self, alpha, width):
        key = (alpha, width)
        row = self._rows.get(key)
        if row is None:
            if len(self._rows) > 512:
                self._rows.clear()
            r, g, b = (c * alpha // 255 for c in self.color)
            row = self._rows[key] = bytes((b, g, r, alpha)) * width
        return row

    def _update(self):
        self._job = None
        page = self.page
        try:
            if page is None or not page.shell.winfo_ismapped() or page.held:
                self.hide()
                return
            canvas = page.canvas
            width, height = canvas.winfo_width(), canvas.winfo_height()
            first, last = canvas.yview()
            strip = min(px(FADE_HEIGHT), height // 3)
            if width < 2 or strip < 4:
                self.hide()
                return
            rect = self.api.wintypes.RECT()
            self.api.user32.GetWindowRect(self.owner, self.api.ctypes.byref(rect))
            self.size = (rect.right - rect.left, rect.bottom - rect.top)
            top = canvas.canvasy(0)
            spans = []
            for card, item, _gap in page.cards:
                y0 = canvas.coords(item)[1] - top
                spans.append((y0, y0 + card.winfo_height()))
            x, y = canvas.winfo_rootx(), canvas.winfo_rooty()
            for edge, needed, y0 in (("top", first > 0.0005, 0), ("bottom", last < 0.9995, height - strip)):
                if not needed:
                    self.strips[edge].hide()
                    self.last.pop(edge, None)
                    continue
                rows = []
                for row in range(strip):
                    t = (row + 0.5) / strip
                    t = 1.0 - t if edge == "top" else t           # 1 at the cut edge
                    alpha = int(255 * t * t * (3 - 2 * t) + 0.5)  # smoothstep
                    line = y0 + row
                    covered = any(a <= line < b for a, b in spans)
                    rows.append(self._row(alpha, width) if covered and alpha else bytes(4 * width))
                pixels = b"".join(rows)
                self.strips[edge].show(x, y + y0, width, strip, pixels, (rect.left, rect.top))
                self.last[edge] = (x - rect.left, y + y0 - rect.top, width, strip, pixels)
        except Exception:  # a fade must never break the page; fall back to the plain edge
            self.hide()

    def hide(self):
        for strip in self.strips.values():
            strip.hide()
        self.last.clear()

    def destroy(self):
        for strip in self.strips.values():
            self.api.user32.DestroyWindow(strip.hwnd)

    # -- window procedure side (no tkinter here) ------------------------------------------------

    def follow(self, x, y, width, height, flags):
        """The main window moved or resized (WM_WINDOWPOSCHANGED)."""
        if x <= -32000:          # minimized: Windows hides owned windows itself
            return
        if not flags & 0x0001 and self.size and (width, height) != self.size:
            for strip in self.strips.values():   # new size: hidden until the page is laid out again
                strip.hide()
            return
        if not flags & 0x0002:
            for strip in self.strips.values():
                strip.move(x, y)


def install(root, chrome):
    """An EdgeFade riding on the custom chrome, or None where it is not available."""
    if sys.platform != "win32" or chrome is None or not chrome.active:
        return None
    try:
        fade = EdgeFade(root, chrome.hwnd)
    except (OSError, AttributeError, ValueError):
        return None
    chrome.followers.append(fade.follow)
    root.bind("<Destroy>", lambda event: event.widget is root and fade.destroy(), add="+")
    return fade
