"""Reopen a saved AGR scene and compare source poses and root translations.

Run with Blender Python: --blend shot.blend --output validation.json.
The matching shot.agrscene and shot.agr-report.json must be beside the scene.
Character material images must be packed. External ``RenderLockMap`` images are
reported as declared limits and return PARTIAL unless --external-map-images
explicitly accepts them. Cloth-modified bones are explicitly unverified:
exit 2/PARTIAL, never full PASS.
"""
import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector
from agr.scene_package import read_scene
from agr.vendor.pose_math import Pose, globals_for
from agr.model_constraints import ModelConstraints
from agr.pose_verification import classify_unpacked_images, cloth_modified_bones, verification_status


def _node_tree_images(tree, images, seen):
    if id(tree) in seen:
        return
    seen.add(id(tree))
    for node in tree.nodes:
        image = getattr(node, "image", None)
        if image is not None:
            images.add(image.name)
        group = getattr(node, "node_tree", None)
        if group is not None:
            _node_tree_images(group, images, seen)


def _material_images(objects):
    """Images reached through material/world node trees of the given objects."""
    images, seen = set(), set()
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            material = slot.material
            if material is not None and material.use_nodes and material.node_tree is not None:
                _node_tree_images(material.node_tree, images, seen)
    return images


def classify_scene_images(full_report, *, external_map_images=False):
    """Split the opened scene's unpacked images by ownership contract.

    Only an explicitly requested external map relaxes images that no material
    reaches (debris left after cutting). Character images stay hard failures.
    """
    from agr.external_map import CHAR_COLLECTION, MAP_COLLECTION
    scene = bpy.context.scene
    unpacked = [i.name for i in bpy.data.images if i.source == "FILE" and not i.packed_file]
    map_collection = bpy.data.collections.get(MAP_COLLECTION)
    char_collection = bpy.data.collections.get(CHAR_COLLECTION)
    map_objects = set(map_collection.all_objects) if map_collection is not None else set()
    char_objects = (set(char_collection.all_objects) if char_collection is not None
                    else set(scene.objects) - map_objects)
    character_images = _material_images(char_objects)
    for world in bpy.data.worlds:
        if world.use_nodes and world.node_tree is not None:
            _node_tree_images(world.node_tree, character_images, set())
    workflow = full_report.get("map_workflow")
    report = full_report.get("blender") or {}
    map_declared = bool(
        (isinstance(workflow, dict) and workflow.get("status") == "external_blend_appended")
        or workflow == "external_blend_appended"
        or full_report.get("external_map")
        or (report.get("map") or {}).get("collection") == MAP_COLLECTION)
    classified = classify_unpacked_images(unpacked, character_images,
        _material_images(map_objects), map_declared=map_declared)
    result = dict(classified, map_images_external_accepted=bool(external_map_images), limits=[])
    if result["map_unpacked_images"] and not external_map_images:
        result["limits"].append(
            f"{len(result['map_unpacked_images'])} map texture image(s) are external by design: "
            "RenderLockMap textures are relinked manually after cutting. Character textures must "
            "still be packed. Re-run with --external-map-images to accept external map textures "
            "explicitly.")
    return result


