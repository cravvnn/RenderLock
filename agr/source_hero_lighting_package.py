"""Identity contract for explicitly partial hero probe/reflection lighting.

Asset validity does not imply surface coverage. The Blender consumer must check
its evaluated surfaces against the bound selection before applying this package.
"""
import copy
import hashlib
import json
from pathlib import Path
import numpy as np
from .asset_converter import ConversionError,compiled,sha
from .source_probe_assets import (WORLD_SHA256,bounds_array,probe_regions,intersects_bounds,
                                 selection_regions,atlas_decode_batches,MAX_ATLAS_BYTES)
from .source_lightmap import SHADER_SHA256,SHADER_RESOURCE
from .source_brdf_assets import validate_brdf_asset
from .source_probe_layout import dimensions_for_plan


REFERENCE_ASSUMPTIONS=dict(probe_intensity='unit_reference',cube_tint='unit_reference',
    normalization_ceiling=[2.0,1.0],normalization_source='local_viewer_reference',
    volume_blend='continuous_reference',cube_filter='ideal_seamless_reference')


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def build_hero_lighting_package(atlas,cubes,brdf,*,allow_partial=False,assumptions=None):
    if allow_partial is not True or assumptions!=REFERENCE_ASSUMPTIONS:
        raise ConversionError('Explicit partial hero lighting and reference assumptions required')
    if atlas.get('status')!='decoded_not_applied' or cubes.get('status')!='decoded_not_applied':
        raise ConversionError('Decoded hero lighting assets required')
    plan=atlas['plan']
    if cubes['world_sha256']!=plan['world_sha256']:raise ConversionError('Cube/atlas world mismatch')
    package=dict(schema_version=1,mode='partial_hero_indirect_reference',allow_partial=True,
        world_sha256=plan['world_sha256'],shader_sha256=plan['shader_sha256'],plan=copy.deepcopy(plan),
        plan_sha256=canonical_sha(plan),atlas=copy.deepcopy(atlas['regions']),cubes=copy.deepcopy(cubes['arrays']),
        brdf=copy.deepcopy(brdf),assumptions=copy.deepcopy(assumptions),
        coverage_status='requires_evaluated_surface_validation',
        limits=['Raw unit probe intensity and cubemap tint; viewer normalization ceiling (2,1).',
                'Continuous blend differs from installed cutoff/fallback; ideal cube filtering differs from hardware.',
                'NPR lighting, rim/outline/transmissive response and material ambient occlusion are not reproduced.',
                'Direct lighting, full Source material semantics, runtime light state and game visual parity unverified.'])
    package['assets_sha256']=canonical_sha(dict(atlas=package['atlas'],cubes=package['cubes'],brdf=package['brdf']))
    validate_hero_lighting_package(package,plan['world_sha256'],Path.cwd())
    return package


