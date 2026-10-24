import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from agr.asset_converter import AssetConverter, ConversionError, sha
from agr import source_lightmap_assets as assets


def world():
    return {'m_worldLightingInfo': {'m_nLightmapVersionNumber': 8, 'm_nLightmapGameVersionNumber': 4,
        'm_nLightmapEncoding': 1, 'm_bHasLightmaps': True, 'm_bCompressionEnabled': True,
        'm_vLightmapUvScale': [1.333313, 1.333313],
        'm_lightMaps': [f'maps/test/directional_irradiance_sh2_{c.lower()}.vtex' for c in assets.CHANNELS]}}


@pytest.mark.parametrize('key,value', [('m_nLightmapVersionNumber',9),('m_nLightmapGameVersionNumber',5),
    ('m_nLightmapEncoding',True),('m_bHasLightmaps',False),('m_bCompressionEnabled',False),
    ('m_vLightmapUvScale',[1,0]),('m_vLightmapUvScale',[1,float('nan')]),('m_vLightmapUvScale',[True,1])])
def test_world_encoding_changes_fail_closed(key,value):
    data=world();data['m_worldLightingInfo'][key]=value
    with pytest.raises(ConversionError):assets.qualify_world_lighting(data)


def test_texture_binding_requires_each_channel_once():
    data=world();assert list(assets.qualify_world_lighting(data))==list(assets.CHANNELS)
    maps=data['m_worldLightingInfo']['m_lightMaps'];maps.append(maps[0])
    with pytest.raises(ConversionError,match='exactly one DC'):assets.qualify_world_lighting(data)
    maps.pop();maps.pop()
    with pytest.raises(ConversionError,match='exactly one B'):assets.qualify_world_lighting(data)


def header(channel):
    return ('VTEX Version = 1\nWidth = 8192\nHeight = 8192\nDepth = 1\nNumMipLevels = 1\n'
            f'Format = {19 if channel=="DC" else 1} (VTEX_FORMAT)\nFlags = 0x00000040\n')


@pytest.mark.parametrize('before,after', [('Depth = 1','Depth = 2'),('Width = 8192','Width = 4096'),
    ('Format = 19','Format = 20'),('Flags = 0x00000040','Flags = 0x00000000'),('NumMipLevels = 1','NumMipLevels = 2')])
def test_unqualified_texture_layout_rejected(before,after):
    with pytest.raises(ConversionError):assets.qualify_texture_header(header('DC').replace(before,after),'DC')


def image_bytes(channel, size=8192):
    if channel=='DC':
        return struct.pack('<II',20000630,2)+b'dataWindow\0box2i\0'+struct.pack('<Iiiii',16,0,0,size-1,size-1)+b'\0'
    return b'\x89PNG\r\n\x1a\n'+struct.pack('>I',13)+b'IHDR'+struct.pack('>II',size,size)+b'\x08\x06\0\0\0'+b'\0'*4


@pytest.mark.parametrize('channel',assets.CHANNELS)
def test_decoded_header_size_and_truncation(tmp_path,channel):
    path=tmp_path/'image';path.write_bytes(image_bytes(channel))
    assert assets.decoded_image_size(path,channel)==[8192,8192]
    path.write_bytes(image_bytes(channel)[:12])
    with pytest.raises(ConversionError):assets.decoded_image_size(path,channel)


def fixture(tmp_path,monkeypatch):
    # These pinned decoder-pair cases model Windows binaries on any test host.
    monkeypatch.setattr(assets.sys, 'platform', 'win32')
    decoder=AssetConverter.__new__(AssetConverter)
    decoder.executable=tmp_path/'converter.exe';decoder.root=tmp_path/'derived-v1';decoder.root.mkdir()
    decoder.identity={'executable_sha256':assets.QUALIFIED_CONVERTER_SHA256,
                      'companions':{'libSkiaSharp.dll':assets.QUALIFIED_SKIA_SHA256}}
    decoder.stats={'cache_hits':0,'conversions':0}
    records={}
    for name in ['world.vwrld_c',assets.SHADER_RESOURCE,*[x+'_c' for x in world()['m_worldLightingInfo']['m_lightMaps']]]:
        path=tmp_path/Path(name).name;path.write_text(name)
        records[name]=SimpleNamespace(local_path=path,sha256=assets.SHADER_SHA256 if name==assets.SHADER_RESOURCE else sha(path))
    provider=SimpleNamespace(resolve=lambda name:records[name])
    monkeypatch.setattr(assets,'selected_world',lambda p:'world.vwrld_c')
    monkeypatch.setattr(assets,'dump_map_data',lambda *args:world())
    calls=[]
    def run(command,**kwargs):
        channel=Path(command[command.index('-i')+1]).stem.split('_')[-1].upper()
        if '-b' in command:return SimpleNamespace(returncode=0,stdout=header(channel).encode(),stderr=b'')
        calls.append(channel)
        Path(command[command.index('-o')+1]).write_bytes(image_bytes(channel))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(assets.subprocess,'run',run)
    return provider,decoder,records,calls


