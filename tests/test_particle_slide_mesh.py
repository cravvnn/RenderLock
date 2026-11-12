from copy import deepcopy
import struct
import numpy as np
import pytest
from agr import particle_slide_mesh as m
from agr.particle_slide_parameters import POLICY
from agr.particle_material_expressions import ParticleExpressionRefused


@pytest.fixture
def inputs(monkeypatch):
    vectors, floats = {}, {}
    for prefix, suffix in [('AlbedoTexcoord','1'), ('SelfIllumTexcoord','1'),
                           ('TranslucentTexcoord','0_1'), ('TranslucentTexcoord','1_1')]:
        vectors['g_v'+prefix+'Scale'+suffix] = [1,1,0,0]
        vectors['g_v'+prefix+'Offset'+suffix] = [0,0,0,0]
        floats['g_fl'+prefix+'Rotation'+suffix] = 0
    vectors.update(g_vAlbedoScrollSpeed1=[0,0,0,0],g_vSelfIllumScrollSpeed1=[0,0,0,0],
                   g_vTranslucentScrollSpeed1_1=[0,0,0,0])
    floats.update(g_flOpacityScale1=1)
    floats['g_flTranslucentTexcoordRotation1_1'] = 90
    authored = dict(m_vectorParams=[dict(m_name=k,m_value=v) for k,v in vectors.items()],
                    m_floatParams=[dict(m_name=k,m_flValue=v) for k,v in floats.items()])
    pin = m.canonical(authored)
    monkeypatch.setattr(m,'SLIDE_PARSED_SHA256',pin)
    material = dict(schema_version=1,policy=POLICY,material_sha256=m.SLIDE_MATERIAL_SHA256,
        parsed_sha256=pin,expression_module_sha256=m.MODULE_SHA256,binding_index=1,
        values=dict(g_vColorTint1=[1,1,1],g_vSelfIllumTint1=[1,1,1],
                    g_flSelfIllumScale1=[0],g_vTranslucentScrollSpeed0_1=[0,2]))
    frame = dict(schema_version=1,policy=m.FRAME_POLICY,material_values=material,
                 game_time=318.59375,game_time_raw=struct.pack('<f',318.59375).hex())
    geometry = dict(static=True,morphs=[],animation_name=None,
        positions=np.array([[0.,0,0],[1,0,0],[0,1,0]]),normals=np.array([[0.,0,1]]*3),
        uv=np.array([[0.,0],[1,0],[0,1]]),indices=np.array([[0,1,2]]))
    return geometry, [[2,.75,0,100],[0,3,0,-200],[0,0,4,300]], frame, authored, [.2,.3,.4,.5]


def project(inputs,**kwargs):
    return m.prepare_slide_model_frame(*inputs,.0254,vertex_shader_sha256=m.VERTEX_SHADER_SHA256,
        pixel_shader_sha256=kwargs.get('shader',m.PIXEL_SHADER_SHA256))


def test_same_mesh_uv_scroll_before_rotation_single_v_flip_and_shear(inputs):
    geometry,matrix,frame,authored,rgba=inputs
    before=deepcopy(frame)
    result=project(inputs)
    np.testing.assert_allclose(result['vertices'],[[2.54,-5.08,7.62],[2.5908,-5.08,7.62],[2.55905,-5.0038,7.62]])
    np.testing.assert_allclose(result['uv_layers']['albedo'],[[0,1],[1,1],[0,0]])
    # FRC(318.59375*2)=.1875; scroll is applied before the transform.
    np.testing.assert_allclose(result['uv_layers']['translucency'],[[0,.8125],[1,.8125],[0,-.1875]])
    np.testing.assert_allclose(result['uv_layers']['alt_translucency'],[[1,1],[1,0],[0,1]],atol=1e-7)
    np.testing.assert_allclose(result['tint_matrix'],np.eye(4))
    np.testing.assert_allclose(result['instance_rgba'],rgba,atol=3e-8)
    assert result['self_illum_scale']==0 and frame==before


