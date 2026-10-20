"""Native file paths for Windows hosts whose process manifest limits path length."""
from __future__ import annotations

import os


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
