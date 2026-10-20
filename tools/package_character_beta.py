"""Stage a Windows character beta from local source and installed dependencies.

No downloads, game files or developer notes enter either archive. Use a new
output directory for each candidate; existing candidates are never overwritten.
"""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from packaging.requirements import Requirement

from package_installed_release import digest, validate_archive


SOURCE_ROOT_FILES = {
    "README.md", "START_HERE.txt", "THIRD_PARTY.md", "requirements.txt", "pyproject.toml",
    "LICENSE", "CREDITS.md", "CONTRIBUTING.md", "CHANGELOG.md", "UPDATING.md", "CLOTH_WORKFLOW.md", "MAP_WORKFLOW.md",
    "bootstrap.py", "Launch_RenderLock.bat", "Launch_RenderLock_Windows.cmd",
    "launch_renderlock.sh", "portable_launcher.py", "Launch_RenderLock.cmd",
}
RUNTIME_REQUIREMENTS = ("numpy", "boon-deadlock", "polars", "python-snappy")
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".git", ".venv", "target", "bin", "obj"}
STALE_TOOLS = {"package_release.py", "package_startup_update.py"}
PARTICLE_COMMANDS = {
    "physics-json-v1", "material-bundle-v1", "decompile-cloth",
    "inventory-cloth-physics", "inventory-animation-clip-events", "inventory-model-hitboxes",
}


