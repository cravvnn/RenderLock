"""Package AGR source and native replay helpers without any game assets."""
import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED


class ReleaseIntegrityError(ValueError):
    """A candidate release is incomplete, changed, or cannot be compiled."""


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_archive(path, prefix=""):
    """Validate the bytes users receive, including every Python source file.

    ZIP CRCs alone are insufficient: a correctly archived file can still contain
    invalid Python. Compare the manifest with the final archive and compile its
    source without importing modules or requiring third-party dependencies.
    """
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ReleaseIntegrityError("Duplicate ZIP entries")
        bad_entry = archive.testzip()
        if bad_entry is not None:
            raise ReleaseIntegrityError(f"ZIP integrity check failed: {bad_entry}")
        entry_prefix = prefix.rstrip("/") + "/" if prefix else ""
        manifest_name = entry_prefix + "release_manifest.json"
        if manifest_name not in names:
            raise ReleaseIntegrityError("Release manifest is missing")
        manifest = json.loads(archive.read(manifest_name))
        expected_names = {manifest_name}
        for item in manifest["files"]:
            relative = item["path"]
            resource_path = PurePosixPath(relative)
            if (resource_path.is_absolute() or ".." in resource_path.parts
                    or resource_path.as_posix() != relative or "\\" in relative):
                raise ReleaseIntegrityError(f"Invalid manifest path: {relative!r}")
            name = entry_prefix + relative
            if name in expected_names:
                raise ReleaseIntegrityError(f"Duplicate manifest entry: {relative}")
            expected_names.add(name)
            if name not in names:
                raise ReleaseIntegrityError(f"Manifest file is missing: {relative}")
            data = archive.read(name)
            if len(data) != item["bytes"]:
                raise ReleaseIntegrityError(f"Manifest size mismatch: {relative}")
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ReleaseIntegrityError(f"Manifest SHA256 mismatch: {relative}")
            if resource_path.suffix == ".py":
                try:
                    compile(data, name, "exec", dont_inherit=True)
                except (SyntaxError, ValueError, UnicodeError) as error:
                    raise ReleaseIntegrityError(
                        f"Invalid Python source {relative}: {error}"
                    ) from error
        if set(names) != expected_names:
            extra = sorted(set(names) - expected_names)
            raise ReleaseIntegrityError(f"ZIP entries missing from manifest: {extra}")
    return manifest


def package(root, output):
    root = root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    version_match = re.search(
        r'__version__ = "([^"\r\n]+)"',
        (root / "agr/__init__.py").read_text(encoding="utf-8"),
    )
    if version_match is None:
        raise ReleaseIntegrityError("AGR version is missing from agr/__init__.py")
    version = version_match.group(1)
    prefix = "Deadlock_AGR_" + version + "_Alpha"
    folders = {"agr", "bin", "native", "tests", "tools", "validation"}
    files = {
        "bootstrap.py", "requirements.txt", "pyproject.toml", "Launch_RenderLock.bat",
        "Launch_RenderLock_Windows.cmd", "launch_renderlock.sh", "README.md", "START_HERE.txt",
        "THIRD_PARTY.md", "VALIDATION.md", "CHANGELOG.md", "STARTUP_REPAIR_README.txt",
        "MAP_WORKFLOW.md", "CLOTH_WORKFLOW.md",
    }
    items = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] not in folders and relative.as_posix() not in files:
            continue
        if any(part in {"__pycache__", ".pytest_cache", ".git", ".venv", "target", "logs"}
               for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".blend", ".blend1", ".vpk", ".glb", ".gltf", ".npz"}:
            continue
        items.append((path, relative.as_posix(), digest(path), path.stat().st_size))
    if not any(name == "bin/agr-extract.exe" for _, name, _, _ in items):
        raise ReleaseIntegrityError("Native Windows replay helper is required")
    manifest = {
        "version": version,
        "game_assets_included": False,
        "files": [
            {"path": name, "sha256": checksum, "bytes": size}
            for _, name, checksum, size in items
        ],
    }
    # Keep any previous good release intact until all final-byte checks pass.
    # The temporary directory is on the output filesystem for atomic replace.
    with tempfile.TemporaryDirectory(prefix=output.name + ".", dir=output.parent) as temporary:
        candidate = Path(temporary) / "candidate.zip"
        with ZipFile(candidate, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            for path, name, _, _ in items:
                archive.write(path, prefix + "/" + name)
            archive.writestr(prefix + "/release_manifest.json", json.dumps(manifest, indent=2))
        validate_archive(candidate, prefix)
        candidate.replace(output)
    return {
        "status": "PASS", "path": str(output), "bytes": output.stat().st_size,
        "sha256": digest(output), "files": len(items) + 1, "game_assets_included": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.source, args.output), indent=2))
