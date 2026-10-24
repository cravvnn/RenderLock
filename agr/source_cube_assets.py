"""Bounded, identity-checked installed cubemap preparation for partial lighting."""
import json
from pathlib import Path
import subprocess
import time
import numpy as np
from .asset_converter import ConversionError,compiled,sha
from .source_probe_assets import ATLAS_HELPER_HASHES,plan_probe_assets,MAX_ATLAS_BYTES,MAX_CUBE_BYTES
from .source_lighting_profiles import qualified_probe_helpers
from .source_probe_layout import dimensions_for_plan

HELPER_SHA256='31dbaa54dd41abd268224ab0ee6ea5d93a61d26264a90e06df30d561154f1180'


def validate_cube_report(directory,source_sha256,indices,*,cube_depth=232):
    directory=Path(directory)
    if type(cube_depth) is not int or not 1<=cube_depth<=4096:
        raise ConversionError('Unqualified cube array depth')
    if not indices or len(indices)>4 or any(type(i) is not int or not 0<=i<cube_depth for i in indices) or len(set(indices))!=len(indices):
        raise ConversionError('Invalid bounded cube indices')
    report=json.loads((directory/'decode-report.json').read_text())
    if report.get('status')!='PASS' or report.get('source_sha256')!=source_sha256:
        raise ConversionError('Cubemap decoder source/status mismatch')
    expected={(i,f,m) for i in indices for f in range(6) for m in range(7)}
    rows=report.get('rows',[])
    keys=[]
    for row in rows:
        key=tuple(row.get(k) for k in ('index','face','mip'))
        if any(type(x) is not int for x in key):raise ConversionError('Invalid cube face/mip index')
        keys.append(key)
    if len(keys)!=len(expected) or set(keys)!=expected:raise ConversionError('Cubemap face/mip inventory mismatch')
    coeff=report.get('coefficients',[])
    if len(coeff)!=len(indices) or {r.get('index') for r in coeff}!=set(indices):raise ConversionError('Cubemap coefficient inventory mismatch')
    for row in coeff:
        c=np.asarray(row.get('coefficients'),dtype=float)
        if type(row['index']) is not int or c.shape!=(27,) or not np.isfinite(c).all():raise ConversionError('Invalid radiance coefficients')
    result=[]
    for row in rows:
        index,face,mip=row['index'],row['face'],row['mip'];size=256>>mip
        name=f'cube-{index}-face-{face}-mip-{mip}.rgba32f'
        if row.get('name')!=name or row.get('width')!=size or row.get('height')!=size or row.get('components')!=size*size*4:
            raise ConversionError('Cubemap decoded layout mismatch')
        path=directory/name
        if path.stat().st_size!=size*size*16 or sha(path)!=row.get('sha256'):raise ConversionError('Cubemap payload changed')
        values=np.memmap(path,dtype='<f4',mode='r')
        if not np.isfinite(values).all() or np.any(values<0):raise ConversionError('Invalid decoded cubemap values')
        del values
        result.append(dict(**row,path=str(path),format='rgba32f_le_yx'))
    return dict(faces=result,coefficients=coeff)


def prepare_cube_assets(provider,decoder,plan,*,probe,dotnet,max_decoded_bytes=256*1024*1024):
    if plan!=plan_probe_assets(provider,decoder,plan['source_bounds'],source_regions=plan.get('source_regions'),
            max_decoded_bytes=plan.get('atlas_batch_limit_bytes',MAX_ATLAS_BYTES)):raise ConversionError('Cube probe plan changed')
    probe=Path(probe).resolve();dotnet=Path(dotnet).resolve()
    required={k:v for k,v in ATLAS_HELPER_HASHES.items() if k!='ProbeAtlasDecode.dll'}|{'CubemapDecode.dll':HELPER_SHA256}
    if probe.name!='CubemapDecode.dll' or not dotnet.is_file():raise ConversionError('Explicit cube helper and .NET required')
    qualified_probe_helpers(probe,required)
    _,cube_depth=dimensions_for_plan(plan)
    from .helper_files import helper_file_inventory
    helpers=helper_file_inventory(probe.parent)
    selections={}
    for p in plan['probes']:selections.setdefault(compiled(p['cubemaptexture']),[]).append(p['array_index'])
    estimate=sum(len(v) for v in selections.values())*sum((256>>m)**2*6*16 for m in range(7))
    if type(max_decoded_bytes) is not int or not 0<max_decoded_bytes<=MAX_CUBE_BYTES:
        raise ConversionError('Invalid cube decoder byte budget')
    bytes_per_cube=sum((256>>m)**2*6*16 for m in range(7))
    batch_count=min(4,max_decoded_bytes//bytes_per_cube)
    if not batch_count or 'atlas_decode_batches' not in plan and estimate>max_decoded_bytes:
        raise ConversionError('Cube decode exceeds explicit storage budget')
    prepared=[]
    for resource,indices in sorted(selections.items()):
        resolved=provider.resolve(resource)
        if resolved.sha256!=plan['inputs'][resource]['sha256']:raise ConversionError('Cube source changed')
        indices=sorted(indices)
        for first in range(0,len(indices),batch_count):
            selected=indices[first:first+batch_count]
            config=dict(path=str(resolved.local_path),sha256=resolved.sha256,indices=selected)
            def build(temp):
                try:
                    path=temp/'decode-config.json';path.write_text(json.dumps(config,indent=2))
                    with (temp/'decode.log').open('wb') as log:
                        run=subprocess.run([str(dotnet),str(probe),str(path),str(temp/'decoded')],stdout=log,stderr=subprocess.STDOUT,timeout=600)
                    if run.returncode:raise ConversionError('Cube decoder failed')
                    validate_cube_report(temp/'decoded',resolved.sha256,selected,cube_depth=cube_depth)
                except Exception as error:
                    diagnostic=decoder.root.parent/'map-diagnostics'/f'cubemap-{time.time_ns()}';diagnostic.mkdir(parents=True,exist_ok=False)
                    for name in ('decode-config.json','decode.log'):
                        if (temp/name).is_file():(diagnostic/name).write_bytes((temp/name).read_bytes())
                    (diagnostic/'error.txt').write_text(str(error))
                    raise ConversionError(f'{error}; diagnostics: {diagnostic}') from error
            directory=decoder._entry('source_cube_array_v1',{resource:resolved.sha256},
                dict(indices=selected,helper=helpers,adapter_sha256=sha(Path(__file__))),build)
            prepared.append(dict(resource=resource,source_sha256=resolved.sha256,indices=selected,
                **validate_cube_report(directory/'decoded',resolved.sha256,selected,cube_depth=cube_depth)))
    return dict(schema_version=1,status='decoded_not_applied',world_sha256=plan['world_sha256'],
        decoded_bytes=estimate,arrays=prepared,limits=['Decoded raw faces/mips and radiance SH; filtering, material wiring and coverage are separate qualifications.'])
