"""Failed jobs deliver bounded, usable support evidence without hiding the error."""
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from agr import cli, export_diagnostics as diagnostics
from agr.scene_package import save_scene
from blender_fixture import write_blender_completion


@pytest.fixture
def support_logs(tmp_path, monkeypatch):
    logs = tmp_path / "user-logs"
    monkeypatch.setattr(diagnostics, "log_directory", lambda: logs)
    monkeypatch.delenv("RENDERLOCK_STARTUP_LOG", raising=False)
    return logs


@pytest.fixture
def job(tmp_path, monkeypatch, support_logs):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "model.blend").write_bytes(b"model fixture; not loaded")
    (assets / "assets.json").write_text(json.dumps({"heroes": {"13": {"model_file": "model.blend"}}}))
    engine = tmp_path / "blender.exe"
    engine.write_bytes(b"fixture; not executed")
    scene = dict(schema_version=1, tick_rate=64, start_tick=10, end_tick=10,
                 entities=[dict(id="5:1", hero_id=13,
                                samples=[dict(tick=10, position=[1, 2, 3], body_angles=[0, 0, 0])])])
    segment = save_scene(scene, tmp_path / "input.json")
    args = cli.parser().parse_args(["convert", "--segment", str(segment), "--assets", str(assets),
                                    "--blender", str(engine), "--output", str(tmp_path / "shot.blend"),
                                    "--motion-only", "--no-effects", "--overwrite"])
    monkeypatch.setattr("agr.blender_runtime.check_blender", lambda *a, **k: {"fixture": True})
    monkeypatch.setattr(cli.subprocess, "run", lambda command, check: write_blender_completion(command))
    return args


def event_from_stdout(text):
    events = [json.loads(line[len(diagnostics.EVENT_PREFIX):]) for line in text.splitlines()
              if line.startswith(diagnostics.EVENT_PREFIX)]
    assert len(events) == 1
    return events[0]


