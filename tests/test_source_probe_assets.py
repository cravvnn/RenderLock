import numpy as np
import pytest
from agr.source_probe_assets import intersects_bounds,probe_regions
from agr.source_probe_assets import verify_decoded_regions
from agr.source_lighting_profiles import WORLD_6779,probe_layout
import json


def probe():
    return dict(classname='env_combined_light_probe_volume',hammeruniqueid='1:2',array_index=4,indoor_outdoor_level=0,origin=[0,0,0],angles=[0,0,0],box_mins=[-10]*3,box_maxs=[10]*3,edge_fade_dists=[1]*3,light_probe_size_x=10,light_probe_size_y=20,light_probe_size_z=30,light_probe_atlas_x=1,light_probe_atlas_y=2,light_probe_atlas_z=3,lightprobetexture='maps/a/ir.vtex',lightprobetexture_dlshd='maps/a/shadow.vtex')


def test_intersecting_box_with_no_contained_corners_selected():
    assert intersects_bounds(probe(),[[-20,20]]*3)
    assert not intersects_bounds(probe(),[[11,12]]*3)


def test_rotated_bounds_select_conservatively():
    p=probe();p['box_mins']=[-20,-1,-1];p['box_maxs']=[20,1,1];p['angles']=[0,90,0]
    assert intersects_bounds(p,[[-.1,.1],[15,16],[-.1,.1]])
    assert not intersects_bounds(p,[[15,16],[-.1,.1],[-.1,.1]])


def test_parented_probe_cannot_be_spatially_ignored():
    p=probe();p['parentname']='moving'
    with pytest.raises(ValueError,match='parented'):intersects_bounds(p,[[100,101]]*3)


def test_atlas_regions_include_filter_border_and_six_slabs():
    rows=probe_regions(probe());assert len(rows)==7
    assert rows[0]['start']==[0,1,2] and rows[0]['size']==[12,22,32]
    assert rows[5]['start']==[0,1,1282]
    assert rows[6]['start']==[0,1,2]


def test_atlas_border_clips_at_texture_boundary():
    p=probe();p['light_probe_atlas_x']=0;p['light_probe_size_x']=240
    r=probe_regions(p)[0];assert r['start'][0]==0 and r['size'][0]==240


@pytest.mark.parametrize('key,value',[('array_index',232),('array_index',True),('light_probe_size_x',0),('light_probe_atlas_z',250),('hammeruniqueid','../bad'),('startdisabled',True)])
def test_unsupported_metadata_fails_closed(key,value):
    p=probe();p[key]=value
    with pytest.raises(ValueError):probe_regions(p)


def test_decode_payload_and_layout_validation(tmp_path):
    row=dict(name='1-shadow',resource='test',start=[0,0,0],size=[1,1,1])
    plan=dict(regions=[row],inputs={'test':{'sha256':'a'*64}})
    meta=dict(name=row['name'],start=row['start'],size=row['size'],source_sha256='a'*64,bytes=16)
    report=tmp_path/'decode-report.json';report.write_text(json.dumps([meta]))
    payload=tmp_path/'1-shadow.rgba32f';payload.write_bytes(np.ones(4,dtype='<f4').tobytes())
    assert verify_decoded_regions(tmp_path,plan)[0]['bytes']==16
    meta['start']=[1,0,0];report.write_text(json.dumps([meta]))
    with pytest.raises(ValueError,match='layout'):verify_decoded_regions(tmp_path,plan)
    meta['start']=[0,0,0];report.write_text(json.dumps([meta]))
    payload.write_bytes(np.full(4,np.nan,dtype='<f4').tobytes())
    with pytest.raises(ValueError,match='values'):verify_decoded_regions(tmp_path,plan)


def test_new_world_atlas_boundary_uses_236_rows_and_preserves_old_limit():
    p=probe();p['light_probe_atlas_y']=225;p['light_probe_size_y']=11
    rows=probe_regions(p,WORLD_6779)
    assert rows[0]['start'][1]==224 and rows[0]['size'][1]==12
    with pytest.raises(ValueError,match='atlas bounds'):probe_regions(p)
    p['light_probe_size_y']=12
    with pytest.raises(ValueError,match='atlas bounds'):probe_regions(p,WORLD_6779)


def test_new_world_cube_index_bound_does_not_reinterpret_old_packages():
    p=probe();p['array_index']=190
    assert len(probe_regions(p,WORLD_6779))==7
    p['array_index']=191
    with pytest.raises(ValueError,match='array index'):probe_regions(p,WORLD_6779)
    assert len(probe_regions(p))==7


def test_unknown_world_layout_still_requires_requalification():
    with pytest.raises(ValueError,match='requalification'):probe_layout('f'*64)
