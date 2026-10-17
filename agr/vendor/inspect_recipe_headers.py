"""Offline resource audit and experimental AG2 recipe header inspection.

Python standard library only. Does not evaluate poses or alter game files.
The four-bit task type list is a format hypothesis, not a named task decoder.
"""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
import struct
import zipfile


def read_bits(data, offset, count):
    if count < 0 or offset < 0 or offset + count > len(data) * 8:
        raise ValueError("Bit read outside declared payload")
    return (int.from_bytes(data, "little") >> offset) & ((1 << count) - 1)


def recipe_header(row, server_tick, task_type_bits=4, topology_dependency_counts=None):
    def field(suffix):
        matches = [v for k, v in row["fields"].items() if k.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(f"Expected one {suffix} field")
        return matches[0]

    version = field("m_nSerializePoseRecipeVersionAG2")
    buffer = row["serialized_pose_recipe_array"]
    if version == 3:
        return _split_recipe_header(row, server_tick, field, buffer,
                                    task_type_bits, topology_dependency_counts)
    size = field("m_nSerializePoseRecipeSizeAG2")
    if version != 2 or not 5 <= size <= len(buffer):
        raise ValueError("Unsupported recipe version or invalid payload length")
    payload = bytes(buffer[:size])  # Tail of fixed-capacity array is not valid data.
    recipe_tick = int.from_bytes(payload[:4], "little")
    width = read_bits(payload, 32, 4)
    if not 1 <= width <= 8:
        raise ValueError("Invalid candidate dependency width")
    count = read_bits(payload, 36, width)
    if count == 0 or count.bit_length() != width:
        raise ValueError("Candidate count is inconsistent with its bit width")
    start = 36 + width
    # Older Esoterica writes a list of all type IDs before per-task data.
    # Four bits is a candidate Valve type width; no task-name mapping is asserted.
    type_ids = [read_bits(payload, start + task_type_bits * i, task_type_bits) for i in range(count)]
    return {
        "hero_id": row["hero_id"],
        "demo_tick": row["demo_tick"],
        "server_tick": server_tick,
        "recipe_update_tick": recipe_tick,
        "recipe_age_ticks": server_tick - recipe_tick,
        "version": version,
        "valid_bytes": size,
        "array_capacity": len(buffer),
        "candidate_dependency_bits": width,
        "candidate_task_count": count,
        "candidate_task_type_ids_4bit": type_ids,
        "candidate_task_data_start_bit": start + task_type_bits * count,
        "payload_hex": payload.hex(),
    }


def _split_recipe_header(row, server_tick, field, buffer, task_type_bits, dependency_counts):
    """V3 stores interleaved task IDs/dependencies separately from values."""
    slot = field("m_nSerializePoseRecipeAG2ActiveSlot")
    count_slots = field("m_SerializePoseRecipeAG2Slots")
    if not isinstance(slot, int) or not 0 <= slot < count_slots <= 32:
        raise ValueError("Invalid AG2 topology slot")
    topology = field(f"m_SerializePoseRecipeAG2Slots.{slot}.m_topology")
    if not isinstance(topology, list):
        raise ValueError("AG2 topology requires lossless bytes; rebuild the native extractor")
    topology = bytes(topology)
    size = field("m_SerializePoseRecipeAG2Dynamic")
    if size != len(buffer) or size < 4:
        raise ValueError("Invalid dynamic AG2 payload length")
    payload = bytes(buffer)
    width = read_bits(topology, 0, 4)
    if not 1 <= width <= 8:
        raise ValueError("Invalid topology dependency width")
    count = read_bits(topology, 4, width)
    if not count or count.bit_length() != width:
        raise ValueError("Invalid topology task count")
    cursor = 4 + width
    tasks = []
    if dependency_counts is None:
        raise ValueError("V3 topology requires the recorded task registry")
    for index in range(count):
        task_type = read_bits(topology, cursor, task_type_bits)
        cursor += task_type_bits
        if task_type not in dependency_counts:
            raise ValueError(f"Unsupported topology task type {task_type}")
        dependencies = []
        for _ in range(dependency_counts[task_type]):
            dependency = read_bits(topology, cursor, width)
            cursor += width
            if dependency >= index:
                raise ValueError("Topology dependency is not an earlier task")
            dependencies.append(dependency)
        tasks.append({"type_id": task_type, "dependencies": dependencies})
    tail = len(topology) * 8 - cursor
    if not 0 <= tail < 8 or read_bits(topology, cursor, tail):
        raise ValueError("Non-padding topology remainder")
    recipe_tick = int.from_bytes(payload[:4], "little")
    return {
        "hero_id": row["hero_id"], "demo_tick": row["demo_tick"],
        "server_tick": server_tick, "recipe_update_tick": recipe_tick,
        "recipe_age_ticks": server_tick - recipe_tick,
        "version": 3, "valid_bytes": size, "array_capacity": len(buffer),
        "candidate_dependency_bits": width, "candidate_task_count": count,
        "candidate_task_type_ids_4bit": [t["type_id"] for t in tasks],
        "candidate_task_data_start_bit": 32, "payload_hex": payload.hex(),
        "topology_tasks": tasks, "topology_slot": slot,
        "topology_hex": topology.hex(),
    }


def external_references(data):
    """Read bounded Source 2 resource headers and RERL; no KV3 decompression."""
    if len(data) < 16:
        raise ValueError("Resource header truncated")
    _, header_version, _, table_relative, count = struct.unpack_from("<IHHII", data)
    if header_version != 12:
        raise ValueError(f"Unsupported resource header {header_version}")
    refs = []
    for i in range(count):
        entry = 8 + table_relative + 12 * i
        if entry + 12 > len(data):
            raise ValueError("Block table truncated")
        kind, relative, size = struct.unpack_from("<4sII", data, entry)
        start = entry + 4 + relative
        end = start + size
        if start < 0 or end > len(data):
            raise ValueError("Block outside resource")
        if kind != b"RERL":
            continue
        offset, ref_count = struct.unpack_from("<II", data, start)
        for j in range(ref_count):
            ref = start + offset + j * 16
            if ref < start or ref + 16 > end:
                raise ValueError("RERL entry outside block")
            resource_id, name_relative = struct.unpack_from("<QQ", data, ref)
            name_start = ref + 8 + name_relative
            if not start <= name_start < end:
                raise ValueError("RERL name outside block")
            name_end = data.index(b"\0", name_start, end)
            refs.append({"resource_id": str(resource_id),
                         "resource_path": data[name_start:name_end].decode("utf-8")})
    return refs


def audit_assets(archive_path, graph_path):
    graph = graph_path.read_bytes()
    with zipfile.ZipFile(archive_path) as archive:
        names = [p.filename.replace("\\", "/") for p in archive.infolist() if not p.is_dir()]
        graph_names = [name for name in names if name.endswith(".vnmgraph_c")]
        refs = external_references(graph)
        for ref in refs:
            compiled = ref["resource_path"] + "_c"
            ref["compiled_path"] = compiled
            ref["archive_matches"] = [name for name in names
                                      if name == compiled or name.endswith("/" + compiled)]
        compiled_clips = [name for name in names if name.endswith(".vnmclip_c")]
        clip_skeletons = collections.Counter()
        for name in compiled_clips:
            for ref in external_references(archive.read(name)):
                if ref["resource_path"].endswith(".vnmskel"):
                    clip_skeletons[ref["resource_path"]] += 1
        return {
            "archive_name": archive_path.name,
            "archive_sha256": hashlib.file_digest(archive_path.open("rb"), "sha256").hexdigest(),
            "graph_sha256": hashlib.sha256(graph).hexdigest(),
            "compiled_clip_count": len(compiled_clips),
            "compiled_graph_files": graph_names,
            "compiled_skeleton_files": [n for n in names if n.endswith(".vnmskel_c")],
            "clip_skeleton_reference_counts": dict(clip_skeletons),
            "main_graph_external_references": refs,
            "missing_direct_child_graphs": [r["compiled_path"] for r in refs
                                            if r["resource_path"].endswith(".vnmgraph")
                                            and not r["archive_matches"]],
        }


def summarize(recipes):
    result = {}
    for hero in sorted({r["hero_id"] for r in recipes}):
        rows = [r for r in recipes if r["hero_id"] == hero]
        ages = [r["recipe_age_ticks"] for r in rows]
        sizes = [r["valid_bytes"] for r in rows]
        result[str(hero)] = {
            "sample_count": len(rows),
            "unique_valid_payloads": len({r["payload_hex"] for r in rows}),
            "valid_byte_range": [min(sizes), max(sizes)],
            "recipe_age_tick_range": [min(ages), max(ages)],
            "matches_current_server_tick": ages.count(0),
            "candidate_task_count_histogram": dict(sorted(collections.Counter(
                r["candidate_task_count"] for r in rows).items())),
            "candidate_type_id_histogram": dict(sorted(collections.Counter(
                t for r in rows for t in r["candidate_task_type_ids_4bit"]).items())),
            "first_stale_demo_tick": next((r["demo_tick"] for r in rows
                                           if r["recipe_age_ticks"] > 0), None),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--network-ticks", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--graph", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.raw.read_text())
    ticks = {r["demo_tick"]: r["server_tick"]
             for r in json.loads(args.network_ticks.read_text())}
    recipes = [recipe_header(row, ticks[row["demo_tick"]]) for row in rows]
    args.out.mkdir(parents=True, exist_ok=True)
    report = {
        "scope": "Partial header interpretation; four-bit type list is a hypothesis. No pose reconstruction.",
        "all_headers_consistent": True,
        "samples_checked": len(recipes),
        "heroes": summarize(recipes),
        "raw_input_sha256": hashlib.sha256(args.raw.read_bytes()).hexdigest(),
        "network_ticks_sha256": hashlib.sha256(args.network_ticks.read_bytes()).hexdigest(),
    }
    if bool(args.archive) != bool(args.graph):
        parser.error("--archive and --graph must be supplied together")
    if args.archive:
        report["assets"] = audit_assets(args.archive, args.graph)
    (args.out / "inspection.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / "recipe_headers.json").write_text(json.dumps(recipes, indent=2) + "\n")
    fields = [k for k in recipes[0] if k not in ("payload_hex", "candidate_task_type_ids_4bit")]
    with (args.out / "recipe_headers.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(recipes)
    print(json.dumps({"samples": len(recipes), "all_headers_consistent": True,
                      "haze": report["heroes"]["13"],
                      "missing_graphs": report.get("assets", {}).get("missing_direct_child_graphs")}, indent=2))


if __name__ == "__main__":
    main()
