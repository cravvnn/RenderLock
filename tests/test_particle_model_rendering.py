from copy import deepcopy
import hashlib
from pathlib import Path
import struct

import pytest

from agr.installed_effects import UnsupportedEffect
from agr.particle_model_assets import model_resource, validate_model, geometry_runtime, MATERIAL_VARIABLES, POLICY
from agr.blender_particle_models import material_profile, validate_model_samples
from agr.blender_simulated_effects import validate_renderer_classes
from agr.particle_control_provenance import confirmed_materialized_points
from test_particle_model_geometry import fixture, write


def renderer():
    return dict(_class='C_OP_RenderModels', m_ModelList=[{'m_model': 'models/test/wing.vmdl'}],
        m_bAnimated=True, m_bForceLoopingAnimation=True, m_bDisableShadows=True,
        m_bAcceptsDecals=False, m_hOverrideMaterial='materials/test/wing.vmat',
        m_MaterialVars=deepcopy(MATERIAL_VARIABLES[1]))


def material():
    return dict(m_shaderName='pbr.vfx', m_renderAttributesUsed=['$SELFILLUM'],
        m_dynamicParams=[dict(m_name='g_flSelfIllumScale1', m_value=[25,22,66,211,186,0])],
        m_intParams=[dict(m_name='F_SELF_ILLUM', m_nValue=1)],
        m_textureParams=[dict(m_name=name, m_pValue='materials/test/'+name+'.vtex') for name in
            ('g_tColor','g_tNormalRoughness','g_tSelfIllumMask','g_tTintMask','g_tAmbientOcclusion','g_tMetalness')])


def assets(tmp_path):
    document, binary = fixture()
    document['animations'][0]['name'] = 'flap'
    # This small validation fixture has a closed loop. Actual changing bat
    # poses and subframes are verified in the installed-asset Blender check.
    accessor = document['accessors'][document['animations'][0]['samplers'][0]['output']]
    offset = document['bufferViews'][accessor['bufferView']]['byteOffset']
    struct.pack_into('<f', binary, offset+4, 0.)
    path = write(tmp_path/'model.glb', document, binary)
    descriptor = dict(schema_version=1, policy=POLICY, resource='models/test/wing.vmdl',
        source_sha256='1'*64, bones=0, material_exported=False,
        material_resource='materials/test/wing.vmat', material=material(), mesh_groups=[], body_groups=0,
        directory=str(tmp_path), geometry_file='model.glb', geometry_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        animations=[dict(index=0,name='flap',fps=1,frames=2,cycle_frames=1,has_flex_data=True)])
    return {'models': {'models/test/wing.vmdl': descriptor}}


def samples():
    return [(tick,1/64,dict(position=[0.,0.,0.],normal=[1.,0.,0.],rotation=[0.,0.,0.],radius=1.,
        age=tick/64,scratch_float0=2.,sequence=0,second_sequence=0)) for tick in range(2)]


def test_model_asset_material_and_capture_preflight(tmp_path):
    r=renderer();a=assets(tmp_path)
    descriptor=validate_model(r,a)
    geometry,schedule,matrices=validate_model_samples(r,descriptor,samples())
    assert len(geometry['morphs'])==1 and len(matrices)==2 and len(schedule)>=2
    _,profile=material_profile(descriptor['material'])
    assert profile['_particle_emission_tint'] is False


@pytest.mark.parametrize('change',[
    {'m_bAnimated':False}, {'m_bForceLoopingAnimation':False}, {'m_bDisableShadows':False},
    {'m_bAcceptsDecals':True}, {'m_bIgnoreRadius':1}, {'m_nAnimationField':19},
    {'m_modelInput':{'m_nType':'PVEC_TYPE_CONTROL_POINT_VALUE'}},
    {'m_bManualAnimFrame':True}, {'m_bManualAnimFrame':True,'m_nManualFrameField':8},
    {'m_flAnimationRate':-1}, {'m_flAnimationRate':True},
    {'m_flAnimationRate':{'m_nType':'PF_TYPE_COLLECTION_AGE'}}, {'m_ModelList':[]},
    {'m_MaterialVars':[]}, {'m_bLocalScale':False,'m_vecComponentScale':{'m_vLiteralValue':[1,2,1]}}
])
def test_unrepresented_renderer_modes_are_rejected(change):
    r=renderer();r.update(change)
    with pytest.raises(UnsupportedEffect):model_resource(r)


