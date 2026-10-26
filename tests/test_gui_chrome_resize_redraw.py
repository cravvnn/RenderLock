"""The recovery dialog has no deferred redraw handler of its own."""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from agr.gui_chrome import WindowChrome, _WM_WINDOWPOSCHANGING


class WindowPos(ctypes.Structure):
    _fields_ = [('hwnd', ctypes.c_void_p), ('insert_after', ctypes.c_void_p),
                ('x', ctypes.c_int), ('y', ctypes.c_int), ('cx', ctypes.c_int),
                ('cy', ctypes.c_int), ('flags', ctypes.c_uint)]


@pytest.mark.parametrize('defer_redraw,should_hide', [(True, True), (False, False)])
def test_resize_only_suspends_content_when_window_has_repaint_handler(defer_redraw, should_hide):
    hidden = []
    def get_window_rect(_hwnd, address):
        rect = ctypes.cast(address, ctypes.POINTER(wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = 0, 0, 620, 500
        return True
    user32 = SimpleNamespace(IsZoomed=lambda _hwnd: False,
                             IsIconic=lambda _hwnd: False,
                             GetWindowRect=get_window_rect,
                             CallWindowProcW=lambda *_args: 73)
    chrome = WindowChrome(None, defer_resize_redraw=defer_redraw)
    chrome.api = SimpleNamespace(WINDOWPOS=WindowPos, wintypes=wintypes,
                                 ctypes=ctypes, user32=user32)
    chrome._hide_content = lambda: hidden.append(True)
    chrome._old_proc = 1
    position = WindowPos(None, None, 10, 10, 720, 560, 0)
    assert chrome._window_proc(1, _WM_WINDOWPOSCHANGING, 0, ctypes.addressof(position)) == 73
    assert bool(hidden) is should_hide
    assert bool(position.flags & 0x0100) is should_hide
