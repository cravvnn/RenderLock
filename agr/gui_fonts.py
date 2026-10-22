"""Load Kara's display faces privately from an owned Deadlock installation.

No fonts ship with RenderLock. Only the three audited loose TrueType files below
are accepted; a changed game font needs a new audit. Cache objects are private
user data, never application/package assets. The standalone updater can copy this
module and use the verified cache without importing any RenderLock backend.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
from tkinter import font as tkfont


# Exact installed-game identities, not redistributed font payloads.
FONT_SOURCES = (
    {"family": "VALVE Oracle Medium", "name": "valveoracle-medium.ttf",
     "bytes": 155484, "sha256": "93ab96ee6775596bb196797fbe4bd2f86c7cf0264c11230be63d51cd9e223c34"},
    {"family": "VALVE Oracle SemiBold", "name": "valveoracle-semibold.ttf",
     "bytes": 169704, "sha256": "52f5c587d31459e1a0ea4f420927b9d1744a672f175c3b7d5773137d2e483a50"},
    {"family": "VALVE Pulp Bold", "name": "valvepulp-bold.ttf",
     "bytes": 240028, "sha256": "60b904cb9353c2a8a8c7b9e6b052c9c368a8ea1e941dd4d4005a185822d205b9"},
)
FR_PRIVATE = 0x10
_FONT_LIMIT = 1024 * 1024
_MANIFEST_LIMIT = 16 * 1024
_FONT_SUBDIR = Path("game/citadel/panorama/fonts")
_MANIFEST_NAME = "manifest.json"
_REGISTERED = {}  # SHA256 -> path; registrations live until this process exits.
_REGISTER_LOCK = threading.Lock()
_GDI32 = None


def _read_bounded(path, limit):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing file or unsupported symbolic link")
    if path.stat().st_size > limit:
        raise ValueError("File exceeds the font cache size limit")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File exceeds the font cache size limit")
    return data


def _verify_font(data, spec):
    """Check the pinned identity before examining a bounded SFNT directory."""
    if len(data) != spec["bytes"] or len(data) > _FONT_LIMIT:
        raise ValueError("Font size does not match the audited game file")
    if hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError("Font SHA256 does not match the audited game file")
    if len(data) < 12 or data[:4] != b"\x00\x01\x00\x00":
        raise ValueError("Unsupported font format; an audited TrueType SFNT is required")
    count = struct.unpack_from(">H", data, 4)[0]
    end = 12 + 16 * count
    if not 1 <= count <= 64 or end > len(data):
        raise ValueError("Invalid TrueType table directory")
    tags = set()
    for pos in range(12, end, 16):
        tag, _checksum, offset, length = struct.unpack_from(">4sIII", data, pos)
        if tag in tags or offset < end or offset + length > len(data):
            raise ValueError("Invalid TrueType table bounds")
        tags.add(tag)
    if b"name" not in tags:
        raise ValueError("TrueType font has no name table")


def _cached_font(cache, spec):
    path = cache / (spec["sha256"] + ".ttf")
    if path.resolve().parent != cache:
        raise ValueError("Font cache object escapes its directory")
    _verify_font(_read_bounded(path, min(spec["bytes"], _FONT_LIMIT)), spec)
    return path


def _manifest_entry(spec):
    return {"family": spec["family"], "file": spec["sha256"] + ".ttf",
            "bytes": spec["bytes"], "sha256": spec["sha256"],
            "source": (_FONT_SUBDIR / spec["name"]).as_posix()}


def _read_manifest(cache):
    manifest = json.loads(_read_bounded(cache / _MANIFEST_NAME,
                                        _MANIFEST_LIMIT).decode("utf-8"))
    if (not isinstance(manifest, dict) or set(manifest) != {"version", "fonts"}
            or type(manifest["version"]) is not int or manifest["version"] != 1
            or not isinstance(manifest["fonts"], list)
            or len(manifest["fonts"]) > len(FONT_SOURCES)):
        raise ValueError("Unsupported font cache provenance manifest")
    expected = {spec["family"]: _manifest_entry(spec) for spec in FONT_SOURCES}
    entries = {}
    for entry in manifest["fonts"]:
        if (not isinstance(entry, dict) or not isinstance(entry.get("family"), str)
                or entry != expected.get(entry["family"])
                or entry["family"] in entries):
            raise ValueError("Font cache provenance does not match the audited fonts")
        entries[entry["family"]] = entry
    return entries


def _atomic_write(path, data, limit):
    """Stage, verify and replace in the same private cache directory."""
    if len(data) > limit:
        raise ValueError("Font cache write exceeds its size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".font-", suffix=".tmp", dir=path.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if _read_bounded(staged, limit) != data:
            raise ValueError("Font cache staged write failed verification")
        os.replace(staged, path)
        if _read_bounded(path, limit) != data:
            raise ValueError("Font cache final write failed verification")
    finally:
        staged.unlink(missing_ok=True)


def _install_root(value):
    selected = Path(value).expanduser().resolve()
    if selected.name.lower().endswith("_dir.vpk"):
        selected = selected.parent
    if selected.name.lower() == "citadel" and selected.parent.name.lower() == "game":
        return selected.parent.parent
    if selected.name.lower() == "game":
        return selected.parent
    return selected


def _register_private(path, digest):
    global _GDI32
    with _REGISTER_LOCK:
        if digest in _REGISTERED:
            return "already_registered"
        if sys.platform != "win32":
            raise OSError("Private game fonts are supported on Windows only")
        if _GDI32 is None:
            import ctypes
            from ctypes import wintypes
            _GDI32 = ctypes.WinDLL("gdi32", use_last_error=True)
            _GDI32.AddFontResourceExW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD,
                                                 ctypes.c_void_p)
            _GDI32.AddFontResourceExW.restype = ctypes.c_int
        # FR_NOT_ENUM would hide the faces from Tk. No registry/global install.
        if _GDI32.AddFontResourceExW(str(path), FR_PRIVATE, None) <= 0:
            raise OSError("Windows could not register the private game font")
        _REGISTERED[digest] = str(path)
        return "registered"


def load_fonts(root, install_path=None, cache_dir=None):
    """Return JSON-safe font availability after private registration, before Tk use.

    Supplying an installation permits preparing the three audited cache objects.
    With no installation this reads the cache only, creating no directories. The
    returned ``families`` are verified private faces also enumerated by Tk;
    callers retain their normal fallback for every missing face. ``fonts`` and
    ``errors`` explain preparation/registration failures without interrupting UI.
    """
    result = {"status": "fallback", "families": [], "fonts": [],
              "cache_dir": None, "errors": []}
    cache = None
    installed = None
    try:
        if cache_dir is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise ValueError("LOCALAPPDATA is unavailable for the private font cache")
            cache_dir = Path(local) / "RenderLock/ui-fonts-v1"
        cache = Path(cache_dir).expanduser().resolve()
        result["cache_dir"] = str(cache)
        if install_path:
            installed = _install_root(install_path)
            if cache == installed or installed in cache.parents:
                raise ValueError("The private font cache must be outside the game installation")
    except (OSError, ValueError, RuntimeError) as exc:
        result["errors"].append(str(exc)[:240])
        cache = None

    entries = {}
    if cache is not None:
        try:
            entries = _read_manifest(cache)
        except (OSError, ValueError, RuntimeError) as exc:
            if (cache / _MANIFEST_NAME).exists():
                result["errors"].append("Invalid font cache manifest: " + str(exc)[:200])

    changed = False
    for spec in FONT_SOURCES:
        state = {"family": spec["family"], "status": "missing", "source": None}
        path = None
        if cache is not None:
            # Provenance is diagnostic. Compiled pins independently authorize
            # objects even after a missing/interrupted manifest write.
            try:
                path = _cached_font(cache, spec)
                state["source"] = "cache"
                if spec["family"] not in entries:
                    entries[spec["family"]] = _manifest_entry(spec)
                    changed = True
            except (OSError, ValueError, RuntimeError) as exc:
                if spec["family"] in entries:
                    state.update(status="invalid", error=str(exc)[:240])
                    entries.pop(spec["family"])
                    changed = True
            if path is None and installed is not None:
                try:
                    source = installed / _FONT_SUBDIR / spec["name"]
                    if not source.resolve().is_relative_to(installed):
                        raise ValueError("Font source escapes the selected game installation")
                    data = _read_bounded(source, min(spec["bytes"], _FONT_LIMIT))
                    _verify_font(data, spec)
                    path = cache / (spec["sha256"] + ".ttf")
                    _atomic_write(path, data, _FONT_LIMIT)
                    path = _cached_font(cache, spec)
                    entries[spec["family"]] = _manifest_entry(spec)
                    changed = True
                    state["source"] = "installed"
                    state.pop("error", None)
                except (OSError, ValueError, RuntimeError) as exc:
                    path = None
                    state.update(status="unavailable", error=str(exc)[:240])
        if path is not None:
            state["cache_path"] = str(path)
            state["sha256"] = spec["sha256"]
            try:
                state["status"] = _register_private(path, spec["sha256"])
            except (OSError, ValueError, RuntimeError, AttributeError) as exc:
                state.update(status="unavailable", error=str(exc)[:240])
        result["fonts"].append(state)

    if changed and cache is not None and installed is not None:
        try:
            manifest = {"version": 1, "fonts": list(entries.values())}
            data = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
            _atomic_write(cache / _MANIFEST_NAME, data, _MANIFEST_LIMIT)
        except (OSError, ValueError, RuntimeError) as exc:
            result["errors"].append("Font cache provenance could not be saved: " + str(exc)[:180])
    try:
        families = set(tkfont.families(root))
        registered = {face["family"] for face in result["fonts"]
                      if face["status"] in {"registered", "already_registered"}}
        result["families"] = [spec["family"] for spec in FONT_SOURCES
                              if spec["family"] in families & registered]
    except Exception as exc:
        # A missing/destroyed Tk interpreter is another ordinary UI fallback.
        result["errors"].append("Tk font enumeration unavailable: " + str(exc)[:180])
    if len(result["families"]) == len(FONT_SOURCES):
        result["status"] = "ready"
    elif result["families"]:
        result["status"] = "partial"
    return result