def read_archive(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def test_failed_blender_job_delivers_log_and_original_error(job, support_logs, monkeypatch, capsys):
    original = RuntimeError("Fixture Blender failed on a missing material")
    original.details = {"resource": "heroes/model.vmat", "expected": 3}
    def fail(command, check):
        output = Path(command[command.index("--output") + 1])
        output.with_suffix(".blender.log").write_text("Exact fixture material error\n")
        raise original
    monkeypatch.setattr(cli.subprocess, "run", fail)
    before = vars(job).copy()
    with pytest.raises(RuntimeError) as caught:
        cli.convert(job)
    assert caught.value is original
    assert vars(job) == before
    stdout = capsys.readouterr().out
    event = event_from_stdout(stdout)
    path = Path(event["path"])
    assert event["status"] == "saved" and path.parent == support_logs
    summary = dict(event['failure'])
    assert summary.pop('blender_log_tail').splitlines() == ['Exact fixture material error']
    assert summary == dict(error=str(original), type="RuntimeError",
                           stage="build_blender_scene", details=original.details)
    assert f"Support ZIP: {path}" in stdout and path.is_absolute()
    files = read_archive(path)
    failure = json.loads(files["failure.json"])
    assert failure["error"] == str(original) and failure["stage"] == "build_blender_scene"
    assert failure["details"] == original.details
    assert "Exact fixture material error" in failure["blender_log_tail"]
    assert files["logs/scene.blender.log"].decode().splitlines() == ["Exact fixture material error"]
    assert (Path(event["work_directory"]) / "failure.json").is_file()
    assert not job.output.exists()


def test_save_failure_archives_independently_of_output_directory(job, support_logs, monkeypatch, capsys):
    job.output.write_bytes(b"previous scene")
    original = PermissionError("Output is locked by Blender")
    def fail(*args, **kwargs):
        raise original
    monkeypatch.setattr("agr.export_publish.publish_export", fail)
    with pytest.raises(PermissionError) as caught:
        cli.convert(job)
    assert caught.value is original and job.output.read_bytes() == b"previous scene"
    event = event_from_stdout(capsys.readouterr().out)
    files = read_archive(event["path"])
    assert Path(event["path"]).parent == support_logs
    assert json.loads(files["failure.json"])["stage"] == "publish_scene"
    assert json.loads(files["export-options.json"])["partial"] is False


def test_preflight_unwritable_output_still_produces_archive(job, support_logs, capsys):
    obstruction = job.output.parent / "not-a-directory"
    obstruction.write_bytes(b"original")
    job.output = obstruction / "shot.blend"
    with pytest.raises(FileExistsError):
        cli.convert(job)
    event = event_from_stdout(capsys.readouterr().out)
    assert event["status"] == "saved" and event["work_directory"] is None
    assert Path(event["path"]).parent == support_logs
    assert json.loads(read_archive(event["path"])["failure.json"])["stage"] == "preflight"
    assert obstruction.read_bytes() == b"original"


def test_work_failure_metadata_write_error_does_not_hide_export_error(job, monkeypatch, capsys):
    original = ValueError("Original replay failure")
    original.details = {"resource": "missing-model", "api_key": "must-redact"}
    def fail(*args):
        raise original
    monkeypatch.setattr(cli, "read_scene", fail)
    open_file = Path.open
    def block(path, *args, **kwargs):
        if path.name == "failure.json":
            raise PermissionError("Export destination is read-only")
        return open_file(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", block)
    with pytest.raises(ValueError) as caught:
        cli.convert(job)
    assert caught.value is original
    event = event_from_stdout(capsys.readouterr().out)
    assert event["status"] == "saved"
    assert event["failure"] == dict(error=str(original), type="ValueError", stage="read_segment",
                                    details={"resource": "missing-model", "api_key": "[REDACTED]"})
    assert any("Could not save work failure.json" in warning for warning in event["warnings"])
    assert json.loads(read_archive(event["path"])["failure.json"])["error"] == str(original)


@pytest.mark.parametrize("fault", ["directory", "archive_write", "verification"])
def test_archive_failure_is_honest_and_preserves_original_exception(job, support_logs, monkeypatch, capsys, fault):
    job.fps = 0  # Fail before any Blender, extraction or work-directory write.
    if fault == "directory":
        support_logs.write_bytes(b"original obstruction")
    elif fault == "archive_write":
        def fail(*args, **kwargs):
            raise OSError("Fixture diagnostic disk full")
        monkeypatch.setattr(zipfile.ZipFile, "writestr", fail)
    else:
        monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: "failure.json")
    with pytest.raises(ValueError, match="Export FPS"):
        cli.convert(job)
    stdout = capsys.readouterr().out
    event = event_from_stdout(stdout)
    assert event["status"] == "failed" and event["path"] is None and event["error"]
    assert event["failure"]["stage"] == "preflight" and "Export FPS" in event["failure"]["error"]
    assert "Support ZIP: " not in stdout and "Support ZIP could not be saved:" in stdout
    if fault == "directory":
        assert support_logs.read_bytes() == b"original obstruction"
    else:
        assert not list(support_logs.glob("*.zip"))


def test_archive_is_bounded_allowlisted_and_redacts_credentials(tmp_path, support_logs):
    support_logs.mkdir()
    (support_logs / "startup.log").write_text("token=startup-secret\nStartup fixture\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / "extracted").mkdir()
    (work / "installed-assets" / "clip_cache").mkdir(parents=True)
    (work / "installed-assets" / "clip_cache" / "private.dem").write_bytes(b"never share this")
    (work / "scene.blend").write_bytes(b"never share this scene")
    (work / "settings.json").write_text('{"password":"settings-secret"}')
    (work / "asset_diagnostic.json").write_text(json.dumps({"error": "model missing", "api_key": "report-secret"}))
    (work / "scene.blender.log").write_text("noise\n" * (diagnostics.MAX_FILE_BYTES // 2) +
                                            'password="log secret"\nAuthorization: Bearer bearer-secret\n'
                                            'Authorization: Basic basic-secret\nCookie: first=cookie-one; second=cookie-two\n'
                                            'Useful final error\n')
    (work / "installed_asset_report.json").write_bytes(b"x" * (diagnostics.MAX_FILE_BYTES + 1))
    args = SimpleNamespace(output=tmp_path / "shot.blend", fps=64, token="args-secret")
    error = RuntimeError("Failed https://user:pass@example.test/?token=url-secret")
    error.details = {"credential": "detail-secret", "cause": "missing material"}
    support = diagnostics.ExportDiagnostics(args, lambda line: None)
    support.work = work
    result = support.finalize(error)
    assert result["status"] == "saved"
    files = read_archive(result["path"])
    all_bytes = b"".join(files.values())
    for secret in (b"startup-secret", b"settings-secret", b"report-secret", b"log secret", b"bearer-secret",
                   b"args-secret", b"url-secret", b"detail-secret", b"user:pass", b"never share this",
                   b"basic-secret", b"cookie-one", b"cookie-two"):
        assert secret not in all_bytes
    assert b"Useful final error" in files["logs/scene.blender.log"]
    assert b"model missing" in files["work/asset_diagnostic.json"]
    assert "work/installed_asset_report.json" not in files
    assert any("size limit" in warning for warning in result["warnings"])
    assert sum(map(len, files.values())) <= diagnostics.MAX_ARCHIVE_BYTES
    assert (work / "scene.blend").read_bytes() == b"never share this scene"


def test_repeat_failures_preserve_existing_evidence_and_archives(tmp_path, support_logs):
    work = tmp_path / "work"
    work.mkdir()
    (work / "failure.json").write_bytes(b"original failure evidence")
    support = diagnostics.ExportDiagnostics(SimpleNamespace(), lambda line: None)
    support.work = work
    first = support.finalize(ValueError("first"))
    first_bytes = Path(first["path"]).read_bytes()
    second = support.finalize(ValueError("second"))
    assert first["path"] != second["path"]
    assert Path(first["path"]).read_bytes() == first_bytes
    assert (work / "failure.json").read_bytes() == b"original failure evidence"
    assert json.loads(read_archive(second["path"])["failure.json"])["error"] == "second"
    assert any("failure.json" in warning for warning in second["warnings"])


def test_particle_coverage_summary_survives_large_scene_report_without_private_payload(tmp_path, support_logs):
    work, effects = tmp_path/'work', tmp_path/'effects'
    work.mkdir()
    effects.mkdir()
    (work/'scene.scene.json').write_bytes(b'x' * (diagnostics.MAX_FILE_BYTES + 1))
    (work/'particle-preflight.json').write_text(json.dumps({'status': 'unavailable', 'checked_instances': 4}))
    (effects/'coverage.json').write_text(json.dumps({'status': 'unavailable', 'reason': 'No instance completed',
        'selected_recorded_instances': 4, 'attempted_instances': 0, 'rendered_instances': 0,
        'instances': [{'private_scene': 'do not include'}],
        'unsupported': [{'resource': f'particles/test{i}', 'reason': 'token=report-secret',
                         'occurrences': 1, 'instance_examples': ['private-instance']}
                        for i in range(60)]}))
    support = diagnostics.ExportDiagnostics(SimpleNamespace(), lambda line: None)
    support.work, support.effect_artifacts = work, effects
    event = support.finalize(ValueError('fixture'))
    assert event['status'] == 'saved'
    files = read_archive(event['path'])
    assert 'work/scene.scene.json' not in files
    assert json.loads(files['work/particle-preflight.json'])['checked_instances'] == 4
    summary = json.loads(files['work/particle-coverage-summary.json'])
    assert summary['status'] == 'unavailable' and summary['attempted_instances'] == 0
    assert len(summary['unsupported']) == 50 and summary['omitted_unsupported_groups'] == 10
    assert b'report-secret' not in b''.join(files.values())
    assert b'private-instance' not in b''.join(files.values())
    assert b'do not include' not in b''.join(files.values())


def test_oversized_particle_report_is_omitted_without_hiding_original_failure(tmp_path, support_logs):
    effects = tmp_path/'effects'
    effects.mkdir()
    (effects/'coverage.json').write_bytes(b'x' * (diagnostics.MAX_PARTICLE_REPORT_BYTES + 1))
    support = diagnostics.ExportDiagnostics(SimpleNamespace(), lambda line: None)
    support.effect_artifacts = effects
    event = support.finalize(ValueError('original'))
    assert event['status'] == 'saved' and event['failure']['error'] == 'original'
    assert any('particle report exceeds' in warning for warning in event['warnings'])
    assert 'work/particle-coverage-summary.json' not in read_archive(event['path'])


def test_archive_name_collision_never_replaces_or_deletes_original(tmp_path, support_logs, monkeypatch):
    open_file = Path.open
    originals = []
    def collision(path, *args, **kwargs):
        if path.suffix == ".zip" and args and args[0] == "xb":
            path.write_bytes(b"original archive")
            originals.append(path)
        return open_file(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", collision)
    result = diagnostics.ExportDiagnostics(SimpleNamespace(), lambda line: None).finalize(ValueError("fixture"))
    assert result["status"] == "failed" and result["path"] is None
    assert len(originals) == 1 and originals[0].read_bytes() == b"original archive"


def test_closed_stdout_does_not_interrupt_diagnostic_retention(support_logs):
    def broken_pipe(line):
        raise BrokenPipeError("GUI closed")
    result = diagnostics.ExportDiagnostics(SimpleNamespace(), broken_pipe).finalize(ValueError("original"))
    assert result["status"] == "saved"
    assert Path(result["path"]).is_file()


def test_success_does_not_create_failure_archive(job, support_logs, capsys):
    assert cli.convert(job)["status"] == "converted"
    assert not support_logs.exists()
    assert diagnostics.EVENT_PREFIX not in capsys.readouterr().out


def test_user_log_location_matches_windows_launchers(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.sys, "platform", "win32")
    monkeypatch.delenv("RENDERLOCK_STARTUP_LOG", raising=False)
    monkeypatch.delenv("RENDERLOCK_LOG_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert diagnostics.log_directory() == (tmp_path / "local" / "RenderLock" / "logs").resolve()
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setattr(diagnostics.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    assert diagnostics.log_directory() == (tmp_path / "temp" / "RenderLock" / "logs").resolve()
    monkeypatch.setenv("RENDERLOCK_LOG_DIR", str(tmp_path / "launcher-logs"))
    assert diagnostics.log_directory() == (tmp_path / "launcher-logs").resolve()
    monkeypatch.setenv("RENDERLOCK_STARTUP_LOG", str(tmp_path / "development-logs" / "RenderLock_startup.log"))
    assert diagnostics.log_directory() == (tmp_path / "development-logs").resolve()
