"""Native file paths for Windows hosts whose process manifest limits path length."""
from __future__ import annotations

import os
import stat

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    from msvcrt import get_osfhandle

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in
                    ('creation_time', 'access_time', 'write_time', 'change_time')]
        _fields_.append(('attributes', wintypes.DWORD))

    _get_file_info = ctypes.WinDLL('kernel32', use_last_error=True).GetFileInformationByHandleEx
    _get_file_info.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _get_file_info.restype = wintypes.BOOL


def native_path(path):
    """Keep long Unicode paths usable inside Blender's embedded Python/native IO.

    Ordinary Python distributions can be long-path aware while an application's
    embedded interpreter is not. The extended Windows spelling addresses the
    same file and does not relocate, shorten or substitute its contents.
    """
    value = os.fspath(path)
    if os.name != "nt":
        return value
    if value.startswith("\\\\?\\"):
        return value
    absolute = os.path.abspath(value)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def file_identity(path):
    """Return the current identity of a regular file, including metadata changes."""
    if os.name == 'nt':
        with open(native_path(path), 'rb', buffering=0) as stream:
            info = _FileBasicInfo()
            if not _get_file_info(get_osfhandle(stream.fileno()), 0, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            details = os.fstat(stream.fileno())
        changed = info.change_time  # Windows stat's ctime is creation time.
    else:
        details = os.stat(path)
        changed = details.st_ctime_ns
    if not stat.S_ISREG(details.st_mode):
        raise OSError(f'Expected a regular file: {path}')
    return details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns, changed
