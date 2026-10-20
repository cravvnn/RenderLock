"""Turn an inclusive replay tick range into AGR's portable scene manifest.

The native helper is required because Boon's public Python player table exposes
camera angles, not the replicated model orientation or serialized AG2 recipes.
Never silently substitute that table when the native helper is unavailable.
"""
from __future__ import annotations

import hashlib
from importlib.metadata import version as package_version
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

SCHEMA_VERSION = 1
_SKEL = "CBodyComponent.m_skeletonInstance."
_ANIM = "CBodyComponent.m_animationController."

# Cosmetic English labels from the locally pinned Boon 6766 catalog. IDs from
# the replay remain authoritative; this table neither selects assets nor claims
# animation compatibility. Unknown future heroes remain selectable by ID.
_HERO_DISPLAY_NAMES = {
    1: "Infernus", 2: "Seven", 3: "Vindicta", 4: "Lady Geist", 6: "Abrams",
    7: "Wraith", 8: "McGinnis", 10: "Paradox", 11: "Dynamo", 12: "Kelvin",
    13: "Haze", 14: "Holliday", 15: "Bebop", 16: "Calico", 17: "Grey Talon",
    18: "Mo & Krill", 19: "Shiv", 20: "Ivy", 21: "Kali", 25: "Warden",
    27: "Yamato", 31: "Lash", 35: "Viscous", 38: "Gunslinger", 39: "The Boss",
    46: "Generic Person", 48: "Wrecker", 49: "Rutger", 50: "Pocket",
    51: "Thumper", 52: "Mirage", 53: "Fathom", 54: "Cadence", 55: "TargetDummy",
    56: "Bomber", 57: "Shield Guy", 58: "Vyper", 59: "Vandal", 60: "Sinclair",
    61: "Trapper", 62: "Raven", 63: "Mina", 64: "Drifter", 65: "Venator",
    66: "Victor", 67: "Paige", 68: "Boho", 69: "The Doorman", 70: "Skyrunner",
    71: "Swan", 72: "Billy", 73: "Druid", 74: "Graf", 75: "Fortuna",
    76: "Graves", 77: "Apollo", 78: "Deadman Danny", 79: "Rem", 80: "Silver",
    81: "Celeste", 82: "Opera", 84: "Rat King", 85: "Solomon", 86: "Violet",
    87: "Nurse Harrow", 88: "Baba",
}


def hero_display_name(hero_id: int) -> str:
    """Return an offline label; a label is never an export identity or guarantee."""
    return _HERO_DISPLAY_NAMES.get(hero_id, f"Hero {hero_id}")


class ReplayError(RuntimeError):
    """An unsupported replay, unavailable dependency, or failed extraction."""


def _dependencies():
    try:
        import boon
        import polars
    except ImportError as exc:
        raise ReplayError(
            "Replay extraction requires boon-deadlock==0.15.0 and polars. "
            "Install the program's requirements.txt with your Python interpreter."
        ) from exc
    installed = package_version('boon-deadlock')
    if installed != '0.15.0':
        raise ReplayError(f"This AGR requires boon-deadlock 0.15.0; found {installed}. Install requirements.txt.")
    return boon, polars


def find_native_extractor() -> Path | None:
    """Find the packaged helper, a development build, or an explicit override."""
    override = os.environ.get("AGR_NATIVE_EXTRACTOR")
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_file():
            raise ReplayError(f"AGR_NATIVE_EXTRACTOR does not name a file: {candidate}")
        return candidate.resolve()
    root = Path(__file__).resolve().parent.parent
    executable = "agr-extract.exe" if os.name == "nt" else "agr-extract"
    for candidate in (
        root / "bin" / executable,
        root / "native" / "target" / "release" / executable,
    ):
        if candidate.is_file():
            return candidate
    found = shutil.which(executable)
    return Path(found).resolve() if found else None


