import hashlib,json
import numpy as np
import pytest
from agr.source_cube_assets import validate_cube_report


def fixture(tmp_path):
    rows=[]
    for mip in range(7):
        n=256>>mip;data=np.zeros((n,n,4),dtype='<f4').tobytes()
        for face in range(6):
            name=f'cube-4-face-{face}-mip-{mip}.rgba32f';(tmp_path/name).write_bytes(data)
            rows.append(dict(name=name,index=4,face=face,mip=mip,width=n,height=n,components=n*n*4,sha256=hashlib.sha256(data).hexdigest()))
    r=dict(status='PASS',source_sha256='a'*64,rows=rows,coefficients=[dict(index=4,coefficients=[0]*27)])
    return r


def save(path,report):
    (path/'decode-report.json').write_text(json.dumps(report))


def test_complete_cube_inventory_and_payload(tmp_path):
    r=fixture(tmp_path);save(tmp_path,r)
    assert len(validate_cube_report(tmp_path,'a'*64,[4])['faces'])==42


@pytest.mark.parametrize('change',['missing','duplicate','bool','path','coefficients','source','payload','nonfinite'])
def test_changed_cube_output_fails_closed(tmp_path,change):
    r=fixture(tmp_path)
    if change=='missing':r['rows'].pop()
    elif change=='duplicate':r['rows'][-1]=r['rows'][0]
    elif change=='bool':r['rows'][0]['face']=False
    elif change=='path':r['rows'][0]['name']='../outside'
    elif change=='coefficients':r['coefficients'][0]['coefficients']=[0]*26
    elif change=='source':r['source_sha256']='b'*64
    elif change=='payload':(tmp_path/r['rows'][0]['name']).write_bytes(b'bad')
    elif change=='nonfinite':
        p=tmp_path/r['rows'][0]['name'];data=bytearray(p.read_bytes());data[:4]=np.float32(np.nan).tobytes();p.write_bytes(data);r['rows'][0]['sha256']=hashlib.sha256(data).hexdigest()
    save(tmp_path,r)
    with pytest.raises(ValueError):validate_cube_report(tmp_path,'a'*64,[4])


@pytest.mark.parametrize('depth',[True,0,-1,4097])
def test_unqualified_depth_is_rejected_before_payload_read(tmp_path,depth):
    with pytest.raises(ValueError,match='depth'):
        validate_cube_report(tmp_path,'a'*64,[4],cube_depth=depth)


def test_6801_cube_depth_is_accepted_from_the_verified_layout(tmp_path):
    r=fixture(tmp_path);save(tmp_path,r)
    assert len(validate_cube_report(tmp_path,'a'*64,[4],cube_depth=171)['faces'])==42


def test_6779_out_of_range_cube_rejected_before_payload_read(tmp_path):
    with pytest.raises(ValueError,match='indices'):
        validate_cube_report(tmp_path,'a'*64,[191],cube_depth=191)


def test_6785_out_of_range_cube_rejected_before_payload_read(tmp_path):
    with pytest.raises(ValueError,match='indices'):
        validate_cube_report(tmp_path,'a'*64,[168],cube_depth=168)
