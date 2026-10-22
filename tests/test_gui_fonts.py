"""Synthetic integrity/control-flow checks; no installed font bytes required."""
import builtins
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace

import pytest

from agr import gui_fonts


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    # pytest's configured base directory keeps these under the owned D: case.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(gui_fonts, "_REGISTERED", {})
    monkeypatch.setattr(gui_fonts, "_GDI32", None)


def synthetic_font(family):
    # A bounded test-only SFNT container, never passed to a real Windows API.
    name = family.encode("utf-16-be")
    table = struct.pack(">HHHHHHHHH", 0, 1, 18, 3, 1, 0x409, 1, len(name), 0) + name
    return (struct.pack(">4sHHHH", b"\0\1\0\0", 1, 16, 0, 0)
            + struct.pack(">4sIII", b"name", 0, 28, len(table)) + table)


@pytest.fixture
def fonts(tmp_path, monkeypatch):
    specs = []
    payloads = {}
    for audited in gui_fonts.FONT_SOURCES:
        data = synthetic_font(audited["family"])
        spec = {**audited, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        specs.append(spec)
        payloads[spec["name"]] = data
    monkeypatch.setattr(gui_fonts, "FONT_SOURCES", tuple(specs))
    monkeypatch.setattr(gui_fonts.sys, "platform", "win32")
    events = []

    def add_font(path, flags, reserved):
        events.append(("register", path, flags, reserved))
        return 1

    def families(root):
        events.append(("enumerate", root))
        return tuple(spec["family"] for spec in specs)

    monkeypatch.setattr(gui_fonts, "_GDI32", SimpleNamespace(AddFontResourceExW=add_font))
    monkeypatch.setattr(gui_fonts.tkfont, "families", families)
    install = tmp_path / "Deadlock"
    directory = install / "game/citadel/panorama/fonts"
    directory.mkdir(parents=True)
    for name, data in payloads.items():
        (directory / name).write_bytes(data)
    return SimpleNamespace(specs=specs, data=payloads, events=events,
                           install=install, directory=directory, cache=tmp_path / "cache")


def object_path(fonts, index=0):
    return fonts.cache / (fonts.specs[index]["sha256"] + ".ttf")


def prepare(fonts):
    return gui_fonts.load_fonts(object(), install_path=fonts.install, cache_dir=fonts.cache)


def test_exact_audited_identities():
    assert [(s["name"], s["family"], s["bytes"], s["sha256"])
            for s in gui_fonts.FONT_SOURCES] == [
        ("valveoracle-medium.ttf", "VALVE Oracle Medium", 155484,
         "93ab96ee6775596bb196797fbe4bd2f86c7cf0264c11230be63d51cd9e223c34"),
        ("valveoracle-semibold.ttf", "VALVE Oracle SemiBold", 169704,
         "52f5c587d31459e1a0ea4f420927b9d1744a672f175c3b7d5773137d2e483a50"),
        ("valvepulp-bold.ttf", "VALVE Pulp Bold", 240028,
         "60b904cb9353c2a8a8c7b9e6b052c9c368a8ea1e941dd4d4005a185822d205b9"),
    ]


def test_private_registration_precedes_tk_and_deduplicates_across_roots(fonts):
    first_root, second_root = object(), object()
    result = gui_fonts.load_fonts(first_root, fonts.install, fonts.cache)
    assert result["status"] == "ready"
    assert all(face["status"] == "registered" for face in result["fonts"])
    assert fonts.events == [
        *(("register", str(object_path(fonts, n)), 0x10, None) for n in range(3)),
        ("enumerate", first_root),
    ]
    again = gui_fonts.load_fonts(second_root, cache_dir=fonts.cache)
    assert all(face["status"] == "already_registered" for face in again["fonts"])
    assert fonts.events[-1] == ("enumerate", second_root)
    assert len(fonts.events) == 5
    assert len(gui_fonts._REGISTERED) == 3
    assert json.loads(json.dumps(result)) == result


def test_prepared_cache_is_content_addressed_and_has_bounded_provenance(fonts):
    prepare(fonts)
    for n, spec in enumerate(fonts.specs):
        assert object_path(fonts, n).read_bytes() == fonts.data[spec["name"]]
    manifest = fonts.cache / "manifest.json"
    assert manifest.stat().st_size < gui_fonts._MANIFEST_LIMIT
    value = json.loads(manifest.read_text())
    assert value == {"version": 1, "fonts": [gui_fonts._manifest_entry(s) for s in fonts.specs]}
    assert not list(fonts.cache.glob(".font-*"))


@pytest.mark.parametrize("kind", ["missing", "malformed", "oversized", "forged"])
def test_pinned_cache_does_not_trust_or_require_manifest(fonts, kind):
    prepare(fonts)
    manifest = fonts.cache / "manifest.json"
    if kind == "missing":
        manifest.unlink()
    elif kind == "malformed":
        manifest.write_text("{truncated")
    elif kind == "oversized":
        manifest.write_bytes(b" " * (gui_fonts._MANIFEST_LIMIT + 1))
    else:
        value = json.loads(manifest.read_text())
        value["fonts"][0]["file"] = "../../injected.ttf"
        manifest.write_text(json.dumps(value))
    before = manifest.read_bytes() if manifest.exists() else None
    result = gui_fonts.load_fonts(object(), cache_dir=fonts.cache)
    assert all(face["source"] == "cache" for face in result["fonts"])
    assert result["status"] == "ready"
    assert (manifest.read_bytes() if manifest.exists() else None) == before
    assert bool(result["errors"]) == (kind != "missing")


@pytest.mark.parametrize("corruption", ["same_size", "truncated", "oversized"])
def test_corrupt_cached_font_is_never_registered(fonts, monkeypatch, corruption):
    prepare(fonts)
    monkeypatch.setattr(gui_fonts, "_REGISTERED", {})
    fonts.events.clear()
    path = object_path(fonts)
    data = path.read_bytes()
    if corruption == "same_size":
        data = data[:-1] + bytes([data[-1] ^ 1])
    elif corruption == "truncated":
        data = data[:-1]
    else:
        data += b"X"
    path.write_bytes(data)
    result = gui_fonts.load_fonts(object(), cache_dir=fonts.cache)
    assert result["fonts"][0]["status"] == "invalid"
    assert result["status"] == "partial"
    assert fonts.specs[0]["family"] not in result["families"]
    assert str(path) not in [event[1] for event in fonts.events if event[0] == "register"]
    assert len([event for event in fonts.events if event[0] == "register"]) == 2


def test_valid_install_repairs_corrupt_cache(fonts, monkeypatch):
    prepare(fonts)
    object_path(fonts).write_bytes(b"corrupt")
    monkeypatch.setattr(gui_fonts, "_REGISTERED", {})
    result = prepare(fonts)
    assert result["fonts"][0]["source"] == "installed"
    assert result["fonts"][0]["status"] == "registered"
    assert object_path(fonts).read_bytes() == fonts.data[fonts.specs[0]["name"]]


def test_wrong_source_and_distractor_do_not_load(fonts):
    spec = fonts.specs[0]
    source = fonts.directory / spec["name"]
    source.rename(fonts.directory / "unrelated.ttf")
    result = prepare(fonts)
    assert result["fonts"][0]["status"] == "unavailable"
    assert not object_path(fonts).exists()
    assert len([event for event in fonts.events if event[0] == "register"]) == 2


def test_changed_source_hash_falls_back_without_registering(fonts):
    source = fonts.directory / fonts.specs[0]["name"]
    data = source.read_bytes()
    source.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    result = prepare(fonts)
    assert "SHA256" in result["fonts"][0]["error"]
    assert not object_path(fonts).exists()


@pytest.mark.parametrize("bad_data", [b"OTTO" + b"\0" * 28,
    struct.pack(">4sHHHH", b"\0\1\0\0", 65, 0, 0, 0),
    struct.pack(">4sHHHH", b"\0\1\0\0", 1, 0, 0, 0)
    + struct.pack(">4sIII", b"name", 0, 0xFFFFFFFF, 1),
    struct.pack(">4sHHHH", b"\0\1\0\0", 1, 0, 0, 0)
    + struct.pack(">4sIII", b"fake", 0, 28, 0)])
def test_font_type_and_table_bounds_checked_even_with_matching_test_pin(bad_data):
    spec = {"bytes": len(bad_data), "sha256": hashlib.sha256(bad_data).hexdigest()}
    with pytest.raises(ValueError, match="font format|table directory|table bounds|name table"):
        gui_fonts._verify_font(bad_data, spec)


def test_atomic_failure_preserves_destination_and_removes_staging(fonts, monkeypatch):
    path = fonts.cache / "existing"
    fonts.cache.mkdir()
    path.write_bytes(b"original")

    def fail_replace(*args):
        raise PermissionError("test replace denied")

    monkeypatch.setattr(gui_fonts.os, "replace", fail_replace)
    with pytest.raises(PermissionError):
        gui_fonts._atomic_write(path, b"replacement", 100)
    assert path.read_bytes() == b"original"
    assert not list(fonts.cache.glob(".font-*"))


def test_unwritable_cache_is_a_graceful_fallback(fonts, monkeypatch):
    def deny(*args):
        raise PermissionError("test cache denied")

    monkeypatch.setattr(gui_fonts, "_atomic_write", deny)
    monkeypatch.setattr(gui_fonts.tkfont, "families", lambda root: ())
    result = prepare(fonts)
    assert result["status"] == "fallback"
    assert all(face["status"] == "unavailable" for face in result["fonts"])
    assert not fonts.events


def test_manifest_write_failure_does_not_block_pinned_cache(fonts, monkeypatch):
    write = gui_fonts._atomic_write

    def deny_manifest(path, data, limit):
        if path.name == "manifest.json":
            raise PermissionError("test manifest denied")
        return write(path, data, limit)

    monkeypatch.setattr(gui_fonts, "_atomic_write", deny_manifest)
    result = prepare(fonts)
    assert result["status"] == "ready"
    assert "provenance could not be saved" in result["errors"][0]
    assert gui_fonts.load_fonts(object(), cache_dir=fonts.cache)["status"] == "ready"


@pytest.mark.parametrize("install_path", [None, ""])
def test_cache_only_missing_path_creates_nothing(fonts, monkeypatch, install_path):
    def no_install(*args):
        pytest.fail("Cache-only loading must not probe an installation")

    monkeypatch.setattr(gui_fonts, "_install_root", no_install)
    monkeypatch.setattr(gui_fonts.tkfont, "families", lambda root: ())
    result = gui_fonts.load_fonts(object(), install_path=install_path, cache_dir=fonts.cache)
    assert result["status"] == "fallback"
    assert not fonts.cache.exists()


def test_default_cache_uses_only_overridden_localappdata(fonts):
    result = gui_fonts.load_fonts(object(), fonts.install)
    expected = Path(os.environ["LOCALAPPDATA"]) / "RenderLock/ui-fonts-v1"
    assert result["cache_dir"] == str(expected.resolve())
    assert (expected / "manifest.json").exists()
    assert gui_fonts.load_fonts(object())["status"] == "ready"


def test_missing_localappdata_does_not_create_a_home_fallback(fonts, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setattr(gui_fonts.tkfont, "families", lambda root: ())
    result = gui_fonts.load_fonts(object(), fonts.install)
    assert result["status"] == "fallback"
    assert result["cache_dir"] is None
    assert "LOCALAPPDATA" in result["errors"][0]


def test_cache_cannot_write_inside_game(fonts):
    result = gui_fonts.load_fonts(object(), fonts.install, fonts.install / "cache")
    assert "outside the game" in result["errors"][0]
    assert not (fonts.install / "cache").exists()


@pytest.mark.parametrize("selection", ["", "game", "game/citadel", "game/citadel/pak01_dir.vpk"])
def test_supported_game_path_forms(fonts, selection):
    result = gui_fonts.load_fonts(object(), fonts.install / selection, fonts.cache)
    assert all(face["status"] == "registered" for face in result["fonts"])


def test_escaped_source_is_rejected(fonts, monkeypatch):
    original = Path.resolve
    target = fonts.directory / fonts.specs[0]["name"]

    def resolve(path, *args, **kwargs):
        if path == target:
            return fonts.install.parent / "escaped.ttf"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    result = prepare(fonts)
    assert "escapes" in result["fonts"][0]["error"]
    assert not object_path(fonts).exists()


def test_failed_registration_is_retried_and_never_retained(fonts, monkeypatch):
    monkeypatch.setattr(gui_fonts, "_GDI32", SimpleNamespace(AddFontResourceExW=lambda *args: 0))
    first = prepare(fonts)
    assert all(face["status"] == "unavailable" for face in first["fonts"])
    assert first["status"] == "fallback"
    assert first["families"] == []  # Same-named system faces cannot authorize a font.
    assert not gui_fonts._REGISTERED
    monkeypatch.setattr(gui_fonts, "_GDI32", SimpleNamespace(AddFontResourceExW=lambda *args: 1))
    second = gui_fonts.load_fonts(object(), cache_dir=fonts.cache)
    assert all(face["status"] == "registered" for face in second["fonts"])


def test_unsupported_platform_never_calls_windows(fonts, monkeypatch):
    monkeypatch.setattr(gui_fonts.sys, "platform", "linux")
    result = prepare(fonts)
    assert all(face["status"] == "unavailable" for face in result["fonts"])
    assert result["status"] == "fallback"
    assert result["families"] == []
    assert all("Windows only" in face["error"] for face in result["fonts"])
    assert not [event for event in fonts.events if event[0] == "register"]


def test_tk_reports_only_exact_faces_and_handles_destroyed_root(fonts, monkeypatch):
    monkeypatch.setattr(gui_fonts.tkfont, "families", lambda root: ("Segoe UI", "VALVE Oracle Medium"))
    result = prepare(fonts)
    assert result["status"] == "partial"
    assert result["families"] == ["VALVE Oracle Medium"]

    def destroyed(root):
        raise RuntimeError("destroyed root")

    monkeypatch.setattr(gui_fonts.tkfont, "families", destroyed)
    result = gui_fonts.load_fonts(object(), cache_dir=fonts.cache)
    assert result["status"] == "fallback"
    assert "Tk font enumeration unavailable" in result["errors"][0]


def test_standalone_cache_load_imports_no_backend(fonts, monkeypatch):
    prepare(fonts)
    original_import = builtins.__import__

    def isolated_import(name, *args, **kwargs):
        if name == "agr" or name.startswith("agr."):
            pytest.fail("Standalone font loading must not import the backend")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", isolated_import)
    spec = importlib.util.spec_from_file_location("standalone_gui_fonts", gui_fonts.__file__)
    standalone = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standalone)
    standalone.FONT_SOURCES = tuple(fonts.specs)
    standalone._GDI32 = gui_fonts._GDI32
    result = standalone.load_fonts(object(), cache_dir=fonts.cache)
    assert all(face["status"] == "registered" for face in result["fonts"])
