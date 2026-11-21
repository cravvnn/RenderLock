"""Startup preflight rejects ineffective executables before costly export work."""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agr import blender_runtime as runtime


def reply(monkeypatch, *, version=(5, 2, 1), capabilities=None, code=0, text=None):
    report = dict(version=list(version), version_string='.'.join(map(str, version)),
                  capabilities=dict.fromkeys(runtime.CORE_CAPABILITIES, True))
    report['capabilities'].update(eevee=True, npr_nodes=True)
    if capabilities:
        report['capabilities'].update(capabilities)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=code, stdout=text if text is not None else
                               'Blender banner\n' + runtime.MARKER + json.dumps(report) + '\nBlender quit\n')
    monkeypatch.setattr(runtime.subprocess, 'run', run)
    return calls


def test_capability_protocol_accepts_compatible_new_version_without_exact_lock(monkeypatch):
    calls = reply(monkeypatch, version=(6, 0, 0))
    assert runtime.check_blender(sys.executable)['version'] == [6, 0, 0]
    argv, kwargs = calls[0]
    assert '--factory-startup' in argv and '--disable-autoexec' in argv
    assert argv[argv.index('--python-exit-code') + 1] == '1'
    assert kwargs['stdin'] == subprocess.DEVNULL and kwargs['timeout'] == 45


def test_shader_capabilities_required_only_when_shader_is_selected(monkeypatch):
    reply(monkeypatch, capabilities={'eevee': False})
    assert runtime.check_blender(sys.executable)['capabilities']['eevee'] is False
    with pytest.raises(runtime.BlenderPreflightError, match='eevee'):
        runtime.check_blender(sys.executable, character_shader=True)


def test_missing_core_api_is_actionable(monkeypatch):
    reply(monkeypatch, capabilities={'gltf_import': False})
    with pytest.raises(runtime.BlenderPreflightError, match='gltf_import') as exc:
        runtime.check_blender(sys.executable)
    assert 'Setup' in str(exc.value)


def test_exit_zero_without_valid_report_is_rejected(monkeypatch):
    reply(monkeypatch, text=runtime.MARKER + '{invalid json')
    with pytest.raises(runtime.BlenderPreflightError, match='capability report'):
        runtime.check_blender(sys.executable)


def test_startup_error_and_timeout_explain_next_action(monkeypatch):
    reply(monkeypatch, code=1, text='Cannot load Blender DLL')
    with pytest.raises(runtime.BlenderPreflightError, match='Cannot load Blender DLL'):
        runtime.check_blender(sys.executable)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(runtime.subprocess, 'run', timeout)
    with pytest.raises(runtime.BlenderPreflightError, match='Open Blender separately'):
        runtime.check_blender(sys.executable, timeout=2)
