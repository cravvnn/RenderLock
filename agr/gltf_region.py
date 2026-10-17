"""Crop a Source 2 Viewer 20 world glTF by placed mesh bounds.

Retains complete intersecting meshes, their scene hierarchy, skins, relevant
animation channels, and directional lights. Reindexes dependencies and copies
whole bufferViews, preserving shared/interleaved data and accessor byte offsets.
This does not cut triangles, reproduce Source materials, or simulate collision.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import shutil
import urllib.parse

import numpy as np


SUPPORTED_EXTENSIONS = {"KHR_lights_punctual", "KHR_texture_transform",
                        "KHR_materials_unlit", "KHR_materials_emissive_strength",
                        "KHR_materials_ior", "KHR_materials_specular",
                        "KHR_materials_clearcoat", "KHR_materials_transmission",
                        "KHR_materials_volume", "KHR_materials_sheen",
                        "KHR_materials_iridescence", "KHR_materials_anisotropy"}
COLLECTIONS = ("nodes", "meshes", "skins", "cameras", "materials", "textures",
               "images", "samplers", "accessors", "bufferViews")
DTYPES = {5120: np.dtype('i1'), 5121: np.dtype('u1'), 5122: np.dtype('<i2'),
          5123: np.dtype('<u2'), 5125: np.dtype('<u4'), 5126: np.dtype('<f4')}
COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class AccessorReader:
    def __init__(self, document, base):
        self.d = document
        self.buffers = []
        for buffer in document["buffers"]:
            path = local_resource(base, buffer["uri"])
            if path.stat().st_size != buffer["byteLength"]:
                raise ValueError(f"Input buffer size mismatch: {path}")
            self.buffers.append(np.memmap(path, dtype=np.uint8, mode="r"))
        for view in document.get('bufferViews', []):
            offset, length, buffer = view.get('byteOffset', 0), view['byteLength'], view['buffer']
            if (not isinstance(buffer, int) or not 0 <= buffer < len(self.buffers)
                    or not isinstance(offset, int) or not isinstance(length, int)
                    or offset < 0 or length <= 0 or offset + length > len(self.buffers[buffer])):
                raise ValueError('bufferView is outside its source buffer')

    def read(self, index):
        a = self.d["accessors"][index]
        if a["type"] not in COMPONENTS:
            raise ValueError(f"Accessor {index}: matrix packing not implemented for {a['type']}")
        dtype, width = DTYPES[a["componentType"]], COMPONENTS[a["type"]]
        if not isinstance(a['count'], int) or a['count'] <= 0:
            raise ValueError(f'Accessor {index}: expected positive element count')
        if "bufferView" in a:
            view = self.d["bufferViews"][a["bufferView"]]
            stride = view.get("byteStride", dtype.itemsize * width)
            if stride < dtype.itemsize * width or a.get('byteOffset', 0) < 0:
                raise ValueError(f'Accessor {index}: invalid stride or offset')
            offset = view.get("byteOffset", 0) + a.get("byteOffset", 0)
            end = a.get("byteOffset", 0) + (a["count"]-1)*stride + dtype.itemsize*width
            if end > view["byteLength"]:
                raise ValueError(f"Accessor {index} exceeds bufferView")
            result = np.ndarray((a["count"], width), dtype=dtype,
                                buffer=self.buffers[view["buffer"]], offset=offset,
                                strides=(stride, dtype.itemsize))
        else:
            result = np.zeros((a["count"], width), dtype=dtype)
        if "sparse" in a:
            result = result.copy()
            sparse = a["sparse"]
            sparse_arrays = []
            for key in ("indices", "values"):
                entry = sparse[key]
                view = self.d["bufferViews"][entry["bufferView"]]
                dt = DTYPES[entry["componentType"]] if key == "indices" else dtype
                w = 1 if key == "indices" else width
                if (entry.get('byteOffset', 0) < 0 or sparse['count'] <= 0
                        or entry.get('byteOffset', 0) + sparse['count'] * w * dt.itemsize > view['byteLength']):
                    raise ValueError(f'Accessor {index}: sparse data exceeds bufferView')
                arr = np.ndarray((sparse["count"], w), dtype=dt,
                                 buffer=self.buffers[view["buffer"]],
                                 offset=view.get("byteOffset", 0)+entry.get("byteOffset", 0))
                sparse_arrays.append(arr)
            ids = sparse_arrays[0].ravel()
            if np.any(ids >= a['count']) or np.any(ids[1:] <= ids[:-1]):
                raise ValueError(f'Accessor {index}: invalid sparse indices')
            result[ids] = sparse_arrays[1]
        return result


def compact_vertices(d, selected_meshes, reader, output):
    """Compact shared attribute arrays to the union of actually referenced vertices.

    No triangles are cut or reordered. Primitive index sequences retain exactly
    the original vertex attributes; shared groups remain shared after compaction.
    """
    groups = {}
    for mesh_id in sorted(selected_meshes):
        for primitive_index, primitive in enumerate(d["meshes"][mesh_id]["primitives"]):
            if "indices" not in primitive:
                continue
            signature = (tuple(sorted(primitive["attributes"].items())),
                         tuple(tuple(sorted(target.items())) for target in primitive.get("targets", [])))
            groups.setdefault(signature, []).append((mesh_id, primitive_index, primitive))
    temporary = output.parent / (output.stem + ".compact_vertices.tmp.bin")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    buffer_id = len(d["buffers"])
    created_accessors = {}
    report = {"groups": len(groups), "compacted_groups": 0, "vertices_before": 0,
              "vertices_after": 0, "index_sequences_verified": 0,
              "new_accessor_source_indices": created_accessors}
    with temporary.open("wb") as f:
        def emit(array, original_id, *, indices=False):
            original = d["accessors"][original_id]
            array = np.ascontiguousarray(array)
            if f.tell() % 4:
                f.write(b"\0" * (-f.tell() % 4))
            view = {"buffer": buffer_id, "byteOffset": f.tell(),
                    "byteLength": array.nbytes, "target": 34963 if indices else 34962}
            view_id = len(d["bufferViews"])
            d["bufferViews"].append(view)
            f.write(array.tobytes())
            a = copy.deepcopy(original)
            a.pop("byteOffset", None)
            a.pop("sparse", None)
            a["bufferView"], a["count"] = view_id, len(array)
            if indices:
                a["componentType"] = 5125
                a.pop("min", None)
                a.pop("max", None)
            else:
                for name, reduction in (("min", np.min), ("max", np.max)):
                    if name in a:
                        a[name] = reduction(array, axis=0).tolist()
            index = len(d["accessors"])
            d["accessors"].append(a)
            created_accessors[str(index)] = original_id
            return index

        for signature, primitives in groups.items():
            count = d["accessors"][dict(signature[0])["POSITION"]]["count"]
            old_indices = {p["indices"]: reader.read(p["indices"]).ravel()
                           for _, _, p in primitives}
            used = np.unique(np.concatenate(list(old_indices.values())))
            if used[0] < 0 or used[-1] >= count:
                raise ValueError("Primitive index is out of POSITION accessor bounds")
            report["vertices_before"] += count
            report["vertices_after"] += len(used)
            if len(used) == count:
                continue
            report["compacted_groups"] += 1
            accessors = set(dict(signature[0]).values())
            accessors.update(i for target in signature[1] for _, i in target)
            new_attributes = {}
            for old in sorted(accessors):
                if d["accessors"][old]["count"] != count:
                    raise ValueError("Mesh attributes have differing vertex counts")
                new_attributes[old] = emit(reader.read(old)[used], old)
            new_indices = {}
            for old, values in old_indices.items():
                reindexed = np.searchsorted(used, values).astype('<u4')
                if not np.array_equal(used[reindexed], values):
                    raise ValueError("Index remapping changed source vertex sequence")
                report["index_sequences_verified"] += 1
                new_indices[old] = emit(reindexed.reshape(-1, 1), old, indices=True)
            for _, _, p in primitives:
                p["attributes"] = {k: new_attributes[v] for k, v in p["attributes"].items()}
                p["indices"] = new_indices[p["indices"]]
                for target in p.get("targets", []):
                    for k, v in list(target.items()):
                        target[k] = new_attributes[v]
        size = f.tell()
    d["buffers"].append({"byteLength": size, "uri": str(temporary), "_temporary_local_path": str(temporary)})
    return report, temporary


def local_resource(base: Path, uri: str) -> Path:
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError(f"Expected a local resource URI: {uri}")
    path = (base / urllib.parse.unquote(parsed.path)).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f"Resource escapes input directory: {uri}")
    return path


def matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    r = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                  [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                  [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
    result = np.eye(4)
    result[:3, :3] = r @ np.diag(node.get("scale", [1, 1, 1]))
    result[:3, 3] = node.get("translation", [0, 0, 0])
    return result


def texture_infos(value: object):
    """Yield glTF texture-info objects, deliberately ignoring arbitrary extras."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "extras":
                continue
            if key.lower().endswith("texture") and isinstance(child, dict) and "index" in child:
                yield child
            else:
                yield from texture_infos(child)
    elif isinstance(value, list):
        for child in value:
            yield from texture_infos(child)


