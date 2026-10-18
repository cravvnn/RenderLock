"""Restore pinned public dependencies, then compile all four C# helpers for CodeQL.

Prepare dependencies BEFORE CodeQL initialization, then run `build` while tracing.
This keeps the scan focused on RenderLock rather than rebuilding upstream code.
Only public source and NuGet dependencies are used; no game files are required.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/ValveResourceFormat/ValveResourceFormat.git"
LIGHTING_REVISION = "9e2e6c7f67e3c4e5a5f443a60c3a5d841ba8f304"  # Source 2 Viewer 20.1
PARTICLE_REVISION = "42743fb93030c552f834fabb30708dad84d0cbf5"
PROJECTS = ("ParticleProbe", "WorldRegionProbe", "ProbeAtlasDecode", "CubemapDecode")
PATCHES = ("vrf-ramp-scalar-spline.patch", "vrf-invalid-transform-identity.patch",
           "vrf-create-on-model-bones.patch", "vrf-kv3-v5-unsigned-constants.patch",
           "vrf-bounded-model-extraction.patch")


def run(*args, cwd=ROOT):
    print("+ " + " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def checkout(path, revision):
    # A fresh workspace avoids relying on a developer's modified upstream tree.
    if path.exists():
        raise ValueError("Choose a fresh dependency workspace: " + str(path))
    path.mkdir(parents=True)
    run("git", "init", path)
    run("git", "remote", "add", "origin", UPSTREAM, cwd=path)
    run("git", "sparse-checkout", "set", "ValveResourceFormat", "Misc", cwd=path)
    run("git", "fetch", "--depth=1", "--filter=blob:none", "origin", revision, cwd=path)
    run("git", "checkout", "--detach", "FETCH_HEAD", cwd=path)
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    if actual != revision:
        raise ValueError("Upstream revision did not match the pinned commit")
    return path / "ValveResourceFormat/ValveResourceFormat.csproj"


def properties(work):
    return ["-p:ParticleVrfProject=" + str(work / "particle/ValveResourceFormat/ValveResourceFormat.csproj"),
            "-p:ProbeReferences=" + str(work / "lighting/ValveResourceFormat/bin/Debug")]


def apply_patch(checkout_root, source, workspace):
    # Windows checkouts may convert .patch files to CRLF while upstream's C#
    # attributes require LF. Normalize the patch transport, never its context.
    destination = workspace / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
    run("git", "apply", "--check", destination, cwd=checkout_root)
    run("git", "apply", destination, cwd=checkout_root)


def prepare(work, dotnet):
    work.mkdir(parents=True, exist_ok=True)
    lighting = checkout(work / "lighting", LIGHTING_REVISION)
    particle = checkout(work / "particle", PARTICLE_REVISION)
    for name in PATCHES:
        apply_patch(work / "particle", ROOT / "tools/patches" / name, work / "patches")
    for project in (lighting, particle):
        run(dotnet, "build", project, "--configuration", "Debug",
            "-p:CopyLocalLockFileAssemblies=true", "-p:UseSharedCompilation=false")
    refs = work / "lighting/ValveResourceFormat/bin/Debug"
    if not (refs / "runtimes/win-x64/native/libSkiaSharp.dll").is_file():
        raise ValueError("Lighting dependency output is missing the Windows Skia native asset")
    for name in PROJECTS:
        run(dotnet, "restore", ROOT / "research" / name / (name + ".csproj"), *properties(work))
    (work / "prepared.json").write_text(json.dumps(dict(lighting=LIGHTING_REVISION,
        particle=PARTICLE_REVISION,projects=list(PROJECTS)), indent=2), encoding="utf-8")


def build(work, dotnet):
    state = json.loads((work / "prepared.json").read_text(encoding="utf-8"))
    if state != dict(lighting=LIGHTING_REVISION,particle=PARTICLE_REVISION,projects=list(PROJECTS)):
        raise ValueError("Dependency preparation does not match this build configuration")
    for name in PROJECTS:
        # A rebuild ensures every helper's compiler invocation is traced. The
        # prebuilt third-party project is a reference, not another traced build.
        run(dotnet, "build", ROOT / "research" / name / (name + ".csproj"),
            "--configuration", "Debug", "--no-restore", "--no-dependencies",
            "-t:Rebuild", "-p:UseSharedCompilation=false", *properties(work))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "build"))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--dotnet", default="dotnet")
    args = parser.parse_args()
    work = args.work.resolve()
    if work.is_relative_to(ROOT.resolve()):
        parser.error("Keep dependency/build work outside the source checkout")
    dotnet = shutil.which(args.dotnet) or str(Path(args.dotnet).resolve())
    (prepare if args.phase == "prepare" else build)(work, dotnet)


if __name__ == "__main__":
    main()
