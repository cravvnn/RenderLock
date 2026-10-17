import hashlib
import numpy as np
import pytest
from agr import source_brdf_assets as brdf


def fixture(tmp_path,monkeypatch):
    layers=np.arange(3*64*64*4,dtype=np.float32).astype('<f2').tobytes()
    data=b'\0'*1048+layers
    source=tmp_path/'source';source.write_bytes(data)
    layer=layers[32768:65536]
    monkeypatch.setattr(brdf,'SOURCE_SHA256',hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(brdf,'LAYER_SHA256',hashlib.sha256(layer).hexdigest())
    return source,layer


def test_exact_layer_offset_and_half_conversion(tmp_path,monkeypatch):
    source,layer=fixture(tmp_path,monkeypatch)
    assert brdf.extract_layer(source)==layer
    path=tmp_path/'layer';path.write_bytes(layer)
    np.testing.assert_array_equal(brdf.read_layer(path),np.frombuffer(layer,dtype='<f2').reshape(64,64,4).astype('f4'))


def test_source_header_or_payload_change_fails_closed(tmp_path,monkeypatch):
    source,_=fixture(tmp_path,monkeypatch);data=bytearray(source.read_bytes());data[0]=1;source.write_bytes(data)
    with pytest.raises(ValueError,match='requalification'):brdf.extract_layer(source)


def test_asset_metadata_and_payload_are_both_checked(tmp_path,monkeypatch):
    _,layer=fixture(tmp_path,monkeypatch);path=tmp_path/'layer';path.write_bytes(layer)
    row=dict(path='layer',sha256=brdf.LAYER_SHA256,source_resource=brdf.RESOURCE,source_sha256=brdf.SOURCE_SHA256,size=[64,64],layer=1,format='rgba16f_le_source_rows')
    assert brdf.validate_brdf_asset(row,tmp_path)==path
    row['layer']=True
    with pytest.raises(ValueError,match='Unqualified'):brdf.validate_brdf_asset(row,tmp_path)
    row['layer']=1;path.write_bytes(b'bad')
    with pytest.raises(ValueError,match='payload'):brdf.validate_brdf_asset(row,tmp_path)
