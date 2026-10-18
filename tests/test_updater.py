"""Release trust, bounded staging, real filesystem rollback and explicit consent."""
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from agr import updater as u, update_worker as w
from agr.update_ui import UpdateUI


def release(version="0.3.0b5", **kw):
    tag = "v" + version
    name = f"RenderLock-{version}-Windows-x64.zip"
    data = dict(draft=False, prerelease=True, tag_name=tag, body="### Notes\n- **Updated.** *Before:* old. *After:* new.",
                assets=[dict(name=name, state="uploaded", size=100,
                    browser_download_url=f"https://github.com/{u.REPOSITORY}/releases/download/{tag}/{name}",
                    digest="sha256:" + "1" * 64)])
    data.update(kw)
    return data


def payload(root, version, extra=None):
    names = u.REQUIRED | {"agr/obsolete.py"} | set(extra or {})
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((extra or {}).get(name, "# " + version if name.endswith(".py") else version))
    files = [dict(path=n, bytes=(root / n).stat().st_size, sha256=u.digest_file(root / n)) for n in sorted(names)]
    (root / u.MANIFEST).write_text(json.dumps(dict(version=version, kind="Windows-x64-portable",
                                                profile="character-beta", files=files)))
    return root


def transaction(tmp_path):
    root = payload(tmp_path / "installed & space", "0.3.0b4")
    work = root / u.WORK
    new = payload(work / "payload", "0.3.0b5")
    (root / "user-scene.blend").write_bytes(b"user scene")
    w.atomic_json(work / "verified.json", {"version": "0.3.0b5"})
    return root, work, new


def test_beta_selection_uses_versions_not_api_order_and_includes_prereleases():
    selected = u.select_release([release("0.3.0b5"), release("0.3.0b3"), release("0.3.0b12")], "0.3.0b4")
    assert selected["version"] == "0.3.0b12"
    assert "Before:" in selected["notes"]
    assert u.select_release([release("0.3.0b4")], "0.3.0b4") is None
    assert u.select_release([release("0.3.1b1")], "0.3.0") is None
    assert u.select_release([release(draft=True)], "0.3.0b4") is None


@pytest.mark.parametrize("field,value", [("digest", None), ("size", -1), ("size", True),
    ("browser_download_url", "https://evil.example/update.zip"), ("state", "new")])
def test_invalid_asset_never_authorizes_a_download(field, value):
    data = release()
    data["assets"][0][field] = value
    with pytest.raises(ValueError):
        u.select_release([data], "0.3.0b4")


@pytest.mark.parametrize("name", ["../file", "/file", "C:/file", "agr/a:ads", "agr/../evil",
    "agr\\evil", "agr/CON.txt", "agr/file.", "agr//file", "agr/./file", "agr/line\nfile"])
def test_windows_unsafe_names_rejected(name):
    with pytest.raises(ValueError):
        u.safe_name(name)


def archive(tmp_path, bad=None):
    root = payload(tmp_path / "source", "0.3.0b5")
    path = tmp_path / "update.zip"
    prefix = "RenderLock-0.3.0b5-Windows-x64/"
    with zipfile.ZipFile(path, "w") as z:
        for file in root.rglob("*"):
            if file.is_file():
                z.write(file, prefix + file.relative_to(root).as_posix())
        if bad:
            z.writestr(bad, "evil")
    return path, dict(version="0.3.0b5", sha256=u.digest_file(path))


@pytest.mark.parametrize("bad", [None, "RenderLock-0.3.0b5-Windows-x64/../escape",
    "RenderLock-0.3.0b5-Windows-x64/agr/GUI.py", "RenderLock-0.3.0b5-Windows-x64/agr/unlisted.py"])
def test_real_zip_verification(tmp_path, bad):
    path, metadata = archive(tmp_path, bad)
    if bad:
        with pytest.raises(ValueError):
            u.extract_verified(path, tmp_path / "extract", metadata)
    else:
        assert u.extract_verified(path, tmp_path / "extract", metadata)["version"] == "0.3.0b5"


