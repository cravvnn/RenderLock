"""Small support archives for failed exports, beside the launcher's user logs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import traceback
from uuid import uuid4
import zipfile

from . import __version__


EVENT_PREFIX = "RENDERLOCK_DIAGNOSTICS "
MAX_FILE_BYTES = 128 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024  # Uncompressed contents, not a cache-size estimate.
MAX_PARTICLE_REPORT_BYTES = 4 * 1024 * 1024
_OPTION_NAMES = (
    "demo", "segment", "output", "assets", "game_install", "asset_cache", "blender",
    "blender_python", "source2viewer", "start_tick", "end_tick", "heroes", "fps",
    "partial", "partial_cloth", "partial_lighting", "overwrite", "motion_only", "no_effects",
    "include_map", "map_name", "map_margin", "secondary_motion", "model_constraints",
    "material_repairs", "source_lighting", "source_hero_lighting", "source_npr_lighting",
    "character_only", "character_shader", "external_map", "shot_hero", "particle_probe", "dotnet",
    "particle_history_ticks", "world_region_probe", "lighting_dotnet",
    "probe_atlas_decoder", "cubemap_decoder", "keep_work",
)
_SECRET_KEY = r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|authorization|cookie|credential)s?"
_SECRET_FIELD = re.compile(_SECRET_KEY, re.I)
_SECRET_VALUE = re.compile(
    r"([\"']?" + _SECRET_KEY + r"[\"']?\s*[:=]\s*)(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&}]+)", re.I)


def log_directory() -> Path:
    """Match existing launchers, including their explicit startup-log location.

    The native launcher and Launch_RenderLock.cmd use LOCALAPPDATA/RenderLock/logs
    on Windows, falling back to the process temp directory when it is absent.
    """
    startup = os.environ.get("RENDERLOCK_STARTUP_LOG")
    explicit = os.environ.get("RENDERLOCK_LOG_DIR")
    if startup:
        return Path(startup).expanduser().resolve().parent
    if explicit:
        return Path(explicit).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        return (base / "RenderLock" / "logs").resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Logs" / "RenderLock").resolve()
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return (base / "RenderLock" / "logs").resolve()


def _redact(text: str) -> str:
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)",
                  "[REDACTED PRIVATE KEY]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"',;]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?im)(\b(?:proxy-authorization|authorization|set-cookie|cookie)[\"']?\s*[:=]\s*)[^\r\n]*",
                  lambda match: match[1] + "[REDACTED]", text)
    return _SECRET_VALUE.sub(lambda match: match[1] + "[REDACTED]", text)


def _safe(value, depth=0):
    """Bound diagnostic metadata and omit credential fields, never serialize locals."""
    if depth > 8:
        return "[metadata depth limit]"
    if isinstance(value, (str, Path)):
        return _redact(str(value))[:16000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:100]:
            key = str(key)[:200]
            result[key] = "[REDACTED]" if _SECRET_FIELD.search(key) else _safe(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe(item, depth + 1) for item in value[:100]]
    return "[" + type(value).__name__ + "]"


def _json(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True, default=lambda item: "[unsupported]") + "\n").encode("utf-8")


def _read_evidence(path: Path, root: Path, *, tail=False):
    """Read only named small files, without following work-directory links."""
    current = path
    while current != root:
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("linked diagnostic file omitted")
        current = current.parent
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > MAX_FILE_BYTES:
            if not tail:
                raise ValueError("report exceeds support ZIP size limit")
            stream.seek(-MAX_FILE_BYTES, os.SEEK_END)
            stream.readline()  # Do not include a partial line (or partial credential).
        content = stream.read(MAX_FILE_BYTES)
    text = content.decode("utf-8", errors="replace")
    if tail:
        if size > MAX_FILE_BYTES:
            text = "[Earlier log lines omitted by support ZIP size limit]\n" + text
        return _redact(text).encode("utf-8")
    return _json(_safe(json.loads(text)))


def _particle_coverage_summary(root: Path):
    """Keep only bounded aggregate particle outcomes, not plans or scene data."""
    path = root / 'coverage.json'
    for item in (root, path):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('linked particle report omitted')
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        if os.fstat(stream.fileno()).st_size > MAX_PARTICLE_REPORT_BYTES:
            raise ValueError('particle report exceeds summary input limit')
        raw = stream.read(MAX_PARTICLE_REPORT_BYTES + 1)
    if len(raw) > MAX_PARTICLE_REPORT_BYTES:
        raise ValueError('particle report exceeds summary input limit')
    report = json.loads(raw)
    if not isinstance(report, dict):
        raise ValueError('invalid particle report')
    fields = ('status', 'stage', 'backend', 'reason', 'selected_recorded_instances',
              'candidate_instances', 'attempted_instances', 'rendered_instances',
              'objects', 'particle_tracks', 'point_lights', 'history_start_tick',
              'pre_shot_rendered_instances', 'unsupported_occurrences',
              'candidate_by_birth_scope', 'unsupported_by_birth_scope')
    result = {key: _safe(report[key]) for key in fields if key in report}
    unsupported = report.get('unsupported', [])
    if isinstance(unsupported, list):
        rows = []
        for item in unsupported[:50]:
            if isinstance(item, dict):
                row = {key: item[key] for key in ('resource', 'reason', 'occurrences', 'by_birth_scope')
                       if key in item}
                for key in ('resource', 'reason'):
                    if isinstance(row.get(key), str):
                        row[key] = row[key][:1024]
                rows.append(_safe(row))
        result['unsupported'] = rows
        result['omitted_unsupported_groups'] = max(0, len(unsupported) - 50)
    result['scope'] = 'Aggregate diagnostic summary; simulation plans, scene data and instance examples are excluded.'
    content = _json(result)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError('particle summary exceeds support ZIP size limit')
    return content


class ExportDiagnostics:
    """Finalize failure evidence without replacing the original export exception."""

    def __init__(self, args, log):
        self.args, self.log = args, log
        self.work = None
        self.effect_artifacts = None
        self.context = {"stage": "preflight", "timings": []}

    def _emit(self, message):
        try:
            self.log(message)
        except Exception:
            # A closed pipe or absent pythonw stdout must not change job failure.
            pass

    def finalize(self, error):
        warnings = []
        event = {"status": "failed", "path": None, "log_directory": None,
                 "work_directory": str(self.work) if self.work else None, "warnings": warnings}
        try:
            failure = dict(self.context, error=str(error), type=type(error).__name__)
            details = getattr(error, "details", None)
            if isinstance(details, dict) and details:
                failure["details"] = details
            failure["traceback"] = "".join(traceback.format_exception(error, limit=20))[-64000:]
            failure = _safe(failure)
            # The GUI cannot rely on failure.json: that write may be the failing
            # operation. Keep the original error separate from archive I/O errors.
            summary = {key: failure[key] for key in ("error", "type", "stage", "details") if key in failure}
            if len(_json(summary)) > MAX_FILE_BYTES:
                summary["details"] = {"omitted": "Structured details exceed the support event size limit"}
                warnings.append("Structured error details omitted from stdout event due to size limit")
            event["failure"] = summary
            entries = {}
            if self.work:
                blender_log = self.work / "scene.blender.log"
                try:
                    content = _read_evidence(blender_log, self.work, tail=True)
                    if content is not None:
                        entries["logs/scene.blender.log"] = content
                        failure["blender_log"] = str(blender_log)
                        failure["blender_log_tail"] = content.decode("utf-8", errors="replace")[-4000:]
                        event["failure"]["blender_log_tail"] = failure["blender_log_tail"]
                except Exception as exc:
                    warnings.append("Blender log unavailable: " + _redact(str(exc)))
                try:
                    # Never replace evidence from a prior attempt.
                    with (self.work / "failure.json").open("x", encoding="utf-8") as stream:
                        stream.write(_json(failure).decode("utf-8"))
                except Exception as exc:
                    warnings.append("Could not save work failure.json: " + _redact(str(exc)))
                self._emit(f"Diagnostic files retained at: {self.work}")
                for relative in ("installed_asset_report.json", "asset_diagnostic.json", "particle-preflight.json",
                                 "installed-assets/animation_asset_report.json",
                                 "extracted/animation_provenance.json", "scene.scene.json",
                                 "publish-report.json", "extracted/extraction.log"):
                    try:
                        content = _read_evidence(self.work / relative, self.work, tail=relative.endswith(".log"))
                        if content is not None:
                            entries["work/" + relative] = content
                    except Exception as exc:
                        warnings.append(relative + " omitted: " + _redact(str(exc)))
            if self.effect_artifacts:
                try:
                    content = _particle_coverage_summary(self.effect_artifacts)
                    if content is not None:
                        entries['work/particle-coverage-summary.json'] = content
                except Exception as exc:
                    warnings.append('Particle coverage summary omitted: ' + _redact(str(exc)))
            directory = log_directory()
            event["log_directory"] = str(directory)
            startup = Path(os.environ["RENDERLOCK_STARTUP_LOG"]).expanduser().resolve() if os.environ.get("RENDERLOCK_STARTUP_LOG") else directory / "startup.log"
            try:
                content = _read_evidence(startup, startup.parent, tail=True)
                if content is not None:
                    entries["logs/startup.log"] = content
            except Exception as exc:
                warnings.append("Startup log unavailable: " + _redact(str(exc)))
            entries["failure.json"] = _json(failure)
            entries["export-options.json"] = _json(_safe({name: getattr(self.args, name)
                                                          for name in _OPTION_NAMES if hasattr(self.args, name)}))
            entries["runtime.json"] = _json({"application": "RenderLock", "version": __version__,
                                             "python": sys.version, "platform": sys.platform,
                                             "created_utc": datetime.now(timezone.utc).isoformat()})
            entries["README.txt"] = (
                "RenderLock failed-export support ZIP\n\n"
                "Send this ZIP with your issue report. failure.json contains the error and stage;\n"
                "export-options.json records the selected options. Available logs/reports are bounded.\n"
                "No replay, game assets, scene, pose arrays, caches, environment or settings file is included.\n"
                "Known credential fields/patterns are redacted. Paths and replay filenames may remain;\n"
                "review before sharing. Original work files were retained separately.\n").encode("utf-8")
            entries["archive-notes.json"] = _json({"warnings": warnings,
                                                   "file_limit_bytes": MAX_FILE_BYTES,
                                                   "archive_limit_bytes": MAX_ARCHIVE_BYTES})
            if sum(len(content) for content in entries.values()) > MAX_ARCHIVE_BYTES:
                raise ValueError("Diagnostic metadata exceeds the support ZIP size limit")
            directory.mkdir(parents=True, exist_ok=True)
            name = "renderlock-support-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:12] + ".zip"
            destination = directory / name
            created = False
            try:
                with destination.open("xb") as stream:
                    created = True
                    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        for name, content in entries.items():
                            archive.writestr(name, content)
                with zipfile.ZipFile(destination) as archive:
                    if set(archive.namelist()) != set(entries) or archive.testzip() is not None:
                        raise OSError("Support ZIP verification failed")
            except Exception:
                if created:
                    try:
                        destination.unlink()
                    except OSError as exc:
                        warnings.append("Incomplete ZIP could not be removed: " + _redact(str(exc)))
                raise
            event.update(status="saved", path=str(destination))
            self._emit(f"Support ZIP: {destination}")
            for warning in warnings:
                self._emit("Support ZIP warning: " + warning)
        except Exception as exc:
            event["error"] = _redact(str(exc))
            self._emit("Support ZIP could not be saved: " + event["error"])
        self._emit(EVENT_PREFIX + json.dumps(event, ensure_ascii=True))
        return event
