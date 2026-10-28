import struct
from types import SimpleNamespace
import pytest
from agr import source_npr_assets as assets
from agr.asset_converter import ConversionError


def source(path,*,width=4096,height=4096,depth=1,fmt=20,mips=11,flags=0,extra=None,data_tail=b''):
    data=struct.pack('<HH4fHHHBBIII',1,flags,.1,.2,.3,0,width,height,depth,fmt,mips,0,8 if extra else 0,1 if extra else 0)
    if extra:
        kind,payload=extra
        data+=struct.pack('<III',kind,8,len(payload))+payload
    metadata=(struct.pack('<IHHII',28+len(data)+len(data_tail),12,1,8,1)+
              struct.pack('<4sII',b'DATA',8,len(data))+data+data_tail)
    payload=sum(max(1,((width>>i)+3)//4)*max(1,((height>>i)+3)//4)*16 for i in range(mips))
    with path.open('wb') as stream:
        stream.write(metadata);stream.truncate(len(metadata)+payload)
    return path


def test_complete_4k_bc7_is_bounded_before_decoding(tmp_path):
    path=source(tmp_path/'fourk.vtex_c')
    assert path.stat().st_size>16*1024*1024
    plan=assets.validate_npr_decode_source(path)
    assert (plan['width'],plan['height'],plan['depth'],plan['format'],plan['mips'])==(4096,4096,1,20,11)
    assert plan['decoded_float_bytes']==256*1024*1024


def test_complete_aligned_4k_bc5_is_bounded_before_decoding(tmp_path):
    path=source(tmp_path/'fourk-bc5.vtex_c',fmt=21)
    plan=assets.validate_npr_decode_source(path)
    assert (plan['stored_size'],plan['format'],plan['mips'])==([4096,4096],21,11)
    assert plan['decoded_float_bytes']==256*1024*1024


@pytest.mark.parametrize('kwargs',[
    {'width':4095}, {'mips':12}, {'data_tail':bytes(4)},
    {'extra':(4,struct.pack('<III',1,8,0))},
])
def test_unqualified_large_bc5_layouts_stay_withheld(tmp_path,kwargs):
    path=source(tmp_path/'unqualified-bc5.vtex_c',fmt=21,**kwargs)
    with pytest.raises(ConversionError,match='BC5 source requires aligned'):
        assets.validate_npr_decode_source(path)


@pytest.mark.parametrize('delta',[-1,1])
def test_large_bc5_requires_exact_complete_mip_bytes(tmp_path,delta):
    path=source(tmp_path/'bc5-truncated.vtex_c',fmt=21)
    with path.open('r+b') as stream:stream.truncate(path.stat().st_size+delta)
    with pytest.raises(ConversionError,match='exact bounded'):
        assets.validate_npr_decode_source(path)


@pytest.mark.parametrize('mutation',[
    (4,'<H',11),(12,'<I',65),(20,'<I',0xffffffff),(24,'<I',0xffffffff),
    (28,'<H',2),(30,'<H',16),(30,'<H',32),(30,'<H',64),
    (48,'<H',0),(48,'<H',8192),(52,'<H',2),(54,'<B',1),(55,'<B',14)])
def test_unknown_layout_or_unbounded_output_rejected_before_decoder(tmp_path,mutation):
    path=source(tmp_path/'bad.vtex_c');offset,fmt,value=mutation
    with path.open('r+b') as stream:stream.seek(offset);stream.write(struct.pack(fmt,value))
    with pytest.raises(ConversionError):assets.validate_npr_decode_source(path)


@pytest.mark.parametrize('delta',[-1,1,16*1024*1024])
def test_extended_payload_must_match_exact_bc7_mips(tmp_path,delta):
    path=source(tmp_path/'bad.vtex_c')
    with path.open('r+b') as stream:stream.truncate(path.stat().st_size+delta)
    with pytest.raises(ConversionError):assets.validate_npr_decode_source(path)


def test_source_identity_failure_precedes_any_decoder_operation(tmp_path,monkeypatch):
    path=source(tmp_path/'source.vtex_c');calls=[]
    monkeypatch.setattr(assets,'sha',lambda p:'decoder')
    monkeypatch.setattr(assets,'QUALIFIED_DECODER_PAIRS',{('decoder','skia')})
    decoder=SimpleNamespace(executable='decoder.exe',identity={'companions':{
        'libSkiaSharp.dll':'skia','libSkiaSharp.so':'skia','libSkiaSharp.dylib':'skia'}},
                            _entry=lambda *a:calls.append('decode'))
    provider=SimpleNamespace(resolve=lambda r:SimpleNamespace(sha256='changed' if r.endswith('.vtex_c') else 'material',local_path=path))
    plan=dict(status='sources_resolved_not_decoded',materials=[dict(material='a.vmat_c',source_sha256='material',
        textures={k:dict(resource='a.vtex_c',source_sha256='original') for k in assets.PARAMETERS})])
    with pytest.raises(ConversionError,match='source changed'):
        assets.decode_npr_material_assets(plan,provider,decoder)
    assert calls==[]


@pytest.mark.parametrize('platform',['win32','linux','darwin'])
def test_rejected_texture_reports_exact_resource_and_material_before_decode(tmp_path,monkeypatch,platform):
    from agr import source_lightmap_assets
    monkeypatch.setattr(source_lightmap_assets,'sys',SimpleNamespace(platform=platform))
    path=source(tmp_path/'unsafe-bc5.vtex_c',fmt=21,mips=12)
    calls=[]
    monkeypatch.setattr(assets,'sha',lambda p:'verified')
    monkeypatch.setattr(assets,'QUALIFIED_DECODER_PAIRS',{('verified','skia')})
    decoder=SimpleNamespace(executable='decoder.exe',identity={'companions':{
        'libSkiaSharp.dll':'skia','libSkiaSharp.so':'skia','libSkiaSharp.dylib':'skia'}},
                            _entry=lambda *a:calls.append('decode'))
    provider=SimpleNamespace(resolve=lambda resource:SimpleNamespace(sha256='verified',local_path=path))
    plan=dict(status='sources_resolved_not_decoded',materials=[dict(material='nurse.vmat_c',source_sha256='verified',
        textures={name:dict(resource='nurse-mask.vtex_c',source_sha256='verified') for name in assets.PARAMETERS})])
    with pytest.raises(ConversionError) as caught:
        assets.decode_npr_material_assets(plan,provider,decoder)
    assert caught.value.details=={'code':'npr_texture_decode_preflight_failed','material':'nurse.vmat_c',
        'parameter':assets.PARAMETERS[0],'resource':'nurse-mask.vtex_c'}
    assert calls==[]


def cropped_source(path,*,crop=(1,1)):
    data=bytearray(struct.pack('<HH4fHHHBBIII',1,0,.1,.2,.3,0,4,4,1,20,1,0,8,1))
    data+=struct.pack('<III',3,8,8)+struct.pack('<HHHH',0,*crop,0)
    metadata=struct.pack('<IHHII',28+len(data),12,1,8,1)+struct.pack('<4sII',b'DATA',8,len(data))+data
    path.write_bytes(metadata+bytes(16));return path


def test_authored_nonpoweroftwo_extent_matches_native_cropped_output(tmp_path):
    plan=assets.validate_npr_decode_source(cropped_source(tmp_path/'constant.vtex_c'))
    assert plan['stored_size']==[4,4] and (plan['width'],plan['height'])==(1,1)
    assert plan['decoded_float_bytes']==16


def test_authored_crop_larger_than_downsampled_image_does_not_expand_decode(tmp_path):
    plan=assets.validate_npr_decode_source(cropped_source(tmp_path/'downsampled.vtex_c',crop=(4096,4096)))
    assert (plan['width'],plan['height'])==(4,4)


@pytest.mark.parametrize('case',['directory','payload','short'])
def test_original_extent_and_extra_directory_remain_bounded(tmp_path,case):
    path=cropped_source(tmp_path/'bad-crop.vtex_c');raw=bytearray(path.read_bytes())
    offset,fmt,value={'directory':(60,'<I',0xffffffff),
                      'payload':(72,'<I',0xffffffff),'short':(76,'<I',2)}[case]
    struct.pack_into(fmt,raw,offset,value);path.write_bytes(raw)
    with pytest.raises(ConversionError):assets.validate_npr_decode_source(path)


@pytest.mark.parametrize('cached',[False,True])
def test_changed_decoder_output_dimensions_cannot_enter_material_package(tmp_path,monkeypatch,cached):
    path=cropped_source(tmp_path/'source.vtex_c');decoded=tmp_path/'entry'/'decoded';decoded.mkdir(parents=True)
    def write_wrong_png():
        # Dimensions only: pixel validation is a separate Blender stage.
        (decoded/'wrong.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II',2,2)+bytes(9))
    monkeypatch.setattr(assets,'sha',lambda p:'verified')
    monkeypatch.setattr(assets,'QUALIFIED_DECODER_PAIRS',{('verified','skia')})
    def run(*a,**k):write_wrong_png();return SimpleNamespace(returncode=0,stderr=b'')
    monkeypatch.setattr(assets.subprocess,'run',run)
    def entry(operation,inputs,settings,build):
        if cached:write_wrong_png()
        else:build(decoded.parent)
        return decoded.parent
    decoder=SimpleNamespace(executable='decoder.exe',identity={'companions':{
        'libSkiaSharp.dll':'skia','libSkiaSharp.so':'skia','libSkiaSharp.dylib':'skia'}},_entry=entry)
    provider=SimpleNamespace(resolve=lambda r:SimpleNamespace(sha256='verified',local_path=path))
    plan=dict(status='sources_resolved_not_decoded',materials=[dict(material='a.vmat_c',source_sha256='verified',
        textures={k:dict(resource='a.vtex_c',source_sha256='verified') for k in assets.PARAMETERS})])
    with pytest.raises(ConversionError,match='dimensions differ'):
        assets.decode_npr_material_assets(plan,provider,decoder)
