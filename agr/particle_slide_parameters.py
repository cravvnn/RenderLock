"""Bind captured artist properties to complete native per-token material queries.

This is a pure projection, not an owner, render admission or clock provider.
The caller must retain the current binding/typed descriptor and before/after
whole-cohort proof. Missing COLOR uses a stored property only after the native
query proves absence in all four contexts; raw particle white is never injected.
"""
import hashlib
import json
import re
import struct
from copy import deepcopy
from .particle_material_expressions import (
    ParticleExpressionRefused,SLIDE_MATERIAL_SHA256,SLIDE_PARSED_SHA256,MODULE_SHA256,
    resolved_material_query_variable,
)

POLICY='agr_slide_captured_artist_values_v1'
MATERIAL='models/particle/materials/dust_ground_hero_streaks.vmat'
SPEC={
    164:('g_flSelfIllumScale1',1,(0.,0.,0.,0.),(10.,1.,1.,1.)),
    239:('g_vColorTint1',3,(-1e9,)*4,(1e9,)*4),
    266:('g_vSelfIllumTint1',3,(-1e9,)*4,(1e9,)*4),
    270:('g_vTranslucentScrollSpeed0_1',2,(-10.,-10.,0.,0.),(10.,10.,1.,1.)),
}

def require(ok,message):
    if not ok:raise ParticleExpressionRefused('Captured slide material: '+message)

def canonical(value):
    try:return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    except (TypeError,ValueError,OverflowError)as error:raise ParticleExpressionRefused('Captured slide material is not finite JSON data')from error

def raw(value,n,label):
    require(type(value)is str and len(value)==n*2 and re.fullmatch('[0-9a-f]+',value)is not None,label+' raw byte count differs')
    return bytes.fromhex(value)

def pointer(value,*,alignment=8):
    require(type(value)is str and re.fullmatch('0x[0-9a-f]{1,12}',value)is not None,'typed source address unavailable')
    result=int(value,16);require(0<result<2**47 and result%alignment==0,'source address bound differs');return result

def resolve_slide_artist_values(authored,artist,binding_index,queries,*,material_sha256,expression_module_sha256):
    require(material_sha256==SLIDE_MATERIAL_SHA256 and expression_module_sha256==MODULE_SHA256,'material/evaluator identity differs')
    require(type(authored)is dict and canonical(authored)==SLIDE_PARSED_SHA256
        and authored.get('m_materialName')==MATERIAL and authored.get('m_shaderName')=='pbr.vfx','parsed authored material differs')
    require(type(artist)is dict and type(binding_index)is int and 0<=binding_index<16,'current artist binding required')
    pointer(artist.get('material_pointer'))
    bindings=artist.get('bindings');require(type(bindings)is list and 1<=len(bindings)<=16,'artist binding table bound')
    selected=[b for b in bindings if b.get('index')==binding_index]
    require(len(selected)==1,'selected artist binding missing or ambiguous')
    stages=[s for s in selected[0]['stages']if s.get('stage')==1]
    require(len(stages)==1 and stages[0].get('state')=='raw_PBR_PS280_artist_candidates','current pixel artist stage unavailable')
    stage=stages[0];mapping=stage['variables']
    require(type(mapping)is list and len(mapping)==4 and [m.get('index')for m in mapping]==list(SPEC),'artist parameter map differs')
    header=raw(stage['value_header_raw'],24,'property vector');count,_,array,capacity,_=struct.unpack('<iIQiI',header)
    require(0<count<=capacity<=512 and 0<array<2**47 and array%8==0,'property vector count/storage bound')
    records=artist.get('records');properties=artist.get('properties');programs=artist.get('programs')
    require(type(records)is list and len(records)==4 and {v.get('index')for v in records}==set(SPEC),'complete typed parameter records required')
    require(type(properties)is list and len(properties)==4 and type(programs)is list and 1<=len(programs)<=4,'complete property/program inventory required')
    authored_programs={v['m_name']:bytes(v['m_value'])for v in authored['m_dynamicParams']}
    defaults={v['m_name']:tuple(v['m_value'])for v in authored['m_vectorParams']}
    defaults.update({v['m_name']:(v['m_flValue'],0.,0.,0.)for v in authored['m_floatParams']})
    require(set(authored_programs)=={v[0]for v in SPEC.values()},'dynamic program coverage differs')
    values={}
    for row in mapping:
        index=row['index'];name,lanes,lower,upper=SPEC[index]
        require(row.get('expected_name')==name and row.get('state')=='raw_material_property','artist property mapping unavailable')
        descriptor=next(r for r in records if r['index']==index)
        require(descriptor.get('address')==row.get('descriptor_record')and descriptor.get('expected_name')==name,'artist descriptor association differs')
        meta=raw(descriptor['raw_hex'],0x218,'artist descriptor')
        require((name.encode()+b'\0')in meta[:0xc8] and struct.unpack_from('<iii',meta,0xec)==(0,-1,lanes)
            and struct.unpack_from('<i',meta,0xf8)[0]==3 and struct.unpack_from('<i',meta,0x100)[0]==lanes,'typed artist descriptor differs')
        require(struct.unpack_from('<4f',meta,0x18c)==lower and struct.unpack_from('<4f',meta,0x19c)==upper,'shader clamp ranges differ')
        property_index=row.get('property_index')
        require(type(property_index)is int and 0<=property_index<count and struct.unpack('<h',raw(row['index_raw'],2,'property index'))[0]==property_index,'property index bound/bytes differ')
        candidates=[p for p in properties if p.get('index')==property_index]
        require(len(candidates)==1,'property record missing or ambiguous');prop=candidates[0]
        require(prop['address']==row.get('property_record')and pointer(prop['address'])==array+property_index*64,'property storage association differs')
        data=raw(prop['raw_hex'],64,'artist property');fallback=struct.unpack_from('<4f',data)
        require(fallback==defaults[name],'captured stored property differs from pinned authored value')
        length=struct.unpack_from('<H',data,0x1c)[0];code_address=struct.unpack_from('<Q',data,0x20)[0]
        require(0<length<=512 and prop.get('program_length')==length and pointer(prop['program_pointer'],alignment=1)==code_address
            and prop.get('program_state')=='captured_raw_program_not_evaluated','property program header/availability differs')
        sources=[p for p in programs if p.get('address')==prop['program_pointer']]
        require(len(sources)==1 and sources[0].get('bytes')==length,'complete property program unavailable')
        code=raw(sources[0]['raw_hex'],length,'artist program')
        require(code==authored_programs[name],'actual property bytecode differs')
        values[name]=resolved_material_query_variable(code,fallback,lower,upper,lanes,queries=queries,
            attribute_coverage='native_order_token_queries_v1',expression_module_sha256=expression_module_sha256)
    return dict(schema_version=1,policy=POLICY,material_sha256=material_sha256,parsed_sha256=SLIDE_PARSED_SHA256,
        expression_module_sha256=expression_module_sha256,binding_index=binding_index,
        artist_inputs_sha256=canonical(artist),queries_sha256=canonical(queries),values=values)


