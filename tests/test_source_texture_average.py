import struct
import pytest
from agr.source_texture_average import read_texture_average,validate_texture_average
from agr.asset_converter import ConversionError,sha


def texture(path):
    # A real resource directory and texture DATA header, no decoded texture
    # pixels are needed to check the authored compiler metadata.
    data=struct.pack('<HH4fHHHBBIII',1,0,.125,.25,.5,0,16,16,1,1,1,0,0,0)
    raw=struct.pack('<IHHII',28+len(data),12,1,8,1)+struct.pack('<4sII',b'DATA',8,len(data))+data
    path.write_bytes(raw)
    return path


def test_average_is_authored_metadata_and_payload_bound(tmp_path):
    path=texture(tmp_path/'color.vtex_c')
    assert read_texture_average(path)==[.125,.25,.5]
    row=dict(schema_version=1,mode='vtex_v1_compiler_average',resource='textures/a.vtex_c',
             path=str(path),source_sha256=sha(path),rgb=[.125,.25,.5])
    assert validate_texture_average(row)==row['rgb']
    row['rgb'][0]=.5
    with pytest.raises(ConversionError,match='metadata changed'):validate_texture_average(row)
    row['rgb'][0]=.125;path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ConversionError,match='source changed'):validate_texture_average(row)


@pytest.mark.parametrize('offset,fmt,value',[(4,'<H',11),(12,'<I',65),(20,'<I',0xffffffff),
                                          (24,'<I',0xffffffff),(28,'<H',2),(32,'<f',float('nan'))])
def test_unknown_or_out_of_bounds_source_never_supplies_pivot(tmp_path,offset,fmt,value):
    path=texture(tmp_path/'bad.vtex_c');raw=bytearray(path.read_bytes());struct.pack_into(fmt,raw,offset,value);path.write_bytes(raw)
    with pytest.raises(ConversionError):read_texture_average(path)
