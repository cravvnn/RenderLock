"""Explicit, verified updates from RenderLock's published GitHub releases.

Only packaged Windows installations can install updates. Checks fetch metadata;
downloads and installation require separate user actions in the desktop UI.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "cravvnn/RenderLock"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100"
MANIFEST = "release_manifest.json"
WORK = ".renderlock-update"
PENDING = ".renderlock-update-pending"
MAX_DOWNLOAD = 1024**3
MAX_UNPACKED = 4 * MAX_DOWNLOAD
ROOT_FILES = {"RenderLock.exe", "Launch_RenderLock.cmd", "portable_launcher.py",
              "release_profile.json", "README.md", "START_HERE.txt", "THIRD_PARTY.md",
              "LICENSE", "CREDITS.md", "CHANGELOG.md", "UPDATING.md"}
REQUIRED = {"RenderLock.exe", "portable_launcher.py", "Launch_RenderLock.cmd",
            "release_profile.json", "agr/__init__.py", "agr/gui.py", "agr/cli.py",
            "agr/updater.py", "agr/update_worker.py", "agr/update_window.py", "agr/gui_theme.py",
            "agr/gui_assets.py", "agr/gui_widgets.py", "agr/gui_chrome.py", "agr/gui_fonts.py",
            "runtime/python.exe", "bin/agr-extract.exe"}


def version_key(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?", value)
    if not match:
        raise ValueError("Unsupported RenderLock release version")
    major, minor, patch, stage, number = match.groups()
    return int(major), int(minor), int(patch), {"a": 0, "b": 1, "rc": 2, None: 3}[stage], int(number or 0)


def digest_file(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def safe_name(name):
    if not isinstance(name, str):
        raise ValueError("Invalid update path")
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or path.as_posix() != name or "\\" in name or ":" in name
            or any(p in (".", "..") or p.endswith((".", " ")) or any(ord(c) < 32 for c in p)
                   or re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", p) for p in path.parts)):
        raise ValueError("Unsafe update path")
    return name


def owned_name(name):
    safe_name(name)
    return name in ROOT_FILES or name.startswith(("agr/", "runtime/", "bin/", "tools/", "notices/"))


def checked_file(root, name):
    if name != MANIFEST and not owned_name(name):
        raise ValueError("Unmanaged update target")
    root = Path(root).absolute()
    path = root / name
    # Refuse junctions and symlinks, including dangling ones and the root itself.
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise ValueError("Linked application paths cannot be updated")
    if not path.resolve().is_relative_to(root.resolve()) or (path.exists() and not path.is_file()):
        raise ValueError("Invalid application file target")
    return path


def read_manifest(root):
    path = checked_file(root, MANIFEST)
    if path.stat().st_size > 8 * 1024**2:
        raise ValueError("Update manifest is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("kind") != "Windows-x64-portable" or data.get("profile") != "character-beta":
        raise ValueError("Not a RenderLock Windows portable package")
    version_key(data["version"])
    files = {}
    for entry in data["files"]:
        name = entry["path"]
        if (not owned_name(name) or name.casefold() in files or type(entry["bytes"]) is not int
                or entry["bytes"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError("Invalid or duplicate manifest file")
        files[name.casefold()] = entry
    if not REQUIRED <= {e["path"] for e in files.values()}:
        raise ValueError("Portable update is missing required application files")
    return data


def verify_payload(root, version):
    manifest = read_manifest(root)
    if manifest["version"] != version:
        raise ValueError("Release version disagrees with package")
    for entry in manifest["files"]:
        path = checked_file(root, entry["path"])
        if path.stat().st_size != entry["bytes"] or digest_file(path) != entry["sha256"]:
            raise ValueError("Update file checksum mismatch: " + entry["path"])
        if path.suffix == ".py":
            compile(path.read_bytes(), entry["path"], "exec", dont_inherit=True)
    return manifest


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if (url.scheme != "https" or url.username or url.password or url.port not in (None, 443)
                or url.hostname not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}):
            raise ValueError("Unexpected update download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": "RenderLock-Updater",
        "Accept": "application/vnd.github+json" if url == RELEASES_URL else "application/octet-stream"})
    return urllib.request.build_opener(ReleaseRedirect).open(request, timeout=20)


def select_release(releases, current):
    if not isinstance(releases, list):
        raise ValueError("Invalid release list")
    candidates = []
    current_key = version_key(current)
    for data in releases:
        if data.get("draft") is not False or type(data.get("prerelease")) is not bool:
            continue
        try:
            key = version_key(data.get("tag_name", ""))
        except (ValueError, TypeError):
            continue  # Unrelated legacy tags do not hide newer app releases.
        # Beta users receive published betas; stable users receive stable only.
        if key <= current_key or (current_key[3] == 3 and (data["prerelease"] or key[3] != 3)):
            continue
        candidates.append((key, data))
    if not candidates:
        return None
    data = max(candidates, key=lambda item: item[0])[1]
    tag = data["tag_name"]
    version = tag.removeprefix("v")
    name = f"RenderLock-{version}-Windows-x64.zip"
    assets = [a for a in data.get("assets", []) if a.get("name") == name and a.get("state") == "uploaded"]
    if len(assets) != 1:
        raise ValueError("Newest release has no unique Windows x64 ZIP yet")
    asset = assets[0]
    url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
    digest = asset.get("digest") or ""
    if (asset.get("browser_download_url") != url or type(asset.get("size")) is not int
            or not 0 < asset["size"] <= MAX_DOWNLOAD or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)):
        raise ValueError("Release ZIP has no valid GitHub size/SHA-256 metadata yet")
    return dict(version=version, url=url, sha256=digest[7:], size=asset["size"],
                notes_url=f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
                notes=str(data.get("body") or "No release notes were supplied.")[:60000])


def check_latest(current):
    with open_url(RELEASES_URL) as response:
        payload = response.read(2 * 1024**2 + 1)
    if len(payload) > 2 * 1024**2:
        raise ValueError("Release metadata is too large")
    return select_release(json.loads(payload), current)


def extract_verified(archive, destination, release):
    if digest_file(archive) != release["sha256"]:
        raise ValueError("Update ZIP checksum mismatch")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    prefix = f"RenderLock-{release['version']}-Windows-x64/"
    names, total = set(), 0
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            name = entry.filename.rstrip("/")
            safe_name(name)
            if not name.startswith(prefix):
                raise ValueError("Unexpected archive layout")
            relative = name[len(prefix):]
            safe_name(relative)
            if (relative.casefold() in names or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.flag_bits & 1 or (not entry.is_dir() and relative != MANIFEST and not owned_name(relative))):
                raise ValueError("Duplicate, linked, encrypted or unmanaged archive entry")
            names.add(relative.casefold())
            total += entry.file_size
            if total > MAX_UNPACKED or len(names) > 20000:
                raise ValueError("Update expands beyond allowed size")
            target = destination / relative
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as inp, target.open("xb") as out:
                    shutil.copyfileobj(inp, out, 1024**2)
    manifest = verify_payload(destination, release["version"])
    expected = {e["path"].casefold() for e in manifest["files"]} | {MANIFEST}
    actual = {p.relative_to(destination).as_posix().casefold() for p in destination.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("Archive contains files outside its manifest")
    return manifest


def installation_enabled(root):
    try:
        return os.name == "nt" and read_manifest(root)["version"] == __import__("agr").__version__
    except (OSError, ValueError, KeyError, TypeError):
        return False


def download_update(release, root, progress=lambda text: None):
    from .update_worker import atomic_json, workspace_path, prepare_worker
    root = Path(root).resolve()
    work = workspace_path(root)
    if (root / PENDING).exists():
        raise ValueError("An interrupted update must be recovered first")
    if work.exists():
        marker = work / "verified.json"
        if marker.is_file() and not (work / "journal.json").exists() and json.loads(marker.read_text()) == release:
            verify_payload(work / "payload", release["version"])
            return work
        # This exact application-owned workspace is the only cleanup target.
        # Keep an unfinished transaction for automatic recovery.
        journal = work / "journal.json"
        if journal.exists() and json.loads(journal.read_text()).get("state") == "applying":
            raise ValueError("An interrupted update must be recovered first")
        shutil.rmtree(work)
    work.mkdir()
    archive = work / "download.zip"
    downloaded = 0
    with open_url(release["url"]) as response, archive.open("xb") as output:
        while chunk := response.read(1024**2):
            downloaded += len(chunk)
            if downloaded > release["size"]:
                raise ValueError("Update download exceeds advertised size")
            output.write(chunk)
            progress(f"Downloading {release['version']}: {downloaded * 100 // release['size']}%")
    if downloaded != release["size"]:
        raise ValueError("Incomplete update download")
    progress("Verifying the downloaded update...")
    extract_verified(archive, work / "payload", release)
    progress("Preparing a safe restart...")
    prepare_worker(root, work)
    atomic_json(work / "verified.json", release)
    archive.unlink()
    return work
