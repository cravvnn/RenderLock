"""Synthetic geometry/profile fixtures; no installed asset or helper execution."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agr import particle_silence_model as silence, particle_model_assets as assets
from agr.installed_effects import UnsupportedEffect
from test_particle_model_geometry import fixture as triangle, write as write_glb


@pytest.fixture
def fixed(tmp_path,monkeypatch):
    renderer=dict(_class='C_OP_RenderModels',m_ModelList=[{'m_model':silence.MODEL}],
        m_hOverrideMaterial=silence.MATERIAL,m_vecComponentScale=dict(m_vLiteralValue=[1,1,2]),
        m_bOrientZ=True,m_bDisableShadows=True,m_bAcceptsDecals=False)
    material=dict(m_textureParams=[],m_floatParams=[],m_vectorParams=[])
    # Isolate admission protocol. Production pins remain unchanged by this test.
    monkeypatch.setattr(silence,'RENDERER_SHA',silence.digest(renderer))
    monkeypatch.setattr(silence,'PARSED_SHA',silence.digest(material))
    document,binary=triangle();document.pop('animations');document['meshes'][0]['primitives'][0].pop('targets')
    directory=tmp_path/'source';directory.mkdir();path=write_glb(directory/'model.glb',document,binary)
    descriptor=dict(schema_version=1,policy=silence.STATIC_POLICY,resource=silence.MODEL,
        source_sha256=silence.MODEL_SHA,bones=1,animations=[],mesh_groups=[],body_groups=0,
        geometry_file='model.glb',geometry_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        material_exported=False,material_resource=silence.MATERIAL,material_sha256=silence.MATERIAL_SHA,
        material=material,directory=str(directory))
    return renderer,descriptor


def samples():
    p=dict(position=[1,2,3],normal=[.3,-.4,.8],rotation=[.2,.4,.6],radius=2,
        alpha=.7,alpha_alternate=1,color=[.1,.2,.3],scratch_float0=4,scratch_float1=.7,scratch_float2=-.25)
    return [(10,.1,p),(11,.1,copy.deepcopy(p))]


def policies():return dict(model_material_fields_policy='agr_model_material_fields_v1',model_aux_scalar_fields_policy=silence.AUX_POLICY)


def test_explicit_static_descriptor_and_aux_inputs_reach_normal_preflight(fixed):
    renderer,descriptor=fixed
    assert assets.model_resource(renderer)==silence.MODEL
    assert assets.validate_model(renderer,dict(models={silence.MODEL:descriptor}))is descriptor
    geometry,matrices=silence.validate_samples(renderer,descriptor,samples(),policies())
    assert geometry['static'] and len(matrices)==2
    from agr.blender_simulated_effects import validate_renderer_classes
    assert validate_renderer_classes({('i','root','start',0):samples()},
        dict(definitions={'start':dict(m_Renderers=[renderer])},models={silence.MODEL:descriptor}),policies())=={}


@pytest.mark.parametrize('field,value',[
    ('policy','agr_particle_model_morph_v1'),('schema_version',True),('bones',True),('bones',0),
    ('body_groups',False),('body_groups',1),('animations',[{}]),('mesh_groups',['variant']),
    ('material_exported',True),('source_sha256','0'*64),('material_sha256','0'*64),
    ('geometry_sha256','0'*64),('geometry_file','other.glb')])
def test_legacy_or_changed_static_contract_never_falls_through_to_morph(fixed,field,value):
    renderer,descriptor=fixed;descriptor[field]=value
    with pytest.raises(UnsupportedEffect):assets.validate_model(renderer,dict(models={silence.MODEL:descriptor}))


def test_normal_preflight_cannot_bypass_missing_aux_policy(fixed):
    from agr.blender_simulated_effects import validate_renderer_classes
    renderer,descriptor=fixed;policy=policies();policy.pop('model_aux_scalar_fields_policy')
    with pytest.raises(UnsupportedEffect,match='fields'):
        validate_renderer_classes({('i','root','start',0):samples()},
            dict(definitions={'start':dict(m_Renderers=[renderer])},models={silence.MODEL:descriptor}),policy)


@pytest.mark.parametrize('unit',[True,0,-1,float('nan')])
def test_invalid_units_cannot_key_a_static_hierarchy(fixed,unit):
    with pytest.raises(UnsupportedEffect):silence.validate_samples(*fixed,samples(),policies(),unit)


def test_missing_tick_and_missing_scalar_refuse(fixed):
    rows=samples();rows[1]=(12,*rows[1][1:])
    with pytest.raises(UnsupportedEffect,match='consecutive'):silence.validate_samples(*fixed,rows,policies())
    rows=samples();rows[1][2].pop('scratch_float2')
    with pytest.raises(UnsupportedEffect,match='fields'):silence.validate_samples(*fixed,rows,policies())


def test_normal_model_dispatch_validates_then_chooses_silence_renderer(fixed,monkeypatch):
    from agr.blender_particle_models import build_model_particle
    from agr import blender_particle_silence
    renderer,descriptor=fixed;calls=[];obj=object()
    def build(name,r,d,g,samples,simulation,collection,texture,unit_scale):
        assert d is descriptor and g['static'] and unit_scale==.0254
        calls.append(name);return obj,['explicit reference material limit']
    monkeypatch.setattr(blender_particle_silence,'build_silence_particle',build)
    result=build_model_particle('startup',renderer,dict(models={silence.MODEL:descriptor}),
        samples(),policies(),None,None,.0254)
    assert result[0]is obj and calls==['startup']
    bad=samples();bad[0][2].pop('scratch_float1')
    with pytest.raises(UnsupportedEffect):build_model_particle('bad',renderer,
        dict(models={silence.MODEL:descriptor}),bad,policies(),None,None,.0254)
    assert calls==['startup']


@pytest.mark.parametrize('legacy_policy',[False,True])
def test_preparation_chooses_one_explicit_static_command_without_fallback(fixed,tmp_path,monkeypatch,legacy_policy):
    renderer,descriptor=fixed;calls=[];published=[]
    helper=tmp_path/'probe.exe';helper.write_bytes(b'helper')
    helper.with_name('ValveResourceFormat.dll').write_bytes(b'parser')
    monkeypatch.setattr(assets,'geometry_runtime',lambda runtime:(helper,None))
    monkeypatch.setattr(assets,'particle_command',lambda *a:['mock-helper'])
    monkeypatch.setattr(assets,'particle_environment',lambda *a:{})
    class Provider:
        install_path=tmp_path
        def resolve(self,name):
            return SimpleNamespace(sha256=silence.MODEL_SHA if name.endswith('.vmdl_c')else silence.MATERIAL_SHA,dependencies=[])
    class Decoder:
        def dump(self,*a):return descriptor['material']
        def _entry(self,kind,inputs,identity,build):
            assert identity['policy']==silence.STATIC_POLICY and 'static_profile_sha256'in identity
            dest=tmp_path/'cache';dest.mkdir();build(dest);published.append(dest);return dest
    def run(command,**kwargs):
        calls.append(command);assert command[1]=='--particle-static-model-v1'
        out=Path(command[-1]);out.mkdir()
        (out/'model.glb').write_bytes((Path(descriptor['directory'])/'model.glb').read_bytes())
        raw={k:v for k,v in descriptor.items()if k not in ('directory','geometry_sha256','material','material_resource','material_sha256')}
        if legacy_policy:raw['policy']='agr_particle_model_morph_v1'
        (out/'model.json').write_text(json.dumps(raw))
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(assets.subprocess,'run',run)
    adapter=SimpleNamespace(decoder=Decoder())
    if legacy_policy:
        with pytest.raises(UnsupportedEffect,match='explicit static'):assets.prepare_model(adapter,renderer,Provider(),{})
        assert not published
    else:
        result,textures=assets.prepare_model(adapter,renderer,Provider(),{})
        assert result['policy']==silence.STATIC_POLICY and textures=={} and len(published)==1
    assert len(calls)==1
