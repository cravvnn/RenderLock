"""Verify a portable installation before opening the desktop workspace."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import subprocess

ROOT = Path(__file__).resolve().parent


def verify(root=ROOT):
    manifest = json.loads((root / "release_manifest.json").read_text(encoding="utf-8"))
    seen = set()
    for entry in manifest["files"]:
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in entry["path"]:
            raise ValueError("Invalid manifest path")
        if entry["path"] in seen:
            raise ValueError("Duplicate manifest path")
        seen.add(entry["path"])
        path = root.joinpath(*relative.parts)
        if path.stat().st_size != entry["bytes"]:
            raise ValueError(f"Changed or incomplete file: {relative}")
        with path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum != entry["sha256"]:
            raise ValueError(f"Changed file: {relative}")
        if relative.suffix == ".py":
            compile(path.read_bytes(), str(relative), "exec", dont_inherit=True)
    required = {"agr/__init__.py", "agr/gui.py", "agr/cli.py", "release_profile.json",
                "portable_launcher.py", "bin/agr-extract.exe", "runtime/python.exe"}
    if not required <= seen:
        raise ValueError("Portable installation manifest is incomplete")
    return manifest


def main(argv=None):
    try:
        if (ROOT / ".renderlock-update-pending").exists():
            work = ROOT / ".renderlock-update"
            subprocess.Popen([str(work / "worker-runtime/python.exe"), "-I", "-B",
                              str(work / "worker.py"), "--recover", "--parent", str(__import__('os').getpid())], cwd=work,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return 0
        manifest = verify()
        from agr.update_worker import application_lock
        with application_lock(ROOT):
            print(f"RenderLock {manifest['version']} - portable files verified", flush=True)
            from agr.cli import main as run
            return run(sys.argv[1:] if argv is None else argv)
    except Exception as error:
        print(f"RenderLock could not start: {error}\nExtract the complete ZIP into a new folder and try again.", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
