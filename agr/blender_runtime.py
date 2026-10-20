"""Bounded Blender capability checks before replay parsing or asset conversion."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


MARKER = "RENDERLOCK_BLENDER_CAPABILITIES="
CORE_CAPABILITIES = ("numpy", "mathutils", "gltf_import", "action_curves",
                     "material_nodes", "image_pixels", "cycles")
PROBE = r'''
import json
import bpy
result = {"version": list(bpy.app.version), "version_string": bpy.app.version_string,
          "capabilities": {}, "errors": {}}
def check(name, function):
    try:
        function()
        result["capabilities"][name] = True
    except Exception as error:
        result["capabilities"][name] = False
        result["errors"][name] = str(error)
def numpy_check():
    import numpy as np
    assert np.isfinite(np.eye(4)).all()
def mathutils_check():
    from mathutils import Matrix, Vector, Quaternion, Euler
    assert tuple(Matrix.Identity(3) @ Vector((1, 2, 3))) == (1, 2, 3)
def gltf_check():
    properties = bpy.ops.import_scene.gltf.get_rna_type().properties
    assert "filepath" in properties and "import_pack_images" in properties
def action_check():
    obj = bpy.data.objects.new("RenderLock capability check", None)
    bpy.context.scene.collection.objects.link(obj)
    action = bpy.data.actions.new("RenderLock capability check")
    obj.animation_data_create()
    if hasattr(action, "slots"):
        slot = action.slots.new(id_type="OBJECT", name=obj.name)
        channels = action.layers.new("Probe").strips.new(type="KEYFRAME").channelbags.new(slot).fcurves
        obj.animation_data.action = action
        obj.animation_data.action_slot = slot
    else:
        obj.animation_data.action = action
        channels = action.fcurves
    curve = channels.new("location", index=0)
    curve.keyframe_points.add(2)
    curve.keyframe_points.foreach_set("co", (1, 0, 2, 1))
    for key in curve.keyframe_points:
        key.interpolation = "LINEAR"
    curve.update_autoflags(obj)
    curve.update()
    assert abs(curve.evaluate(1.5) - .5) < 1e-6
def material_check():
    material = bpy.data.materials.new("RenderLock capability check")
    material.use_nodes = True
    node = material.node_tree.nodes.get("Principled BSDF")
    for name in ("Base Color", "Roughness", "Alpha", "Normal"):
        assert name in node.inputs
def image_check():
    image = bpy.data.images.new("RenderLock capability check", width=1, height=1, float_buffer=True)
    image.pixels.foreach_set((.25, .5, .75, 1))
    image.pack()
def engine_check(engine):
    bpy.context.scene.render.engine = engine
def npr_check():
    tree = bpy.data.node_groups.new("RenderLock capability check", "ShaderNodeTree")
    tree.interface.new_socket(name="Probe", in_out="INPUT", socket_type="NodeSocketColor")
    tree.nodes.new("ShaderNodeShaderToRGB")
for name, function in (("numpy", numpy_check), ("mathutils", mathutils_check),
                       ("gltf_import", gltf_check), ("action_curves", action_check),
                       ("material_nodes", material_check), ("image_pixels", image_check),
                       ("cycles", lambda: engine_check("CYCLES")),
                       ("eevee", lambda: engine_check("BLENDER_EEVEE")),
                       ("npr_nodes", npr_check)):
    check(name, function)
print("RENDERLOCK_BLENDER_CAPABILITIES=" + json.dumps(result), flush=True)
'''


class BlenderPreflightError(ValueError):
    """The selected program cannot provide the requested Blender APIs."""


def check_blender(executable, *, python_mode=False, character_shader=False, timeout=45):
    """Return verified API availability without reading a replay or saving a scene.

    This is an API startup probe, not a render/driver test or certification of
    untested Blender versions. Feature-specific exact solver qualification, such
    as the authored cloth clock, remains enforced by its existing implementation.
    """
    path = Path(executable).expanduser().absolute()
    if not path.is_file():
        raise BlenderPreflightError(f"Blender executable was not found: {path}. Choose Blender in Setup.")
    command = ([str(path), "-X", "utf8", "-c", PROBE] if python_mode else
               [str(path), "--background", "--factory-startup", "--disable-autoexec",
                "--python-exit-code", "1", "--python-expr", PROBE])
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    for key in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    try:
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        raise BlenderPreflightError(
            f"Blender did not finish its startup check within {timeout} seconds. "
            "Open Blender separately to resolve startup problems, then choose the working executable in Setup.") from exc
    except OSError as exc:
        raise BlenderPreflightError(f"Could not run Blender: {exc}. Choose a working Blender executable in Setup.") from exc
    if completed.returncode:
        detail = completed.stdout[-1500:].strip()
        raise BlenderPreflightError(
            f"Blender startup check failed (exit {completed.returncode}). "
            f"Choose a working Blender installation in Setup.\n{detail}")
    lines = [line[len(MARKER):] for line in completed.stdout.splitlines() if line.startswith(MARKER)]
    try:
        if len(lines) != 1:
            raise ValueError("missing or duplicate capability report")
        report = json.loads(lines[0])
        version = report["version"]
        capabilities = report["capabilities"]
        if (not isinstance(version, list) or len(version) != 3
                or any(type(part) is not int or part < 0 for part in version)
                or not isinstance(report["version_string"], str) or not isinstance(capabilities, dict)):
            raise ValueError("invalid capability report")
    except (ValueError, KeyError, TypeError) as exc:
        raise BlenderPreflightError(
            "The selected program did not return a Blender capability report. "
            "Choose blender.exe in Setup; Blender 5.2.1 LTS is the tested installation.") from exc
    required = CORE_CAPABILITIES + (("eevee", "npr_nodes") if character_shader else ())
    missing = [name for name in required if capabilities.get(name) is not True]
    if missing:
        raise BlenderPreflightError(
            f"Blender {report['version_string']} is missing required capabilities: {', '.join(missing)}. "
            "Repair Blender or choose another installation in Setup. Blender 5.2.1 LTS is the tested installation.")
    return report