@pytest.mark.parametrize('mutation',['shader','material','clock','raw_time','binding','missing_value','nonfinite','alpha','missing_rgba','clamp'])
def test_unknown_shader_material_time_and_partial_inputs_refuse(inputs,mutation):
    if mutation=='shader':
        with pytest.raises(ParticleExpressionRefused):project(inputs,shader='0'*64)
        return
    frame=inputs[2]
    if mutation=='material':inputs[3]['m_floatParams'][0]['m_flValue']=1
    elif mutation=='clock':frame['game_time']+=1
    elif mutation=='raw_time':frame['game_time_raw']='0000807f'
    elif mutation=='binding':frame['material_values']['binding_index']=0
    elif mutation=='missing_value':del frame['material_values']['values']['g_flSelfIllumScale1']
    elif mutation=='nonfinite':frame['material_values']['values']['g_vColorTint1'][0]=float('nan')
    elif mutation=='alpha':inputs[4][3]=1.1
    elif mutation=='missing_rgba':inputs[4].pop()
    elif mutation=='clamp':frame['material_values']['values']['g_flSelfIllumScale1'][0]=11
    with pytest.raises(ParticleExpressionRefused):project(inputs)


def test_negative_uv_is_retained_and_input_geometry_not_mutated(inputs):
    inputs[0]['uv'][0]=[-.5,-.25]
    before=inputs[0]['uv'].copy()
    result=project(inputs)
    np.testing.assert_allclose(result['uv_layers']['albedo'][0],[-.5,1.25])
    np.testing.assert_array_equal(inputs[0]['uv'],before)


def test_source_repeated_index_padding_is_counted_and_never_sent_to_blender(inputs):
    inputs[0]['indices']=np.array([[0,1,2],[2,2,2],[0,0,1]])
    result=project(inputs)
    assert result['source_triangles']==3 and result['discarded_degenerate_triangles']==2
    np.testing.assert_array_equal(result['indices'],[[0,1,2]])
    inputs[0]['indices']=np.array([[0,0,1]])
    with pytest.raises(ParticleExpressionRefused,match='nondegenerate'):project(inputs)


def native_color_inputs():
    rgba=[.772549033164978,.7568627595901489,.729411780834198,.004644125699996948]
    return dict(schema='private_cmdl_evaluated_color_v1',scene_module_sha256=m.SCENE_MODULE_SHA256,initial_read_bytes=24,
        rows=[dict(slot=0,wrapper_pointer='0x70001000',state='captured_CMDL_SetColor_float4_not_GPU',
        producer=dict(vtable='0x625bc8',slot='0x70',setter='0x17f460',offset='0x110'),
        header_raw=struct.pack('<Q',0x190000000+0x625bc8).hex(),color_raw=struct.pack('<4f',*rgba).hex(),color=rgba)])


def project_native(inputs,colors):
    return m.prepare_slide_model_frame_from_cmdl(*inputs[:4],colors,.0254,slot=0,expected_wrapper=0x70001000,
        scene_base=0x190000000,vertex_shader_sha256=m.VERTEX_SHADER_SHA256,pixel_shader_sha256=m.PIXEL_SHADER_SHA256)


def test_observed_output_alpha_is_independent_of_lifetime_and_raw_particle_fallback(inputs):
    colors=native_color_inputs();before=deepcopy(colors)
    result=project_native(inputs,colors)
    assert result['instance_rgba'][3]==colors['rows'][0]['color'][3]
    assert result['instance_rgba'][3]!=.1
    assert result['instance_rgba'][0]<colors['rows'][0]['color'][0]
    assert result['instance_color_source']['raw_hex']==colors['rows'][0]['color_raw']
    assert colors==before


@pytest.mark.parametrize('mutation',['module','wrapper','type','producer','raw_value','nonfinite','missing','slot'])
def test_evaluated_color_association_cannot_be_replaced_or_defaulted(inputs,mutation):
    colors=native_color_inputs();row=colors['rows'][0]
    if mutation=='module':colors['scene_module_sha256']='0'*64
    elif mutation=='wrapper':row['wrapper_pointer']='0x70002000'
    elif mutation=='type':row['header_raw']='00'*8
    elif mutation=='producer':row['producer']['offset']='0x120'
    elif mutation=='raw_value':row['color'][3]=.1
    elif mutation=='nonfinite':row['color_raw']=struct.pack('<4f',1,1,1,float('nan')).hex()
    elif mutation=='missing':del row['color_raw']
    elif mutation=='slot':row['slot']=1
    with pytest.raises(ParticleExpressionRefused):project_native(inputs,colors)
