"""Fresh typed model-rest input through the separate geometry helper.

The caller owns the output directory and existing stage/rollback lifecycle.
No cache, retries, native owner selection or effect admission is added here.
"""
import json
from pathlib import Path
import subprocess
import re

from .asset_converter import sha
from .game_assets import safe_resource_path
from .particle_model_assets import geometry_runtime
from .particle_model_rest import model_inverse_rest,POLICY
from .particle_runtime import particle_command,particle_environment

MAX_INVENTORY_BYTES=4*1024*1024


def collect_model_rest(provider,runtime,model,output):
    """Return validated metadata/provenance; never accept a stale output file."""
    output=Path(output).resolve()
    if output.exists():raise ValueError('Choose a fresh model-rest output path')
    if not output.parent.is_dir():raise ValueError('Model-rest output directory must exist')
    if not isinstance(model,dict):raise ValueError('Model-rest requires authored model constraints')
    resource=safe_resource_path(model.get('resource',''));names=model.get('names');expected=model.get('resource_sha256')
    parents=model.get('parents')
    if (not resource.endswith('.vmdl_c')or not isinstance(expected,str)or re.fullmatch('[0-9a-f]{64}',expected)is None
            or not isinstance(names,list)or not 0<len(names)<=4096
            or any(not isinstance(n,str)or not n for n in names)or len(set(names))!=len(names)
            or not isinstance(parents,list)or len(parents)!=len(names)
            or any(type(p)is not int or not -1<=p<len(names)for p in parents)):
        raise ValueError('Model-rest requires exact source identity and model bone order')
    if provider.resolve(resource).sha256!=expected:
        raise ValueError('Installed model differs from the selected rig source')
    probe,dotnet=geometry_runtime(runtime);parser=probe.parent/'ValveResourceFormat.dll'
    before={str(p):sha(p)for p in(probe,parser)}
    result=subprocess.run(particle_command(probe,dotnet)+['--inventory-model-rest',
        str(Path(provider.install_path)/'game/citadel/gameinfo.gi'),resource.removesuffix('_c'),str(output)],
        capture_output=True,text=True,timeout=60,env=particle_environment(probe,dotnet))
    if result.returncode!=0:
        raise ValueError('Authored model-rest helper failed: '+(result.stderr or result.stdout)[-2000:])
    if not output.is_file()or not 0<output.stat().st_size<=MAX_INVENTORY_BYTES:
        raise ValueError('Authored model-rest helper output is missing or exceeds bounds')
    with output.open('rb')as stream:raw=stream.read(MAX_INVENTORY_BYTES+1)
    if len(raw)>MAX_INVENTORY_BYTES:raise ValueError('Authored model-rest output grew beyond bounds')
    inventory=json.loads(raw)
    model_inverse_rest(inventory,resource=resource,resource_sha256=expected,names=names)
    if inventory['parents']!=model['parents']:
        raise ValueError('Authored model-rest and selected rig parent orders differ')
    if any(sha(Path(p))!=digest for p,digest in before.items()):
        raise ValueError('Model-rest helper runtime changed during extraction')
    import hashlib
    return inventory,dict(policy=POLICY,resource=resource,source_sha256=expected,
        helper_sha256=before[str(probe)],parser_sha256=before[str(parser)],
        inventory_sha256=hashlib.sha256(raw).hexdigest())
