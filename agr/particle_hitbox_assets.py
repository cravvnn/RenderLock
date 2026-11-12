"""Fresh authored inventory from the simulation helper, never a cached result."""
import hashlib
import json
from pathlib import Path
import subprocess

from .asset_converter import compiled, sha
from .game_assets import safe_resource_path
from .particle_observations import _object, _constant
from .particle_runtime import particle_command, particle_environment
from .particle_silence_inputs import require

MAX_BYTES = 4 * 1024 * 1024


def collect_inventory(provider, runtime, model, output):
    output = Path(output).resolve()
    log = output.with_suffix('.log')
    require(not output.exists() and not log.exists() and output.parent.is_dir(), 'fresh hitbox inventory output required')
    resource = safe_resource_path(model.get('resource', ''))
    require(resource.endswith('.vmdl_c') and provider.resolve(resource).sha256 == model.get('resource_sha256'),
            'installed hitbox model differs from the rig')
    probe = Path(runtime['probe']).resolve()
    parser = probe.parent / 'ValveResourceFormat.dll'
    before = {str(p): sha(p) for p in (probe, parser)}
    result = subprocess.run(particle_command(probe, runtime.get('dotnet')) + ['--inventory-model-hitboxes',
        str(Path(runtime['install_path']) / 'game/citadel/gameinfo.gi'), resource.removesuffix('_c'), str(output)],
        capture_output=True, text=True, timeout=60, env=particle_environment(probe, runtime.get('dotnet')))
    with log.open('x', encoding='utf-8') as stream:
        stream.write(result.stdout + result.stderr)
    require(result.returncode == 0, 'authored hitbox inventory failed; see its retained log')
    require(output.is_file() and 0 < output.stat().st_size <= MAX_BYTES, 'missing or oversized hitbox inventory')
    with output.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, 'hitbox inventory grew beyond bounds')
    try:
        inventory = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError('Invalid authored hitbox inventory') from exc
    require(isinstance(inventory, dict) and type(inventory.get('schema_version')) is int
        and inventory['schema_version'] == 1 and inventory.get('model') == resource.removesuffix('_c')
        and inventory.get('model_bone_names') == model.get('names'), 'authored hitbox model/order differs')
    sources = inventory.get('sources')
    require(isinstance(sources, list) and 0 < len(sources) <= 33
        and sources[0].get('route') == 'model' and sources[0].get('resource') == inventory['model']
        and sources[0].get('sha256') == model['resource_sha256'], 'missing exact hitbox source inventory')
    require(all(provider.resolve(compiled(safe_resource_path(s['resource']))).sha256 == s.get('sha256')
                for s in sources), 'authored hitbox source changed')
    require(all(sha(Path(p)) == value for p, value in before.items()), 'hitbox helper changed during extraction')
    return inventory, dict(inventory_sha256=hashlib.sha256(raw).hexdigest(),
        helper_sha256=before[str(probe)], parser_sha256=before[str(parser)], sources=sources)