def validate_hero_lighting_package(package,world_sha256,manifest_dir):
    p=package
    if not isinstance(p,dict) or type(p.get('schema_version')) is not int or p['schema_version']!=1 or p.get('mode')!='partial_hero_indirect_reference' or p.get('allow_partial') is not True:
        raise ConversionError('Unsupported or implicit hero lighting package')
    if p.get('world_sha256')!=world_sha256:
        raise ConversionError('Hero lighting world mismatch')
    if p.get('assumptions')!=REFERENCE_ASSUMPTIONS or p.get('coverage_status')!='requires_evaluated_surface_validation':
        raise ConversionError('Unqualified hero lighting assumptions/coverage')
    if canonical_sha(dict(atlas=p['atlas'],cubes=p['cubes'],brdf=p['brdf']))!=p.get('assets_sha256'):
        raise ConversionError('Hero asset metadata identity changed')
    plan=p['plan']
    layout=plan.get('probe_layout')
    if layout is None:
        # Legacy packages keep the old pinned probe-shader contract.
        if p.get('shader_sha256')!=SHADER_SHA256:
            raise ConversionError('Hero lighting shader mismatch')
    else:
        shader=plan.get('inputs',{}).get(SHADER_RESOURCE,{}).get('sha256')
        if (not isinstance(shader,str) or p.get('shader_sha256')!=shader
                or plan.get('shader_sha256')!=shader):
            raise ConversionError('Hero lighting shader identity changed')
    if canonical_sha(plan)!=p.get('plan_sha256') or plan.get('world_sha256')!=world_sha256:
        raise ConversionError('Hero probe plan identity changed')
    bounds=bounds_array(plan['source_bounds']);probes=plan['probes']
    sparse=selection_regions(bounds,plan.get('source_regions'))
    batched='atlas_decode_batches' in plan
    if batched != (sparse is not None):raise ConversionError('Incomplete sparse hero decode contract')
    _,cube_depth=dimensions_for_plan(plan)
    if not 1<=len(probes)<=(cube_depth if batched else 32) or len({str(q['hammeruniqueid']) for q in probes})!=len(probes) or len({q['array_index'] for q in probes})!=len(probes):
        raise ConversionError('Invalid hero probe inventory')
    regions_to_check=[row['bounds'] for row in sparse] if sparse else [bounds]
    if any(not any(intersects_bounds(q,region) for region in regions_to_check) for q in probes):raise ConversionError('Probe outside bound selection')
    layout_or_world=layout if layout is not None else world_sha256
    if batched and plan['atlas_decode_batches']!=atlas_decode_batches(probes,layout_or_world,
            max_decoded_bytes=plan.get('atlas_batch_limit_bytes',0)):
        raise ConversionError('Hero atlas batch inventory or allocation bound changed')
    regions=[r for q in probes for r in probe_regions(q,layout_or_world)]
    if regions!=plan['regions']:raise ConversionError('Hero atlas plan layout changed')
    expected={r['name']:r for r in regions};rows=p['atlas']
    if len(rows)!=len(expected) or {r['name'] for r in rows}!=set(expected):raise ConversionError('Hero atlas inventory changed')
    total=0
    def payload(row,length):
        path=Path(row['path'])
        if not path.is_absolute():path=Path(manifest_dir)/path
        if path.stat().st_size!=length or sha(path)!=row.get('sha256'):raise ConversionError('Hero lighting payload changed')
        values=np.memmap(path,dtype='<f4',mode='r')
        if not np.isfinite(values).all() or np.any(values<0):raise ConversionError('Invalid hero lighting pixels')
        del values
        return path.resolve()
    resolved_atlas={}
    for row in rows:
        e=expected[row['name']]
        if any(row.get(k)!=v for k,v in e.items()) or row.get('format')!='rgba32f_le_zyx':raise ConversionError('Hero atlas metadata changed')
        length=int(np.prod(e['size']))*16;total+=length
        if row.get('bytes')!=length:raise ConversionError('Hero atlas size changed')
        resolved_atlas[row['name']]=payload(row,length)
    if not batched and total>MAX_ATLAS_BYTES:raise ConversionError('Hero atlas storage bound exceeded')
    if batched and total!=plan.get('decoded_atlas_bytes'):raise ConversionError('Batched hero atlas byte count changed')
    selection={(compiled(q['cubemaptexture']),q['array_index']) for q in probes}
    seen=set();resolved_cubes={};coefficients={}
    for array in p['cubes']:
        if not 1<=len(array['indices'])<=4:raise ConversionError('Hero cube batch bound exceeded')
        resource=array['resource'];source=plan['inputs'].get(resource)
        if source is None or source['sha256']!=array['source_sha256']:raise ConversionError('Hero cube source mismatch')
        for index in array['indices']:
            key=(resource,index)
            if type(index) is not int or key not in selection or key in seen:raise ConversionError('Hero cube index mismatch')
            seen.add(key)
        expected_faces={(i,f,m) for i in array['indices'] for f in range(6) for m in range(7)}
        faces=array['faces'];keys=[(r['index'],r['face'],r['mip']) for r in faces]
        if len(keys)!=len(expected_faces) or set(keys)!=expected_faces or any(type(x) is not int for key in keys for x in key):raise ConversionError('Hero cube face/mip inventory changed')
        for row in faces:
            n=256>>row['mip'];name=f"cube-{row['index']}-face-{row['face']}-mip-{row['mip']}.rgba32f"
            if row['name']!=name or row['width']!=n or row['height']!=n or row['components']!=n*n*4 or row.get('format')!='rgba32f_le_yx':raise ConversionError('Hero cube layout changed')
            resolved_cubes[(resource,row['index'],row['face'],row['mip'])]=payload(row,n*n*16)
        coeff=array['coefficients']
        if len(coeff)!=len(array['indices']) or {r['index'] for r in coeff}!=set(array['indices']):raise ConversionError('Hero radiance coefficient inventory changed')
        for row in coeff:
            values=np.asarray(row['coefficients'],dtype=float)
            if values.shape!=(27,) or not np.isfinite(values).all():raise ConversionError('Invalid hero radiance coefficients')
            coefficients[(resource,row['index'])]=values
    if seen!=selection:raise ConversionError('Missing hero cubemap')
    return dict(atlas=resolved_atlas,cubes=resolved_cubes,coefficients=coefficients,
                brdf=validate_brdf_asset(p['brdf'],manifest_dir))
