import copy
from pathlib import Path
import pytest

from agr.snapshot_bone_package import CONVENTION, bind_snapshot_capture, validate_snapshot_capture


@pytest.fixture
def fixture(tmp_path):
    entity = {'id':'86:46','hero_id':67,'samples':[{'tick':10,'position':[1,2,3]}]}
    for field in ('model_file','skeleton_file','pose_file'):
        path=tmp_path/field;path.write_bytes(field.encode());entity[field]=str(path)
    scene={'start_tick':10,'end_tick':11,'tick_rate':64,'metadata':{'demo_sha256':'a'*64},'entities':[entity]}
    capture={'schema_version':1,'matrix_convention':CONVENTION,'unit_scale':.0254,
             'start_tick':10,'end_tick':11,'tick_rate':64,'bone_names':['book_cover_0_R'],
             'frames':[{'tick':t,'bones':{'book_cover_0_R':[[1,0,0,t],[0,1,0,2],[0,0,1,3]]}} for t in (10,11)]}
    return scene,capture


def test_capture_roundtrip_and_caller_mutation_does_not_modify_package(fixture):
    scene,capture=fixture
    package=bind_snapshot_capture(capture,scene,'86:46','b'*64)
    capture['frames'][0]['bones']['book_cover_0_R'][0][3]=900
    validate_snapshot_capture(package,scene,'86:46','b'*64)
    assert package['capture']['frames'][0]['bones']['book_cover_0_R'][0][3]==10


@pytest.mark.parametrize('change',['replay','track','pose','model','skeleton','timing','entity','rig','matrix'])
def test_changed_binding_is_rejected(fixture,change):
    scene,capture=fixture;package=bind_snapshot_capture(capture,scene,'86:46','b'*64)
    entity_id,rig='86:46','b'*64
    if change=='replay':scene['metadata']['demo_sha256']='c'*64
    elif change=='track':scene['entities'][0]['samples'][0]['position'][0]+=1
    elif change in ('pose','model','skeleton'):Path(scene['entities'][0][change+'_file']).write_bytes(b'changed')
    elif change=='timing':scene['end_tick']+=1
    elif change=='entity':entity_id='86:47'
    elif change=='rig':rig='c'*64
    elif change=='matrix':package['capture']['frames'][0]['bones']['book_cover_0_R'][0][3]+=1
    with pytest.raises(ValueError):validate_snapshot_capture(package,scene,entity_id,rig)


@pytest.mark.parametrize('change',['missing_tick','duplicate_tick','missing_bone','scale','reflection','nan','shape','coordinates','missing_demo','duplicate_entity'])
def test_invalid_capture_cannot_be_bound(fixture,change):
    scene,capture=fixture;m=capture['frames'][0]['bones']['book_cover_0_R']
    if change=='missing_tick':capture['frames'].pop()
    elif change=='duplicate_tick':capture['frames'][1]['tick']=10
    elif change=='missing_bone':capture['frames'][1]['bones'].clear()
    elif change=='scale':m[0][0]=2
    elif change=='reflection':m[0][0]=-1
    elif change=='nan':m[0][3]=float('nan')
    elif change=='shape':m.pop()
    elif change=='coordinates':scene['metadata']['coordinate_system']={'origin':[1,0,0]}
    elif change=='missing_demo':scene['metadata'].clear()
    elif change=='duplicate_entity':scene['entities'].append(copy.deepcopy(scene['entities'][0]))
    with pytest.raises(ValueError):bind_snapshot_capture(capture,scene,'86:46','b'*64)
