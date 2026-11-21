"""Read-only dashboard preload verification for Dolly-owned game processes.

This is independent of the camera backend. The layout is accepted only for the
reviewed client AND resource-system images; Native camera compatibility alone
does not authorize it. No game functions are called and no memory is written.
See docs/internal/PRELOAD.md for the reviewed predicate and update procedure.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import struct
import time


class PreloadError(RuntimeError):
    """Verification unavailable; never permission to dispatch a replay."""


# Several monitors inspect large reviewed game files during startup and shot
# preparation. Cache bytes/digest under path metadata including file identity;
# consumers still compare the digest with their reviewed pin. Refuse a changing
# read and return oversized bytes for validation without retaining them.
_FILE_CACHE = {}
_FILE_CACHE_BUDGET = 128 * 1024 * 1024


def game_file(path):
    """Return (data, sha256_hexdigest) for a reviewed game file, memoized.

    Keyed on the resolved path and observed file identity/metadata. Ordinary
    replacement invalidates cached bytes even when size and mtime match.
    Misses must retain their stamp through the read; the caller still compares
    the digest against its expected reviewed value. This is not an OS load lock.
    """
    path = Path(path)
    key = str(path.resolve())
    def identity():
        info = path.stat()
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    try:
        before = identity()
        cached = _FILE_CACHE.get(key)
        if cached is not None and cached[0] == before:
            return cached[1], cached[2]
        data = path.read_bytes()
        if identity() != before or len(data) != before[2]:
            raise PreloadError(f'Reviewed game file changed while reading {path.name}.')
    except OSError as exc:
        raise PreloadError(f'Cannot read reviewed game file {path.name}.') from exc
    digest = hashlib.sha256(data).hexdigest()
    if len(data) <= _FILE_CACHE_BUDGET:
        total = sum(len(entry[1]) for entry in _FILE_CACHE.values())
        old = _FILE_CACHE.get(key)
        if old is not None:
            total -= len(old[1])
        if total + len(data) > _FILE_CACHE_BUDGET:
            _FILE_CACHE.clear()
        _FILE_CACHE[key] = (before, data, digest)
    else:
        _FILE_CACHE.pop(key, None)
    return data, digest


class _ReviewedBuildMismatch(PreloadError):
    """A successful read disagreed with the reviewed build; never retried away."""


from ._runtime_generated import Preload as _profile

CLIENT_SHA256 = _profile.CLIENT_SHA256
RESOURCE_SHA256 = _profile.RESOURCE_SHA256
RESOURCE_RENDERER_SHA256 = _profile.RESOURCE_RENDERER_SHA256
MANAGER = _profile.MANAGER
MANAGER_VTABLE = _profile.MANAGER_VTABLE
RESOURCE_GLOBAL = _profile.RESOURCE_GLOBAL
RESOURCE_VTABLE = _profile.RESOURCE_VTABLE
RESOURCE_QUERY = _profile.RESOURCE_QUERY
INTRO_GLOBAL = _profile.INTRO_GLOBAL
INTRO_VTABLE = _profile.INTRO_VTABLE

# Build 6745: status observes two lifecycle flags and consumes the finished
# job handle itself. Check the producer/callback as well as the UI predicate.
CLIENT_CODE_SPANS = _profile.CLIENT_CODE_SPANS


def _post_intro_escape(session):
    """Queue one Escape pair to the owned game window, never global input."""
    api = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    api.EnumWindows.restype = wintypes.BOOL
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowThreadProcessId.restype = wintypes.DWORD
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetWindowTextW.restype = ctypes.c_int
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.IsWindowVisible.restype = wintypes.BOOL
    api.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.PostMessageW.restype = wintypes.BOOL
    candidates = []

    def owned(hwnd):
        owner = wintypes.DWORD()
        title = ctypes.create_unicode_buffer(256)
        api.GetWindowTextW(hwnd, title, len(title))
        return (session.running and api.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                and owner.value == session.pid and title.value == 'Deadlock')

    @callback_type
    def visit(hwnd, _):
        if owned(hwnd) and api.IsWindowVisible(hwnd):
            candidates.append(hwnd)
        return True

    if not api.EnumWindows(visit, 0) or len(candidates) != 1:
        raise PreloadError('Cannot identify the owned Deadlock intro window.')
    hwnd = candidates[0]
    if not owned(hwnd):
        raise PreloadError('Deadlock intro window ownership changed.')
    # WM_KEYDOWN/UP, VK_ESCAPE, scan code 1. No focus change or SendInput.
    down = api.PostMessageW(hwnd, 0x100, 0x1b, 0x00010001)
    up = api.PostMessageW(hwnd, 0x101, 0x1b, 0xc0010001) if owned(hwnd) else False
    if not down or not up:
        raise PreloadError('Could not advance the Deadlock intro; replay remains unloaded.')


def _image_bytes(data, rva, size):
    """Read a reviewed PE's file-backed RVA without a runtime dependency."""
    try:
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        if data[:2] != b'MZ' or data[pe:pe+4] != b'PE\0\0':
            raise ValueError('not PE')
        count = struct.unpack_from('<H', data, pe+6)[0]
        optional = struct.unpack_from('<H', data, pe+20)[0]
        for index in range(count):
            section = pe+24+optional+40*index
            start, raw_size, offset = struct.unpack_from('<III', data, section+12)
            if start <= rva and rva+size <= start+raw_size:
                result = data[offset+rva-start:offset+rva-start+size]
                if len(result) == size:
                    return result
    except (ValueError, struct.error):
        pass
    raise PreloadError('Could not verify the preload code image.')