def bind_slide_frame_parameters(material_values,game_time_query,source_game_time_raw):
    """Bind explicit draw GameTime to the independently captured source scalar.

The original capture owner supplies both witnesses and their cohort/identity
proof. This checks their numeric join only; it creates no clock and makes no
submitted-GPU-frame claim. Integer/missing/unread/unequal inputs are refused.
There is deliberately no Age, replay tick, FPS or inferred-time argument.
"""
    require(type(material_values)is dict and type(material_values.get('schema_version'))is int and material_values.get('schema_version')==1
        and material_values.get('policy')==POLICY and material_values.get('material_sha256')==SLIDE_MATERIAL_SHA256
        and material_values.get('parsed_sha256')==SLIDE_PARSED_SHA256
        and material_values.get('expression_module_sha256')==MODULE_SHA256,'resolved material identity differs')
    require(set(material_values.get('values',{}))=={name for name,*_ in SPEC.values()},'resolved material variable coverage differs')
    canonical(material_values)
    from .particle_material_expressions import _vector
    for name,lanes,lo,hi in SPEC.values():
        value=material_values['values'][name]
        require(type(value)in(list,tuple)and len(value)==lanes,'resolved material variable shape differs')
        checked=_vector(list(value)+[0.]*(4-lanes))
        require(all(lo[i]<=checked[i]<=hi[i]for i in range(lanes)),'resolved material value outside shader clamp')
    scalar_raw=raw(source_game_time_raw,4,'source GameTime')
    require(type(game_time_query)is dict,'explicit direct draw GameTime query required')
    origin=game_time_query.get('origin');bits=game_time_query.get('raw_bits')
    require(set(game_time_query)=={'present','origin','checked_stages','kind','raw_bits'}
        and game_time_query['present']is True and type(origin)is int and origin in(1,2)
        and game_time_query['kind']=='vector'and game_time_query['checked_stages']==list(range(1,origin+1))
        and all(type(v)is int for v in game_time_query['checked_stages']), 'complete direct vector GameTime query required')
    require(type(bits)is list and len(bits)==4 and all(type(v)is int and 0<=v<2**32 for v in bits)
        and len(set(bits))==1 and struct.pack('<I',bits[0])==scalar_raw,'direct draw GameTime differs from captured source scalar')
    time=struct.unpack('<f',scalar_raw)[0]
    require(time==time and abs(time)!=float('inf'),'finite source GameTime required')
    return dict(schema_version=1,policy='agr_slide_source_bound_material_frame_v1',
        material_values=deepcopy(material_values),game_time_raw=source_game_time_raw,game_time=time,
        game_time_query_sha256=canonical(game_time_query),
        scope='Current draw material values and matching source GameTime; caller retains capture/cohort/owner proof, not GPU execution.')
