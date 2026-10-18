"""Build an independent Blender scene from AGR's versioned segment manifest.

Run with Blender 4.4+ (or Python with bpy installed):
  blender -b --factory-startup --python blender_scene.py -- \
    --segment segment.json --assets assets --output shot.blend --fps 64

The module can be imported without bpy, to inspect the manifest/asset contracts.
All source positions and pose translations use the manifest's unit_scale.
Bone quaternions in the NPZ are XYZW, in parent space. Tick endpoints are inclusive.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys


def tick_frame(tick, start_tick, tick_rate, fps):
    return 1.0 + (float(tick) - float(start_tick)) * float(fps) / float(tick_rate)


def unwrap_angle(angle, previous):
    return angle if previous is None else previous + (angle - previous + math.pi) % (2 * math.pi) - math.pi


def asset_spec(entity, root, manifest_dir):
    """Explicit entity paths override assets.json and conventional GLB discovery.

    assets.json: {"heroes": {"13": {"model_file": "Haze.glb",
      "skeleton_file": "Haze_skeleton.json", "model_yaw_degrees": 180,
      "pose_basis_yaw_degrees": 0, "model_scale": 1}}}
    A .blend entry additionally requires asset_objects:["haze_ARM"] or
    asset_collection:"Haze assets"; no scene or arbitrary root is appended.
    """
    root = Path(root).resolve() if root else None
    spec = {}
    if root and (root / "assets.json").is_file():
        catalog = json.loads((root / "assets.json").read_text(encoding="utf-8"))
        entries = catalog.get("heroes", catalog.get("assets", {}))
        for key in (str(entity.get("hero_id", "")), entity.get("hero_name", ""), str(entity.get("hero_name", "")).lower()):
            if key in entries:
                entry = entries[key]
                spec.update({"model_file": entry} if isinstance(entry, str) else entry)
                break
    for key in ("model_file", "skeleton_file", "pose_file", "asset_objects", "asset_collection", "model_yaw_degrees", "pose_basis_yaw_degrees", "model_scale", "rig_object", "model_local_transform", "require_weighted_bone_mapping", "static_generated_cloth"):
        if entity.get(key) is not None:
            spec[key] = entity[key]
    if entity.get('model_constraints') is not None:
        spec['model_constraints'] = entity['model_constraints']
    if not spec.get("model_file") and root:
        name = str(entity.get("hero_name") or entity.get("hero_id"))
        for candidate in (root / (name + ".glb"), root / (name.lower() + ".glb"), root / name / (name + ".glb"), root / "models" / (name + ".glb")):
            if candidate.is_file():
                spec["model_file"] = str(candidate)
                break
    for key in ("model_file", "skeleton_file", "pose_file"):
        if spec.get(key):
            path = Path(spec[key]).expanduser()
            if not path.is_absolute():
                base = Path(manifest_dir) if entity.get(key) else root
                path = (base or Path(manifest_dir)) / path
            spec[key] = str(path.resolve())
    return spec


def _init_blender():
    global bpy, np, Matrix, Quaternion, Vector, Euler
    import bpy
    import numpy as np
    from mathutils import Matrix, Quaternion, Vector, Euler
    # Blender executes this file directly; make sibling AGR modules importable.
    program_root = str(Path(__file__).resolve().parents[1])
    if program_root not in sys.path:
        sys.path.insert(0, program_root)


def _action_channels(obj, name):
    action = bpy.data.actions.new(name)
    obj.animation_data_create()
    if hasattr(action, "slots"):
        slot = action.slots.new(id_type="OBJECT", name=obj.name)
        bag = action.layers.new("Replay samples").strips.new(type="KEYFRAME").channelbags.new(slot)
        obj.animation_data.action = action
        obj.animation_data.action_slot = slot
        return action, bag.fcurves
    obj.animation_data.action = action
    return action, action.fcurves


def _curve(channels, obj, path, component, frames, values, constant=False, steps=None):
    curve = channels.new(path, index=component)
    curve.keyframe_points.add(len(frames))
    curve.keyframe_points.foreach_set("co", np.column_stack((frames, values)).astype(np.float32).ravel())
    for index, key in enumerate(curve.keyframe_points):
        key.interpolation = "CONSTANT" if constant or (steps and index in steps) else "LINEAR"
    curve.update_autoflags(obj)
    curve.update()
    return len(frames)


def _empty(name, collection):
    obj = bpy.data.objects.new(name, None)
    collection.objects.link(obj)
    obj.empty_display_type = "PLAIN_AXES"
    obj.empty_display_size = 0.5
    return obj


def _material(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = 0.55
    return mat


def _clear_animation(objects):
    for obj in objects:
        obj["agr_asset_hidden"] = bool(obj.hide_viewport and obj.hide_render)
        obj.animation_data_clear()
        obj.hide_viewport = obj["agr_asset_hidden"]
        obj.hide_render = obj["agr_asset_hidden"]
        obj.hide_set(False)
        if obj.type == "ARMATURE":
            for pb in obj.pose.bones:
                if pb.constraints:
                    raise ValueError("Asset rig has constraints that could alter baked poses: " + obj.name + "/" + pb.name)
                pb.matrix_basis = Matrix.Identity(4)
                pb.rotation_mode = "QUATERNION"
        if obj.data and hasattr(obj.data, "animation_data_clear"):
            obj.data.animation_data_clear()
        if obj.type == "MESH" and obj.data.shape_keys:
            obj.data.shape_keys.animation_data_clear()


def _append_blend(spec, collection):
    path = spec["model_file"]
    roots = spec.get("asset_objects", [])
    if isinstance(roots, str):
        roots = [roots]
    collection_name = spec.get("asset_collection")
    if not roots and not collection_name:
        raise ValueError("Prepared .blend assets require asset_objects or asset_collection in assets.json")
    before = set(bpy.data.objects)
    old_actions = set(bpy.data.actions)
    with bpy.data.libraries.load(path, link=False) as (available, selected):
        if collection_name:
            if collection_name not in available.collections:
                raise ValueError("Asset collection not found: " + collection_name)
            selected.collections = [collection_name]
        else:
            missing = set(roots) - set(available.objects)
            if missing:
                raise ValueError("Asset objects not found: " + ", ".join(sorted(missing)))
            # Blender appends dependencies but not children. Load objects, retain only
            # the explicit asset hierarchies, and immediately remove all other objects.
            selected.objects = list(available.objects)
    imported = set(bpy.data.objects) - before
    if collection_name:
        wanted = set(selected.collections[0].all_objects)
    else:
        chosen = {obj for obj in imported if obj.name.split(".00")[0] in roots or obj.name in roots}
        # Names can acquire numeric suffixes on repeated imports. The returned list
        # preserves source order and is the authoritative source-name mapping.
        chosen = {obj for name, obj in zip(available.objects, selected.objects) if name in roots and obj is not None}
        wanted = set(chosen)
        for obj in chosen:
            wanted.update(obj.children_recursive)
    # Armature modifiers and bone-parented weapons are legitimate dependencies.
    for obj in list(wanted):
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object:
                wanted.add(modifier.object)
    for obj in wanted:
        if obj.parent not in wanted:
            # Asset local transform is intentional; world transform would retain
            # the prepared shot's replay root and corrupt the new replay track.
            local = obj.matrix_basis.copy()
            obj.parent = None
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = local
        if obj.name not in collection.objects:
            collection.objects.link(obj)
        for old in list(obj.users_collection):
            if old != collection:
                old.objects.unlink(obj)
    for obj in imported - wanted:
        bpy.data.objects.remove(obj, do_unlink=True)
    for action in set(bpy.data.actions) - old_actions:
        action.use_fake_user = False
    return list(wanted)


def _import_asset(spec, collection, root, name):
    model = Path(spec["model_file"])
    if not model.is_file():
        raise FileNotFoundError("Model is missing: " + str(model))
    if model.suffix.lower() in (".glb", ".gltf"):
        from agr.blender_helpers import gltf_import_options, exclude_gltf_bone_helpers
        before = set(bpy.data.objects)
        materials_before = set(bpy.data.materials)
        bpy.ops.import_scene.gltf(filepath=str(model), import_pack_images=True, **gltf_import_options(bpy))
        from agr.blender_materials import repair_imported_unlit
        repair_imported_unlit(set(bpy.data.materials) - materials_before)
        objects = list(set(bpy.data.objects) - before)
        objects, helpers = exclude_gltf_bone_helpers(objects)
        for obj in objects:
            for old in list(obj.users_collection):
                old.objects.unlink(obj)
            collection.objects.link(obj)
    elif model.suffix.lower() == ".blend":
        objects = _append_blend(spec, collection)
    else:
        raise ValueError("Supported model formats are GLB, glTF and explicit .blend asset hierarchies: " + str(model))
    _clear_animation(objects)
    model_root = _empty(name + " | model basis", collection)
    model_root.parent = root
    model_root.rotation_euler.z = math.radians(float(spec.get("model_yaw_degrees", 0)))
    model_root.scale = (float(spec.get("model_scale", 1)),) * 3
    for obj in objects:
        if obj.parent not in objects:
            obj.parent = model_root
            obj.matrix_parent_inverse = Matrix.Identity(4)
            if spec.get("model_local_transform") == "identity":
                obj.matrix_basis = Matrix.Identity(4)
        obj["agr_entity_id"] = root["agr_entity_id"]
    rigs = [obj for obj in objects if obj.type == "ARMATURE"]
    selected_rig = spec.get("rig_object")
    if selected_rig:
        rigs = [obj for obj in rigs if obj.name == selected_rig or obj.name.startswith(selected_rig + ".")]
    return objects, rigs, model_root


def _placeholder(root, collection, name):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=1)
    obj = bpy.context.object
    obj.name = name + " | missing asset marker"
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    collection.objects.link(obj)
    obj.parent = root
    obj.location = (0, 0, 1)
    obj.scale = (0.4, 0.4, 1)
    obj.data.materials.append(_material(name + " missing model", (0.8, 0.22, 0.08)))
    return [obj]


def _animate_root(root, entity, data, coordinates, fps):
    samples = sorted(entity["samples"], key=lambda row: row["tick"])
    frames = [tick_frame(row["tick"], data["start_tick"], data["tick_rate"], fps) for row in samples]
    basis = Matrix.Rotation(math.radians(coordinates["world_yaw_degrees"]), 3, "Z")
    origin = Vector(coordinates["origin"])
    positions = [basis @ ((Vector(row["position"]) - origin) * coordinates["unit_scale"]) for row in samples]
    rotations, last = [], None
    for row in samples:
        angles = row.get("body_angles") or [0, 0, 0]
        pitch, yaw, roll = map(math.radians, angles)
        yaw = unwrap_angle(yaw, last)
        last = yaw
        # Source pitch/yaw/roll correspond to local Y/Z/X; world basis is a Z rotation.
        rotations.append((roll, pitch, yaw + math.radians(coordinates["world_yaw_degrees"])))
    # Source AngleMatrix = Rz(yaw) @ Ry(pitch) @ Rx(roll), Blender XYZ order.
    root.rotation_mode = "XYZ"
    action, channels = _action_channels(root, root.name + " | recorded transforms")
    action["agr_source"] = "Recorded entity position and body angles"
    action["start_tick"] = data["start_tick"]
    action["tick_rate"] = float(data["tick_rate"])
    gap_limit = float(data.get("metadata", {}).get("max_interpolation_gap_ticks", 2))
    teleport_distance = float(data.get("metadata", {}).get("teleport_distance_meters", 10))
    steps = {i for i in range(len(samples) - 1) if (positions[i + 1] - positions[i]).length > teleport_distance or samples[i + 1]["tick"] - samples[i]["tick"] > gap_limit or samples[i + 1].get("teleport", False)}
    for component in range(3):
        _curve(channels, root, "location", component, frames, [p[component] for p in positions], steps=steps)
        _curve(channels, root, "rotation_euler", component, frames, [r[component] for r in rotations], steps=steps)
    for prop, default in (("health", 0), ("alive", True), ("demo_tick", 0)):
        root[prop] = default
        values = [float(row["tick"] if prop == "demo_tick" else row.get(prop, default)) for row in samples]
        _curve(channels, root, '["' + prop + '"]', 0, frames, values, constant=True)
    return samples, positions, len(steps)


def visibility_changes(entity, samples, data, fps):
    """Return constant (frame, hidden) keys for observed availability.

    Observation intervals do not assert a spawn, death, or despawn. Missing/PVS
    inactive entities are hidden; an observed alive=false body remains visible.
    The optional explicit spawn/despawn bounds further restrict availability.
    """
    samples = sorted(samples, key=lambda sample: sample["tick"])
    intervals = entity.get("observed_intervals")
    if intervals is None:
        intervals = []
        gap_limit = float(data.get("metadata", {}).get("max_interpolation_gap_ticks", 2))
        for sample in samples:
            tick = sample["tick"]
            if not intervals or tick - (intervals[-1]["end_tick_exclusive"] - 1) > gap_limit:
                intervals.append({"start_tick": tick, "end_tick_exclusive": tick + 1})
            else:
                intervals[-1]["end_tick_exclusive"] = tick + 1
    events = {}
    def event(tick):
        return events.setdefault(tick, {"delta": 0})
    for interval in intervals:
        first, last = interval["start_tick"], interval["end_tick_exclusive"]
        if not math.isfinite(float(first)) or not math.isfinite(float(last)) or last <= first:
            raise ValueError("Observed intervals require finite start_tick < end_tick_exclusive")
        event(first)["delta"] += 1
        event(last)["delta"] -= 1
    for sample in samples:
        if "present" in sample:
            event(sample["tick"])["present"] = bool(sample["present"])
    spawn, despawn = entity.get("spawn_tick"), entity.get("despawn_tick")
    for boundary in (spawn, despawn, data["start_tick"]):
        if boundary is not None:
            event(boundary)
    observed, present = 0, True
    changes = []
    for tick, change in sorted(events.items()):
        # The output's final integer frame can extend beyond the exact last tick.
        # Hold that requested endpoint instead of applying later absence events.
        if tick > data["end_tick"]:
            break
        observed += change["delta"]
        present = change.get("present", present)
        if tick < data["start_tick"]:
            continue
        visible = observed > 0 and present and (spawn is None or tick >= spawn) and (despawn is None or tick < despawn)
        hidden = not visible
        if not changes or changes[-1][1] != hidden:
            changes.append((tick_frame(tick, data["start_tick"], data["tick_rate"], fps), hidden))
    return changes


def _visibility(objects, entity, samples, data, fps):
    changes = visibility_changes(entity, samples, data, fps)
    for obj in objects:
        if obj.type != "MESH":
            continue
        if obj.get("agr_asset_hidden", False):
            # Preserve the asset author's disabled alternate LOD meshes.
            obj.hide_render = obj.hide_viewport = True
            continue
        if obj.animation_data and obj.animation_data.action:
            raise ValueError("Imported mesh unexpectedly retains animation: " + obj.name)
        _, channels = _action_channels(obj, obj.name + " | lifetime")
        for prop in ("hide_render", "hide_viewport"):
            _curve(channels, obj, prop, 0, [x[0] for x in changes], [float(x[1]) for x in changes], constant=True)
    return changes


def _skeleton_fields(skeleton):
    names = skeleton.get("m_boneIDs", skeleton.get("bone_names", skeleton.get("names")))
    parents = skeleton.get("m_parentIndices", skeleton.get("parents"))
    if names is None or parents is None or len(names) != len(parents):
        raise ValueError("Skeleton must contain m_boneIDs/m_parentIndices or names/parents")
    if len(set(names)) != len(names):
        raise ValueError("Skeleton contains duplicate bone names")
    for index, parent in enumerate(parents):
        if parent < -1 or parent >= index:
            raise ValueError("Skeleton parent indices must precede their children")
    return names, parents


def weighted_bone_coverage(bone_parents, weighted_names, source_names, *, static_generated_cloth=False):
    """Distinguish pose-driven weights, inherited helpers and disconnected weights.

    Blender's pose transfer lets an unmapped helper retain its parent-relative
    rest transform. That is explicit inheritance, not recovered helper animation.
    This pure helper is also used to verify the mapping before baking a rig.
    """
    source = set(source_names)
    weighted_names = set(weighted_names)
    mapped, inherited, missing, static_cloth = [], {}, [], []
    for name in sorted(weighted_names):
        if name in source:
            mapped.append(name)
            continue
        parent = bone_parents.get(name)
        visited = {name}
        while parent is not None and parent not in source and parent not in visited:
            visited.add(parent)
            parent = bone_parents.get(parent)
        if parent in source:
            inherited[name] = parent
        elif static_generated_cloth is True and re.fullmatch(r"\$cloth_m\d+p\d+", name):
            # Source-generated cloth proxies have no replay pose or mapped root.
            # The current pose transfer holds their original rig rest transform.
            static_cloth.append(name)
        else:
            missing.append(name)
    return {"weighted_bones": len(weighted_names), "mapped_weighted_bones": mapped,
            "inherited_weighted_bones": inherited, "unmapped_weighted_bones": missing,
            "inherited_behavior": "Parent-relative rest transform; helper animation is not recovered",
            "static_generated_cloth_bones": static_cloth,
            "static_generated_cloth_bone_count": len(static_cloth),
            "cloth": "rest_pose_no_simulation" if static_cloth else "not_evaluated"}


def _check_weighted_mapping(rig, source_names, *, static_generated_cloth=False):
    bones = {bone.name: bone.parent.name if bone.parent else None for bone in rig.data.bones}
    weighted = set()
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if obj.parent == rig and obj.parent_type == "BONE" and obj.parent_bone:
            weighted.add(obj.parent_bone)
        if not any(mod.type == "ARMATURE" and mod.object == rig for mod in obj.modifiers):
            continue
        group_bones = {group.index: group.name for group in obj.vertex_groups if group.name in bones}
        for vertex in obj.data.vertices:
            weighted.update(group_bones[group.group] for group in vertex.groups
                            if group.weight > 1e-6 and group.group in group_bones)
    report = weighted_bone_coverage(bones, weighted, source_names,
                                    static_generated_cloth=static_generated_cloth)
    if report["unmapped_weighted_bones"]:
        raise ValueError("Installed model has weighted bones with no source-pose mapping or mapped ancestor: "
                         + ", ".join(report["unmapped_weighted_bones"]))
    if not report["weighted_bones"]:
        raise ValueError("Installed model has no positive skin weights bound to its selected rig")
    return report


def _bake_pose(rig, spec, data, coordinates, fps):
    skeleton = json.loads(Path(spec["skeleton_file"]).read_text(encoding="utf-8"))
    names, parents = _skeleton_fields(skeleton)
    with np.load(spec["pose_file"], allow_pickle=False) as arrays:
        q, t, s, ticks = [np.array(arrays[key]) for key in ("q", "t", "s", "ticks")]
        fresh = np.array(arrays["fresh"], dtype=bool) if "fresh" in arrays.files else None
    if q.shape != (len(ticks), len(names), 4) or t.shape != (len(ticks), len(names), 3) or s.shape not in ((len(ticks), len(names)), (len(ticks), len(names), 3)):
        raise ValueError("Pose array dimensions do not match the skeleton")
    if not len(ticks) or not all(np.isfinite(x).all() for x in (q, t, s, ticks)) or np.any(np.diff(ticks) <= 0):
        raise ValueError("Pose arrays require finite values and strictly increasing ticks")
    from agr.particle_history import history_start
    indices = np.flatnonzero((ticks >= history_start(data)) & (ticks <= data["end_tick"]))
    if not len(indices):
        return {"status": "no poses in selected range", "sample_count": 0}
    bones = list(rig.data.bones)
    constraint_solver = None
    constraint_spec = spec.get('model_constraints')
    if constraint_spec is not None:
        from agr.model_constraints import ModelConstraints
        if constraint_spec.get('schema_version') != 1:
            raise ValueError('Unsupported authored model constraint schema')
        model_names = constraint_spec['names']
        model_parents = constraint_spec['parents']
        if set(model_names) != set(rig.data.bones.keys()):
            raise ValueError('Authored constraint model identity differs from imported rig')
        constraint_solver = ModelConstraints(model_names, model_parents, constraint_spec['definitions'])
        for i, name in enumerate(model_names):
            actual_parent = rig.data.bones[name].parent
            expected_parent = model_names[model_parents[i]] if model_parents[i] >= 0 else None
            if (actual_parent.name if actual_parent else None) != expected_parent:
                raise ValueError('Authored constraint parent hierarchy differs from imported rig')
        # Preserve all available earlier poses for stateful twist warmup.
        indices = np.flatnonzero(ticks <= data['end_tick'])
    mapped = set(names) & set(rig.data.bones.keys())
    if not mapped:
        raise ValueError("Model rig has no bones matching the source skeleton")
    weight_coverage = (_check_weighted_mapping(rig, names,
                       static_generated_cloth=spec.get("static_generated_cloth") is True)
                       if spec.get("require_weighted_bone_mapping") else None)
    if constraint_solver is not None and weight_coverage is not None:
        weight_coverage['model_constraint_affected_weighted_bones'] = sorted(
            constraint_solver.affected_names & (set(weight_coverage['mapped_weighted_bones']) |
                                               set(weight_coverage['inherited_weighted_bones'])))
        weight_coverage['inherited_behavior'] = ('Parent-relative rest before authored constraints; '
            'listed constraint-affected bones then receive authored constraint motion')
    values = np.empty((len(indices), len(bones), 10), dtype=np.float32)
    previous = {}
    correction = Matrix.Rotation(math.radians(float(spec.get("pose_basis_yaw_degrees", 0))), 4, "Z")
    for frame_index, source_index in enumerate(indices):
        globals_ = []
        for index, parent in enumerate(parents):
            scale = s[source_index, index]
            scale_vector = Vector((float(scale),) * 3) if np.ndim(scale) == 0 else Vector(scale)
            rotation = Quaternion((q[source_index, index, 3], *q[source_index, index, :3]))
            if rotation.magnitude < 1e-8:
                raise ValueError("Pose contains zero quaternion")
            rotation.normalize()
            local = Matrix.LocRotScale(Vector(t[source_index, index]) * coordinates["unit_scale"], rotation, scale_vector)
            globals_.append(globals_[parent] @ local if parent >= 0 else local)
        source = {name: correction @ globals_[index] for index, name in enumerate(names)}
        targets = {}
        # Blender bone iteration is not guaranteed to be parent first.
        def target(bone):
            if bone.name not in targets:
                if bone.name in source:
                    targets[bone.name] = source[bone.name]
                elif bone.parent:
                    targets[bone.name] = target(bone.parent) @ bone.parent.matrix_local.inverted() @ bone.matrix_local
                else:
                    targets[bone.name] = bone.matrix_local.copy()
            return targets[bone.name]
        if constraint_solver is not None:
            prepared = []
            unit_scale = float(coordinates['unit_scale'])
            if not math.isfinite(unit_scale) or unit_scale <= 0:
                raise ValueError('Constraints require positive finite unit scale')
            for name in constraint_solver.names:
                if name not in constraint_solver.required_names:
                    prepared.append([0.,0.,0.,1.,0.,0.,0.,1.])
                    continue
                loc, rotation, scale = target(rig.data.bones[name]).decompose()
                if min(scale) <= 0 or max(scale)-min(scale) > 1e-5:
                    raise ValueError(f'Authored constraints require positive uniform model scale: '
                                     f'{rig.name}, bone {name}, tick {ticks[source_index]}, scale {tuple(scale)}')
                prepared.append([*(loc/unit_scale), sum(scale)/3,
                                 rotation.x,rotation.y,rotation.z,rotation.w])
            if ticks[source_index] != int(ticks[source_index]):
                raise ValueError('Authored constraints require integer source ticks')
            constrained = constraint_solver.evaluate(prepared,tick=int(ticks[source_index]),
                                                     names=constraint_solver.names)
            # Unrelated bones retain their original matrices, including any
            # imported cloth rest transforms outside this solver's scope.
            for bone in rig.data.bones:
                target(bone)
            targets.update({name: Matrix.LocRotScale(Vector(row[:3])*unit_scale,
                        Quaternion((row[7],*row[4:7])),Vector((row[3],)*3))
                       for name,row in zip(constraint_solver.names,constrained)
                       if name in constraint_solver.affected_names})
        for index, bone in enumerate(bones):
            kwargs = {"parent_matrix": target(bone.parent), "parent_matrix_local": bone.parent.matrix_local} if bone.parent else {}
            basis = bone.convert_local_to_pose(target(bone), bone.matrix_local, invert=True, **kwargs)
            loc, rotation, scale = basis.decompose()
            if bone.name in previous and rotation.dot(previous[bone.name]) < 0:
                rotation.negate()
            previous[bone.name] = rotation.copy()
            values[frame_index, index] = (*loc, *rotation, *scale)
    action, channels = _action_channels(rig, rig.name + " | evaluated replay poses")
    frames = [tick_frame(ticks[index], data["start_tick"], data["tick_rate"], fps) for index in indices]
    steps = {i for i in range(len(indices) - 1) if ticks[indices[i + 1]] - ticks[indices[i]] > 2}
    keys = 0
    for index, bone in enumerate(bones):
        pb = rig.pose.bones[bone.name]
        pb.rotation_mode = "QUATERNION"
        for prop, offset, count in (("location", 0, 3), ("rotation_quaternion", 3, 4), ("scale", 7, 3)):
            for component in range(count):
                channel = values[:, index, offset + component]
                if np.max(abs(channel - channel[0])) < 1e-7:
                    keys += _curve(channels, rig, pb.path_from_id(prop), component, frames[:1], channel[:1])
                else:
                    keys += _curve(channels, rig, pb.path_from_id(prop), component, frames, channel, steps=steps)
    action["agr_source"] = "Offline evaluated source parent-space poses; recorded ragdolls unavailable"
    action["tick_rate"] = float(data["tick_rate"])
    return {"status": "baked", "sample_count": len(indices), "first_tick": int(ticks[indices[0]]), "last_tick": int(ticks[indices[-1]]), "mapped_bones": len(mapped), "source_bones": len(names), "rig_bones": len(bones), "source_bones_absent_from_rig": sorted(set(names) - mapped), "rig_bones_following_parent_rest": sorted(set(rig.data.bones.keys()) - mapped - (constraint_solver.affected_names if constraint_solver else set())), "keyframe_count": keys, "held_recipe_samples": int((~fresh[indices]).sum()) if fresh is not None else None,
            "weighted_bone_coverage": weight_coverage,
            "model_constraints": {"status": "baked", "count": len(constraint_spec['definitions']),
                                  "scope": "Authored bone-only constraints; full engine parity unverified",
                                  "history_first_tick": int(ticks[indices[0]])} if constraint_solver else {"status": "not_requested"},
            "cloth": weight_coverage["cloth"] if weight_coverage else "not_evaluated",
            "static_generated_cloth_bone_count": weight_coverage["static_generated_cloth_bone_count"] if weight_coverage else 0}


def overview_bounds(positions, padding=3.0):
    """Include every trajectory sample, plus room for the character models."""
    if not positions:
        return (-5.0, -5.0, -3.0), (5.0, 5.0, 3.0)
    return (tuple(min(float(p[axis]) for p in positions) - padding for axis in range(3)),
            tuple(max(float(p[axis]) for p in positions) + padding for axis in range(3)))


def _review_setup(scene, positions):
    low_values, high_values = overview_bounds(positions)
    low, high = Vector(low_values), Vector(high_values)
    center = (low + high) / 2
    span = max(8.0, max(high - low))
    ground = min(float(p[2]) for p in positions) - 0.05 if positions else -0.05
    reference = bpy.data.collections.new("AGR | reference grid and lighting")
    scene.collection.children.link(reference)
    mat = _material("Reference grid", (0.12, 0.18, 0.23))
    curve = bpy.data.curves.new("Reference grid (not map geometry)", "CURVE")
    curve.dimensions, curve.bevel_depth = "3D", min(0.025, span / 1500)
    half = span * 0.6
    for value in np.linspace(-half, half, 13):
        for a, b in (((center.x + value, center.y - half, ground), (center.x + value, center.y + half, ground)), ((center.x - half, center.y + value, ground), (center.x + half, center.y + value, ground))):
            poly = curve.splines.new("POLY")
            poly.points.add(1)
            poly.points[0].co, poly.points[1].co = (*a, 1), (*b, 1)
    obj = bpy.data.objects.new(curve.name, curve)
    reference.objects.link(obj)
    curve.materials.append(mat)
    # Imported assets may contain cameras. The export contract is exactly one
    # static scene overview, regardless of player count or asset contents.
    for imported_camera in [obj for obj in bpy.data.objects if obj.type == "CAMERA"]:
        bpy.data.objects.remove(imported_camera, do_unlink=True)
    camera = bpy.data.objects.new("AGR | overview camera", bpy.data.cameras.new("Overview"))
    reference.objects.link(camera)
    radius = (high - low).length / 2
    distance = max(10.0, radius * 2)
    camera.location = center + Vector((.8, -1, .85)).normalized() * distance
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type, camera.data.ortho_scale = "ORTHO", 1.0
    rotation_inverse = camera.rotation_euler.to_matrix().transposed()
    corners = [rotation_inverse @ (Vector((x, y, z)) - center)
               for x in (low.x, high.x) for y in (low.y, high.y) for z in (low.z, high.z)]
    # view_frame accounts for render aspect ratio and camera sensor fit, avoiding
    # cropped edges when the selected range is wide, tall, or spans a respawn.
    unit_frame = camera.data.view_frame(scene=scene)
    half_width = max(abs(point.x) for point in unit_frame)
    half_height = max(abs(point.y) for point in unit_frame)
    camera.data.ortho_scale = max(max(abs(point.x) for point in corners) / half_width,
                                  max(abs(point.y) for point in corners) / half_height) * 1.1
    camera.data.clip_start, camera.data.clip_end = .01, max(2000, distance + radius + 10)
    scene.camera = camera
    for name, offset, power in (("Key", (0.5, -0.5, 1.2), 1400), ("Fill", (-0.6, 0.4, 0.6), 700)):
        lamp = bpy.data.objects.new("AGR | " + name, bpy.data.lights.new(name, "AREA"))
        reference.objects.link(lamp)
        lamp.location = center + Vector(offset) * span
        lamp.rotation_euler = (center - lamp.location).to_track_quat("-Z", "Y").to_euler()
        lamp.data.energy, lamp.data.size = power * (span / 10) ** 2, span * .5
    world = bpy.data.worlds.new("AGR background")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.025, 0.035, 0.05, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
    scene.world = world
    return {"name": camera.name, "mode": "static overview", "camera_count": 1,
            "framing": "all exported trajectory samples with 3 metre model padding",
            "bounds_min": list(low), "bounds_max": list(high)}


def build_scene(segment, assets, output, fps=64, partial=False, particle_runtime=None, shot_hero=None,
                character_shader=None, character_only=False):
    _init_blender()
    segment, output = Path(segment).resolve(), Path(output).resolve()
    data = json.loads(segment.read_text(encoding="utf-8"))
    from agr.blender_scene_npr import validate_npr_runtime
    validate_npr_runtime(data,allow_partial=partial)
    if data.get('npr_runtime') is not None and shot_hero is None:
        raise ValueError('NPR scene build requires a perspective hero shot (--shot-hero)')
    if character_shader is not None:
        if not partial or data.get('npr_runtime') is None:
            raise ValueError('Character shader requires partial NPR Source lighting')
        from agr.blender_character_shader import validate_character_shader_library
        character_shader=validate_character_shader_library(character_shader)
    if data.get("schema_version", 1) not in (1, "1", "1.0"):
        raise ValueError("Unsupported AGR segment schema_version")
    if data["tick_rate"] <= 0 or data["end_tick"] < data["start_tick"] or fps <= 0:
        raise ValueError("Invalid tick range, tick rate or FPS")
    coordinates = {"unit_scale": .0254, "origin": [0, 0, 0], "world_yaw_degrees": 0, "model_yaw_degrees": 0}
    declared_coordinates = data.get("metadata", {}).get("coordinate_system", {})
    if declared_coordinates == "source2_z_up_hammer_units":
        declared_coordinates = {}
    if not isinstance(declared_coordinates, dict):
        raise ValueError("metadata.coordinate_system must be a coordinate mapping or source2_z_up_hammer_units")
    coordinates.update(declared_coordinates)
    if coordinates["unit_scale"] <= 0 or len(coordinates["origin"]) != 3 or not all(math.isfinite(float(x)) for x in [coordinates["unit_scale"], *coordinates["origin"], coordinates["world_yaw_degrees"], coordinates["model_yaw_degrees"]]):
        raise ValueError("Invalid coordinate system")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = "DeadBlend | replay segment"
    scene.render.fps = round(fps)
    scene.render.fps_base = round(fps) / fps
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1280, 800, 100
    scene.frame_start = 1
    scene.frame_end = math.ceil(tick_frame(data["end_tick"], data["start_tick"], data["tick_rate"], fps) - 1e-9)
    scene["agr_start_tick"], scene["agr_end_tick"], scene["agr_tick_rate"] = data["start_tick"], data["end_tick"], float(data["tick_rate"])
    scene["agr_coordinate_system"] = json.dumps(coordinates)
    scene["agr_end_frame_exact"] = tick_frame(data["end_tick"], data["start_tick"], data["tick_rate"], fps)
    scene["agr_limitations"] = "Supported installed particle layers and a static map region are optional. Full effect parity and cloth on unsupported heroes remain unfinished. Supported garment motion is an approximate bake. Death animation is user-authored; automatic ragdolls are outside the current scope."
    scene.unit_settings.system, scene.unit_settings.scale_length = "METRIC", 1
    report = {"schema_version": 1, "blender_version": bpy.app.version_string, "segment": str(segment), "output": str(output), "start_tick": data["start_tick"], "end_tick": data["end_tick"], "tick_rate": data["tick_rate"], "fps": fps, "frame_end": scene.frame_end, "end_frame_exact": scene["agr_end_frame_exact"], "coordinates": coordinates, "entities": [], "warnings": [], "coverage": data.get("coverage", {}), "partial_allowed": partial}
    poses_requested = data.get("coverage", {}).get("animation") != "not_requested"
    roots, all_positions, entity_objects = [], [], {}
    for entity in data["entities"]:
        if not entity.get("samples"):
            report["warnings"].append("Entity " + str(entity["id"]) + " has no movement samples and was skipped")
            continue
        name = str(entity.get("hero_name") or entity.get("hero_id") or "Entity") + " [" + str(entity["id"]) + "]"
        collection = bpy.data.collections.new(name)
        scene.collection.children.link(collection)
        root = _empty(name + " | replay root", collection)
        root["agr_entity_id"], root["hero_id"] = str(entity["id"]), str(entity.get("hero_id", ""))
        roots.append(root)
        samples, positions, steps = _animate_root(root, entity, data, coordinates, fps)
        all_positions.extend(positions)
        spec = asset_spec(entity, assets, segment.parent)
        spec.setdefault("model_yaw_degrees", coordinates["model_yaw_degrees"])
        entry = {"id": entity["id"], "hero_name": entity.get("hero_name"), "root": root.name, "movement_samples": len(samples), "stepped_discontinuities": steps, "model_yaw_degrees": spec["model_yaw_degrees"], "pose_basis_yaw_degrees": spec.get("pose_basis_yaw_degrees", 0)}
        from agr.network_render_state import attach_render_state
        entry['network_render_state'] = attach_render_state(root, entity)
        if spec.get("model_file") and Path(spec["model_file"]).is_file():
            objects, rigs, model_root = _import_asset(spec, collection, root, name)
            entry.update({"model_status": "imported", "model_file": spec["model_file"], "model_basis_object": model_root.name, "mesh_count": sum(obj.type == "MESH" for obj in objects)})
        else:
            if not partial:
                raise ValueError(name + ": model unavailable; provide an asset pack or explicitly enable --partial")
            objects, rigs = _placeholder(root, collection, name), []
            entry["model_status"] = "missing asset placeholder"
            report["warnings"].append(name + ": model unavailable; visible placeholder used")
        pose_inputs_available = all(spec.get(key) and Path(spec[key]).is_file() for key in ("pose_file", "skeleton_file"))
        if poses_requested and pose_inputs_available and rigs:
            if len(rigs) != 1:
                raise ValueError(name + ": select rig_object because asset has multiple armatures")
            entry["pose"] = _bake_pose(rigs[0], spec, data, coordinates, fps)
            if entry["pose"]["status"] != "baked" and not partial:
                raise ValueError(name + ": " + entry["pose"]["status"])
            entry["rig"] = rigs[0].name
        else:
            if poses_requested and not partial:
                raise ValueError(name + ": skeletal bake requires a rig, pose_file and skeleton_file; supply these or explicitly enable --partial")
            entry["pose"] = {"status": "unavailable; imported rest pose" if poses_requested else "not requested; imported rest pose"}
            if poses_requested:
                report["warnings"].append(name + ": skeletal bake requires a rig, pose_file and skeleton_file")
        entry["visibility"] = {"basis": "observed availability and PVS presence; alive state does not hide the body", "keys": [{"frame": frame, "hidden": hidden} for frame, hidden in _visibility(objects, entity, samples, data, fps)]}
        entity_objects[str(entity["id"])] = {"root": root, "rig": rigs[0] if len(rigs) == 1 else None,
                                            "model_root": model_root if objects and entry["model_status"] != "missing asset placeholder" else None}
        if entity.get("secondary_motion_file") and len(rigs) == 1:
            from agr.blender_cloth import bake_secondary_motion
            definition = Path(entity["secondary_motion_file"])
            if not definition.is_absolute():
                definition = segment.parent / definition
            try:
                entry["secondary_motion"] = bake_secondary_motion(
                    rigs[0], definition, list(range(scene.frame_start, scene.frame_end + 1)), fps,
                    collection=collection, unit_scale=float(coordinates.get("unit_scale", 0.0254)))
            except Exception as exc:
                entry["secondary_motion"] = {"status": "failed", "error": str(exc)}
                if getattr(exc, "diagnostics", None):
                    entry["secondary_motion"]["diagnostics"] = exc.diagnostics
                report["warnings"].append(name + ": secondary motion was withheld: " + str(exc))
        report["entities"].append(entry)
        print("AGR scene:", name, entry["model_status"], entry["pose"]["status"], flush=True)
    # Keep the preview clean: visible armature bones otherwise draw over the
    # meshes and stray bones poke through clothing. The rigs keep driving the
    # animation; unhide them in the Outliner (or Alt+H) to inspect or pose.
    for obj in scene.objects:
        if obj.type == "ARMATURE":
            obj.show_in_front = False
            obj.hide_viewport = True
            obj["agr_preview_hidden"] = True
    if not roots:
        raise ValueError("Segment contains no entities with movement samples")
    if character_only:
        report['map']=dict(status='not_requested',reason='Separate map workflow; scene geometry omitted',
                           source_world_sha256=data.get('map',{}).get('source_world_sha256'))
        scene['d2b_coordinate_system']=json.dumps(coordinates)
    elif data.get("map"):
        try:
            from agr.blender_map import build_map
            report["map"] = build_map(data, segment.parent, scene)
            if report['map'].get('lighting',{}).get('status') == 'partial':
                report['warnings'].append('Source map lighting is explicitly partial static diffuse; see map lighting coverage and limits.')
            if report['map'].get('material_repair_unsupported'):
                report['warnings'].append('Some map overlay materials have unsupported alpha settings; see map material coverage.')
        except Exception as exc:
            report["map"] = {"status": "failed", "stage": "rendering", "error": str(exc)}
            report["warnings"].append("Map import failed: " + str(exc))
    report["camera"] = _review_setup(scene, all_positions)
    if shot_hero is not None:
        selected = [root for root in roots if root.get('hero_id') == str(shot_hero)]
        if len(selected) != 1:
            raise ValueError('Hero shot requires exactly one exported entity with the requested hero ID')
        from agr.blender_shot import frame_actor
        report['camera'] = frame_actor(scene, selected[0])
        if report['camera']['minimum_sampled_visibility'] < 1:
            report['warnings'].append('Hero shot has sampled obstructions; inspect framing in Blender.')
    if data.get('hero_lighting') is not None:
        from agr.blender_hero_lighting import apply_hero_lighting
        report['hero_lighting'] = apply_hero_lighting(scene, roots, data['hero_lighting'],
            data.get('map', {}).get('source_world_sha256'), segment.parent, coordinates)
        report['warnings'].append('Hero Source lighting is explicitly partial with reference assumptions; see hero lighting coverage and limits.')
    if data.get('npr_runtime') is not None:
        from agr.blender_scene_npr import apply_scene_npr
        report['npr_lighting']=apply_scene_npr(scene,roots,data,manifest_dir=segment.parent,
            coordinates=coordinates,allow_partial=partial)
        report['warnings'].append('NPR indirect is partial; direct lighting and frame-dependent rim remain incomplete. See npr_lighting coverage.')
    if character_shader is not None:
        from agr.blender_character_shader import apply_character_shader
        report['character_shader']=apply_character_shader(scene,roots,data,character_shader,
            manifest_dir=segment.parent,allow_partial=partial)
        report['warnings'].append('Character shader uses an artistic Source-lighting bridge and Fresnel rim; Source emissive/status/outline and depth-aware rim remain incomplete.')
    if data.get("effects") or particle_runtime:
        try:
            if particle_runtime:
                from agr.recorded_effect_simulation import build_recorded_effects
                runtime=json.loads(Path(particle_runtime).read_text(encoding='utf-8'))
                report["effects"]=build_recorded_effects(data,segment.parent,scene,scene.camera,entity_objects,runtime)
            else:
                from agr.blender_effects import build_effects
                report["effects"] = build_effects(data, segment.parent, scene, scene.camera, entity_objects, fps)
        except Exception as exc:
            report["effects"] = {"status": "failed", "stage": "rendering", "error": str(exc)}
            report["warnings"].append("Particle rendering was incomplete: " + str(exc))
    for marker in data.get("markers", []):
        tick = marker.get("tick")
        if tick is not None and data["start_tick"] <= tick <= data["end_tick"]:
            scene.timeline_markers.new(str(marker.get("label", marker.get("name", "Replay event"))), frame=round(tick_frame(tick, data["start_tick"], data["tick_rate"], fps)))
    for entry in report["entities"]:
        root = bpy.data.objects[entry["root"]]
        entry["transform_checks"] = []
        for frame in sorted({1.0, scene["agr_end_frame_exact"], (1 + scene["agr_end_frame_exact"]) / 2}):
            scene.frame_set(math.floor(frame), subframe=frame % 1)
            evaluated = root.evaluated_get(bpy.context.evaluated_depsgraph_get())
            entry["transform_checks"].append({"frame": frame, "location": list(evaluated.location), "rotation_euler_radians": list(evaluated.rotation_euler), "world_matrix": [list(row) for row in evaluated.matrix_world]})
    readme = bpy.data.texts.new("AGR_README")
    readme.write("DEADBLEND REPLAY SEGMENT\n\nSpace: play. Timeline event markers refer to recorded ticks.\n"
                   + "Armature bones are hidden for a clean preview; unhide the rig in the Outliner to inspect or pose.\n"
                 + "Ticks " + str(data["start_tick"]) + "â€“" + str(data["end_tick"]) + " inclusive; " + str(fps) + " FPS.\n"
                 + "Meshes can be in rest pose when evaluated pose data is unavailable. Map/effect/secondary-motion coverage is listed below.\n"
                 + "Death animation can be replaced manually; automatic ragdoll is outside the current scope. Prepared cloth proxies are tied to the body animation, range and FPS; regenerate the export after editing them. Body angles do not reproduce the client backward-dash turn.\n"
                 + "Model basis yaw is explicit per asset; world coordinates and root tracks remain independently editable.\n\n"
                 + json.dumps(report, indent=2))
    scene.render.engine = 'BLENDER_EEVEE' if character_shader is not None else 'CYCLES'
    report['renderer']=scene.render.engine
    scene.cycles.samples = 24
    scene.frame_set(1)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.region_3d.view_perspective = "CAMERA"
                area.spaces.active.clip_end = 2000
                area.spaces.active.overlay.show_bones = False
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    roots[0].select_set(True)
    bpy.context.view_layer.objects.active = roots[0]
    # Appending selected hierarchies can load unused dependencies from the asset
    # file. Remove these before packing, otherwise every hero duplicates the pack.
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    bpy.ops.file.pack_all()
    report["unpacked_images"] = [image.name for image in bpy.data.images if image.source == "FILE" and not image.packed_file]
    if report["unpacked_images"]:
        report["warnings"].append("Some image textures could not be packed: " + ", ".join(report["unpacked_images"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output), compress=True)
    output.with_suffix(".scene.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "scene_report": str(output.with_suffix(".scene.json")), "entities": len(roots), "warnings": report["warnings"]}), flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segment", type=Path, required=True)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=64)
    parser.add_argument("--partial", action="store_true", help="Allow missing assets or poses, with explicit placeholders and coverage warnings")
    parser.add_argument("--particle-runtime",type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--shot-hero', type=int)
    parser.add_argument('--character-shader',type=Path)
    parser.add_argument('--character-only',action='store_true')
    args = parser.parse_args(argv)
    return build_scene(args.segment, args.assets, args.output, args.fps, args.partial,args.particle_runtime,
                       args.shot_hero,args.character_shader,args.character_only)


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    main(arguments)
