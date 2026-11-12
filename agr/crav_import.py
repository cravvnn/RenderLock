"""Pure reconstruction planning for lightweight .crav files.

This validates decoded replay identity and plans separate shot jobs. It does not
claim map selection, asset compatibility or reconstruction has happened. The
caller supplies metadata from the actual replay, including its computed SHA-256.
"""
from __future__ import annotations

import copy
import hashlib
import math
import os
from pathlib import Path

from .replay import inspect_replay
from .shot_package import (
    MAX_BYTES, RECONSTRUCTION_PROFILE, ShotCollectionError,
    read_shot_collection, validate_shot_collection,
)


def _identity(info):
    # Windows pathname stat may expose birth time as ctime, whereas fstat uses
    # change time. Compare ctime only between observations of the same handle.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _stable_digest(path, *, limit, check_cancelled):
    check_cancelled()
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not 0 < before.st_size <= limit:
                raise ShotCollectionError(f"{path.name}: file exceeds the supported size or is empty")
            digest = hashlib.sha256()
            size = 0
            while chunk := stream.read(1024 * 1024):
                check_cancelled()
                size += len(chunk)
                if size > limit or size > before.st_size:
                    raise ShotCollectionError(f"{path.name}: file changed during preflight")
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            if (size != before.st_size or _identity(before) != _identity(after)
                    or before.st_ctime_ns != after.st_ctime_ns
                    or _identity(after) != _identity(path.stat())):
                raise ShotCollectionError(f"{path.name}: file changed during preflight")
    except OSError as exc:
        raise ShotCollectionError(f"Cannot read {path.name}: {exc}") from exc
    check_cancelled()
    return digest.hexdigest(), _identity(after)


MAP_PENDING = "whole-path map selection and lighting dependencies"
CAPTURE_MAP_PENDING = "capture-time map content identity (the .crav does not pin installed map content)"


def _load_map(path):
    from .map_package import load_package
    try:
        return load_package(path)
    except ValueError as exc:
        raise ShotCollectionError(str(exc)) from exc


def _map_identity(package):
    doc = package["document"]
    return (package["manifest_sha256"], doc["library"]["sha256"], doc["inventory"]["geometry_sha256"])


def preflight_reconstruction(package, *, replay=None, shot_ids=None, map_package=None,
                             cancelled=lambda: False):
    """Resolve an explicit local replay, decode it, and plan validated shot jobs.

    The embedded path is only a location hint. An explicit replay override is
    checked against every declared identity field. Recheck both files after
    decoding; this read-only result is not an immutable snapshot or permission
    to bypass extraction-time identity, asset, map or scene gates.

    ``map_package`` optionally names a complete prepared RenderLock map. Every
    declared byte is verified before decoding and its identity is rechecked
    afterwards. The map is the caller's explicit choice; the replay header
    label (for example ``start``) never selects it.
    """
    def check():
        if cancelled():
            raise ShotCollectionError("RenderLock .crav preflight cancelled")

    check()
    package = Path(package).expanduser().resolve()
    pin = _stable_digest(package, limit=MAX_BYTES, check_cancelled=check)
    collection = read_shot_collection(package)
    if collection.get("profile") != RECONSTRUCTION_PROFILE:
        raise ShotCollectionError("A lightweight reconstruction profile is required")
    demo = Path(replay if replay is not None else collection["replay"]["path"]).expanduser().resolve()
    prepared_map = _load_map(map_package) if map_package is not None else None
    check()
    replay_pin = _stable_digest(demo, limit=4 * 1024**3, check_cancelled=check)
    if replay_pin[0] != collection["replay"]["sha256"]:
        raise ShotCollectionError("Local replay SHA-256 does not match .crav identity")
    check()
    metadata = inspect_replay(demo)
    check()
    if replay_pin != _stable_digest(demo, limit=4 * 1024**3, check_cancelled=check):
        raise ShotCollectionError("Replay changed while decoding .crav preflight")
    if pin != _stable_digest(package, limit=MAX_BYTES, check_cancelled=check):
        raise ShotCollectionError("Shot collection changed during .crav preflight")
    if prepared_map is not None and _map_identity(_load_map(map_package)) != _map_identity(prepared_map):
        raise ShotCollectionError("Prepared map changed during .crav preflight")
    check()
    metadata = dict(metadata, demo_sha256=replay_pin[0])
    plan = plan_reconstruction(collection, metadata, shot_ids=shot_ids, map_package=prepared_map)
    check()
    return dict(plan, package=str(package), package_sha256=pin[0], replay=str(demo),
                replay_metadata=metadata, replay_sha256=replay_pin[0],
                preflight="local files and decoded identity checked; reconstruction pending")


