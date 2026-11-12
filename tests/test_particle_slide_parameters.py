from copy import deepcopy
import struct
import pytest
from agr import particle_slide_parameters as p
from agr.particle_material_expressions import ATTRIBUTE_TOKENS,ParticleExpressionRefused,MODULE_SHA256,SLIDE_MATERIAL_SHA256

@pytest.fixture
def inputs(monkeypatch):
    programs={
        'g_flSelfIllumScale1':bytes.fromhex('191642d3ba00'),
        'g_vColorTint1':bytes.fromhex('19f7f8a1c400'),
        'g_vSelfIllumTint1':bytes.fromhex('19f7f8a1c400'),
        'g_vTranslucentScrollSpeed0_1':bytes.fromhex('07000000001951cefbad061a0000')}
    defaults={name:(0.,0.,0.,0.)if index==164 else(0.,2.,0.,0.)if index==270 else(1.,1.,1.,0.)for index,(name,*_)in p.SPEC.items()}
    authored=dict(m_materialName=p.MATERIAL,m_shaderName='pbr.vfx',m_dynamicParams=[dict(m_name=name,m_value=list(code))for name,code in programs.items()],
        m_floatParams=[dict(m_name='g_flSelfIllumScale1',m_flValue=0.)],m_vectorParams=[dict(m_name=name,m_value=list(value))for name,value in defaults.items()if name!='g_flSelfIllumScale1'])
    monkeypatch.setattr(p,'SLIDE_PARSED_SHA256',p.canonical(authored))
    mapping=[];records=[];properties=[];codes=[];addresses={};array=0x74000000
    for ordinal,(index,(name,lanes,lo,hi))in enumerate(p.SPEC.items()):
        code=programs[name]
        if code not in addresses:
            address=0x76000000+len(addresses)*0x100+1;addresses[code]=address
            codes.append(dict(address=hex(address),bytes=len(code),raw_hex=code.hex()))
        address=addresses[code];descriptor=0x73000000+index*0x218
        raw=bytearray(0x218);raw[:len(name)+1]=name.encode()+b'\0'
        struct.pack_into('<iii',raw,0xec,0,-1,lanes);struct.pack_into('<i',raw,0xf8,3);struct.pack_into('<i',raw,0x100,lanes)
        struct.pack_into('<4f',raw,0x18c,*lo);struct.pack_into('<4f',raw,0x19c,*hi)
        records.append(dict(index=index,address=hex(descriptor),expected_name=name,raw_hex=raw.hex()))
        prop=bytearray(64);struct.pack_into('<4f',prop,0,*defaults[name]);struct.pack_into('<H',prop,0x1c,len(code));struct.pack_into('<Q',prop,0x20,address)
        properties.append(dict(index=ordinal,address=hex(array+ordinal*64),raw_hex=prop.hex(),program_length=len(code),program_pointer=hex(address),program_state='captured_raw_program_not_evaluated'))
        mapping.append(dict(index=index,expected_name=name,descriptor_record=hex(descriptor),property_index=ordinal,index_raw=struct.pack('<h',ordinal).hex(),
            state='raw_material_property',property_record=hex(array+ordinal*64)))
    stage=dict(stage=1,state='raw_PBR_PS280_artist_candidates',variables=mapping,value_header_raw=struct.pack('<iIQiI',4,0,array,4,0).hex())
    artist=dict(material_pointer='0x70000000',bindings=[dict(index=1,stages=[stage])],records=records,properties=properties,programs=codes)
    def query(values):return dict(present=True,origin=1,checked_stages=[1],kind='vector',raw_bits=list(struct.unpack('<4I',struct.pack('<4f',*values))))
    queries={'$COLOR':query((.25,.5,.75,0)),'$SELFILLUM':query((3,3,3,3)),'$SCROLLSPEED':query((2,2,2,2))}
    return authored,artist,queries

def resolve(inputs,**changes):
    authored,artist,queries=inputs
    args=dict(material_sha256=SLIDE_MATERIAL_SHA256,expression_module_sha256=MODULE_SHA256);args.update(changes)
    return p.resolve_slide_artist_values(authored,artist,1,queries,**args)

def test_actual_layout_programs_unaligned_bytecode_and_resolved_values(inputs):
    authored,artist,queries=inputs;before=deepcopy(inputs)
    values=resolve(inputs)['values']
    assert values=={'g_flSelfIllumScale1':(3.,),'g_vColorTint1':(.25,.5,.75),'g_vSelfIllumTint1':(.25,.5,.75),'g_vTranslucentScrollSpeed0_1':(0.,2.)}
    assert inputs==before

def test_proved_absence_selects_captured_stored_color_only(inputs):
    inputs[2]['$COLOR']=dict(present=False,origin=0,checked_stages=[1,2,3,4])
    assert resolve(inputs)['values']['g_vColorTint1']==(1.,1.,1.)
    inputs[2]['$COLOR']['checked_stages']=[1,2,3]
    with pytest.raises(ParticleExpressionRefused,match='prefix'):resolve(inputs)

