"""Closes the game a Blender session launched once that Blender is gone.

Started next to the game with both process IDs. It only waits while Blender runs. If Blender
closes, crashes or is ended first, it asks the game to close the normal way (a window close
request, never a forced end) and answers the game's development assertion dialog, which would
otherwise keep the game open with nobody left to press it. Dolly's own cleanup helper then
puts the game's files back, as after every session.
"""
import ctypes
import subprocess
import sys
import time

SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF
CLOSE_WITHIN = 180.0   # seconds to keep asking; a game that still runs after that is left alone


def close_game(pid):
    """One normal close request to this exact process."""
    subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main(blender_pid, game_pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.WaitForMultipleObjects.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint]
    # The handles keep both process IDs from being reused while this helper waits.
    game = kernel.OpenProcess(SYNCHRONIZE, False, game_pid)
    if not game:
        return 0  # the game is already gone
    blender = kernel.OpenProcess(SYNCHRONIZE, False, blender_pid)
    if blender:
        handles = (ctypes.c_void_p * 2)(blender, game)
        if kernel.WaitForMultipleObjects(2, handles, False, INFINITE) != 0:
            return 0  # the game closed first: nothing to do
    from dolly.assert_watcher import dismiss_assert_dialogs
    deadline = time.monotonic() + CLOSE_WITHIN
    ask_at = 0.0
    while kernel.WaitForSingleObject(game, 500) != 0 and time.monotonic() < deadline:
        if time.monotonic() >= ask_at:
            close_game(game_pid)
            ask_at = time.monotonic() + 3.0
        dismiss_assert_dialogs(game_pid)
    return 0


def start(game_pid, blender_pid, python):
    """Start the helper for this game, detached, so it outlives Blender."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([python, __file__, str(int(blender_pid)), str(int(game_pid))],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True, creationflags=flags)


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), int(sys.argv[2])))
