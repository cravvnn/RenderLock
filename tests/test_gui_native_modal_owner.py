"""Real Win32 regression; run only on an explicitly owned, unswitched desktop."""
import ctypes
from ctypes import wintypes
import json
import os
import sys
import tkinter as tk

import pytest


@pytest.mark.skipif(sys.platform != 'win32' or not os.environ.get('RENDERLOCK_TEST_DESKTOP'),
                    reason='Requires the isolated Windows desktop runner; never display on the user desktop')
def test_branded_modals_remain_owned_above_main_when_returning_from_another_window(tmp_path, monkeypatch):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    desktop = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    assert user32.GetUserObjectInformationW(user32.GetThreadDesktop(kernel32.GetCurrentThreadId()),
        2, desktop, ctypes.sizeof(desktop), ctypes.byref(needed))
    assert desktop.value == os.environ['RENDERLOCK_TEST_DESKTOP']
    assert desktop.value.startswith('RenderLockTest_'), 'Refusing to create test windows on the active desktop'
    for name, args, result in (
        ('GetWindow', [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        ('GetWindowLongPtrW', [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
        ('SetActiveWindow', [wintypes.HWND], wintypes.HWND),
        ('SetWindowPos', [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
        ('ShowWindow', [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        ('IsWindowVisible', [wintypes.HWND], wintypes.BOOL),
    ):
        function = getattr(user32, name)
        function.argtypes, function.restype = args, result

    monkeypatch.setenv('APPDATA', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.setenv('D2B_UI_MOTION', '0')
    from agr import gui, gui_dialogs, gui_export_recovery
    settings = gui.settings_path()
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(dict(check_updates='0', disable_ui_animations='1')))
    root = tk.Tk()
    errors = []
    root.report_callback_exception = lambda *error: errors.append(repr(error))
    app = gui.AGRApp(root)
    root.after(180, root.quit)
    root.mainloop()
    parent = int(root.wm_frame(), 16)
    peer = tk.Toplevel(root)
    peer.geometry('400x300+20+20')
    root.after(180, root.quit)
    root.mainloop()
    peer_handle = int(peer.wm_frame(), 16)
    observed = []

    def inspect():
        try:
            dialog = next(w for w in root.winfo_children()
                          if isinstance(w, tk.Toplevel) and w is not peer)
            handle = int(dialog.wm_frame(), 16)
            assert user32.GetWindow(handle, 4) == parent  # GW_OWNER
            assert not user32.GetWindowLongPtrW(handle, -20) & 0x40000  # WS_EX_APPWINDOW
            assert not user32.GetWindowLongPtrW(handle, -20) & 8  # not global topmost
            assert root.grab_current() == dialog
            user32.SetActiveWindow(peer_handle)
            user32.SetWindowPos(parent, None, 0, 0, 0, 0, 0x1 | 0x2 | 0x10)
            user32.SetActiveWindow(parent)
            above = user32.GetWindow(parent, 3)  # GW_HWNDPREV
            while above and above != handle:
                above = user32.GetWindow(above, 3)
            assert above == handle, 'The modal was covered after returning to the main app'
            assert user32.SetWindowPos(handle, None, dialog.winfo_x() + 24,
                dialog.winfo_y() + 24, 0, 0, 0x1 | 0x4 | 0x10)
            user32.ShowWindow(parent, 6)  # SW_MINIMIZE
            assert not user32.IsWindowVisible(handle)
            user32.ShowWindow(parent, 9)  # SW_RESTORE
            assert user32.IsWindowVisible(handle)
            observed.append(True)
        except BaseException as error:
            errors.append(repr(error))
        finally:
            for window in root.winfo_children():
                if isinstance(window, tk.Toplevel) and window is not peer:
                    window.destroy()

    try:
        for kind in ('recovery', 'error', 'recovery_minimized', 'error_minimized'):
            user32.SetActiveWindow(peer_handle)
            if kind.endswith('_minimized'):
                user32.ShowWindow(parent, 6)
                root.after(120, lambda: user32.ShowWindow(parent, 9))  # taskbar restore
            timer = root.after(5000, lambda: [w.destroy() for w in root.winfo_children()
                if isinstance(w, tk.Toplevel) and w is not peer])
            root.after(600, inspect)
            if kind.startswith('recovery'):
                assert gui_export_recovery.show_failure(app, ['python', '-m', 'agr', 'convert'],
                    'AGR error: Test export failure') is None
            else:
                assert gui_dialogs.showerror('Export failed', 'Test export failure', parent=root) == 'ok'
            root.after_cancel(timer)
            assert not errors, errors
            assert root.grab_current() is None
        assert observed == [True, True, True, True]
    finally:
        app.update_ui.closed = True
        root.destroy()