def test_cache_reuse_and_single_changed_source_rebuild(tmp_path,monkeypatch):
    provider,decoder,records,calls=fixture(tmp_path,monkeypatch)
    first=assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    assert calls==list(assets.CHANNELS)
    assert assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)==first
    assert calls==list(assets.CHANNELS)
    records['maps/test/directional_irradiance_sh2_r.vtex_c'].sha256='b'*64
    second=assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    assert calls==[*assets.CHANNELS,'R']
    assert second['lightmaps']['DC']==first['lightmaps']['DC']
    assert second['lightmaps']['R']['path']!=first['lightmaps']['R']['path']


def test_partial_selection_and_shader_identity_before_decoding(tmp_path,monkeypatch):
    provider,decoder,records,calls=fixture(tmp_path,monkeypatch)
    with pytest.raises(ConversionError,match='explicit'):assets.prepare_lightmap_assets(provider,decoder)
    records[assets.SHADER_RESOURCE].sha256='a'*64
    with pytest.raises(ConversionError,match='shader changed'):assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    assert calls==[]


def test_changed_cached_image_is_not_reused(tmp_path,monkeypatch):
    provider,decoder,records,calls=fixture(tmp_path,monkeypatch)
    first=assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    Path(first['lightmaps']['G']['path']).write_bytes(b'corrupt')
    rebuilt=assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    assert calls==[*assets.CHANNELS,'G']
    assert rebuilt==first


def test_changed_native_decoder_rejected_before_work(tmp_path,monkeypatch):
    provider,decoder,records,calls=fixture(tmp_path,monkeypatch)
    decoder.identity['companions']['libSkiaSharp.dll']='b'*64
    with pytest.raises(ConversionError,match='native image decoder'):
        assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    assert calls==[]


def test_decode_failure_retains_diagnostic_without_cache_success(tmp_path,monkeypatch):
    provider,decoder,records,calls=fixture(tmp_path,monkeypatch)
    def fail(*args,**kwargs):return SimpleNamespace(returncode=1,stdout=b'bad header',stderr=b'bad decode')
    monkeypatch.setattr(assets.subprocess,'run',fail)
    with pytest.raises(ConversionError,match='diagnostics retained'):assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    assert not list(decoder.root.glob('*/conversion.json'))
    assert list(tmp_path.glob('map-diagnostics/*/header.txt'))


def test_source_2_viewer_20_1_decoder_pair_qualified(tmp_path,monkeypatch):
    provider,decoder,records,calls=fixture(tmp_path,monkeypatch)
    decoder.identity={'executable_sha256':assets.QUALIFIED_CONVERTER_20_1_SHA256,
                      'companions':{'libSkiaSharp.dll':assets.QUALIFIED_SKIA_20_1_SHA256}}
    first=assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    assert calls==list(assets.CHANNELS)
    assert first['lightmaps']['DC']['size']==[8192,8192]


def test_decoder_and_image_companion_must_match_as_one_pair(tmp_path,monkeypatch):
    provider,decoder,records,calls=fixture(tmp_path,monkeypatch)
    decoder.identity={'executable_sha256':assets.QUALIFIED_CONVERTER_20_1_SHA256,
                      'companions':{'libSkiaSharp.dll':assets.QUALIFIED_SKIA_SHA256}}
    with pytest.raises(ConversionError,match='native image decoder'):
        assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    decoder.identity={'executable_sha256':assets.QUALIFIED_CONVERTER_SHA256,
                      'companions':{'libSkiaSharp.dll':assets.QUALIFIED_SKIA_20_1_SHA256}}
    with pytest.raises(ConversionError,match='native image decoder'):
        assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    decoder.identity={'executable_sha256':'0'*64,
                      'companions':{'libSkiaSharp.dll':assets.QUALIFIED_SKIA_SHA256}}
    with pytest.raises(ConversionError,match='texture decoder is not qualified'):
        assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    assert calls==[]


def test_source_2_viewer_20_1_names_dump_after_resource(tmp_path,monkeypatch):
    provider,decoder,records,calls=fixture(tmp_path,monkeypatch)
    def run(command,**kwargs):
        stem=Path(command[command.index('-i')+1]).stem
        channel=stem.split('_')[-1].upper()
        if '-b' in command:return SimpleNamespace(returncode=0,stdout=header(channel).encode(),stderr=b'')
        calls.append(channel)
        target=Path(command[command.index('-o')+1])
        (target.parent/(stem+target.suffix)).write_bytes(image_bytes(channel))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(assets.subprocess,'run',run)
    result=assets.prepare_lightmap_assets(provider,decoder,allow_partial=True)
    assert calls==list(assets.CHANNELS)
    for channel in assets.CHANNELS:
        image=Path(result['lightmaps'][channel]['path'])
        assert image.name==('lightmap.exr' if channel=='DC' else 'lightmap.png')
        assert assets.decoded_image_size(image,channel)==[8192,8192]
