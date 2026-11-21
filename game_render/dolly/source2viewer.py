"""One-time, verified Source2Viewer CLI setup for reconstructed clip audio.

Dolly uses ValveResourceFormat's Source2Viewer-CLI only to extract individual
``.vsnd`` assets from Deadlock's VPK. Asking every user to install it or set an
environment variable is unnecessary: the first reconstructed-audio export
downloads the pinned official Windows CLI, verifies its SHA-256, and caches it
under ``%LOCALAPPDATA%\\DeadlockDolly\\tools\\source2viewer``.

The download is optional in the sense that game-only audio never needs it. The
desktop asks once before a reconstructed take. ``DOLLY_SOURCE2VIEWER`` remains
an advanced override for an existing CLI.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile

from .runtime import resource_root

LOG = logging.getLogger("dolly")

SOURCE2VIEWER_VERSION = "20.1"
SOURCE2VIEWER_URL = (
    "https://github.com/ValveResourceFormat/ValveResourceFormat/releases/download/"
    f"{SOURCE2VIEWER_VERSION}/cli-windows-x64.zip")
SOURCE2VIEWER_SHA256 = "67aaf8a67a85ca60fd09ff343e62103a4cdf3a2d177fc656231d7d734c5b3aa3"
SOURCE2VIEWER_SIZE = 52_773_073
SOURCE2VIEWER_CLI_SHA256 = "ef9f6fb3943c30b73f0991bce6654128727b0807968f7997a6bb014efbec8830"
HELP_SIZE_MB = SOURCE2VIEWER_SIZE / (1024 * 1024)
_ALLOWED_FILES = ("Source2Viewer-CLI.exe", "libSkiaSharp.dll", "spirv-cross.dll")
_ALLOWED_HOSTS = ("github.com", "release-assets.githubusercontent.com",
                  "objects.githubusercontent.com")
_NOTICE = f"""Source 2 Viewer CLI (ValveResourceFormat) {SOURCE2VIEWER_VERSION}
https://github.com/ValveResourceFormat/ValveResourceFormat
Downloaded from: {SOURCE2VIEWER_URL}
Archive SHA-256: {SOURCE2VIEWER_SHA256}
Source2Viewer-CLI.exe SHA-256: {SOURCE2VIEWER_CLI_SHA256}

Licensed under the MIT License; see LICENSE.txt in this folder.
This tool is downloaded separately by Dolly and is not part of Dolly.
"""
_LICENSE = """The MIT License (MIT)

Copyright (c) 2015 ValveResourceFormat Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "DeadlockDolly" / "tools" / "source2viewer" / SOURCE2VIEWER_VERSION
    return Path.home() / ".cache" / "deadlock-dolly" / "source2viewer" / SOURCE2VIEWER_VERSION


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _candidate_paths() -> list[Path]:
    paths = []
    configured = os.environ.get("DOLLY_SOURCE2VIEWER")
    if configured:
        paths.append(Path(configured))
    paths.extend((
        resource_root() / "tools" / "source2viewer" / "Source2Viewer-CLI.exe",
        resource_root() / "native" / "bin" / "win64" / "Source2Viewer-CLI.exe",
    ))
    try:
        home = Path.home()
    except RuntimeError:
        home = None
    if home is not None:
        paths.extend((
            home / "Tools" / "ValveResourceFormat-20.1" / "Source2Viewer-CLI.exe",
            home / "Tools" / "ValveResourceFormat-20.0" / "Source2Viewer-CLI.exe",
        ))
    return paths


def find_viewer() -> Path | None:
    """Return an existing CLI quickly; the caller may still validate it."""
    for candidate in _candidate_paths():
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    cached = cache_root() / "Source2Viewer-CLI.exe"
    marker = cache_root() / ".complete"
    try:
        if cached.is_file() and marker.is_file():
            return cached
    except OSError:
        pass
    found = shutil.which("Source2Viewer-CLI.exe")
    return Path(found) if found else None


