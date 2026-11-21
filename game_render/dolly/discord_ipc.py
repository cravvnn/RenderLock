"""Bounded local Discord RPC. No HTTP, OAuth, bot, or third-party runtime."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import json
import os
import struct
import sys
import time
import uuid

MAX_FRAME = 64 * 1024
RESPONSE_TIMEOUT = 2.0


def frame(opcode, payload):
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise ValueError("Discord RPC frame is too large")
    return struct.pack("<II", opcode, len(body)) + body


class FrameReader:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > 2 * (MAX_FRAME + 8):
            raise ValueError("Discord RPC buffer limit exceeded")
        frames = []
        while len(self.buffer) >= 8:
            opcode, size = struct.unpack_from("<II", self.buffer)
            if opcode > 4 or size > MAX_FRAME:
                raise ValueError("Invalid Discord RPC frame")
            if len(self.buffer) < size + 8:
                break
            frames.append((opcode, bytes(self.buffer[8:size + 8])))
            del self.buffer[:size + 8]
            if len(frames) > 32:
                raise ValueError("Too many Discord RPC frames")
        return frames


class WindowsPipe:
    """One nonblocking named-pipe handle, owned solely by the presence worker."""
    def __init__(self, path):
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                    wt.DWORD, wt.DWORD, wt.HANDLE]
        api.CreateFileW.restype = wt.HANDLE
        api.SetNamedPipeHandleState.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD),
                                                ctypes.c_void_p, ctypes.c_void_p]
        api.PeekNamedPipe.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                      ctypes.c_void_p, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
        for name in ("ReadFile", "WriteFile"):
            getattr(api, name).argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                           ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
        api.CloseHandle.argtypes = [wt.HANDLE]
        self.api = api
        self.handle = api.CreateFileW(path, 0xc0000000, 0, None, 3, 0, None)
        if self.handle == ctypes.c_void_p(-1).value:
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        mode = wt.DWORD(1)  # PIPE_READMODE_BYTE | PIPE_NOWAIT: never wait on Discord.
        if not api.SetNamedPipeHandleState(self.handle, ctypes.byref(mode), None, None):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def send(self, data):
        buffer = ctypes.create_string_buffer(data)
        written = wt.DWORD()
        if (not self.api.WriteFile(self.handle, buffer, len(data), ctypes.byref(written), None)
                or written.value != len(data)):
            raise OSError("Discord pipe closed or its write buffer is full")

    def receive(self):
        available = wt.DWORD()
        if not self.api.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
            raise OSError("Discord pipe closed")
        if not available.value:
            return b""
        buffer = ctypes.create_string_buffer(min(available.value, MAX_FRAME + 8))
        received = wt.DWORD()
        if not self.api.ReadFile(self.handle, buffer, len(buffer), ctypes.byref(received), None):
            raise OSError("Discord pipe read failed")
        if not received.value:
            raise OSError("Discord pipe closed")
        return buffer.raw[:received.value]

    def close(self):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None


def connect_pipe():
    if sys.platform != "win32":
        raise OSError("Discord presence is available in the Windows app")
    for index in range(10):
        try:
            return WindowsPipe(r"\\?\pipe\discord-ipc-" + str(index))
        except OSError:
            continue
    raise OSError("Discord desktop is not running")


class DiscordRpc:
    def __init__(self, application_id, cancelled, *, connector=connect_pipe):
        if not isinstance(application_id, str) or not application_id.isascii() or not application_id.isdecimal():
            raise ValueError("Invalid Discord application ID")
        self.pipe = connector()
        self.reader = FrameReader()
        self.ready = False
        try:
            self.pipe.send(frame(0, {"v": 1, "client_id": application_id}))
            self._response(lambda value: value.get("evt") == "READY", cancelled)
            self.ready = True
        except BaseException:
            self.pipe.close()
            raise

    def poll(self):
        replies = []
        for opcode, raw in self.reader.feed(self.pipe.receive()):
            if opcode == 3:
                self.pipe.send(struct.pack("<II", 4, len(raw)) + raw)
            elif opcode == 2:
                raise OSError("Discord closed the RPC session")
            elif opcode == 1:
                value = json.loads(raw)
                if not isinstance(value, dict) or value.get("evt") == "ERROR":
                    raise ValueError("Discord rejected the RPC request")
                replies.append(value)
            elif opcode != 4:
                raise ValueError("Unexpected Discord RPC opcode")
        return replies

    def _response(self, matches, cancelled):
        deadline = time.monotonic() + RESPONSE_TIMEOUT
        while not cancelled.is_set():
            for reply in self.poll():
                if matches(reply):
                    return
            if time.monotonic() >= deadline:
                raise TimeoutError("Discord RPC response timed out")
            cancelled.wait(0.05)
        raise InterruptedError("Discord presence cancelled")

    def set_activity(self, activity, cancelled):
        if cancelled.is_set():
            raise InterruptedError("Discord presence cancelled")
        nonce = uuid.uuid4().hex
        self.pipe.send(frame(1, {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": activity},
                                 "nonce": nonce}))
        self._response(lambda value: value.get("nonce") == nonce and value.get("cmd") == "SET_ACTIVITY", cancelled)

    def close(self):
        try:
            if self.ready:
                self.pipe.send(frame(1, {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": None},
                                         "nonce": uuid.uuid4().hex}))
        except OSError:
            pass
        finally:
            self.ready = False
            self.pipe.close()