def _check_demo(demo: Path) -> Path:
    demo = Path(demo).expanduser().resolve()
    if not demo.is_file():
        raise ReplayError(f"Replay file does not exist: {demo}")
    with demo.open("rb") as stream:
        header = stream.read(16)
    if len(header) != 16 or header[:8] != b"PBDEMS2\0":
        raise ReplayError("Expected a decompressed Source 2 .dem replay (PBDEMS2 header).")
    return demo


def inspect_replay(demo: Path) -> dict[str, Any]:
    """Read metadata and recorded hero membership without large per-tick tables.

    The compact player roster identifies hero types present in the recording,
    not whether their rigs and animation are supported in a requested clip.
    Player names and account identities are deliberately not returned.
    """
    demo = _check_demo(demo)
    boon, _ = _dependencies()
    try:
        parsed = boon.Demo(str(demo), preload=False)
        metadata = {name: getattr(parsed, name) for name in (
            "build", "map_name", "match_id", "tick_rate", "total_ticks", "total_seconds"
        )}
    except Exception as exc:
        raise ReplayError(f"Boon could not read this replay: {exc}") from exc
    metadata.update(heroes=[], roster_source="recorded_players")
    try:
        counts: dict[int, int] = {}
        teams: dict[int, list[int]] = {}
        invalid_rows = 0
        for player in parsed.players.to_dicts():
            hero_id = player.get("hero_id") if isinstance(player, dict) else None
            if isinstance(hero_id, bool) or not isinstance(hero_id, int) or hero_id <= 0:
                invalid_rows += 1
                continue
            counts[hero_id] = counts.get(hero_id, 0) + 1
            team = player.get("team_num")  # the raw team number; only used to group heroes in the UI
            if isinstance(team, int) and not isinstance(team, bool):
                teams.setdefault(hero_id, []).append(team)
        metadata["heroes"] = [
            {"hero_id": hero_id, "hero_name": hero_display_name(hero_id),
             "player_count": counts[hero_id],
             # A team only when every player of that hero was on the same one.
             **({"team": teams[hero_id][0]} if len(teams.get(hero_id, ())) == counts[hero_id]
                and len(set(teams[hero_id])) == 1 else {})}
            for hero_id in sorted(counts)
        ]
        if invalid_rows:
            metadata["roster_warning"] = (
                f"{invalid_rows} recorded player row(s) have no valid positive hero ID; "
                "the character list may be incomplete."
            )
        elif not counts:
            metadata["roster_warning"] = (
                "No recorded hero IDs were found; the character list is unavailable."
            )
    except Exception as exc:
        metadata["roster_source"] = "unavailable"
        metadata["roster_warning"] = (
            f"Recorded character list unavailable ({type(exc).__name__}); "
            "replay metadata was read successfully."
        )
    return metadata


def _write_json(path: Path, data: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        stream.write("\n")
    temporary.replace(path)


def _vector(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) for v in value):
        return None
    result = [float(v) for v in value]
    return result if all(math.isfinite(v) for v in result) else None


def _position(fields: dict[str, Any]) -> list[float] | None:
    """Require all cell and offset values; missing coordinates are not zero."""
    result = []
    for axis in "XYZ":
        cell = fields.get(f"{_SKEL}m_vecOrigin.m_cell{axis}")
        offset = fields.get(f"{_SKEL}m_vecOrigin.m_vec{axis}")
        if (isinstance(cell, bool) or isinstance(offset, bool)
                or not isinstance(cell, (int, float)) or not isinstance(offset, (int, float))):
            return None
        # City Never Sleeps uses a 128-cell grid (Boon 0.12+). This only
        # affects new replay extraction; saved scene positions stay untouched.
        result.append(float(cell) * 512.0 - 32768.0 + float(offset))
    return result if all(math.isfinite(v) for v in result) else None