class _Module(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('module_id', wintypes.DWORD),
                ('pid', wintypes.DWORD), ('global_usage', wintypes.DWORD),
                ('process_usage', wintypes.DWORD), ('base', ctypes.c_void_p),
                ('image_size', wintypes.DWORD), ('module', wintypes.HMODULE),
                ('name', wintypes.WCHAR * 256), ('path', wintypes.WCHAR * 260)]


def _module_snapshot(api, pid):
    # Windows documents ERROR_BAD_LENGTH when the module list changes during
    # enumeration. Retry only that transient result, with a bounded wait.
    for attempt in range(8):
        snapshot = api.CreateToolhelp32Snapshot(0x8 | 0x10, pid)
        if snapshot != wintypes.HANDLE(-1).value:
            return snapshot
        error = ctypes.get_last_error()
        if error != 24 or attempt == 7:
            raise PreloadError(f'Cannot verify loaded preload modules (Windows error {error}).')
        time.sleep(.02)
    raise AssertionError('Unreachable snapshot retry state')


class _Memory:
    def __init__(self, session, executable):
        if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise PreloadError('Preload verification requires 64-bit Windows.')
        self.handle = None
        self.bytes_read = 0
        self.api = api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        api.ReadProcessMemory.restype = wintypes.BOOL
        api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                   wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        api.QueryFullProcessImageNameW.restype = wintypes.BOOL
        api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for name in ('Module32FirstW', 'Module32NextW'):
            function = getattr(api, name)
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Module)]
            function.restype = wintypes.BOOL
        self.handle = api.OpenProcess(0x10 | 0x400, False, session.pid)
        if not self.handle:
            raise PreloadError('Cannot read preload status from the launched game.')
        try:
            path = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path))
            if (not api.QueryFullProcessImageNameW(self.handle, 0, path, ctypes.byref(size))
                    or Path(path.value).resolve() != executable or not session.running):
                raise PreloadError('Preload process identity differs from the launched game.')
            self.modules = {}
            snapshot = _module_snapshot(api, session.pid)
            try:
                entry = _Module()
                entry.size = ctypes.sizeof(entry)
                valid = api.Module32FirstW(snapshot, ctypes.byref(entry))
                while valid:
                    self.modules[entry.name.casefold()] = (entry.base, Path(entry.path).resolve())
                    valid = api.Module32NextW(snapshot, ctypes.byref(entry))
            finally:
                api.CloseHandle(snapshot)
        except BaseException:
            self.close()
            raise

    def read(self, address, size):
        if not address or not 0x10000 <= address < 0x7fffffffffff or not 0 < size <= 4096:
            raise PreloadError('Invalid preload status address.')
        self.bytes_read += size
        if self.bytes_read > 4*1024*1024:
            raise PreloadError('Preload observation limit reached.')
        buffer, received = ctypes.create_string_buffer(size), ctypes.c_size_t()
        if (not self.api.ReadProcessMemory(self.handle, address, buffer, size, ctypes.byref(received))
                or received.value != size):
            raise PreloadError('Could not read complete preload status; replay remains unloaded.')
        return buffer.raw

    def pointer(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class PreloadMonitor:
    def __init__(self, session):
        self.session = session
        self.memory = None
        command = tuple(session.command)
        if (not session.running or not command or '-dev' not in command or '-insecure' not in command
                or tuple(session.process.args) != command or not session.owns_console_port()):
            raise PreloadError('Preload verification requires the Dolly-owned development session.')
        executable = Path(command[0]).resolve()
        game = executable.parents[2]
        paths = (game/'citadel/bin/win64/client.dll', game/'bin/win64/resourcesystem.dll',
                 game/'bin/win64/rendersystemdx11.dll')
        images = []
        for path, expected in zip(paths, (CLIENT_SHA256, RESOURCE_SHA256, RESOURCE_RENDERER_SHA256)):
            try:
                data, observed = game_file(path)
            except PreloadError as exc:
                raise PreloadError('Cannot verify installed preload modules.') from exc
            if observed != expected:
                raise PreloadError(f'Preload verification is not supported for this game build '
                                   f'({path.name}: {observed}). Update Dolly or use manual startup; '
                                   'automatic replay loading was stopped.')
            images.append(data)
        try:
            self.memory = memory = _Memory(session, executable)
            bases = []
            for path in paths:
                module = memory.modules.get(path.name)
                if not module or module[1] != path.resolve():
                    raise PreloadError('Loaded preload module path differs from the reviewed installation.')
                bases.append(module[0])
            self.base, self.resource_base, self.renderer_base = bases
            for base, data, spans in (
                    (self.base, images[0], CLIENT_CODE_SPANS),
                    (self.renderer_base, images[2], ((RESOURCE_QUERY, 13),))):
                for rva, length in spans:
                    if memory.read(base+rva, length) != _image_bytes(data, rva, length):
                        raise PreloadError('Live preload code differs from the reviewed game build.')
            self.identity = {'client_sha256': CLIENT_SHA256, 'resource_sha256': RESOURCE_SHA256,
                             'renderer_sha256': RESOURCE_RENDERER_SHA256,
                             'method': 'reviewed_dashboard_preload', 'pid': session.pid}
            self.intro_sent = False
        except BaseException:
            self.close()
            raise

    def sample(self):
        if not self.session.running:
            raise PreloadError('Deadlock closed during preload verification.')
        try:
            return self._sample()
        except _ReviewedBuildMismatch:
            raise
        except PreloadError:
            # The preload object lives in the game's writable data and can be
            # briefly unreadable while the hideout is still loading. That is
            # "not ready yet", not an incompatible build, so keep polling
            # rather than abort automatic startup. A read that succeeds but
            # contradicts the reviewed build still fails closed above.
            return {'coherent': False}

    def _sample(self):
        memory = self.memory
        raw = memory.read(self.base+MANAGER, _profile.MANAGER_SIZE)
        if raw != memory.read(self.base+MANAGER, _profile.MANAGER_SIZE):
            return {'coherent': False}
        if struct.unpack_from('<Q', raw)[0] != self.base+MANAGER_VTABLE:
            raise _ReviewedBuildMismatch('Preload object type differs from the reviewed build.')
        completed, total = struct.unpack_from('<ii', raw, _profile.COMPLETED)
        if not 0 <= completed <= 10000000 or not 0 <= total <= 10000000:
            raise _ReviewedBuildMismatch('Preload counters are outside their reviewed range.')
        resource = struct.unpack_from('<Q', raw, _profile.RESOURCE)[0]
        job = struct.unpack_from('<i', raw, _profile.JOB)[0]
        scheduled, job_complete = raw[_profile.LIFECYCLE_BEGIN:_profile.LIFECYCLE_END]
        if scheduled not in (0, 1) or job_complete not in (0, 1):
            raise _ReviewedBuildMismatch('Preload lifecycle flags differ from the reviewed build.')
        # The resource-system global is populated partway through the hideout
        # load. Until then it reads as a null (or not-yet-mapped) pointer; that
        # is "not started", not a build mismatch, so it must not abort automatic
        # startup. A non-null value still has to match the reviewed vtable.
        system = memory.pointer(self.base+RESOURCE_GLOBAL)
        if not system:
            return {'coherent': False}
        try:
            vtable = memory.pointer(system)
        except PreloadError:
            return {'coherent': False}
        if (vtable != self.renderer_base+RESOURCE_VTABLE
                or memory.pointer(vtable+_profile.RESOURCE_QUERY_SLOT) != self.renderer_base+RESOURCE_QUERY):
            raise _ReviewedBuildMismatch('Resource completion query differs from the reviewed build.')
        resource_done = memory.read(resource+_profile.RESOURCE_DONE, 1)[0] if resource else 1
        if resource_done not in (0, 1) or raw != memory.read(self.base+MANAGER, _profile.MANAGER_SIZE):
            return {'coherent': False}
        intro_phase = None
        if not resource:
            intro = memory.pointer(self.base+INTRO_GLOBAL)
            if intro:
                try:
                    state = memory.read(intro, _profile.INTRO_SIZE)
                except PreloadError:
                    return {'coherent': False}
                if struct.unpack_from('<Q', state)[0] != self.base+INTRO_VTABLE:
                    raise _ReviewedBuildMismatch('Intro object type differs from the reviewed build.')
                intro_phase = struct.unpack_from('<i', state, _profile.INTRO_PHASE)[0]
                if intro_phase not in (0, 1, 2, 3):
                    raise _ReviewedBuildMismatch('Unrecognized Deadlock intro state.')
                if (memory.pointer(self.base+INTRO_GLOBAL) != intro
                        or memory.read(intro+_profile.INTRO_PHASE, 4) != state[_profile.INTRO_PHASE:_profile.INTRO_SIZE]):
                    return {'coherent': False}
        return {'coherent': True, 'started': bool(resource), 'completed': completed, 'total': total,
                'intro_phase': intro_phase,
                'scheduled': bool(scheduled), 'job_complete': bool(job_complete),
                'resource_complete': bool(resource_done), 'job_active': job != 0,
                # Unlike the game's UI, never call an unscheduled preload ready.
                # Its status function waits/releases the completed job and clears
                # the handle; observe that final state without calling it ourselves.
                'ready': bool(resource) and scheduled == 1 and job_complete == 1
                         and job == 0 and resource_done == 1 and completed >= total}

    def advance_intro(self):
        """Only once, only at the reviewed interactive intro, with fresh state."""
        if self.intro_sent:
            return False
        sample = self.sample()
        if not sample.get('coherent') or sample.get('started') or sample.get('intro_phase') != 2:
            return False
        self.intro_sent = True
        _post_intro_escape(self.session)
        return True

    def close(self):
        if self.memory is not None:
            self.memory.close()
            self.memory = None
