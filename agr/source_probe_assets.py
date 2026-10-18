"""Strict installed probe inventory and bounded decode planning.

A plan is not lighting coverage proof. The eventual Blender consumer must check
evaluated surfaces against the supplied bounds and selected volumes.
"""
import itertools
import json
from pathlib import Path
import re
import subprocess
import time
import numpy as np
from .asset_converter import ConversionError,compiled,sha
from .installed_map import selected_world,_entity_properties
from .map_data import dump_map_data
from .source_lightmap import SHADER_RESOURCE,SHADER_SHA256
from .source_lighting_profiles import probe_layout,qualified_probe_helpers

WORLD_SHA256='4459031ca1fe3900c7c4516a4db552eb330369895b5b3aa228157695e5a0485b'
MAX_SELECTED_PROBES = 32
MAX_ATLAS_BYTES = 256 * 1024 * 1024
MAX_CUBE_BYTES = 256 * 1024 * 1024


class LightingRegionError(ConversionError):
    """A qualified lighting selection that exceeds the bounded preview workflow."""

    def __init__(self, message, **details):
        super().__init__(message)
        self.details = dict(code='lighting_region_unsupported', **details)


def validate_probe_budget(selected_count, atlas_bytes, *, max_decoded_bytes=MAX_ATLAS_BYTES):
    """Check both decoders before either writes lighting payloads."""
    cube_bytes = selected_count * sum((256 >> m)**2 * 6 * 16 for m in range(7))
    details = dict(selected_probe_count=selected_count, max_probe_count=MAX_SELECTED_PROBES,
                   atlas_bytes=atlas_bytes, atlas_limit_bytes=max_decoded_bytes,
                   cube_bytes=cube_bytes, cube_limit_bytes=MAX_CUBE_BYTES)
    remedy = ('Select fewer characters in Export, shorten the tick range, or reduce the Map margin. '
              'A follow camera does not restrict which characters are exported. '
              'For a character export without Source/NPR lighting, turn off the Lighting lab options '
              'and clear the optional Elagrimm shader field. An external map .blend does not supply this lighting data.')
    if not selected_count:
        raise LightingRegionError('No installed lighting probes intersect the selected map region. '
                                  'Check the installed map name and replay positions. ' + remedy, **details)
    if (selected_count > MAX_SELECTED_PROBES or atlas_bytes > max_decoded_bytes
            or cube_bytes > MAX_CUBE_BYTES):
        raise LightingRegionError(
            f'Source/NPR lighting region is too large: {selected_count} probes '
            f'(preview limit {MAX_SELECTED_PROBES}), {atlas_bytes / 1024**2:.1f} MiB atlas data '
            f'and {cube_bytes / 1024**2:.1f} MiB reflection data. ' + remedy, **details)

ATLAS_HELPER_HASHES={
    'ProbeAtlasDecode.dll':'9411eb82a939e63c86ce4c8023edca2f5774392d4c370ea91cac76b70140ee4a',
    'ValveResourceFormat.dll':'5475c86ef49bab2163e5f8e8e83f0e01514c4a2c07ec734abad5e42a28c18951',
    'TinyBCSharp.dll':'5e71c5b4d9ee0788a5ed22601efb054d499ff01a8ebec9f6083a1cdd4a957227',
    'SkiaSharp.dll':'061c87b06091baf9819d4985e67421b4692b45b434eae9a9e383354d74498c76',
    'libSkiaSharp.dll':'09fb4b1afee9810f127672baff0e050ed076b6d889b90adec7271914c3894125'}


def vector(value):
    result=np.asarray(value.split() if isinstance(value,str) else value,dtype=float)
    if result.shape!=(3,) or not np.isfinite(result).all():raise ConversionError('Invalid probe vector')
    return result


def rotation(angles):
    p,y,r=np.deg2rad(vector(angles));cp,cy,cr=np.cos([p,y,r]);sp,sy,sr=np.sin([p,y,r])
    return np.array([[1,0,0],[0,cr,sr],[0,-sr,cr]])@np.array([[cp,0,-sp],[0,1,0],[sp,0,cp]])@np.array([[cy,sy,0],[-sy,cy,0],[0,0,1]])


def bounds_array(bounds):
    bounds=np.asarray(bounds,dtype=float)
    if bounds.shape!=(3,2) or not np.isfinite(bounds).all() or np.any(bounds[:,1]<=bounds[:,0]):
        raise ConversionError('Invalid Source surface bounds')
    return bounds