@pytest.mark.parametrize('field,value',[
    ('bones',1),('bones',False),('body_groups',1),('material_exported',True),
    ('resource','models/test/other.vmdl'),('material_resource','materials/test/other.vmat'),
    ('geometry_sha256','0'*64),('animations',[]),('policy','unqualified')])
def test_changed_geometry_or_descriptor_does_not_reach_renderer(tmp_path,field,value):
    a=assets(tmp_path);a['models']['models/test/wing.vmdl'][field]=value
    with pytest.raises(UnsupportedEffect):validate_model(renderer(),a)


@pytest.mark.parametrize('field,value',[
    ('second_sequence',None),('second_sequence',False),('second_sequence',1),
    ('scratch_float0',None),('scratch_float0',-1),('sequence',True)])
def test_missing_or_unqualified_capture_fields_fail_before_scene_mutation(tmp_path,field,value):
    s=samples();s[0][2][field]=value
    with pytest.raises(UnsupportedEffect):
        validate_model_samples(renderer(),validate_model(renderer(),assets(tmp_path)),s)


def test_manual_mode_cannot_guess_missing_frame38(tmp_path):
    r=renderer();r.update(m_bManualAnimFrame=True,m_nManualFrameField=38)
    with pytest.raises(UnsupportedEffect,match='animation timing'):
        validate_model_samples(r,validate_model(r,assets(tmp_path)),samples())


def test_model_material_attributes_must_match_renderer(tmp_path):
    r=renderer();r['m_MaterialVars']=deepcopy(MATERIAL_VARIABLES[0])
    with pytest.raises(UnsupportedEffect,match='attributes differ'):
        validate_model(r,assets(tmp_path))


def test_model_preflight_runs_before_supported_sprite_siblings(tmp_path):
    a=assets(tmp_path);a['definitions']={'effects/test.vpcf':{'m_Renderers':[
        {'_class':'C_OP_RenderSprites'},renderer()]}}
    s=samples();s[0][2].pop('second_sequence')
    with pytest.raises(UnsupportedEffect,match='sequence selector'):
        validate_renderer_classes({('instance','root','effects/test.vpcf',0):s},a)


def test_geometry_process_cannot_share_material_parser_directory(tmp_path):
    sim=tmp_path/'ParticleProbe.exe';sim.write_bytes(b'fixture')
    helper=tmp_path/'ParticleGeometryProbe.exe';helper.write_bytes(b'fixture')
    with pytest.raises(UnsupportedEffect,match='separate directories'):
        geometry_runtime(dict(probe=sim,geometry_probe=helper))


def test_expression_control_provenance_keeps_missing_and_external_guards():
    row=dict(control_point_input_policy='agr_authored_expression_controls_v3',
        declared_control_point_inputs=[0],authored_control_point_indices=[10],
        unprovided_control_point_indices=[],materialized_control_point_indices=[0,10])
    assert confirmed_materialized_points(row,[0])
    for change in ({'unprovided_control_point_indices':[10]}, {'authored_control_point_indices':[0,10]},
                   {'declared_control_point_inputs':[]}, {'materialized_control_point_indices':[0]},
                   {'authored_control_point_indices':[True]}):
        assert not confirmed_materialized_points(dict(row,**change),[0])


@pytest.mark.parametrize('group', ['m_intParams','m_textureParams','m_dynamicParams'])
def test_duplicate_material_parameters_cannot_be_silently_discarded(group):
    value=material();value[group].append(deepcopy(value[group][0]))
    with pytest.raises(UnsupportedEffect,match='Duplicate'):
        material_profile(value)