def test_clamping_is_applied_from_the_current_shader_descriptor(inputs):
    inputs[2]['$SELFILLUM'].update(raw_bits=list(struct.unpack('<4I',struct.pack('<4f',20,20,20,20))))
    inputs[2]['$SCROLLSPEED'].update(raw_bits=list(struct.unpack('<4I',struct.pack('<4f',-20,-20,-20,-20))))
    values=resolve(inputs)['values'];assert values['g_flSelfIllumScale1']==(10.,);assert values['g_vTranslucentScrollSpeed0_1']==(0.,-10.)

@pytest.mark.parametrize('kind',['missing_binding','duplicate_property','wrong_index','wrong_storage','wrong_descriptor','changed_clamp','changed_code','changed_default','unavailable_program'])
def test_named_property_associations_and_raw_inputs_refuse(inputs,kind):
    a=inputs[1];m=a['bindings'][0]['stages'][0]['variables'][0]
    if kind=='missing_binding':a['bindings'][0]['index']=2
    elif kind=='duplicate_property':a['properties'][1]['index']=0
    elif kind=='wrong_index':m['index_raw']='0100'
    elif kind=='wrong_storage':m['property_record']='0x74000100'
    elif kind=='wrong_descriptor':m['descriptor_record']='0x73000000'
    elif kind=='changed_clamp':
        data=bytearray.fromhex(a['records'][0]['raw_hex']);struct.pack_into('<f',data,0x19c,11.);a['records'][0]['raw_hex']=data.hex()
    elif kind=='changed_code':a['programs'][0]['raw_hex']='00'*a['programs'][0]['bytes']
    elif kind=='changed_default':
        data=bytearray.fromhex(a['properties'][0]['raw_hex']);struct.pack_into('<f',data,0,1.);a['properties'][0]['raw_hex']=data.hex()
    elif kind=='unavailable_program':a['properties'][0]['program_state']='unavailable'
    with pytest.raises(ParticleExpressionRefused):resolve(inputs)

def test_source_identity_and_current_selected_stage_are_required(inputs):
    for changes in(dict(material_sha256='0'*64),dict(expression_module_sha256='0'*64)):
        with pytest.raises(ParticleExpressionRefused,match='identity'):resolve(inputs,**changes)
    inputs[1]['bindings'][0]['stages'][0]['stage']=0
    with pytest.raises(ParticleExpressionRefused,match='pixel artist'):resolve(inputs)

def game_time_query(origin=2,raw='004c9f43'):
    return dict(present=True,origin=origin,checked_stages=list(range(1,origin+1)),kind='vector',raw_bits=[struct.unpack('<I',bytes.fromhex(raw))[0]]*4)

def test_frame_requires_exact_direct_game_time_and_independent_scalar(inputs):
    values=resolve(inputs)
    for origin in(1,2):
        result=p.bind_slide_frame_parameters(values,game_time_query(origin),'004c9f43')
        assert result['game_time']==318.59375 and result['material_values']==values and result['material_values']is not values
    values['values']['g_flSelfIllumScale1']=(9.,)
    assert result['material_values']['values']['g_flSelfIllumScale1']==(3.,)

def test_frame_refuses_tampered_resolved_shapes_nonfinite_values_and_schema(inputs):
    values=resolve(inputs)
    for changed in (dict(values,schema_version=True),
            dict(values,values=dict(values['values'],g_vColorTint1=[1.])),
            dict(values,values=dict(values['values'],g_flSelfIllumScale1=[float('nan')])),
            dict(values,values=dict(values['values'],g_flSelfIllumScale1=[11.]))):
        with pytest.raises(ParticleExpressionRefused):p.bind_slide_frame_parameters(changed,game_time_query(),'004c9f43')

@pytest.mark.parametrize('kind',['absent','integer','partial','different','unreplicated','nonfinite','wrong_source','bool_prefix'])
def test_missing_unread_or_mismatching_game_time_is_not_replaced(inputs,kind):
    values=resolve(inputs);query=game_time_query();source='004c9f43'
    if kind=='absent':query=dict(present=False,origin=0,checked_stages=[1,2,3,4])
    elif kind=='integer':query=dict(present=True,origin=3,checked_stages=[1,2,3],kind='integer',value=318)
    elif kind=='partial':query['checked_stages']=[2]
    elif kind=='different':query['raw_bits']=[0]*4
    elif kind=='unreplicated':query['raw_bits'][3]^=1
    elif kind=='nonfinite':query=game_time_query(raw='0000807f');source='0000807f'
    elif kind=='wrong_source':source='014c9f43'
    elif kind=='bool_prefix':query['checked_stages']=[True,2]
    with pytest.raises(ParticleExpressionRefused):p.bind_slide_frame_parameters(values,query,source)
