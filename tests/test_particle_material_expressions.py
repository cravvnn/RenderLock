import struct
import pytest
from agr.particle_material_expressions import (
    ATTRIBUTE_TOKENS,MODULE_SHA256,ParticleExpressionRefused,particle_vector,model_material_attribute,
    resolved_material_variable)

def attr(name):return b'\x19'+struct.pack('<I',ATTRIBUTE_TOKENS[name])

def test_attribute_preserves_vector_lanes_and_binary32():
    values=[.25,-2.,3.5,-0.]
    assert struct.pack('<4f',*particle_vector(attr('$COLOR')+b'\0',{'$COLOR':values}))==struct.pack('<4f',*values)
    code=b'\x07'+struct.pack('<f',0.)+attr('$SCROLLSPEED')+b'\x06\x1a\0\0'
    assert particle_vector(code,{'$SCROLLSPEED':[2.,3.,4.,5.]})==(0.,2.,0.,0.)

@pytest.mark.parametrize('code',[
    attr('$COLOR')*33+b'\0',
])
def test_malformed_unbound_and_outside_subset_refuse(code):
    with pytest.raises(ParticleExpressionRefused):particle_vector(code,{'$COLOR':[1.,2.,3.,4.]})

@pytest.mark.parametrize('attributes',[
    {},
])
def test_missing_or_invalid_attribute_is_never_defaulted(attributes):
    with pytest.raises(ParticleExpressionRefused):particle_vector(attr('$COLOR')+b'\0',attributes)

def test_model_attribute_vector_has_zero_w_scalar_replicates_scaled_value():
    assert model_material_attribute(6,[.25,.5,.75],scale=2.)==(.5,1.,1.5,0.)
    assert model_material_attribute(26,[2.],scale=.5)==(1.,)*4

@pytest.mark.parametrize('field,values,scale',[
    (17,[1.,2.,3.],1.),
])
def test_model_attribute_requires_observed_typed_field_and_scale(field,values,scale):
    with pytest.raises(ParticleExpressionRefused):model_material_attribute(field,values,scale=scale)


def resolved(code=None,**changes):
    args=dict(primary_vectors={},secondary_vectors={},primary_integers={},secondary_integers={},
        attribute_coverage='complete_primary_and_secondary_v1',expression_module_sha256=MODULE_SHA256)
    args.update(changes)
    return resolved_material_variable(code or attr('$COLOR')+b'\0',(1.,1.,1.,0.),(-10.,)*4,(10.,)*4,3,**args)


def test_complete_missing_context_uses_explicit_property_fallback():
    assert resolved()==(1.,1.,1.)
    with pytest.raises(ParticleExpressionRefused,match='Unbound'):
        particle_vector(attr('$COLOR')+b'\0',{})


def test_both_vector_contexts_precede_integer_contexts():
    assert resolved(primary_integers={'$COLOR':7},secondary_vectors={'$COLOR':(.25,.5,.75,0)})==(.25,.5,.75)
    assert resolved(primary_vectors={'$COLOR':(2,3,4,0)},secondary_vectors={'$COLOR':(5,6,7,0)})==(2.,3.,4.)
    assert resolved(primary_integers={'$COLOR':3},secondary_integers={'$COLOR':8})==(3.,)*3


def test_native_clamp_and_requested_lane_count_are_preserved():
    result=resolved_material_variable(attr('$SELFILLUM')+b'\0',(0.,)*4,(0.,)*4,(10.,)*4,1,
        primary_vectors={'$SELFILLUM':(50,2,3,4)},secondary_vectors={},primary_integers={},secondary_integers={},
        attribute_coverage='complete_primary_and_secondary_v1',expression_module_sha256=MODULE_SHA256)
    assert result==(10.,)
    assert resolved(primary_vectors={'$COLOR':(-20,20,.5,9)})==(-10.,10.,.5)
    result=resolved_material_variable(attr('$COLOR')+b'\0',(0.,)*4,(0.,)*4,(1.,)*4,1,
        primary_vectors={'$COLOR':(-0.,)*4},secondary_vectors={},primary_integers={},secondary_integers={},
        attribute_coverage='complete_primary_and_secondary_v1',expression_module_sha256=MODULE_SHA256)
    assert struct.pack('<f',result[0])==b'\0'*4


@pytest.mark.parametrize('changes',[
    dict(expression_module_sha256='0'*64),
])
def test_missing_provenance_or_malformed_context_is_never_defaulted(changes):
    with pytest.raises(ParticleExpressionRefused):resolved(**changes)


@pytest.mark.parametrize('code',[
    b'\x19\x01\x02\x03\x04\0',
])
def test_unbound_first_instruction_cannot_hide_invalid_remaining_program(code):
    with pytest.raises(ParticleExpressionRefused):resolved(code)
