"""Validate and slice the versioned replay scene interchange."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path


class SceneError(ValueError):
    pass


def read_scene(path: Path) -> dict:
    path = Path(path).resolve()
    if path.is_dir():
        path = path / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_scene(data)
    # Resolve paths before moving a manifest into another working directory.
    for entity in data["entities"]:
        for name in ("pose_file", "skeleton_file", "model_file", "secondary_motion_file"):
            if entity.get(name):
                p = Path(entity[name])
                entity[name] = str(p if p.is_absolute() else (path.parent / p).resolve())
    for key, field in (("effects", "manifest_file"), ("map", "model_file")):
        if isinstance(data.get(key), dict) and data[key].get(field):
            p = Path(data[key][field])
            data[key][field] = str(p if p.is_absolute() else (path.parent / p).resolve())
    return data


def validate_scene(data: dict) -> None:
    if data.get("schema_version") != 1:
        raise SceneError("Unsupported scene schema; expected version 1.")
    rate = data.get("tick_rate")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
        raise SceneError("Scene tick_rate must be a finite positive number.")
    start, end = data.get("start_tick"), data.get("end_tick")
    if any(type(x) is not int or x < 0 for x in (start, end)) or end < start:
        raise SceneError("Scene requires nonnegative inclusive start/end ticks in order.")
    entities = data.get("entities")
    if not isinstance(entities, list) or not entities:
        raise SceneError("No selected entities have samples in this range.")
    identities = set()
    for entity in entities:
        ident = entity.get("id")
        if not isinstance(ident, str) or not ident or ident in identities:
            raise SceneError("Each entity lifetime requires a unique string id.")
        identities.add(ident)
        previous = -1
        if not entity.get("samples"):
            raise SceneError(f"{ident}: empty sample track")
        for sample in entity["samples"]:
            tick = sample.get("tick")
            if type(tick) is not int or tick <= previous:
                raise SceneError(f"{ident}: samples must have unique increasing integer ticks")
            previous = tick
            for key in ("position", "body_angles"):
                value = sample.get(key)
                if not isinstance(value, (list, tuple)) or len(value) != 3 or any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value
                ):
                    raise SceneError(f"{ident} tick {tick}: invalid {key}")


def slice_scene(data: dict, start: int | None, end: int | None, heroes: list[int] | None = None) -> dict:
    validate_scene(data)
    start = data["start_tick"] if start is None else start
    end = data["end_tick"] if end is None else end
    if type(start) is not int or type(end) is not int or start > end:
        raise SceneError("Start tick must be less than or equal to end tick.")
    if start < data["start_tick"] or end > data["end_tick"]:
        raise SceneError(f"Saved package covers {data['start_tick']}–{data['end_tick']}; requested range is outside it.")
    result = copy.deepcopy(data)
    # A normal slice removes historical root samples; it cannot retain a claim
    # that those samples were recovered. Explicit history selection restores it.
    result.pop('particle_history', None)
    result["start_tick"], result["end_tick"] = start, end
    if (start, end) != (data['start_tick'], data['end_tick']) and 'particle_instances' in data:
        from .particle_events import slice_particle_instances
        result['particle_instances'] = slice_particle_instances(data['particle_instances'], start, end, data['entities'])
        if 'particle_extraction' in result:
            result['source_particle_extraction'] = result.pop('particle_extraction')
        result['particle_slice'] = {
            'scope': 'Saved instance histories cropped; original packet counts are source provenance',
            'start_tick': start, 'end_tick': end,
            'retained_instances': len(result['particle_instances']),
            'retained_events': sum(len(i.get('events', [])) for i in result['particle_instances'])}
    result["entities"] = []
    for entity in data["entities"]:
        if heroes is not None and entity.get("hero_id") not in heroes:
            continue
        selected = [s for s in entity["samples"] if start <= s["tick"] <= end]
        if not selected:
            continue
        clone = copy.deepcopy(entity)
        clone["samples"] = selected
        # Preserve data-support intervals without asserting a biological spawn/death.
        if "observed_intervals" not in clone:
            intervals = []
            for sample in entity["samples"]:
                tick = sample["tick"]
                if intervals and intervals[-1]["end_tick_exclusive"] == tick:
                    intervals[-1]["end_tick_exclusive"] = tick+1
                else:
                    intervals.append({"start_tick": tick, "end_tick_exclusive": tick+1})
            clone["observed_intervals"] = intervals
        result["entities"].append(clone)
    result["markers"] = [m for m in data.get("markers", []) if start <= m.get("tick", -1) <= end]
    result.setdefault("metadata", {})["source_range"] = [data["start_tick"], data["end_tick"]]
    # Counts from an earlier four-hero evaluation must not describe a one-hero slice.
    if "coverage" in data:
        result["source_coverage"] = copy.deepcopy(data["coverage"])
    result["coverage"] = {"scope": "current selected range", "selected_entities": len(result["entities"]),
                          "movement_samples": sum(len(e["samples"]) for e in result["entities"])}
    chosen_ids = {e["id"] for e in result["entities"]}
    if isinstance(result.get("animation"), dict):
        result["source_animation"] = copy.deepcopy(data["animation"])
        result["animation"]["entities"] = [
            {"id": e["id"], "requested_samples": len(e["samples"]), "source": "saved_pose_tracks"}
            for e in result["entities"] if e.get("pose_file") and e.get("skeleton_file")]
        result["animation"]["diagnostics"] = [d for d in result["animation"].get("diagnostics", []) if d.get("id") in chosen_ids]
    if any(e.get("pose_file") for e in result["entities"]):
        ready = sum(bool(e.get("pose_file") and e.get("skeleton_file")) for e in result["entities"])
        result.setdefault("coverage", {})["skeletal_animation"] = {
            "evaluated_entities": ready, "unsupported_entities": len(result["entities"])-ready,
            "scope": "selected entities; cached source evaluation"}
    validate_scene(result)
    return result


def save_scene(data: dict, path: Path) -> Path:
    validate_scene(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return path
