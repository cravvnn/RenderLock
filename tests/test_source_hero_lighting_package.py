import copy,hashlib
import numpy as np
import pytest
from agr import source_hero_lighting_package as package
from agr.source_probe_assets import WORLD_SHA256,probe_regions
from agr.source_lighting_profiles import WORLD_6779
from agr.source_lightmap import SHADER_SHA256


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    monkeypatch.setattr(package,'validate_brdf_asset',lambda row,root:tmp_path/'brdf')
    p=dict(classname='env_combined_light_probe_volume',hammeruniqueid='1',array_index=4,indoor_outdoor_level=0,origin=[0,0,0],angles=[0,0,0],box_mins=[-10]*3,box_maxs=[10]*3,edge_fade_dists=[1]*3,light_probe_size_x=1,light_probe_size_y=1,light_probe_size_z=1,light_probe_atlas_x=1,light_probe_atlas_y=1,light_probe_atlas_z=1,lightprobetexture='ir.vtex',lightprobetexture_dlshd='shadow.vtex',cubemaptexture='cube.vtex')
    regions=probe_regions(p);rows=[]
    for r in regions:
        data=np.zeros((*r['size'],4),dtype='<f4').tobytes();path=tmp_path/(r['name']+'.rgba32f');path.write_bytes(data)
        rows.append(dict(**r,path=str(path),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data),format='rgba32f_le_zyx'))
    plan=dict(world_sha256=WORLD_SHA256,shader_sha256=SHADER_SHA256,source_bounds=[[-5,5]]*3,probes=[p],regions=regions,inputs={'cube.vtex_c':{'sha256':'a'*64}})
    faces=[]
    for m in range(7):
        n=256>>m;data=np.zeros((n,n,4),dtype='<f4').tobytes()
        for f in range(6):
            name=f'cube-4-face-{f}-mip-{m}.rgba32f';path=tmp_path/name;path.write_bytes(data)
            faces.append(dict(name=name,index=4,face=f,mip=m,width=n,height=n,components=n*n*4,sha256=hashlib.sha256(data).hexdigest(),path=str(path),format='rgba32f_le_yx'))
    atlas=dict(status='decoded_not_applied',plan=plan,regions=rows)
    cubes=dict(status='decoded_not_applied',world_sha256=WORLD_SHA256,arrays=[dict(resource='cube.vtex_c',source_sha256='a'*64,indices=[4],faces=faces,coefficients=[dict(index=4,coefficients=[0]*27)])])
    return package.build_hero_lighting_package(atlas,cubes,{},allow_partial=True,assumptions=package.REFERENCE_ASSUMPTIONS)


def test_valid_package_resolves_exact_assets(prepared,tmp_path):
    r=package.validate_hero_lighting_package(prepared,WORLD_SHA256,tmp_path)
    assert len(r['atlas'])==7 and len(r['cubes'])==42


@pytest.mark.parametrize('key,value',[('allow_partial',False),('allow_partial',1),('schema_version',True),('world_sha256','a'*64),('coverage_status','complete'),('assumptions',{})])
def test_implicit_or_unqualified_package_rejected(prepared,tmp_path,key,value):
    prepared[key]=value
    with pytest.raises(ValueError):package.validate_hero_lighting_package(prepared,WORLD_SHA256,tmp_path)


def test_plan_change_rejected(prepared,tmp_path):
    prepared['plan']['probes'][0]['origin']=[1,2,3]
    with pytest.raises(ValueError,match='plan identity'):package.validate_hero_lighting_package(prepared,WORLD_SHA256,tmp_path)


def test_coefficient_change_rejected(prepared,tmp_path):
    prepared['cubes'][0]['coefficients'][0]['coefficients'][0]=1
    with pytest.raises(ValueError,match='metadata identity'):package.validate_hero_lighting_package(prepared,WORLD_SHA256,tmp_path)


def test_payload_tampering_rejected(prepared,tmp_path):
    from pathlib import Path
    Path(prepared['atlas'][0]['path']).write_bytes(b'bad')
    with pytest.raises(ValueError,match='payload'):package.validate_hero_lighting_package(prepared,WORLD_SHA256,tmp_path)


def test_missing_cube_face_rejected_even_with_updated_metadata_digest(prepared,tmp_path):
    prepared['cubes'][0]['faces'].pop()
    prepared['assets_sha256']=package.canonical_sha({k:prepared[k] for k in ('atlas','cubes','brdf')})
    with pytest.raises(ValueError,match='inventory'):package.validate_hero_lighting_package(prepared,WORLD_SHA256,tmp_path)


def test_old_package_cannot_be_bound_to_new_world(prepared,tmp_path):
    with pytest.raises(ValueError,match='world mismatch'):
        package.validate_hero_lighting_package(prepared,WORLD_6779,tmp_path)


def test_sparse_package_requires_exact_bounded_batch_manifest(prepared,tmp_path):
    from agr.source_probe_assets import atlas_decode_batches,MAX_ATLAS_BYTES
    plan=prepared['plan']
    plan.update(source_regions=[dict(entity_id='actor',bounds=plan['source_bounds'])],
        atlas_batch_limit_bytes=MAX_ATLAS_BYTES,
        atlas_decode_batches=atlas_decode_batches(plan['probes'],WORLD_SHA256),
        decoded_atlas_bytes=sum(row['bytes'] for row in prepared['atlas']))
    prepared['plan_sha256']=package.canonical_sha(plan)
    assert len(package.validate_hero_lighting_package(prepared,WORLD_SHA256,tmp_path)['atlas'])==7
    plan['atlas_decode_batches'][0]['region_names'].pop()
    prepared['plan_sha256']=package.canonical_sha(plan)
    with pytest.raises(ValueError,match='batch inventory'):
        package.validate_hero_lighting_package(prepared,WORLD_SHA256,tmp_path)