def verify(blend, *, all_frames=False, external_map_images=False):
    data = read_scene(blend.with_suffix(".agrscene"))
    full_report = json.loads(blend.with_suffix(".agr-report.json").read_text())
    report = full_report["blender"]
    bpy.ops.wm.open_mainfile(filepath=str(blend.resolve()), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    basis = report["coordinates"]
    scale = basis["unit_scale"]
    origin = np.asarray(basis["origin"])
    world = Matrix.Rotation(math.radians(basis["world_yaw_degrees"]), 4, "Z")
    result = {"blender": bpy.app.version_string, "entities": [], "max_root_error_metres": 0., "max_bone_matrix_error": 0., "bone_checks": 0, "root_checks": 0,
              "cloth_bone_checks_not_performed": 0, "limits": []}
    descriptions = {e["id"]: e for e in report["entities"]}
    for entity in data["entities"]:
        item = descriptions[entity["id"]]
        roots = bpy.data.objects[item["root"]]
        rig = bpy.data.objects[item["rig"]]
        excluded = cloth_modified_bones(item.get('secondary_motion'),
            {b.name: b.parent.name if b.parent else None for b in rig.data.bones})
        sk = json.loads(Path(entity["skeleton_file"]).read_text())
        definition = entity.get('model_constraints')
        solver = ModelConstraints(definition['names'], definition['parents'], definition['definitions'],
                                  attachments=definition.get('attachments'),
                                  binding_policy=definition.get('binding_policy','strict')) if definition else None
        with np.load(entity["pose_file"], allow_pickle=False) as src:
            indices = np.flatnonzero((src["ticks"] >= data["start_tick"]) & (src["ticks"] <= data["end_tick"]))
            if not len(indices):
                raise ValueError('No source pose samples in the requested range')
            chosen = (indices.tolist() if all_frames else
                      sorted(set(indices[np.linspace(0, len(indices)-1, min(16, len(indices))).astype(int)].tolist())))
            from agr.blender_scene import source_rig_mapping
            mapping,aliases=source_rig_mapping(sk['m_boneIDs'],sk['m_parentIndices'],rig.data.bones)
            if aliases!=item.get('pose',{}).get('source_bone_aliases',[]):
                raise ValueError('Saved source/model bone aliases differ')
            missing = sorted(set(sk['m_boneIDs']) - set(mapping))
            declared_missing = item.get('pose', {}).get('source_bones_absent_from_rig', [])
            if missing != sorted(declared_missing) or len(missing) == len(sk['m_boneIDs']):
                raise ValueError('Saved rig has unexpected missing source bones: ' + str(missing))
            samples = {s["tick"]: s for s in entity["samples"]}
            C = Matrix.Rotation(math.radians(item["pose_basis_yaw_degrees"]), 4, "Z")
            evaluation_indices = (np.flatnonzero(src['ticks'] <= data['end_tick']) if solver else chosen)
            for i in evaluation_indices:
                tick = int(src["ticks"][i])
                globals_ = globals_for(Pose(src["q"][i], src["t"][i], src["s"][i]), np.asarray(sk["m_parentIndices"]))
                expected_bones = {}
                for b, name in enumerate(sk['m_boneIDs']):
                    if name not in mapping:continue
                    name=mapping[name]
                    q = globals_.q[b]
                    expected_bones[name] = C @ Matrix.LocRotScale(Vector(globals_.t[b]*scale),
                        Quaternion((q[3],*q[:3])),Vector([globals_.s[b]]*3))
                if solver:
                    def expected_target(bone):
                        if bone.name not in expected_bones:
                            expected_bones[bone.name] = (expected_target(bone.parent) @ bone.parent.matrix_local.inverted() @ bone.matrix_local
                                if bone.parent else bone.matrix_local.copy())
                        return expected_bones[bone.name]
                    values = []
                    for name in solver.names:
                        if name not in solver.required_names:
                            values.append([0.,0.,0.,1.,0.,0.,0.,1.])
                            continue
                        p,q,s = expected_target(rig.data.bones[name]).decompose()
                        if min(s) <= 0 or max(s)-min(s) > 1e-5:
                            raise ValueError('Verification requires uniform positive model scale')
                        values.append([*(p/scale),sum(s)/3,q.x,q.y,q.z,q.w])
                    values = solver.evaluate(values,tick=tick,names=solver.names)
                    expected_bones.update({name:Matrix.LocRotScale(Vector(v[:3])*scale,
                        Quaternion((v[7],*v[4:7])),Vector((v[3],)*3)) for name,v in zip(solver.names,values)
                        if name in solver.affected_names})
                if i not in chosen:
                    continue
                frame = 1 + (tick-data["start_tick"]) * report["fps"] / data["tick_rate"]
                scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
                expected = world @ Vector((np.asarray(samples[tick]["position"])-origin)*scale)
                error = (roots.matrix_world.translation-expected).length
                result["max_root_error_metres"] = max(result["max_root_error_metres"], error)
                result["root_checks"] += 1
                checked_names = set(mapping.values()) | (solver.affected_names if solver else set())
                result['cloth_bone_checks_not_performed'] += len(checked_names & excluded)
                checked_names -= excluded
                for name in checked_names:
                    if name not in rig.pose.bones:
                        continue
                    expected_matrix = expected_bones[name]
                    error = float(np.max(np.abs(np.asarray(rig.pose.bones[name].matrix)-np.asarray(expected_matrix))))
                    result["max_bone_matrix_error"] = max(result["max_bone_matrix_error"], error)
                    result["bone_checks"] += 1
            result["entities"].append({"id": entity["id"], "checked_frames": len(chosen),
                                       "cloth_bones_not_verified": sorted(excluded)})
    result["external_libraries"] = len(bpy.data.libraries)
    # Split the packed character contract from the deliberately external map
    # textures. See classify_scene_images for the exact ownership rules.
    images = classify_scene_images(full_report, external_map_images=external_map_images)
    result["limits"].extend(images.pop("limits"))
    result.update(images)
    result["status"] = verification_status(result)
    if any(e['cloth_bones_not_verified'] for e in result['entities']):
        result['limits'].append('Recorded body roots and unmodified bones checked. Cloth-modified bones require a separate bake comparison; their motion is not verified here. Use a cloth-off export for full recorded-pose verification.')
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--blend", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--all-frames", action="store_true", help="Check every recorded source tick, including fractional output frames")
    ap.add_argument("--external-map-images", action="store_true",
        help="Accept external RenderLockMap textures as a declared limit instead of PARTIAL; character textures must still be packed")
    argv = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:]
    args = ap.parse_args(argv)
    result = verify(args.blend, all_frames=args.all_frames,
                    external_map_images=args.external_map_images)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result))
    if result["status"] != "PASS":
        raise SystemExit(2 if result['status'] == 'PARTIAL' else 1)
