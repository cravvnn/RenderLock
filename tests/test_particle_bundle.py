import hashlib
import importlib.util
import json
from pathlib import Path
import pytest


@pytest.fixture
def bundle(tmp_path):
    path = Path(__file__).parents[1] / 'tools/stage_particle_bundle.py'
    spec = importlib.util.spec_from_file_location('particle_bundle', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = tmp_path / 'runtime'
    notices = tmp_path / 'notices'
    runtime.mkdir()
    (notices / 'embedded').mkdir(parents=True)
    files = []
    for name in ('ParticleProbe.exe', 'ParticleProbe.dll', 'ParticleProbe.deps.json',
                 'ParticleProbe.runtimeconfig.json', 'ValveResourceFormat.dll', 'coreclr.dll', 'debug.pdb'):
        data = name.encode()
        (runtime / name).write_bytes(data)
        files.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    (notices / 'inventory.json').write_text(json.dumps({'missing_direct_license': [], 'published_files': files}))
    (notices / 'embedded/sources.json').write_text(json.dumps({'missing': []}))
    return module, runtime, notices, tmp_path / 'destination'


def test_bundle_keeps_runtime_and_notices_but_excludes_debug_files(bundle):
    module, runtime, notices, dest = bundle
    result = module.stage(runtime, notices, dest)
    assert result['status'] == 'STAGED'
    assert (dest / 'coreclr.dll').read_bytes() == (runtime / 'coreclr.dll').read_bytes()
    assert not (dest / 'debug.pdb').exists()
    assert (dest / 'notices/embedded/sources.json').is_file()
    with pytest.raises(ValueError, match='already exists'):
        module.stage(runtime, notices, dest)


@pytest.mark.parametrize('change', ['tamper', 'extra', 'missing_notice'])
def test_bundle_rejects_changed_inputs_before_creating_destination(bundle, change):
    module, runtime, notices, dest = bundle
    if change == 'tamper':
        (runtime / 'coreclr.dll').write_bytes(b'changed')
    elif change == 'extra':
        (runtime / 'untracked.dll').touch()
    else:
        (notices / 'embedded/sources.json').write_text(json.dumps({'missing': ['font']}))
    with pytest.raises(ValueError):
        module.stage(runtime, notices, dest)
    assert not dest.exists()