def map_plan(collection: dict, prepared_map: dict, header_map) -> dict:
    """Resolve camera-path map selection with an explicit full prepared map.

    A complete prepared map contains every converted map object, so it is a
    superset of any camera-path crop and margin. Lighting dependencies must be
    present as packaged lightmaps or volumes. Map content is today's installed
    or hosted build; it is not proven to be the content loaded at capture time.
    """
    selection = collection["reconstruction"]["map_selection"]
    doc = prepared_map["document"]
    lighting = sorted(row["file"] for row in doc["files"]
                      if row["file"].split("/", 1)[0] in {"lightmaps", "volumes"})
    if selection["include_lighting_dependencies"] and not lighting:
        raise ShotCollectionError("The prepared map has no packaged lighting dependencies")
    return dict(status="full prepared map", selection="explicit", map_name=doc["map_name"],
                replay_header_map=header_map, crop="none (superset of camera path and margin)",
                package_id=doc["package_id"], manifest_sha256=prepared_map["manifest_sha256"],
                library_sha256=doc["library"]["sha256"], geometry_sha256=doc["inventory"]["geometry_sha256"],
                source_kind=doc["source"]["kind"], game_version=doc["source"]["game_version"],
                lighting_files=len(lighting))


def plan_reconstruction(collection: dict, replay_metadata: dict, *, shot_ids=None,
                        map_package=None) -> dict:
    validate_shot_collection(collection)
    if collection.get("profile") != RECONSTRUCTION_PROFILE:
        raise ShotCollectionError("A lightweight reconstruction profile is required")
    if not isinstance(replay_metadata, dict):
        raise ShotCollectionError("Decoded replay metadata is required")
    replay = collection["replay"]
    for declared, decoded in (("sha256", "demo_sha256"), ("map", "map_name"),
                              ("build", "build"), ("tick_rate", "tick_rate"),
                              ("total_ticks", "total_ticks")):
        actual = replay_metadata.get(decoded)
        if actual is None or isinstance(actual, bool) or actual != replay[declared]:
            raise ShotCollectionError(f"Decoded replay {decoded} does not match .crav identity")
    if "match_id" in replay and str(replay_metadata.get("match_id")) != replay["match_id"]:
        raise ShotCollectionError("Decoded replay match_id does not match .crav identity")
    available = {shot["id"] for shot in collection["shots"]}
    if shot_ids is None:
        selected = available
    else:
        if (not isinstance(shot_ids, (list, tuple)) or not shot_ids
                or any(not isinstance(value, str) for value in shot_ids)
                or len(set(shot_ids)) != len(shot_ids) or not set(shot_ids) <= available):
            raise ShotCollectionError("Select unique shot IDs present in the .crav")
        selected = set(shot_ids)
    jobs = []
    for shot in collection["shots"]:
        if shot["id"] not in selected:
            continue
        # The last fractional sample needs the following integer replay tick.
        # Separate jobs preserve each shot's own FPS and avoid encoding gaps.
        jobs.append(dict(shot_id=shot["id"], start_tick=shot["range"]["start_tick"],
                         end_tick=math.ceil(shot["range"]["end_tick_exact"]),
                         fps=shot["camera"]["fps"], camera=copy.deepcopy(shot["camera"])))
    pending = ["qualified installed assets", MAP_PENDING,
               "camera-first scene reconstruction", "saved-scene and visual verification"]
    result = dict(status="planned", jobs=jobs,
                  map_selection=copy.deepcopy(collection["reconstruction"]["map_selection"]),
                  missing_content="error")
    if map_package is not None:
        result["map"] = map_plan(collection, map_package, replay_metadata.get("map_name"))
        pending[pending.index(MAP_PENDING)] = CAPTURE_MAP_PENDING
    return dict(result, pending=pending)
