"""Portable startup refuses changed source and incomplete manifests."""
import hashlib
import json
from pathlib import Path

import pytest

import portable_launcher


@pytest.fixture
def installation(tmp_path):
    names = ["agr/__init__.py", "agr/gui.py", "agr/cli.py", "release_profile.json",
             "portable_launcher.py", "bin/agr-extract.exe", "runtime/python.exe"]
    entries = []
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b'print("ok")\n' if name.endswith(".py") else b"fixture"
        path.write_bytes(data)
        entries.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    (tmp_path / "release_manifest.json").write_text(json.dumps(dict(version="fixture", files=entries)))
    return tmp_path


def test_changed_runtime_rejected_before_application_import(installation):
    assert portable_launcher.verify(installation)["version"] == "fixture"
    (installation / "runtime/python.exe").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Changed"):
        portable_launcher.verify(installation)


def test_matching_hash_does_not_hide_broken_python(installation):
    path = installation / "agr/gui.py"
    path.write_bytes(b"def broken(:\n")
    manifest = installation / "release_manifest.json"
    data = json.loads(manifest.read_text())
    item = next(item for item in data["files"] if item["path"] == "agr/gui.py")
    item.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    manifest.write_text(json.dumps(data))
    with pytest.raises(SyntaxError):
        portable_launcher.verify(installation)


def test_manifest_must_include_startup_and_runtime(installation):
    manifest = installation / "release_manifest.json"
    data = json.loads(manifest.read_text())
    data["files"] = [item for item in data["files"] if item["path"] != "runtime/python.exe"]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="incomplete"):
        portable_launcher.verify(installation)
