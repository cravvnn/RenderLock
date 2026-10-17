"""Check delivered source bytes before a release can replace a good ZIP."""
import hashlib
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

import pytest


@pytest.fixture
def packager():
    path = Path(__file__).parents[1] / "tools/package_installed_release.py"
    spec = importlib.util.spec_from_file_location("agr_release_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source (release)"
    (root / "agr").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "agr/__init__.py").write_bytes(b'__version__ = "0.2.0"\n')
    (root / "agr/gui.py").write_bytes(b'print("AGR")\n')
    (root / "bin/agr-extract.exe").write_bytes(b"native helper fixture")
    (root / "README.md").write_text("Release fixture.\n", encoding="utf-8")
    return root


def test_release_manifest_matches_final_zip_bytes(packager, source, tmp_path):
    output = tmp_path / "release.zip"
    result = packager.package(source, output)
    assert result["status"] == "PASS"
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    prefix = "Deadlock_AGR_0.2.0_Alpha"
    with ZipFile(output) as archive:
        manifest = json.loads(archive.read(prefix + "/release_manifest.json"))
        assert manifest["version"] == "0.2.0"
        assert manifest["game_assets_included"] is False
        assert len(archive.namelist()) == len(manifest["files"]) + 1
        for item in manifest["files"]:
            data = archive.read(prefix + "/" + item["path"])
            assert len(data) == item["bytes"]
            assert hashlib.sha256(data).hexdigest() == item["sha256"]
            if item["path"].endswith(".py"):
                compile(data, item["path"], "exec")


@pytest.mark.parametrize("filename", ["agr/__init__.py", "agr/gui.py"])
def test_null_source_rejected_and_previous_release_preserved(packager, source, tmp_path, filename):
    output = tmp_path / "release.zip"
    packager.package(source, output)
    good_release = output.read_bytes()
    path = source / filename
    path.write_bytes(path.read_bytes() + b"\x00")
    with pytest.raises(packager.ReleaseIntegrityError, match="Invalid Python source.*null bytes"):
        packager.package(source, output)
    assert output.read_bytes() == good_release
    assert list(tmp_path.glob("release.zip.*")) == []


def test_source_changed_while_archiving_is_rejected(packager, source, tmp_path, monkeypatch):
    output = tmp_path / "release.zip"
    packager.package(source, output)
    good_release = output.read_bytes()
    original_write = packager.ZipFile.write

    def changed_write(archive, filename, arcname=None, *args, **kwargs):
        if arcname.endswith("/agr/gui.py"):
            # Same size, still valid Python, but different from the manifest.
            Path(filename).write_bytes(b'print("BAD")\n')
        return original_write(archive, filename, arcname, *args, **kwargs)

    monkeypatch.setattr(packager.ZipFile, "write", changed_write)
    with pytest.raises(packager.ReleaseIntegrityError, match="Manifest SHA256 mismatch: agr/gui.py"):
        packager.package(source, output)
    assert output.read_bytes() == good_release
    assert list(tmp_path.glob("release.zip.*")) == []


@pytest.mark.parametrize("field", ["bytes", "sha256"])
def test_manifest_must_describe_written_archive_bytes(packager, source, tmp_path, field):
    output = tmp_path / "release.zip"
    packager.package(source, output)
    prefix = "Deadlock_AGR_0.2.0_Alpha"
    with ZipFile(output) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    manifest_name = prefix + "/release_manifest.json"
    manifest = json.loads(contents[manifest_name])
    manifest["files"][0][field] = 0 if field == "bytes" else "0" * 64
    contents[manifest_name] = json.dumps(manifest).encode("utf-8")
    candidate = tmp_path / "tampered.zip"
    with ZipFile(candidate, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    # Rewriting the ZIP produces valid CRCs; manifest validation must catch it.
    with ZipFile(candidate) as archive:
        assert archive.testzip() is None
    with pytest.raises(packager.ReleaseIntegrityError, match="Manifest .* mismatch"):
        packager.validate_archive(candidate, prefix)


def test_validation_supports_flat_update_archives(packager, source, tmp_path):
    output = tmp_path / "release.zip"
    packager.package(source, output)
    prefix = "Deadlock_AGR_0.2.0_Alpha/"
    update = tmp_path / "update.zip"
    with ZipFile(output) as full, ZipFile(update, "w") as flat:
        for name in full.namelist():
            flat.writestr(name.removeprefix(prefix), full.read(name))
    assert packager.validate_archive(update)["version"] == "0.2.0"