def _valid_cached_viewer(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0 and _hash(path) == SOURCE2VIEWER_CLI_SHA256
    except OSError:
        return False


def _download(archive: Path, progress) -> None:
    request = urllib.request.Request(
        SOURCE2VIEWER_URL,
        headers={"User-Agent": "Deadlock-Dolly/audio-helper",
                 "Accept": "application/octet-stream"})
    received = 0
    with urllib.request.urlopen(request, timeout=60) as response:
        final = urllib.parse.urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in _ALLOWED_HOSTS:
            raise RuntimeError("The reconstructed-audio helper download used an unexpected host.")
        with archive.open("wb") as target:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                received += len(block)
                if received > SOURCE2VIEWER_SIZE + 1024 * 1024:
                    raise RuntimeError("The reconstructed-audio helper download is larger than expected.")
                target.write(block)
                if progress is not None:
                    progress(received, SOURCE2VIEWER_SIZE)
    if received != SOURCE2VIEWER_SIZE or _hash(archive) != SOURCE2VIEWER_SHA256:
        raise RuntimeError("The reconstructed-audio helper download failed its checksum.")


def _safe_extract(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        total = 0
        for info in bundle.infolist():
            name = PurePosixPath(info.filename).name
            if info.is_dir() or name not in _ALLOWED_FILES:
                continue
            if info.file_size <= 0 or info.file_size > 200 * 1024 * 1024:
                raise RuntimeError("The reconstructed-audio helper archive contains an invalid file.")
            total += info.file_size
            if total > 200 * 1024 * 1024:
                raise RuntimeError("The reconstructed-audio helper archive is too large.")
            with bundle.open(info) as source, (destination / name).open("wb") as target:
                shutil.copyfileobj(source, target)
    cli = destination / "Source2Viewer-CLI.exe"
    if not cli.is_file() or _hash(cli) != SOURCE2VIEWER_CLI_SHA256:
        raise RuntimeError("The reconstructed-audio helper executable failed its checksum.")
    return cli


def ensure_viewer(progress=None) -> Path:
    """Return a working CLI, downloading the pinned official build if needed.

    ``progress(downloaded, total)`` is optional and runs on the calling worker.
    The download is verified before any file is executed. A valid cache is reused
    without network access.
    """
    existing = find_viewer()
    if existing is not None:
        if existing.resolve().is_relative_to(cache_root().resolve()):
            if _valid_cached_viewer(existing):
                return existing
        else:
            return existing

    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".dolly-helper-", dir=root,
                                         ignore_cleanup_errors=True) as directory:
            archive = Path(directory) / "cli-windows-x64.zip"
            staging = Path(directory) / "staging"
            if progress is not None:
                progress(0, SOURCE2VIEWER_SIZE)
            LOG.info("Downloading reconstructed-audio helper (%.0f MB, one time).", HELP_SIZE_MB)
            _download(archive, progress)
            cli = _safe_extract(archive, staging)
            for name in _ALLOWED_FILES:
                source = staging / name
                if source.is_file():
                    source.replace(root / name)
            if not _valid_cached_viewer(cli if cli.parent == root else root / cli.name):
                raise RuntimeError("The reconstructed-audio helper could not be installed.")
            (root / "NOTICE.txt").write_text(_NOTICE, encoding="utf-8", newline="\n")
            (root / "LICENSE.txt").write_text(_LICENSE, encoding="utf-8", newline="\n")
            (root / ".complete").write_text(
                json.dumps({"version": SOURCE2VIEWER_VERSION,
                            "sha256": SOURCE2VIEWER_CLI_SHA256}) + "\n",
                encoding="utf-8", newline="\n")
            LOG.info("Reconstructed-audio helper installed at %s", root)
            return root / "Source2Viewer-CLI.exe"
    except Exception as exc:
        LOG.info("Reconstructed-audio helper setup failed: %s", exc)
        raise RuntimeError(
            "Dolly could not download the reconstructed-audio helper. Check your internet "
            "connection and try again. Advanced users can set DOLLY_SOURCE2VIEWER to an "
            "existing Source2Viewer-CLI.exe.") from exc
