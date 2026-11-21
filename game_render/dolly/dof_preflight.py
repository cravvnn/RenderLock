"""Read-only Native DOF support preflight for the installed game.

The engine can render ``r_dof_override`` from the precompiled ``dof`` shader in
``game/citadel/shaders_pc_000.vpk`` (or the Vulkan pack). The reviewed install's
``vfx_dx11.dll`` statically imports an absent ``slang.dll``, so that compiler
cannot load. This check looks for the shipped shader and material entries;
index presence does not verify payload integrity, permutations or GPU output.

This module only reads directory indexes from the game install. No process
access, no writes, no game binaries and no absolute paths in the report, so a
result can be pasted into a support thread unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from . import panorama_assets

RENDERERS = ("dx11", "vulkan")
PACK_NAMES = {"dx11": "shaders_pc_dir.vpk", "vulkan": "shaders_vulkan_dir.vpk"}
SHADER_PREFIXES = {"dx11": "shaders/vfx/dof_pc_", "vulkan": "shaders/vfx/dof_vulkan_"}
MATERIALS = ("materials/dev/dof1_general.vmat_c", "materials/dev/dof1_blur.vmat_c")
COMPILER_FILES = ("vfx_dx11.dll", "slang.dll")
DOF_SHADER_SETTING = "mat_disable_dynamic_shader_compile"

READY = "ready"
VERIFY_FILES = "verify_files"
UNKNOWN_INSTALL = "unknown_install"

_VARIANT = re.compile(r"^shaders/vfx/dof_(?:pc|vulkan)_(\d+)_(vs|ps|features)\.vcs$")
_BUILD = re.compile(r"^\s*ClientVersion\s*=\s*(\d+)\s*$", re.M)
_MAX_TREE_BYTES = 64 * 1024 * 1024


def game_dirs(path):
    """Resolve one selected install path to ``(game, citadel, bin)`` folders.

    Accepts the installation root, ``game``, ``game/bin/win64``, the executable
    or ``game/citadel``. Never touches the filesystem beyond constructing paths.
    """
    base = Path(path).expanduser()
    if base.suffix.casefold() == ".exe":
        base = base.parent
    name = base.name.casefold()
    if name == "win64" and base.parent.name.casefold() == "bin":
        game = base.parents[1]
    elif name == "bin":
        game = base.parent
    elif name == "citadel":
        game = base.parent
    elif name == "game":
        game = base
    else:
        game = base / "game"
    return game, game / "citadel", game / "bin" / "win64"


def _relative(path, game):
    try:
        return Path(path).relative_to(game.parent).as_posix()
    except ValueError:
        return Path(path).name


def _build_number(citadel):
    try:
        text = (citadel / "steam.inf").read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return None
    match = _BUILD.search(text)
    return match.group(1) if match else None


def _pack_problem(pack, exc):
    if isinstance(exc, OSError):
        # OSError's text includes its absolute filename. Keep the report safe
        # to paste into a support thread, including when access is denied.
        detail = type(exc).__name__
        if isinstance(exc.errno, int):
            detail += f" (errno {exc.errno})"
        return f"Could not read {pack.name}: {detail}."
    return f"{pack.name}: {exc}"


def _shader_variants(citadel, renderer):
    pack = citadel / PACK_NAMES[renderer]
    try:
        if not pack.is_file():
            return pack, {}, [f"missing {PACK_NAMES[renderer]}"]
        found = panorama_assets.list_vpk_entries(
            pack, prefixes=(SHADER_PREFIXES[renderer],), suffixes=(".vcs",),
            max_tree_bytes=_MAX_TREE_BYTES)
    except (OSError, ValueError) as exc:
        return pack, {}, [_pack_problem(pack, exc)]
    models = {}
    for key in found:
        match = _VARIANT.match(key)
        if match is None:
            continue
        models.setdefault(match.group(1), set()).add(match.group(2))
    return pack, {model: sorted(stages) for model, stages in models.items()}, []


def _materials(citadel):
    pack = citadel / "pak01_dir.vpk"
    try:
        if not pack.is_file():
            return pack, {name: False for name in MATERIALS}, ["missing pak01_dir.vpk"]
        found = panorama_assets.list_vpk_entries(
            pack, prefixes=("materials/dev/dof1_",), suffixes=(".vmat_c",),
            max_tree_bytes=_MAX_TREE_BYTES)
    except (OSError, ValueError) as exc:
        return pack, {name: False for name in MATERIALS}, [_pack_problem(pack, exc)]
    return pack, {name: name in found for name in MATERIALS}, []


def check_installation(path, *, renderer="dx11", dynamic_compile=None):
    """Report whether the install can render Native DOF without a compiler.

    ``path`` may be ``None`` when no game has been selected. ``renderer`` is
    ``dx11`` (Dolly's launch path) or ``vulkan``. ``dynamic_compile`` is the
    live ``mat_disable_dynamic_shader_compile`` value when known. Never raises
    for an unreadable install: the report fails closed with ``verify_files``.
    """
    if renderer not in RENDERERS:
        raise ValueError("Unknown renderer")
    report = {
        "status": UNKNOWN_INSTALL,
        "renderer": renderer,
        "build": None,
        "shader_pack": None,
        "shader_variants": [],
        "shader_models": {},
        "missing_shader": [],
        "materials": {},
        "missing_materials": [],
        "dynamic_compile": {"available": False, "present": [], "missing": list(COMPILER_FILES)},
        "dynamic_compile_setting": None,
        "notes": [],
        "problems": [],
    }
    if not path:
        report["notes"].append(
            "Dolly has no Deadlock install path yet. Launch or select the game once, then check again.")
        return report
    game, citadel, bin_dir = game_dirs(path)
    try:
        has_citadel = citadel.is_dir()
    except OSError as exc:
        report["status"] = VERIFY_FILES
        report["problems"].append(_pack_problem(citadel, exc))
        report["notes"].append(
            "Dolly could not inspect the game's citadel folder. Check folder access and verify the game files in Steam.")
        return report
    if not has_citadel:
        report["status"] = UNKNOWN_INSTALL
        report["problems"].append("Deadlock's game/citadel folder was not found at the selected path.")
        report["notes"].append(
            "Select the Deadlock installation folder, or game/bin/win64/deadlock.exe, and check again.")
        return report

    report["build"] = _build_number(citadel)
    pack, models, shader_problems = _shader_variants(citadel, renderer)
    report["shader_pack"] = _relative(pack, game)
    report["shader_models"] = models
    report["shader_variants"] = [
        key for model in sorted(models) for key in
        (f"shaders/vfx/dof_{'pc' if renderer == 'dx11' else 'vulkan'}_{model}_{stage}.vcs"
         for stage in models[model])]
    complete = [model for model, stages in models.items() if {"vs", "ps"} <= set(stages)]
    if not complete:
        report["missing_shader"] = [f"{PACK_NAMES[renderer]} entry for the precompiled dof shader"]
    report["problems"].extend(shader_problems)

    _material_pack, materials, material_problems = _materials(citadel)
    report["materials"] = materials
    report["missing_materials"] = sorted(name for name, present in materials.items() if not present)
    report["problems"].extend(material_problems)

    present = []
    for name in COMPILER_FILES:
        try:
            if (bin_dir / name).is_file():
                present.append(name)
        except OSError as exc:
            # A compiler is optional for the shipped-bytecode path. Record an
            # unreadable DLL as unavailable, so it cannot justify a reload.
            report["notes"].append(_pack_problem(bin_dir / name, exc))
    missing = [name for name in COMPILER_FILES if name not in present]
    report["dynamic_compile"] = {"available": not missing and bool(present),
                                 "present": present, "missing": missing}
    if dynamic_compile is not None:
        report["dynamic_compile_setting"] = dynamic_compile

    ready = bool(complete) and not report["missing_materials"] and not report["problems"]
    report["status"] = READY if ready else VERIFY_FILES
    if ready:
        report["notes"].append(
            "The precompiled DOF shader and material entries are present; no vfx shader compiler is needed "
            "for the shipped-bytecode path. This index check does not verify payload integrity, "
            "shader permutations or rendered pixels.")
    else:
        report["notes"].append(
            "The precompiled Native DOF shader or material entries could not be found or read. "
            "Verify the game files in Steam "
            "(Deadlock > Properties > Installed Files > Verify integrity of game files), then check again.")
    if missing:
        report["notes"].append(
            "Dynamic shader compilation is unavailable (" + ", ".join(missing) + "). That is normal for "
            "retail Deadlock and only matters if something forces a recompile.")
    if dynamic_compile == 1:
        report["notes"].append(
            f"{DOF_SHADER_SETTING} was 1; Dolly clears it for the session before Native DOF is used.")
    return report


def summary(report):
    """One short sentence for a status bar or dialog title."""
    status = (report or {}).get("status")
    if status == READY:
        return "Native DOF is ready: the shipped precompiled shader and DOF material are present."
    if status == VERIFY_FILES:
        return ("Native DOF's shipped shader and material entries could not be verified. "
                "Verify the game files in Steam.")
    return "Deadlock's install path is not known yet, so Native DOF support could not be checked."


def format_report(report):
    """Plain multi-line text for a dialog or issue thread (no absolute paths)."""
    lines = ["Native DOF support: " + str((report or {}).get("status")),
             summary(report), ""]
    if report:
        if report.get("build"):
            lines.append("Build: " + str(report["build"]))
        if report.get("shader_pack"):
            lines.append("Shader pack: " + str(report["shader_pack"]))
        models = report.get("shader_models") or {}
        if models:
            lines.append("Precompiled dof shader: " + ", ".join(
                f"{model} ({'/'.join(stages)})" for model, stages in sorted(models.items())))
        if report.get("missing_shader"):
            lines.append("Missing shader: " + ", ".join(report["missing_shader"]))
        materials = report.get("materials") or {}
        if materials:
            lines.append("DOF materials: " + ", ".join(
                f"{name.rsplit('/', 1)[-1]}={'yes' if present else 'no'}"
                for name, present in sorted(materials.items())))
        compiler = report.get("dynamic_compile") or {}
        if compiler:
            lines.append("Dynamic compiler: " + ("available" if compiler.get("available")
                                                 else "unavailable (expected in retail)"))
        for note in report.get("notes") or ():
            lines.append("")
            lines.append(note)
        for problem in report.get("problems") or ():
            lines.append("")
            lines.append("Problem: " + str(problem))
    return "\n".join(lines)


def main(argv=None):
    """CLI: ``python -m dolly.dof_preflight <install or deadlock.exe>``."""
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [arg for arg in args if arg != "--json"]
    renderer = "dx11"
    if "--renderer" in args:
        index = args.index("--renderer")
        if index + 1 >= len(args):
            print("--renderer needs dx11 or vulkan", file=sys.stderr)
            return 2
        renderer = args[index + 1]
        del args[index:index + 2]
    if len(args) != 1 or renderer not in RENDERERS:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: python -m dolly.dof_preflight <install-or-exe> [--renderer dx11|vulkan] [--json]",
              file=sys.stderr)
        return 2
    report = check_installation(args[0], renderer=renderer)
    print(json.dumps(report, indent=2) if as_json else format_report(report))
    return 0 if report["status"] == READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