def verify_particle_helper(directory, dotnet_runtime):
    """Reject stale helper binaries before staging a candidate."""
    from agr.model_material_groups import PARSER_SHA256
    if dotnet_runtime is None:
        raise ValueError("The particle helper requires its bundled .NET runtime")
    probe = directory.resolve() / "ParticleProbe.dll"
    runtime = dotnet_runtime.resolve()
    dotnet = runtime / "dotnet.exe"
    if not probe.is_file() or not dotnet.is_file():
        raise ValueError("Particle helper DLL or bundled .NET executable missing")
    parser = probe.with_name("ValveResourceFormat.dll")
    native_image = probe.with_name("libSkiaSharp.dll")
    if not parser.is_file() or digest(parser) != PARSER_SHA256:
        raise ValueError("Particle helper requires the qualified material parser assembly")
    if not native_image.is_file():
        raise ValueError("Particle helper native image decoder missing")
    environment = dict(os.environ, DOTNET_ROOT=str(runtime))
    try:
        result = subprocess.run([str(dotnet), str(probe), "--capabilities-v1"],
                                env=environment, capture_output=True, text=True,
                                encoding="utf-8", timeout=30, check=True)
        capabilities = json.loads(result.stdout)
        commands = capabilities.get("commands")
        if (capabilities.get("schema_version") != 1 or not isinstance(commands, list)
                or not all(isinstance(command, str) for command in commands)
                or not PARTICLE_COMMANDS.issubset(commands)):
            raise ValueError("Required particle/typed-physics commands are absent")
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError) as exc:
        raise ValueError("Particle helper capability check failed; rebuild it before packaging") from exc
    return dict(sha256=digest(probe), parser_sha256=digest(parser),
                native_image_sha256=digest(native_image), capabilities=capabilities)


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def stage_source(root, output):
    """Keep executable source, tests and notices; omit diagnostic workspaces."""
    excluded_tests = []
    for folder in ("agr", "native", "tests", "tools", "artwork"):
        for path in sorted((root / folder).rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or SKIP_PARTS.intersection(relative.parts[1:]):
                continue
            if "licenses" not in relative.parts and path.suffix.lower() not in {".py", ".rs", ".toml", ".lock", ".md", ".txt", ".patch", ".png", ".ico"}:
                continue
            if folder == "tools" and relative.name in STALE_TOOLS:
                # Superseded 0.2.x packaging scripts; the current flow uses
                # package_character_beta.py and package_installed_release.py.
                continue
            if folder == "tests" and "research" in path.read_text(encoding="utf-8"):
                # These test developer oracle scripts, not the shipped runtime.
                excluded_tests.append(relative.as_posix())
                continue
            copy_file(path, output / relative)
    for name in sorted(SOURCE_ROOT_FILES):
        copy_file(root / name, output / name)
    (output / "release_profile.json").write_text('{"profile": "character-beta"}\n', encoding="utf-8")
    return excluded_tests


def distributions():
    pending = list(RUNTIME_REQUIREMENTS)
    result = {}
    while pending:
        distribution = metadata.distribution(pending.pop())
        name = distribution.metadata["Name"]
        if name in result:
            continue
        result[name] = distribution
        for item in distribution.requires or []:
            requirement = Requirement(item)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return result


def stage_runtime(output):
    base = Path(sys.base_prefix).resolve()
    if sys.platform != "win32" or not (base / "python.exe").is_file():
        raise ValueError("Build the Windows portable candidate with a Windows Python installation")
    output.mkdir(parents=True)
    for path in base.iterdir():
        if path.is_file() and (path.suffix.lower() in {".dll", ".exe"} or path.name == "LICENSE.txt"):
            copy_file(path, output / path.name)
    for folder in ("DLLs", "Lib", "tcl"):
        for path in (base / folder).rglob("*"):
            relative = path.relative_to(base)
            if not path.is_file() or any(part in {"__pycache__", "site-packages", "test", "tests", "idlelib", "turtledemo", "ensurepip"} for part in relative.parts):
                continue
            if path.suffix in {".pyc", ".pdb"}:
                continue
            copy_file(path, output / relative)
    resolved = distributions()
    for distribution in resolved.values():
        for relative in distribution.files or []:
            if ".." in relative.parts or "__pycache__" in relative.parts or relative.suffix == ".pyc":
                continue
            # Wheels can include developer workspaces and test data. Neither is
            # needed to run the converter; keep actual packages and licenses.
            if any(part.startswith(".") or part in {"tests", "test"} for part in relative.parts):
                continue
            if relative.name in {"AGENTS.md", "SKILL.md"}:
                continue
            if relative.name == "direct_url.json":
                continue
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                copy_file(source, output / "Lib/site-packages" / relative)
    (output / "python._pth").write_text("Lib\nDLLs\nLib/site-packages\n..\nimport site\n", encoding="utf-8")
    return {name: distribution.version for name, distribution in sorted(resolved.items())}


def write_manifest(root, version, kind, dependencies=None):
    items = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "release_manifest.json":
            items.append(dict(path=path.relative_to(root).as_posix(), bytes=path.stat().st_size, sha256=digest(path)))
    manifest = dict(version=version, kind=kind, profile="character-beta", game_assets_included=False,
                    files=items)
    if dependencies is not None:
        manifest["runtime_dependencies"] = dependencies
    (root / "release_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def archive(root, output):
    temporary = output.with_suffix(".zip.tmp")
    with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=6) as target:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                target.write(path, root.name + "/" + path.relative_to(root).as_posix())
    validate_archive(temporary, root.name)
    temporary.replace(output)
    return dict(path=str(output), bytes=output.stat().st_size, sha256=digest(output))


def stage_source2viewer(portable, directory):
    """Bundle the qualified Source 2 Viewer CLI so testers need no separate install."""
    directory = directory.resolve()
    target = portable / "tools/source2viewer"
    target.mkdir(parents=True)
    for name in ("Source2Viewer-CLI.exe", "libSkiaSharp.dll", "spirv-cross.dll"):
        path = directory / name
        if not path.is_file():
            raise ValueError(f"Source 2 Viewer file missing: {path}")
        copy_file(path, target / name)
    (target / "NOTICE.txt").write_text(
        "Source 2 Viewer CLI (ValveResourceFormat) 20.1\n"
        "https://github.com/ValveResourceFormat/ValveResourceFormat\n"
        "Licensed under the MIT License; see agr/vendor/licenses/ValveResourceFormat_MIT.txt\n"
        f"Source2Viewer-CLI.exe SHA-256: {digest(directory / 'Source2Viewer-CLI.exe')}\n",
        encoding="utf-8")


def stage_lighting_kit(portable, directory):
    """Bundle the qualified .NET lighting helpers used by the character look."""
    directory = directory.resolve()
    target = portable / "tools/lighting"
    for folder in ("world", "atlas", "cube"):
        source_dir = directory / folder
        if not source_dir.is_dir():
            raise ValueError(f"Lighting kit folder missing: {source_dir}")
        for path in sorted(source_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in (".dll", ".exe", ".json"):
                copy_file(path, target / folder / path.name)


def stage_particles(portable, directory):
    """Bundle the qualified particle simulation helper (DLL plus manifests)."""
    target = portable / "tools/particles"
    for path in sorted(directory.resolve().iterdir()):
        if path.is_file() and path.suffix.lower() in (".dll", ".json"):
            copy_file(path, target / path.name)


def stage_dotnet(portable, directory):
    """Bundle the shared .NET runtime used by the helper tools."""
    directory = directory.resolve()
    target = portable / "tools/dotnet"
    target.mkdir(parents=True)
    for name in ("dotnet.exe", "LICENSE.txt", "ThirdPartyNotices.txt"):
        copy_file(directory / name, target / name)
    shutil.copytree(directory / "host", target / "host")
    shared = directory / "shared" / "Microsoft.NETCore.App"
    runtime = sorted(path for path in shared.iterdir() if path.is_dir())[-1]
    shutil.copytree(runtime, target / "shared" / "Microsoft.NETCore.App" / runtime.name)


def package(root, output, native_helper, source2viewer_dir=None, lighting_kit=None,
            particles_dir=None, dotnet_runtime=None, launcher_exe=None):
    root = root.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError("Choose a new candidate directory; existing candidates are preserved")
    if not native_helper.is_file():
        raise ValueError("A verified Windows native replay helper is required")
    particle_helper = (verify_particle_helper(particles_dir, dotnet_runtime)
                       if particles_dir is not None else None)
    from agr import __version__
    output.mkdir(parents=True)
    source = output / f"RenderLock-{__version__}-source"
    excluded_tests = stage_source(root, source)
    write_manifest(source, __version__, "source")
    portable = output / f"RenderLock-{__version__}-Windows-x64"
    for folder in ("agr",):
        shutil.copytree(source / folder, portable / folder)
    for name in ("README.md", "START_HERE.txt", "THIRD_PARTY.md", "LICENSE", "CREDITS.md", "CHANGELOG.md", "UPDATING.md", "Launch_RenderLock.cmd", "portable_launcher.py", "release_profile.json"):
        copy_file(source / name, portable / name)
    copy_file(native_helper, portable / "bin/agr-extract.exe")
    if launcher_exe is not None:
        copy_file(launcher_exe, portable / "RenderLock.exe")
    if source2viewer_dir is not None:
        stage_source2viewer(portable, source2viewer_dir)
    if lighting_kit is not None:
        stage_lighting_kit(portable, lighting_kit)
    if particles_dir is not None:
        stage_particles(portable, particles_dir)
    if dotnet_runtime is not None:
        stage_dotnet(portable, dotnet_runtime)
    shutil.copytree(root / "native/licenses", portable / "notices/native")
    copy_file(root / "native/THIRD_PARTY.md", portable / "notices/native/THIRD_PARTY.md")
    dependencies = stage_runtime(portable / "runtime")
    write_manifest(portable, __version__, "Windows-x64-portable", dependencies)
    receipts = dict(version=__version__, source=archive(source, output / (source.name + ".zip")),
                    portable=archive(portable, output / (portable.name + ".zip")),
                    native_helper_sha256=digest(native_helper), runtime_dependencies=dependencies,
                    particle_helper=particle_helper,
                    excluded_developer_tests=excluded_tests)
    (output / "packaging-receipt.json").write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipts, indent=2))
    return receipts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-helper", type=Path, required=True)
    parser.add_argument("--source2viewer-dir", type=Path)
    parser.add_argument("--lighting-kit", type=Path)
    parser.add_argument("--particles-dir", type=Path)
    parser.add_argument("--dotnet-runtime", type=Path)
    parser.add_argument("--launcher-exe", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    package(args.source, args.output, args.native_helper,
            source2viewer_dir=args.source2viewer_dir, lighting_kit=args.lighting_kit,
            particles_dir=args.particles_dir, dotnet_runtime=args.dotnet_runtime,
            launcher_exe=args.launcher_exe)
