import copy
import hashlib
import json
import struct
import zlib
from types import SimpleNamespace
import pytest
from agr.effect_assets import EffectAssetAdapter
from agr.asset_converter import ConversionError
from agr.installed_effects import UnsupportedEffect
from agr.game_assets import GameAssetError
from agr.particle_texture_animation import validate_asset_sheet

def sheet():
    return dict(schema_version=1,policy='agr_source_particle_sheet_v1',input_sha256='a'*64,
        image='atlas.png',width=2,height=1,sequences=[dict(index=0,clamp=False,total_time=1,
        frames=[dict(duration=.5,min=[0,0],max=[.5,1]),dict(duration=.5,min=[.5,0],max=[1,1])])])

def png(path):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,1,8,6,0,0,0))+
        chunk(b'IDAT',zlib.compress(b'\0'+b'\xff'*8))+chunk(b'IEND',b''))

@pytest.mark.parametrize('change',[lambda s:s.update(input_sha256='b'*64),lambda s:s.update(schema_version=True),
    lambda s:s['sequences'][0].update(index=False),lambda s:s['sequences'][0].update(frames=[None])])
def test_original_sheet_identity_and_typed_order_cannot_be_guessed(change):
    value=sheet();texture=dict(resource='materials/test.vtex_c',source_sheet=value)
    validate_asset_sheet(texture,{'materials/test.vtex':'a'*64})
    change(value)
    with pytest.raises(UnsupportedEffect):validate_asset_sheet(texture,{'materials/test.vtex':'a'*64})

@pytest.mark.parametrize('failure',[None,'exit','source','image','dimensions','path','null'])
def test_typed_sheet_cache_admission_checks_actual_image_and_source(tmp_path,monkeypatch,failure):
    import agr.particle_runtime as runtime
    import agr.effect_assets as module
    monkeypatch.setattr(runtime,'particle_command',lambda *a:['fixture-helper'])
    monkeypatch.setattr(runtime,'particle_environment',lambda *a:{})
    probe=tmp_path/'probe.dll';probe.write_bytes(b'fixture helper')
    probe.with_name('ValveResourceFormat.dll').write_bytes(b'fixture parser')
    source=tmp_path/'input.vtex_c';source.write_bytes(b'fixture input')
    target=tmp_path/'entry';calls=[]
    def entry(kind,inputs,identity,build):
        target.mkdir();calls.append((kind,inputs,identity));build(target);return target
    def run(command,**kwargs):
        assert command[:2]==['fixture-helper','--particle-texture-sheet-v1']
        output=target/'sheet';value=sheet();png(output/'atlas.png')
        value['image_sha256']=hashlib.sha256((output/'atlas.png').read_bytes()).hexdigest()
        if failure=='source':value['input_sha256']='b'*64
        if failure=='image':value['image_sha256']='c'*64
        if failure=='dimensions':value['width']=3
        if failure=='path':value['image']='../elsewhere.png'
        if failure=='null':value=None
        (output/'sheet.json').write_text(json.dumps(value))
        return SimpleNamespace(returncode=int(failure=='exit'),stdout='',stderr='fixture')
    monkeypatch.setattr(module.subprocess,'run',run)
    adapter=EffectAssetAdapter(SimpleNamespace(_entry=entry))
    provider=SimpleNamespace(resolve=lambda resource:SimpleNamespace(local_path=source,sha256='a'*64))
    if failure:
        with pytest.raises((ConversionError,UnsupportedEffect,GameAssetError)):adapter.texture_sheet('materials/test.vtex',provider,probe)
    else:
        result=adapter.texture_sheet('materials/test.vtex',provider,probe)
        assert result['directory']==str(target/'sheet') and len(result['sequences'][0]['frames'])==2
        assert calls[0][1]=={'materials/test.vtex_c':'a'*64}
        assert 'helper_sha256'in calls[0][2] and 'validator_sha256'in calls[0][2]

def test_normal_asset_preparer_requests_original_timing_only_for_animated_sheet(tmp_path,monkeypatch):
    import agr.particle_model_assets as models
    from agr.particle_render_assets import prepare_render_assets
    calls=[];value=sheet()
    monkeypatch.setattr(models,'geometry_runtime',lambda runtime:('qualified-geometry-helper','qualified-dotnet'))
    definition=dict(m_Renderers=[dict(_class='C_OP_RenderSprites',m_vecTexturesInput=[dict(m_hTexture='materials/test.vtex')])])
    descriptor=dict(resource='materials/test.vtex_c',sequences={'0':dict(status='unsupported',frames=[{},{}])})
    def typed(resource,provider,probe,dotnet):
        calls.append((resource,probe,dotnet));return copy.deepcopy(value)
    adapter=SimpleNamespace(texture=lambda *a:(tmp_path,descriptor),texture_sheet=typed)
    decoder=SimpleNamespace(identity={},dump=lambda *a:definition)
    provider=SimpleNamespace(resolve=lambda *a:SimpleNamespace(sha256='a'*64))
    result=prepare_render_assets({'particles/test.vpcf'},decoder,adapter,provider,{})
    assert calls==[('materials/test.vtex','qualified-geometry-helper','qualified-dotnet')]
    assert result['textures']['materials/test.vtex']['source_sheet']==value
    descriptor['sequences']['0']['frames']=[{}];calls.clear()
    result=prepare_render_assets({'particles/test.vpcf'},decoder,adapter,provider,{})
    assert not calls and 'source_sheet'not in result['textures']['materials/test.vtex']