def test_bad_zip_hash_never_extracts(tmp_path):
    path, metadata = archive(tmp_path)
    metadata["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="ZIP checksum"):
        u.extract_verified(path, tmp_path / "extract", metadata)
    assert not (tmp_path / "extract").exists()


def test_bounded_download_does_not_change_installed_files(tmp_path, monkeypatch):
    root = payload(tmp_path / "installed", "0.3.0b4")
    before = u.digest_file(root / "agr/gui.py")
    monkeypatch.setattr(u, "open_url", lambda url: io.BytesIO(b"too many bytes"))
    with pytest.raises(ValueError, match="advertised size"):
        u.download_update(dict(version="0.3.0b5", url="unused", size=1, sha256="0"*64), root)
    assert u.digest_file(root / "agr/gui.py") == before
    assert not (root / u.PENDING).exists()


def test_real_install_preserves_unmanaged_files_and_removes_retired_owned_files(tmp_path):
    root, work, new = transaction(tmp_path)
    data = json.loads((new / u.MANIFEST).read_text())
    data["files"] = [e for e in data["files"] if e["path"] != "agr/obsolete.py"]
    (new / "agr/obsolete.py").unlink()
    (new / u.MANIFEST).write_text(json.dumps(data))
    w.install(root)
    assert u.read_manifest(root)["version"] == "0.3.0b5"
    assert (root / "user-scene.blend").read_bytes() == b"user scene"
    assert not (root / "agr/obsolete.py").exists()
    assert not (root / u.PENDING).exists()
    assert (work / "backup/agr/gui.py").read_text() == "# 0.3.0b4"


def test_identical_runtime_is_not_replaced_or_backed_up(tmp_path):
    root, work, new = transaction(tmp_path)
    (new / "runtime/python.exe").write_bytes((root / "runtime/python.exe").read_bytes())
    data = json.loads((new / u.MANIFEST).read_text())
    entry = next(e for e in data["files"] if e["path"] == "runtime/python.exe")
    entry.update(bytes=(new / "runtime/python.exe").stat().st_size,
                 sha256=u.digest_file(new / "runtime/python.exe"))
    (new / u.MANIFEST).write_text(json.dumps(data))
    replaced = []
    def replace(source, target):
        replaced.append(target.relative_to(root).as_posix())
        w.replace_file(source, target)
    w.install(root, replace=replace)
    assert "runtime/python.exe" not in replaced
    assert not (work / "backup/runtime/python.exe").exists()


@pytest.mark.parametrize("failure", ["replace", "health"])
def test_failed_install_restores_exact_old_files(tmp_path, failure):
    root, work, new = transaction(tmp_path)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
              if p.is_file() and u.WORK not in p.parts}
    count = 0
    def replace(source, target):
        nonlocal count
        count += 1
        if count == 3 and failure == "replace":
            raise OSError("simulated locked file")
        w.replace_file(source, target)
    def health(root):
        if failure == "health":
            raise RuntimeError("simulated startup failure")
    with pytest.raises((OSError, RuntimeError), match="simulated"):
        w.install(root, health, replace)
    assert all((root / n).read_bytes() == data for n, data in before.items())
    assert not (root / u.PENDING).exists()
    assert json.loads((work / "journal.json").read_text())["state"] == "rolled_back"
    # Recovery is repeatable; backups are retained, never consumed.
    w.rollback(root)


def test_modified_staging_and_unknown_file_collisions_refuse_before_mutation(tmp_path):
    root, work, new = transaction(tmp_path)
    (new / "agr/gui.py").write_text("# damaged")
    with pytest.raises(ValueError, match="checksum"):
        w.install(root)
    assert not (root / u.PENDING).exists()
    assert (root / "agr/gui.py").read_text() == "# 0.3.0b4"


def test_interrupted_transaction_recovery(tmp_path):
    root, work, new = transaction(tmp_path)
    original = (root / "agr/gui.py").read_bytes()
    backup = work / "backup/agr/gui.py"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(original)
    w.atomic_json(work / "journal.json", dict(state="applying", target=str(root),
        before={"agr/gui.py": hashlib.sha256(original).hexdigest(), "agr/new.py": None}))
    w.atomic_json(root / u.PENDING, {})
    (root / "agr/gui.py").write_text("broken")
    (root / "agr/new.py").write_text("new")
    w.rollback(root)
    assert (root / "agr/gui.py").read_bytes() == original
    assert not (root / "agr/new.py").exists()
    assert not (root / u.PENDING).exists()


def test_interruption_before_pending_marker_leaves_only_a_prepared_journal(tmp_path, monkeypatch):
    root, work, new = transaction(tmp_path)
    original = w.atomic_json
    def interrupted(path, data):
        if Path(path).name == u.PENDING:
            raise OSError("simulated interruption before mutation")
        return original(path, data)
    monkeypatch.setattr(w, "atomic_json", interrupted)
    with pytest.raises(OSError, match="simulated interruption"):
        w.install(root)
    assert json.loads((work / "journal.json").read_text())["state"] == "prepared"
    assert (root / "agr/gui.py").read_text() == "# 0.3.0b4"
    assert not (root / u.PENDING).exists()


def test_application_lock_blocks_second_session(tmp_path):
    with w.application_lock(tmp_path):
        with pytest.raises((RuntimeError, OSError)):
            with w.application_lock(tmp_path):
                pytest.fail("second instance acquired the update lock")


class Widget:
    def __init__(self):
        self.options = {}
    def configure(self, **kw):
        self.options.update(kw)
    def set(self, value):
        self.value = value
    def delete(self, *args):
        pass
    def insert(self, *args):
        self.text = args[-1]


def ui(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "installation_enabled", lambda root: True)
    app = SimpleNamespace(root=SimpleNamespace(after=lambda *a: None, grab_current=lambda: None),
        values={"check_updates": SimpleNamespace(get=lambda: "0")}, busy=False,
        update_status=Widget(), update_check=Widget(), update_action=Widget(),
        update_notes=Widget(), update_notes_link=Widget(), nav_items={"Updates": {"text": Widget()}})
    controller = UpdateUI(app, tmp_path)
    return app, controller


def test_metadata_check_and_download_completion_never_install(tmp_path, monkeypatch):
    app, controller = ui(tmp_path, monkeypatch)
    metadata = u.select_release([release()], "0.3.0b4")
    controller.events.put((0, "checked", metadata))
    controller.poll()
    assert controller.available == metadata and not controller.restarting
    assert "Before:" in app.update_notes.text and "**" not in app.update_notes.text
    controller.running = True
    controller.events.put((0, "downloaded", tmp_path))
    controller.poll()
    assert controller.ready == tmp_path and not controller.restarting
    assert app.update_action.options["text"] == "Install and restart"


def test_job_blocks_download_and_install_and_close_revokes_ui_action(tmp_path, monkeypatch):
    app, controller = ui(tmp_path, monkeypatch)
    controller.available = u.select_release([release()], "0.3.0b4")
    controller.ready = tmp_path
    app.busy = True
    monkeypatch.setattr(w, "launch_worker", lambda *a: pytest.fail("updater launched during a job"))
    controller.action()
    controller.install()
    assert not controller.restarting
    controller.closed = True
    app.busy = False
    controller.action()
    assert not controller.restarting
