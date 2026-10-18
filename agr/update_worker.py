"""Standalone Windows update transaction, rollback and interrupted recovery."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from .updater import (MANIFEST, PENDING, WORK, checked_file, digest_file,
                      read_manifest, verify_payload, version_key)


def atomic_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            out.write((json.dumps(value, indent=2) + "\n").encode())
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def workspace_path(root):
    root = Path(root).absolute()
    work = root / WORK
    for item in (work, *work.parents):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise ValueError("Linked update workspaces are not supported")
    if work.resolve().parent != root.resolve():
        raise ValueError("Unexpected update workspace")
    return work


@contextmanager
def application_lock(root):
    """A portable session and its update transaction cannot overlap."""
    path = Path(root) / ".renderlock-running.lock"
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("Linked application lock")
    with path.open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("RenderLock is already running or updating in this folder.") from error
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def replace_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".renderlock-new-", delete=False) as out:
        temporary = Path(out.name)
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("ab") as durable:
            durable.flush()
            os.fsync(durable.fileno())
        for attempt in range(8):
            try:
                os.replace(temporary, target)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.25 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def rollback(root):
    root = Path(root).resolve()
    work = workspace_path(root)
    state = json.loads((work / "journal.json").read_text())
    if state["target"] != str(root):
        raise ValueError("Rollback target mismatch")
    for name, checksum in state["before"].items():
        target = checked_file(root, name)
        if checksum is None:
            target.unlink(missing_ok=True)
        elif not target.is_file() or digest_file(target) != checksum:
            backup = checked_file(work / "backup", name)
            if digest_file(backup) != checksum:
                raise ValueError("Update backup is damaged; retained for manual recovery")
            replace_file(backup, target)
    state["state"] = "rolled_back"
    atomic_json(work / "journal.json", state)
    (root / PENDING).unlink(missing_ok=True)


def install(root, health_check=lambda root: None, replace=replace_file):
    root = Path(root).resolve()
    work = workspace_path(root)
    payload = work / "payload"
    release = json.loads((work / "verified.json").read_text())
    old, new = read_manifest(root), verify_payload(payload, release["version"])
    if version_key(new["version"]) <= version_key(old["version"]):
        raise ValueError("Refusing a same-version update or downgrade")
    if (root / PENDING).exists() or (work / "journal.json").exists():
        raise ValueError("An unfinished or consumed update must be recovered or downloaded again")
    old_names = {e["path"] for e in old["files"]}
    names = sorted(old_names | {e["path"] for e in new["files"]} | {MANIFEST})
    new_names = {e["path"] for e in new["files"]}
    new_hashes = {e["path"]: e["sha256"] for e in new["files"]}
    before = {}
    for name in names:
        path = checked_file(root, name)
        if name != MANIFEST and name not in old_names and path.exists():
            raise ValueError("Update would overwrite an unowned file: " + name)
        checksum = digest_file(path) if path.is_file() else None
        if name != MANIFEST and name in new_hashes and checksum == new_hashes[name]:
            continue  # Unchanged runtimes/tools need neither replacement nor backup.
        before[name] = checksum
        if path.is_file():
            backup = checked_file(work / "backup", name)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, backup)
            with backup.open("ab") as durable:
                durable.flush()
                os.fsync(durable.fileno())
            if digest_file(backup) != before[name]:
                raise ValueError("Update backup verification failed")
    state = dict(state="prepared", target=str(root), before=before)
    atomic_json(work / "journal.json", state)
    # Marker is durable before the first mutation. The native launcher can use
    # the independent recovery runtime even if the installed Python is damaged.
    atomic_json(root / PENDING, {"schema": 1})
    try:
        state["state"] = "applying"
        atomic_json(work / "journal.json", state)
        for name in [n for n in before if n != MANIFEST] + [MANIFEST]:
            path = checked_file(root, name)
            if name == MANIFEST or name in new_names:
                replace(checked_file(payload, name), path)
            else:
                path.unlink(missing_ok=True)
        verify_payload(root, new["version"])
        health_check(root)
        state["state"] = "complete"
        atomic_json(work / "journal.json", state)
        (root / PENDING).unlink()
    except BaseException:
        rollback(root)
        raise


def clean_environment():
    env = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY"):
        env.pop(name, None)
    return env


def prepare_worker(root, work):
    root = Path(root).resolve()
    if Path(work).resolve() != workspace_path(root):
        raise ValueError("Unexpected update workspace")
    runtime = work / "worker-runtime"
    if not runtime.exists():
        shutil.copytree(root / "runtime", runtime,
                        ignore=shutil.ignore_patterns("site-packages", "__pycache__"))
    package = work / "agr"
    package.mkdir(exist_ok=True)
    for name in ("__init__.py", "updater.py", "update_worker.py", "update_window.py",
                 "gui_theme.py", "gui_assets.py", "gui_widgets.py"):
        shutil.copyfile(root / "agr" / name, package / name)
    script = work / "worker.py"
    script.write_text("from agr.update_worker import main\nraise SystemExit(main())\n", encoding="utf-8")
    atomic_json(work / "plan.json", {"target": str(root)})
    return runtime, script


def launch_worker(root, work):
    runtime, script = prepare_worker(root, work)
    return subprocess.Popen([str(runtime / "python.exe"), "-I", "-B", str(script),
                             "--parent", str(os.getpid())], cwd=work, env=clean_environment(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def wait_parent(pid):
    if not pid:
        return
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    api.OpenProcess.restype = ctypes.c_void_p
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    api.WaitForSingleObject.restype = ctypes.c_uint
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise OSError("Cannot confirm that RenderLock closed")
    try:
        if api.WaitForSingleObject(handle, 120000) != 0:
            raise RuntimeError("RenderLock did not close; update was not installed")
    finally:
        api.CloseHandle(handle)


def smoke(root):
    # Called while the updater holds the application lock, so verify directly,
    # without the session-locking portable launcher. No game or Blender launch.
    code = ("import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
            "import portable_launcher;portable_launcher.verify(Path(sys.argv[1]));"
            "from agr import gui,gui_theme;import tkinter as tk;"
            "gui_theme.enable_dpi_awareness();r=tk.Tk();r.withdraw();"
            "gui_theme.initialize(r);gui_theme.brand_window(r);r.update();r.destroy()")
    result = subprocess.run([str(root / "runtime/python.exe"), "-I", "-B", "-c", code, str(root)],
                            cwd=root, env=clean_environment(), timeout=120, capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("Updated RenderLock failed its startup check")


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=int, default=0)
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--no-relaunch", action="store_true")
    args = parser.parse_args(argv)
    work = Path(__file__).resolve().parents[1]
    plan = json.loads((work / "plan.json").read_text())
    root = Path(plan["target"]).resolve()
    if workspace_path(root) != work:
        raise ValueError("Recovery location disagrees with installation")
    from .update_window import UpdateWindow
    display = UpdateWindow(recover=args.recover)
    window = display.root
    import queue
    import threading
    events = queue.Queue()

    def run():
        can_relaunch = False
        try:
            wait_parent(args.parent)
            with application_lock(root):
                can_relaunch = True
                if args.recover:
                    if (root / PENDING).exists():
                        rollback(root)
                else:
                    install(root, smoke)
            events.put((None, can_relaunch))
        except Exception as error:
            events.put((str(error), can_relaunch))

    def poll():
        try:
            error, can_relaunch = events.get_nowait()
        except queue.Empty:
            window.after(100, poll)
            return
        def finish():
            if can_relaunch and not args.no_relaunch and not (root / PENDING).exists():
                subprocess.Popen([str(root / "RenderLock.exe")], cwd=root, env=clean_environment())
            display.progress.stop()
            window.destroy()
        if error:
            display.show_error(f"{error}\n\nRecovery folder:\n{work}\n\n"
                "Open RenderLock.exe again to recover an interrupted update.", finish)
        else:
            finish()
    threading.Thread(target=run, daemon=True).start()
    window.after(100, poll)
    window.mainloop()
    return 0
