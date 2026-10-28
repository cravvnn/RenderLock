"""Verify a portable installation before opening the desktop workspace."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent


def python_syntax_record(entries):
    """Bind packaging-time syntax checks to exact source hashes and interpreter."""
    sources = sorted((entry['path'], entry['bytes'], entry['sha256'])
                     for entry in entries if PurePosixPath(entry['path']).suffix == '.py')
    payload = json.dumps(sources, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return dict(schema_version=1, implementation=sys.implementation.name,
                cache_tag=sys.implementation.cache_tag, interpreter=sys.version,
                sources_sha256=hashlib.sha256(payload).hexdigest())


def _verify_file(root, entry, compile_source):
    relative = PurePosixPath(entry['path'])
    path = root.joinpath(*relative.parts)
    if path.stat().st_size != entry['bytes']:
        raise ValueError(f'Changed or incomplete file: {relative}')
    source = compile_source and relative.suffix == '.py'
    with path.open('rb') as stream:
        # Small files and sources needing syntax validation are read once.
        # Large binaries are streamed, with at most four files in flight.
        if source or entry['bytes'] <= 256 * 1024:
            data = stream.read()
            checksum = hashlib.sha256(data).hexdigest()
            if len(data) != entry['bytes']:
                raise ValueError(f'Changed or incomplete file: {relative}')
        else:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    if checksum != entry['sha256']:
        raise ValueError(f'Changed file: {relative}')
    if source:
        compile(data, str(relative), 'exec', dont_inherit=True)


def verify(root=ROOT):
    manifest = json.loads((root / "release_manifest.json").read_text(encoding="utf-8"))
    seen = set()
    for entry in manifest["files"]:
        relative = PurePosixPath(entry["path"])
        if (relative.is_absolute() or ".." in relative.parts or "\\" in entry["path"]
                or ':' in entry['path'] or relative.as_posix() != entry['path']):
            raise ValueError("Invalid manifest path")
        if entry["path"] in seen:
            raise ValueError("Duplicate manifest path")
        seen.add(entry["path"])
    required = {"agr/__init__.py", "agr/gui.py", "agr/cli.py", "release_profile.json",
                "portable_launcher.py", "bin/agr-extract.exe", "runtime/python.exe"}
    if not required <= seen:
        raise ValueError("Portable installation manifest is incomplete")
    compile_source = manifest.get('python_syntax') != python_syntax_record(manifest['files'])
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='RenderLock verify') as workers:
        # Consume in manifest order so failures still identify a concrete file.
        for _ in workers.map(lambda entry: _verify_file(root, entry, compile_source), manifest['files']):
            pass
    return manifest


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass
    try:
        if (ROOT / ".renderlock-update-pending").exists():
            work = ROOT / ".renderlock-update"
            subprocess.Popen([str(work / "worker-runtime/python.exe"), "-I", "-X", "utf8", "-B",
                              str(work / "worker.py"), "--recover", "--parent", str(__import__('os').getpid())], cwd=work,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return 0
        started = time.perf_counter()
        print('RenderLock startup: checking portable files...', flush=True)
        manifest = verify()
        from agr.update_worker import application_lock
        with application_lock(ROOT):
            print(f"RenderLock {manifest['version']} - portable files verified in {time.perf_counter()-started:.3f}s", flush=True)
            from agr.cli import main as run
            return run(sys.argv[1:] if argv is None else argv)
    except Exception as error:
        print(f"RenderLock could not start: {error}\nExtract the complete ZIP into a new folder and try again.", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