def intersects_bounds(p,bounds):
    """Conservative rejection in probe-local axes, including triangle interiors."""
    bounds=bounds_array(bounds)
    if p.get('parentname') or p.get('uselocaloffset') or p.get('moveable'):
        raise ConversionError('Moving or parented probe selection is unqualified')
    corners=np.array(list(itertools.product(*bounds)))
    local=(corners-vector(p['origin']))@rotation(p['angles']).T
    low,high=vector(p['box_mins'])-.02,vector(p['box_maxs'])+.02
    if np.any(high<=low):raise ConversionError('Invalid probe volume extent')
    return bool(np.all(local.max(axis=0)>=low) and np.all(local.min(axis=0)<=high))


def probe_regions(p,world_sha256=WORLD_SHA256):
    dimensions,cube_depth=probe_layout(world_sha256)
    if p.get('classname')!='env_combined_light_probe_volume' or p.get('startdisabled') or p.get('light_probe_volume_from_cubemap'):
        raise ConversionError('Unsupported probe type/state')
    identity=str(p['hammeruniqueid'])
    if not re.fullmatch(r'[0-9]+(?::[0-9]+)*',identity):raise ConversionError('Invalid probe identity')
    if type(p.get('array_index')) is not int or not 0<=p['array_index']<cube_depth:
        raise ConversionError('Unqualified cubemap array index')
    if type(p.get('indoor_outdoor_level')) is not int:raise ConversionError('Invalid probe priority')
    if np.any(vector(p['edge_fade_dists'])<0):raise ConversionError('Invalid probe edge fade')
    size=[];offset=[]
    for axis,limit in zip('xyz',dimensions):
        n,o=p.get('light_probe_size_'+axis),p.get('light_probe_atlas_'+axis)
        if type(n) is not int or type(o) is not int or n<=0 or o<0 or o+n>limit:
            raise ConversionError('Unqualified probe atlas bounds')
        size.append(n);offset.append(o)
    start=np.maximum(np.array(offset)-1,0);end=np.minimum(np.array(offset)+size+1,dimensions);extent=end-start
    prefix=identity.replace(':','_');rows=[]
    for slab in range(6):
        first=start.copy();first[2]+=dimensions[2]*slab
        rows.append(dict(name=f'{prefix}-irradiance-{slab}',resource=compiled(p['lightprobetexture']),start=first.tolist(),size=extent.tolist()))
    rows.append(dict(name=prefix+'-shadow',resource=compiled(p['lightprobetexture_dlshd']),start=start.tolist(),size=extent.tolist()))
    return rows


def plan_probe_assets(provider,decoder,source_bounds,*,max_decoded_bytes=256*1024*1024):
    bounds=bounds_array(source_bounds)
    name=selected_world(provider);identity=provider.resource_identity(name)
    probe_layout(identity['sha256'])
    shader=provider.resource_identity(SHADER_RESOURCE)
    if shader['sha256']!=SHADER_SHA256:raise ConversionError('Probe shader requires requalification')
    world=dump_map_data(decoder,name,provider);inputs={name:identity,SHADER_RESOURCE:shader}
    queue=list(world['m_entityLumps']);seen=set();selected=[];inventory=0
    while queue:
        name=compiled(queue.pop(0))
        if name in seen:continue
        if len(seen)>=64:raise ConversionError('Probe entity traversal bound exceeded')
        seen.add(name);inputs[name]=provider.resource_identity(name)
        data=dump_map_data(decoder,name,provider);queue.extend(data.get('m_childLumps',[]))
        for entity in data.get('m_entityKeyValues',[]):
            p=_entity_properties(entity)
            if p.get('classname') not in ('env_light_probe_volume','env_combined_light_probe_volume'):continue
            inventory+=1
            if intersects_bounds(p,bounds):selected.append(p)
    if len({str(p['hammeruniqueid']) for p in selected})!=len(selected) or len({p['array_index'] for p in selected})!=len(selected):
        raise ConversionError('Duplicate selected probe identity/index')
    selected.sort(key=lambda p:(-p['indoor_outdoor_level'],p['array_index']))
    regions=[r for p in selected for r in probe_regions(p,identity['sha256'])]
    total=sum(int(np.prod(r['size']))*16 for r in regions)
    if type(max_decoded_bytes) is not int or max_decoded_bytes<=0:
        raise ConversionError('Invalid explicit probe storage budget')
    validate_probe_budget(len(selected), total, max_decoded_bytes=max_decoded_bytes)
    resources={r['resource'] for r in regions}|{compiled(p['cubemaptexture']) for p in selected}
    for resource in sorted(resources):inputs[resource]=provider.resource_identity(resource)
    return dict(schema_version=1,status='planned_not_applied',world_sha256=identity['sha256'],shader_sha256=SHADER_SHA256,
        source_bounds=bounds.tolist(),inputs=inputs,inventory_count=inventory,probes=selected,regions=regions,
        decoded_atlas_bytes=total,limits=['Conservative volume selection only; evaluated surface coverage must be verified before shading.',
        'Rigid authored transforms/priority are viewer mappings; runtime state and engine intensity constants unverified.'])