def _entities_from_raw(rows: list[dict[str, Any]], start: int, end: int,
                       heroes: set[int] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    invalid = 0
    invalid_by_identity: dict[str, int] = {}
    missing_recipes = 0
    for row in rows:
        tick = int(row["demo_tick"])
        hero = int(row["hero_id"])
        if not start <= tick <= end or (heroes is not None and hero not in heroes):
            continue
        identity = row.get("entity_id")
        if not identity or "entity_serial" not in row:
            raise ReplayError("Native output lacks entity serials; cannot preserve pawn lifetimes.")
        fields = row["fields"]
        model_key = _SKEL + "m_modelState.m_hModel"
        model = fields.get(model_key)
        if model_key in fields and (type(model) is not int or not 0 <= model < 1 << 64):
            raise ReplayError(f"Invalid recorded model resource ID for pawn {identity} at tick {tick}")
        position = _position(fields)
        body_angles = _vector(fields.get(_SKEL + "m_angRotation"))
        if position is None or body_angles is None:
            invalid += 1
            invalid_by_identity[identity] = invalid_by_identity.get(identity, 0) + 1
            continue
        if identity not in entities:
            entities[identity] = {
                "id": identity,
                "entity_index": int(row["entity_index"]),
                "entity_serial": int(row["entity_serial"]),
                "hero_id": hero,
                "hero_name": row.get("hero_name", f"Hero {hero}"),
                "samples": [],
            }
            if model is not None:
                # Resource handles are not file paths. Keep the full uint64 as text.
                entities[identity]["model_handle"] = str(model)
        entity = entities[identity]
        if entity["hero_id"] != hero:
            raise ReplayError(f"Hero changed within pawn lifetime {identity}; split-hero tracks are not yet supported.")
        health = fields.get("m_iHealth")
        life_state = fields.get("m_lifeState")
        sample = {
            "tick": tick,
            "position": position,
            "body_angles": body_angles,
            "health": health,
            "alive": (int(life_state) == 0 if life_state is not None else None),
            "active": bool(row.get("active", True)),
            "present": bool(row.get("active", True)),
        }
        if model_key in fields:
            sample['model_handle'] = str(model)
        # Preserve network render state without asserting a GPU instance-buffer
        # binding. Missing fields stay missing, including in older raw exports.
        render_state = {}
        for field, key in (("m_clrRender", "rgba8_packed"), ("m_nRenderMode", "mode"),
                           (_SKEL+"m_materialGroup", "material_group")):
            if field not in fields:
                continue
            value = fields[field]
            if type(value) is not int or not 0 <= value <= 0xffffffff:
                raise ReplayError(f"Invalid recorded {field} for pawn {identity} at tick {tick}")
            render_state[key] = value
        if render_state:
            sample["network_render_state"] = render_state
        mesh_key=_SKEL+'m_modelState.m_MeshGroupMask'
        if mesh_key in fields:
            mesh_mask=fields[mesh_key]
            if type(mesh_mask) is not int or not 0<=mesh_mask<1<<64:
                raise ReplayError(f'Invalid recorded mesh-group mask for pawn {identity} at tick {tick}')
            sample['mesh_group_mask']=str(mesh_mask)
        eye = _vector(fields.get("m_angEyeAngles"))
        if eye is not None:
            sample["eye_angles"] = eye
        scale = fields.get(_SKEL + "m_flScale")
        if scale is not None:
            sample["model_scale"] = scale
        if not row.get("serialized_pose_recipe_array"):
            missing_recipes += 1
        entity["samples"].append(sample)
    if not entities:
        raise ReplayError("No matching hero samples have complete recorded position and body rotation in this range.")
    seen_heroes = {entity["hero_id"] for entity in entities.values()}
    if heroes is not None and heroes - seen_heroes:
        raise ReplayError(f"Requested hero IDs absent from valid samples: {sorted(heroes - seen_heroes)}")
    summaries = {}
    for entity in entities.values():
        samples = sorted(entity["samples"], key=lambda row: row["tick"])
        ticks = [row["tick"] for row in samples]
        if len(set(ticks)) != len(ticks):
            raise ReplayError(f"Duplicate completed-tick samples for pawn {entity['id']}")
        entity["samples"] = samples
        # These are half-open support intervals for observed, usable transforms.
        # A gap can mean missing/invalid data rather than an actual deletion. Do
        # not invent a spawn/despawn event or prolong the last pose through it.
        intervals: list[dict[str, int]] = []
        for tick in ticks:
            if intervals and intervals[-1]["end_tick_exclusive"] == tick:
                intervals[-1]["end_tick_exclusive"] = tick + 1
            else:
                intervals.append({"start_tick": tick, "end_tick_exclusive": tick + 1})
        entity["observed_intervals"] = intervals
        summaries[entity["id"]] = {
            "samples": len(samples), "first_tick": ticks[0], "last_tick": ticks[-1],
            "interior_missing_ticks": ticks[-1] - ticks[0] + 1 - len(ticks),
            "dormant_samples": sum(not sample["active"] for sample in samples),
            "invalid_transform_samples_skipped": invalid_by_identity.get(entity["id"], 0),
            "observed_intervals": intervals,
        }
    return sorted(entities.values(), key=lambda e: (e["hero_id"], e["id"])), {
        "invalid_transform_samples_skipped": invalid,
        "samples_without_raw_ag2": missing_recipes,
        "visibility_basis": "Half-open intervals of valid observed samples; present follows recorded PVS activity. "
                            "Gaps and interval boundaries are not asserted spawn, despawn, or death events.",
        "entities": summaries,
    }


def extract_replay(demo: Path, output: Path, start_tick: int, end_tick: int,
                   hero_ids: list[int] | None = None) -> Path:
    """Extract a fresh .dem range; both boundary ticks are included.

    Writes segment.json, raw_animation_state.json, network_ticks.json, event
    sidecars, and an extraction log. Refuses to overwrite an existing extraction.
    No prepared Blender scene, named player, fixed hero, or match-clock offset is
    involved. Scene time is relative to replay ticks; game HUD time is not guessed.
    """
    if (isinstance(start_tick, bool) or isinstance(end_tick, bool)
            or not isinstance(start_tick, int) or not isinstance(end_tick, int)
            or start_tick < 0 or end_tick < start_tick or end_tick >= 2**31 - 1):
        raise ReplayError("Tick range must contain integers with 0 <= start <= end < 2147483647.")
    if hero_ids is not None:
        if not hero_ids or any(isinstance(h, bool) or not isinstance(h, int) or h <= 0 for h in hero_ids):
            raise ReplayError("hero_ids must be None for all heroes, or a nonempty list of positive integers.")
        hero_ids = sorted(set(hero_ids))
    demo = _check_demo(demo)
    output = Path(output).expanduser().resolve()
    helper = find_native_extractor()
    if helper is None:
        raise ReplayError(
            "The AGR native extractor is required for body angles, pawn identities, and raw AG2 recipes. "
            "Boon's public Python tables cannot provide those fields. Place agr-extract(.exe) in bin/, "
            "set AGR_NATIVE_EXTRACTOR, or build native/Cargo.toml with cargo build --release."
        )
    boon, pl = _dependencies()
    try:
        parsed = boon.Demo(str(demo))
    except Exception as exc:
        raise ReplayError(f"Boon could not read this replay: {exc}") from exc
    metadata = {name: getattr(parsed, name) for name in (
        "build", "map_name", "match_id", "tick_rate", "total_ticks", "total_seconds"
    )}
    if not isinstance(metadata["tick_rate"], (int, float)) or metadata["tick_rate"] <= 0:
        raise ReplayError("Replay metadata does not provide a valid tick rate.")
    if end_tick > metadata["total_ticks"]:
        raise ReplayError(f"End tick {end_tick} exceeds replay total_ticks {metadata['total_ticks']}.")
    reserved = ["segment.json", "raw_animation_state.json", "network_ticks.json", "native_coverage.json",
                "animation_provenance.json", "kills.json", "abilities.json", "extraction.log"]
    existing = [name for name in reserved if (output / name).exists()]
    if existing:
        raise ReplayError(f"Output already contains extraction files: {', '.join(existing)}. Choose a new output folder.")
    output.mkdir(parents=True, exist_ok=True)
    command = [str(helper), str(demo), str(output), str(start_tick), str(end_tick)]
    if hero_ids is not None:
        command.append(",".join(map(str, hero_ids)))
    with (output / "extraction.log").open("w", encoding="utf-8") as log:
        process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    if process.returncode:
        detail = (output / "extraction.log").read_text(encoding="utf-8", errors="replace")[-4000:]
        raise ReplayError(f"Native replay extraction failed (exit {process.returncode}). {detail}")
    rows = json.loads((output / "raw_animation_state.json").read_text(encoding="utf-8"))
    native_coverage = json.loads((output / "native_coverage.json").read_text(encoding="utf-8"))
    if native_coverage.get('source_world_half') != 32768 or native_coverage.get('boon_version') != '0.15.0':
        raise ReplayError('Native replay helper does not match the current parser/coordinate format. Replace bin/agr-extract with the 0.2.1 release helper.')
    entities, transforms = _entities_from_raw(rows, start_tick, end_tick, set(hero_ids) if hero_ids else None)
    warnings = []
    if native_coverage.get("raw_ag2_empty_samples") or native_coverage.get("raw_ag2_missing_array_indices"):
        warnings.append("Some recorded AG2 recipe arrays are empty or incomplete; skeletal coverage must be checked before baking.")
    if native_coverage.get("unresolved_animation_warmup"):
        warnings.append("Some stale animation seeds could not be recovered; inspect native.unresolved_animation_warmup.")
    markers = []
    event_coverage = {}
    try:
        parsed.load("kills", "abilities")
        for name in ("kills", "abilities"):
            frame = getattr(parsed, name).filter(pl.col("tick").is_between(start_tick, end_tick))
            event_rows = frame.to_dicts()
            _write_json(output / f"{name}.json", event_rows)
            event_coverage[name] = len(event_rows)
            for event in event_rows:
                if name == "kills":
                    # Name catalogs may download on first access. Recorded IDs
                    # keep optional markers offline and independent of catalogs.
                    label = f"Hero {event['victim_hero_id']} death"
                else:
                    label = str(event.get("ability", "Ability"))
                markers.append({"tick": int(event["tick"]), "label": label, "kind": name, "data": event})
    except Exception as exc:
        warnings.append(f"Optional kill/ability marker extraction failed: {type(exc).__name__}: {exc}")
        event_coverage["status"] = "incomplete"
    with demo.open("rb") as stream:
        metadata["demo_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    provenance = {"demo_sha256": metadata["demo_sha256"]}
    for key, name in (("raw_sha256", "raw_animation_state.json"),
                      ("network_ticks_sha256", "network_ticks.json")):
        with (output / name).open("rb") as stream:
            provenance[key] = hashlib.file_digest(stream, "sha256").hexdigest()
    _write_json(output / "animation_provenance.json", provenance)
    metadata.update({
        "source_filename": demo.name,
        "parser": f"boon-deadlock {package_version('boon-deadlock')}",
        "source_world_half": 32768,
        "source_coordinate_system": "source2_z_up_hammer_units",
        "coordinate_system": {"unit_scale": 0.0254, "origin": [0, 0, 0],
                              "world_yaw_degrees": 0, "model_yaw_degrees": 180},
        "body_angle_source": _SKEL + "m_angRotation",
        "angle_unit": "degrees",
        "state_reconstruction": "from_signon",
        "timeline_basis": "replay_ticks; no HUD clock offset assumed",
        "requested_hero_ids": hero_ids,
    })
    coverage = {
        "native": native_coverage,
        "transforms": transforms,
        "markers": event_coverage,
        "animation": "raw_ag2_preserved; evaluation is a separate stage",
        "particles": "not_extracted",
        "map": "not_extracted",
        "ragdoll": "not_simulated",
        "warnings": warnings,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "metadata": metadata,
        "tick_rate": metadata["tick_rate"],
        "start_tick": start_tick,
        "end_tick": end_tick,
        "entities": entities,
        "markers": sorted(markers, key=lambda item: (item["tick"], item["kind"])),
        "coverage": coverage,
    }
    destination = output / "segment.json"
    _write_json(destination, manifest)
    return destination