def crop(source: Path, output: Path, source_bounds: np.ndarray) -> dict:
    source, output = source.resolve(), output.resolve()
    source_bounds = np.asarray(source_bounds, dtype=np.float64)
    if source_bounds.shape != (3, 2) or not np.all(np.isfinite(source_bounds)) or np.any(source_bounds[:, 0] >= source_bounds[:, 1]):
        raise ValueError('Crop bounds require three finite increasing min/max pairs')
    if source == output or source.parent == output.parent:
        raise ValueError("Output must use a separate directory from source assets")
    d = json.loads(source.read_text(encoding="utf-8"))
    original_counts = {k: len(d.get(k, [])) for k in COLLECTIONS}
    original_binary_bytes = sum(b["byteLength"] for b in d["buffers"])
    original_buffer_count = len(d["buffers"])
    generator = d.get("asset", {}).get("generator", "")
    if not generator.startswith("Source 2 Viewer 20."):
        raise ValueError(f"Unverified exporter basis: {generator!r}; inspect conversion before using Source bounds")
    unsupported = set(d.get("extensionsUsed", [])) - SUPPORTED_EXTENSIONS
    if unsupported:
        raise ValueError(f"Unsupported extension reference remapping: {sorted(unsupported)}")
    inactive_emission_repairs = []
    # This export references one absent default mask only from disabled emissive
    # channels. Remove that inactive slot, never synthesize replacement pixels.
    for i, material in enumerate(d.get("materials", [])):
        emissive = material.get("emissiveTexture")
        if emissive is None:
            continue
        texture = d["textures"][emissive["index"]]
        image = d["images"][texture["source"]]
        if image.get("uri") != "default_mask_tga_6eb59dad.png":
            continue
        if local_resource(source.parent, image["uri"]).is_file():
            continue
        zero_factor = all(float(x) == 0 for x in material.get("emissiveFactor", [0, 0, 0]))
        source_scale = material.get("extras", {}).get("vmat", {}).get("FloatParams", {}).get("g_flSelfIllumScale")
        has_strength = "KHR_materials_emissive_strength" in material.get("extensions", {})
        if zero_factor and source_scale == 0 and not has_strength:
            material.pop("emissiveTexture")
            inactive_emission_repairs.append({"original_material_index": i,
                                               "material_name": material.get("name"),
                                               "missing_uri": image["uri"],
                                               "reason": "glTF emission factor is zero and source self-illumination scale is zero; inactive texture removed"})
    bounds = source_bounds[[1, 2, 0]] * .0254
    nodes = d["nodes"]
    scene_index = d.get("scene", 0)
    scene = d["scenes"][scene_index]
    world, parents = {}, {}

    def traverse(index, parent, path):
        if index in path:
            raise ValueError("Cyclic node hierarchy")
        if index in world:
            raise ValueError(f"Node {index} appears more than once in active scene")
        parents[index] = parent
        world[index] = (np.eye(4) if parent is None else world[parent]) @ matrix(nodes[index])
        for child in nodes[index].get("children", []):
            traverse(child, index, path | {index})

    for index in scene.get("nodes", []):
        traverse(index, None, set())

    reader = AccessorReader(d, source.parent)
    mesh_bounds = {}
    primitive_bounds = {}
    for i, mesh in enumerate(d.get("meshes", [])):
        bb = []
        for primitive in mesh["primitives"]:
            pos_id, index_id = primitive["attributes"]["POSITION"], primitive.get("indices")
            key = (pos_id, index_id)
            if key not in primitive_bounds:
                points = reader.read(pos_id)
                if index_id is not None:
                    indices = reader.read(index_id).ravel()
                    if indices.min() < 0 or indices.max() >= len(points):
                        raise ValueError(f"Mesh {i} has an out-of-range vertex index")
                    points = points[indices]
                primitive_bounds[key] = np.array([points.min(axis=0), points.max(axis=0)])
            bb.append(primitive_bounds[key])
        mesh_bounds[i] = np.array([np.min([b[0] for b in bb], axis=0),
                                   np.max([b[1] for b in bb], axis=0)])

    selected_mesh_nodes, selected_other_nodes = set(), set()
    placed_bounds = {}
    lights = d.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])
    for i, transform in world.items():
        node = nodes[i]
        if "mesh" in node:
            bb = mesh_bounds[node["mesh"]]
            corners = np.array([(*p, 1) for p in itertools.product(*zip(bb[0], bb[1]))])
            transformed = (transform @ corners.T).T[:, :3]
            lo, hi = transformed.min(axis=0), transformed.max(axis=0)
            placed_bounds[i] = np.array([lo, hi])
            if np.all(hi >= bounds[:, 0]) and np.all(lo <= bounds[:, 1]):
                selected_mesh_nodes.add(i)
        lamp = node.get("extensions", {}).get("KHR_lights_punctual")
        if lamp is not None:
            light = lights[lamp["light"]]
            radius = light.get("range", float("inf"))
            pos = transform[:3, 3]
            if light["type"] == "directional" or (np.all(pos + radius >= bounds[:, 0]) and np.all(pos - radius <= bounds[:, 1])):
                selected_other_nodes.add(i)
    if not selected_mesh_nodes:
        raise ValueError("No mesh node intersects requested region")

    needed = {k: set() for k in COLLECTIONS}
    needed["nodes"] = selected_mesh_nodes | selected_other_nodes
    needed["skins"] = {nodes[i]["skin"] for i in selected_mesh_nodes if "skin" in nodes[i]}
    for i in needed["skins"]:
        skin = d["skins"][i]
        needed["nodes"].update(skin["joints"])
        if "skeleton" in skin:
            needed["nodes"].add(skin["skeleton"])
        if "inverseBindMatrices" in skin:
            needed["accessors"].add(skin["inverseBindMatrices"])
    for i in list(needed["nodes"]):
        while i is not None:
            if i not in world:
                raise ValueError(f"Required joint/node {i} is outside active scene")
            needed["nodes"].add(i)
            i = parents[i]
    needed["meshes"] = {nodes[i]["mesh"] for i in selected_mesh_nodes}
    geometry_compaction, temporary_vertices = compact_vertices(d, needed["meshes"], reader, output)

    kept_animations = []
    for animation in d.get("animations", []):
        channels = [copy.deepcopy(c) for c in animation["channels"]
                    if c["target"].get("node") in needed["nodes"]]
        if not channels:
            continue
        indices = sorted({c["sampler"] for c in channels})
        sampler_map = {old: new for new, old in enumerate(indices)}
        animation_copy = copy.deepcopy(animation)
        animation_copy["samplers"] = [copy.deepcopy(animation["samplers"][i]) for i in indices]
        animation_copy["channels"] = channels
        for c in channels:
            c["sampler"] = sampler_map[c["sampler"]]
        for sampler in animation_copy["samplers"]:
            needed["accessors"].update((sampler["input"], sampler["output"]))
        kept_animations.append(animation_copy)

    for i in needed["meshes"]:
        for primitive in d["meshes"][i]["primitives"]:
            needed["accessors"].update(primitive["attributes"].values())
            if "indices" in primitive:
                needed["accessors"].add(primitive["indices"])
            if "material" in primitive:
                needed["materials"].add(primitive["material"])
            for target in primitive.get("targets", []):
                needed["accessors"].update(target.values())
    for i in needed["materials"]:
        needed["textures"].update(info["index"] for info in texture_infos(d["materials"][i]))
    for i in needed["textures"]:
        texture = d["textures"][i]
        if "source" in texture:
            needed["images"].add(texture["source"])
        if "sampler" in texture:
            needed["samplers"].add(texture["sampler"])
    for i in needed["images"]:
        if "bufferView" in d["images"][i]:
            needed["bufferViews"].add(d["images"][i]["bufferView"])
    for i in needed["accessors"]:
        accessor = d["accessors"][i]
        if "bufferView" in accessor:
            needed["bufferViews"].add(accessor["bufferView"])
        if "sparse" in accessor:
            needed["bufferViews"].update(accessor["sparse"][k]["bufferView"] for k in ("indices", "values"))

    order = {k: sorted(v) for k, v in needed.items()}
    remap = {k: {old: new for new, old in enumerate(v)} for k, v in order.items()}
    result = {"asset": copy.deepcopy(d["asset"]), "scene": 0}
    for k in ("extensionsUsed", "extensionsRequired", "extensions", "extras"):
        if k in d:
            result[k] = copy.deepcopy(d[k])
    for k, ids in order.items():
        if ids:
            result[k] = [copy.deepcopy(d[k][i]) for i in ids]
    result["scenes"] = [copy.deepcopy(scene)]
    result["scenes"][0]["nodes"] = [remap["nodes"][i] for i in scene.get("nodes", []) if i in needed["nodes"]]
    for old, node in zip(order["nodes"], result["nodes"]):
        if "children" in node:
            node["children"] = [remap["nodes"][i] for i in node["children"] if i in needed["nodes"]]
            if not node["children"]:
                node.pop("children")
        if old in selected_mesh_nodes:
            node["mesh"] = remap["meshes"][node["mesh"]]
            if "skin" in node:
                node["skin"] = remap["skins"][node["skin"]]
        else:
            node.pop("mesh", None)
            node.pop("skin", None)
            node.pop("weights", None)
        node.pop("camera", None)
        if old not in selected_other_nodes and "extensions" in node:
            node["extensions"].pop("KHR_lights_punctual", None)
            if not node["extensions"]:
                node.pop("extensions")
    for skin in result.get("skins", []):
        skin["joints"] = [remap["nodes"][i] for i in skin["joints"]]
        if "skeleton" in skin:
            skin["skeleton"] = remap["nodes"][skin["skeleton"]]
        if "inverseBindMatrices" in skin:
            skin["inverseBindMatrices"] = remap["accessors"][skin["inverseBindMatrices"]]
    for mesh in result["meshes"]:
        for primitive in mesh["primitives"]:
            primitive["attributes"] = {k: remap["accessors"][i] for k, i in primitive["attributes"].items()}
            for key, kind in (("indices", "accessors"), ("material", "materials")):
                if key in primitive:
                    primitive[key] = remap[kind][primitive[key]]
            for target in primitive.get("targets", []):
                for k, i in list(target.items()):
                    target[k] = remap["accessors"][i]
    for animation in kept_animations:
        for channel in animation["channels"]:
            channel["target"]["node"] = remap["nodes"][channel["target"]["node"]]
        for sampler in animation["samplers"]:
            for k in ("input", "output"):
                sampler[k] = remap["accessors"][sampler[k]]
    if kept_animations:
        result["animations"] = kept_animations
    for material in result.get("materials", []):
        for info in texture_infos(material):
            info["index"] = remap["textures"][info["index"]]
    for texture in result.get("textures", []):
        for key, kind in (("source", "images"), ("sampler", "samplers")):
            if key in texture:
                texture[key] = remap[kind][texture[key]]
    for accessor in result.get("accessors", []):
        if "bufferView" in accessor:
            accessor["bufferView"] = remap["bufferViews"][accessor["bufferView"]]
        if "sparse" in accessor:
            for k in ("indices", "values"):
                accessor["sparse"][k]["bufferView"] = remap["bufferViews"][accessor["sparse"][k]["bufferView"]]
    output.parent.mkdir(parents=True, exist_ok=True)
    missing_images = []
    for old_image in order["images"]:
        image = d["images"][old_image]
        if "uri" not in image or local_resource(source.parent, image["uri"]).is_file():
            continue
        texture_ids = {i for i in needed["textures"] if d["textures"][i].get("source") == old_image}
        uses = [{"index": i, "name": d["materials"][i].get("name", ""),
                 "source_vmat": d["materials"][i].get("extras", {}).get("vmat", {}).get("Name")}
                for i in order["materials"]
                if any(info["index"] in texture_ids for info in texture_infos(d["materials"][i]))]
        missing_images.append({"uri": image["uri"], "original_image_index": old_image,
                               "selected_materials": uses})
    if missing_images:
        blocked = {"status": "BLOCKED_MISSING_SOURCE_IMAGES", "input": str(source),
                   "selected_mesh_nodes": len(selected_mesh_nodes),
                   "output_counts": {k: len(v) for k, v in needed.items()},
                   "missing_images": missing_images}
        output.with_suffix(".crop_report.json").write_text(json.dumps(blocked, indent=2) + "\n")
        raise FileNotFoundError(f"{len(missing_images)} referenced source image(s) missing; exact selected materials listed in {output.with_suffix('.crop_report.json')}")
    binary = output.with_suffix(".bin")
    input_buffers = {i: (temporary_vertices if i == original_buffer_count else local_resource(source.parent, b["uri"]))
                     for i, b in enumerate(d["buffers"])}
    for i, path in input_buffers.items():
        if path.stat().st_size < d["buffers"][i]["byteLength"]:
            raise ValueError(f"Input buffer is incomplete: {path}")
    handles = {i: path.open("rb") for i, path in input_buffers.items()}
    view_hashes = []
    try:
        with binary.with_suffix(".bin.tmp").open("wb") as f:
            for old, view in zip(order["bufferViews"], result["bufferViews"]):
                original = d["bufferViews"][old]
                handle = handles[original["buffer"]]
                handle.seek(original.get("byteOffset", 0))
                if f.tell() % 4:
                    f.write(b"\0" * (-f.tell() % 4))
                view["buffer"], view["byteOffset"] = 0, f.tell()
                remain = original["byteLength"]
                digest = hashlib.sha256()
                while remain:
                    chunk = handle.read(min(remain, 8 * 1024 * 1024))
                    if not chunk:
                        raise IOError("Truncated input bufferView")
                    f.write(chunk)
                    digest.update(chunk)
                    remain -= len(chunk)
                view_hashes.append(digest.hexdigest())
            binary_size = f.tell()
        binary.with_suffix(".bin.tmp").replace(binary)
    finally:
        for handle in handles.values():
            handle.close()
    result["buffers"] = [{"byteLength": binary_size, "uri": binary.name}]
    copied_image_bytes = 0
    for image in result.get("images", []):
        if "bufferView" in image:
            image["bufferView"] = remap["bufferViews"][image["bufferView"]]
        if "uri" in image:
            source_image = local_resource(source.parent, image["uri"])
            relative = source_image.relative_to(source.parent)
            target = output.parent / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_image, target)
            copied_image_bytes += target.stat().st_size
    output.write_text(json.dumps(result, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")

    # Read back each unique copied bufferView; validate bytes, not just existence.
    with binary.open("rb") as f:
        for i, (view, expected) in enumerate(zip(result["bufferViews"], view_hashes)):
            f.seek(view["byteOffset"])
            digest = hashlib.sha256()
            remain = view["byteLength"]
            while remain:
                data = f.read(min(remain, 8 * 1024 * 1024))
                if not data:
                    raise IOError("Repacked bufferView is truncated")
                digest.update(data)
                remain -= len(data)
            if digest.hexdigest() != expected:
                raise IOError(f"Repacked bufferView {i} hash mismatch")
    actual_min = np.min([placed_bounds[i][0] for i in selected_mesh_nodes], axis=0)
    actual_max = np.max([placed_bounds[i][1] for i in selected_mesh_nodes], axis=0)
    report = {
        "status": "PASS", "input": str(source), "output": str(output), "generator": generator,
        "source_bounds_xyz": source_bounds.tolist(), "gltf_bounds_xyz_metres": bounds.tolist(),
        "selection": "World-space AABB of indexed vertices; whole meshes, no triangle clipping",
        "selected_mesh_nodes": len(selected_mesh_nodes), "input_active_nodes": len(world),
        "input_counts": original_counts,
        "output_counts": {k: len(result.get(k, [])) for k in COLLECTIONS},
        "animations_in": len(d.get("animations", [])), "animations_out": len(kept_animations),
        "input_binary_bytes": original_binary_bytes,
        "output_binary_bytes": binary_size, "output_image_bytes": copied_image_bytes,
        "selected_gltf_aabb_xyz_metres": np.array([actual_min, actual_max]).T.tolist(),
        "verified_buffer_view_hashes": len(view_hashes),
        "geometry_compaction": geometry_compaction,
        "inactive_missing_emission_slots_removed": [r for r in inactive_emission_repairs if r["original_material_index"] in needed["materials"]],
        "original_node_indices": order["nodes"], "original_mesh_indices": order["meshes"],
        "original_accessor_indices": [geometry_compaction["new_accessor_source_indices"].get(str(i), i)
                                      for i in order["accessors"]],
        "limits": ["Bounds are bind/rest geometry; animation can extend beyond these bounds.",
                   "Whole merged meshes may extend far outside crop; use this for region selection, not exact clipping.",
                   "Physics companion includes Source tools/triggers; select solid colliders separately before simulation."]
    }
    output.with_suffix(".crop_report.json").write_text(json.dumps(report, indent=2) + "\n")
    temporary_vertices.unlink()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-bounds", type=float, nargs=6,
                        default=[-3584, 2560, 2304, 11520, -2304, 3072],
                        metavar=("XMIN", "XMAX", "YMIN", "YMAX", "ZMIN", "ZMAX"))
    args = parser.parse_args()
    bounds = np.array(args.source_bounds).reshape(3, 2)
    if not np.all(np.isfinite(bounds)) or np.any(bounds[:, 0] >= bounds[:, 1]):
        parser.error("Bounds must be finite with min < max on each axis")
    report = crop(args.input, args.output, bounds)
    summary = {k: v for k, v in report.items()
               if not k.startswith("original_") and k not in ("geometry_compaction", "inactive_missing_emission_slots_removed")}
    summary["geometry_compaction"] = {k: v for k, v in report["geometry_compaction"].items()
                                      if k != "new_accessor_source_indices"}
    summary["inactive_missing_emission_slots_removed"] = len(report["inactive_missing_emission_slots_removed"])
    print(json.dumps(summary, indent=2))