def prepare_probe_atlas(provider,decoder,plan,*,probe,dotnet):
    """Decode a fresh validated plan into the normal immutable derived cache."""
    fresh=plan_probe_assets(provider,decoder,plan['source_bounds'])
    if plan!=fresh:raise ConversionError('Probe plan differs from current installed evidence')
    probe=Path(probe).resolve();dotnet=Path(dotnet).resolve()
    if probe.name!='ProbeAtlasDecode.dll' or not dotnet.is_file():raise ConversionError('Explicit atlas helper and .NET required')
    qualified_probe_helpers(probe,ATLAS_HELPER_HASHES)
    helpers={p.relative_to(probe.parent).as_posix():sha(p) for p in sorted(probe.parent.rglob('*')) if p.is_file() and p.suffix in ('.dll','.json','.exe')}
    config=[]
    for resource in sorted({r['resource'] for r in plan['regions']}):
        resolved=provider.resolve(resource)
        if resolved.sha256!=plan['inputs'][resource]['sha256']:raise ConversionError('Atlas source changed')
        config.append(dict(path=str(resolved.local_path),sha256=resolved.sha256,
            regions=[{k:r[k] for k in ('name','start','size')} for r in plan['regions'] if r['resource']==resource]))
    def build(temp):
        try:
            path=temp/'decode-config.json';path.write_text(json.dumps(config,indent=2))
            with (temp/'decode.log').open('wb') as log:
                run=subprocess.run([str(dotnet),str(probe),str(path),str(temp/'decoded')],stdout=log,stderr=subprocess.STDOUT,timeout=600)
            if run.returncode:raise ConversionError('Probe atlas decoder failed')
            verify_decoded_regions(temp/'decoded',plan)
        except Exception as error:
            diagnostic=decoder.root.parent/'map-diagnostics'/f'probe-atlas-{time.time_ns()}'
            diagnostic.mkdir(parents=True,exist_ok=False)
            for name in ('decode-config.json','decode.log'):
                if (temp/name).is_file():(diagnostic/name).write_bytes((temp/name).read_bytes())
            (diagnostic/'error.txt').write_text(str(error))
            raise ConversionError(f'{error}; diagnostics: {diagnostic}') from error
    directory=decoder._entry('source_probe_atlas_v1',{k:v['sha256'] for k,v in plan['inputs'].items()},
        dict(bounds=plan['source_bounds'],regions=plan['regions'],helper=helpers,adapter_sha256=sha(Path(__file__))),build)
    rows=verify_decoded_regions(directory/'decoded',plan)
    return dict(schema_version=1,status='decoded_not_applied',plan=plan,regions=rows,
                limits=plan['limits']+['Atlas decoding only; cube preparation, shader wiring and evaluated-surface coverage are separate.'])


def verify_decoded_regions(directory,plan):
    directory=Path(directory);rows=json.loads((directory/'decode-report.json').read_text())
    expected={r['name']:r for r in plan['regions']}
    if len(rows)!=len(expected) or {r['name'] for r in rows}!=set(expected):raise ConversionError('Atlas output inventory mismatch')
    result=[]
    for row in rows:
        item=expected[row['name']];size=int(np.prod(item['size']))*16
        if row['start']!=item['start'] or row['size']!=item['size'] or row['source_sha256']!=plan['inputs'][item['resource']]['sha256'] or row['bytes']!=size:
            raise ConversionError('Atlas output layout/source mismatch')
        path=directory/(item['name']+'.rgba32f')
        if path.stat().st_size!=size:raise ConversionError('Atlas payload length mismatch')
        values=np.memmap(path,dtype='<f4',mode='r')
        if not np.isfinite(values).all() or np.any(values<0):raise ConversionError('Invalid decoded atlas values')
        del values
        result.append(dict(**item,path=str(path),sha256=sha(path),bytes=size,format='rgba32f_le_zyx'))
    return result
