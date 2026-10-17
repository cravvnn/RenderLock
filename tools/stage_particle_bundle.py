"""Stage a hash-verified local particle runtime and its collected notices."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def stage(runtime, notices, destination):
    runtime, notices, destination = map(lambda p: Path(p).resolve(), (runtime, notices, destination))
    if destination.exists():
        raise ValueError('Particle bundle destination already exists')
    inventory = json.loads((notices / 'inventory.json').read_text())
    supplements = json.loads((notices / 'embedded/sources.json').read_text())
    if inventory['missing_direct_license'] or supplements['missing']:
        raise ValueError('Collected runtime notices are incomplete')
    expected = set()
    entries = []
    for item in inventory['published_files']:
        relative = Path(item['path'])
        source = (runtime / relative).resolve()
        if not source.is_relative_to(runtime) or source in expected:
            raise ValueError('Unsafe or duplicated runtime inventory path')
        expected.add(source)
        if source.stat().st_size != item['bytes'] or hashlib.sha256(source.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError(f'Runtime changed since inventory: {relative}')
        if relative.suffix.lower() not in {'.pdb', '.xml'}:
            entries.append((source, relative))
    if expected != {p.resolve() for p in runtime.rglob('*') if p.is_file()}:
        raise ValueError('Uninventoried runtime files')
    for required in ('ParticleProbe.exe', 'ParticleProbe.dll', 'ParticleProbe.deps.json',
                     'ParticleProbe.runtimeconfig.json', 'ValveResourceFormat.dll', 'coreclr.dll'):
        if not any(relative.as_posix() == required for _, relative in entries):
            raise ValueError(f'Incomplete portable runtime: {required}')
    destination.mkdir(parents=True)
    for source, relative in entries:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copytree(notices, destination / 'notices')
    manifest = {'backend': 'experimental recorded particle simulation',
                'notice_review': 'Collected upstream notices; inventory is provenance, not an automated license certification.',
                'files': [{'path': p.relative_to(destination).as_posix(), 'bytes': p.stat().st_size,
                           'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                          for p in sorted(destination.rglob('*')) if p.is_file()]}
    (destination / 'bundle.json').write_text(json.dumps(manifest, indent=2))
    return {'status': 'STAGED', 'path': str(destination), 'files': len(manifest['files'])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--notices', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.runtime, args.notices, args.destination), indent=2))
