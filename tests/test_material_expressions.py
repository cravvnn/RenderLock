import copy
import struct
from types import SimpleNamespace
import numpy as np
import pytest
from agr.material_expressions import constant_vector,expression_contract,apply_vector_overrides,verify_expression_module,UnqualifiedExpression
from agr.source_material_routes import source_material_contract
from agr.npr_authored_albedo import neutral_tint_controls,DEFAULT_SHADER_SHA256


def literal(value):return b'\x07'+struct.pack('<f',value)
def vector(*values):return b''.join(literal(v) for v in values)+bytes([6,{2:26,3:25,4:24}[len(values)],0,0])


def authored(code,name='g_vAlbedoContrastSaturationBrightness1'):
    return dict(m_shaderName='pbr.vfx',m_intParams=[dict(m_name='F_USE_NPR_LIGHTING',m_nValue=1)],
                m_dynamicParams=[dict(m_name=name,m_value=list(code))])


def test_actual_ratking_constant_matches_current_native_vm_receipt():
    code=bytes([7,184,30,133,63,7,0,0,64,63,7,0,0,64,64,6,25,0,0])
    assert constant_vector(code)==[1.0399999618530273,.75,3,0]
    source=authored(code);before=copy.deepcopy(source);contract=source_material_contract(source,resolve_constants=True)
    assert source==before and contract['runtime']['status']=='constant_authored_expressions'
    assert not contract['fidelity_gaps']
    evaluation=contract['expression_evaluation'];parameters=apply_vector_overrides({},evaluation)
    with pytest.raises(ValueError,match='authenticated original texture average'):
        neutral_tint_controls(parameters,assume_white_instance_color=True,shader_sha256=DEFAULT_SHADER_SHA256)
    controls=neutral_tint_controls(parameters,assume_white_instance_color=True,shader_sha256=DEFAULT_SHADER_SHA256,texture_average=[.2,.3,.4])
    assert not np.allclose(controls['correction_matrix'],np.eye(4))


@pytest.mark.parametrize('values',[(1,2),(-1,.75,3),(1,2,3,4)])
def test_vector_constructor_retains_native_zero_fill(values):
    assert constant_vector(vector(*values))==[*values,*([0.]*(4-len(values)))]


@pytest.mark.parametrize('code',[b'',b'\x07',literal(float('nan'))+b'\0',literal(1),literal(1)+b'\0\0',vector(1,2)[:-1],b'\x06\x19\0\0',literal(1)+b'\x06\x19\x01\0'])
def test_malformed_or_incomplete_constant_program_does_not_become_literal_default(code):
    with pytest.raises(ValueError):constant_vector(code)


def test_attribute_color_binding_remains_explicit_and_generic_consumers_are_not_claimed_applied():
    code=bytes([25,247,248,161,196,0])
    with pytest.raises(UnqualifiedExpression):constant_vector(code)
    contract=source_material_contract(authored(code,'g_vColorTint1'),resolve_constants=True)
    assert contract['runtime']['status']=='unbound_static_reference'
    assert contract['expression_evaluation']['programs'][0]['status']=='unbound_runtime_expression'
    generic=authored(vector(1,2,3));generic['m_intParams']=[]
    contract=source_material_contract(generic,resolve_constants=True)
    assert contract['expression_evaluation']['vector_overrides']=={}
    assert contract['expression_evaluation']['programs'][0]['status']=='unbound_parameter_consumer'


def test_evaluator_module_must_match_current_qualified_instructions(tmp_path):
    path=tmp_path/'game/bin/win64/materialsystem2.dll';path.parent.mkdir(parents=True);path.write_bytes(b'updated module')
    with pytest.raises(ValueError,match='requalification'):verify_expression_module(SimpleNamespace(install_path=tmp_path))


def test_unqualified_module_reports_actual_identity_without_accepting_it(tmp_path):
    import hashlib
    from agr.material_expressions import expression_module_status,QUALIFIED_MODULE_SHA256
    path=tmp_path/'game/bin/win64/materialsystem2.dll';path.parent.mkdir(parents=True)
    path.write_bytes(b'future unqualified instructions')
    provider=SimpleNamespace(install_path=tmp_path)
    status=expression_module_status(provider)
    assert status['status']=='requalification_required'
    assert status['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert set(status['qualified_sha256'])==QUALIFIED_MODULE_SHA256
    with pytest.raises(ValueError,match='requalification') as error:verify_expression_module(provider)
    assert error.value.details['code']=='material_expression_evaluator_unqualified'
    assert error.value.details['sha256']==status['sha256']


@pytest.mark.parametrize('module',[
    '4c96327f70565633b421bfe799cd540241327e98c343fd9ff129011ed6936557',
    '26a029e4e33db12801ebab80ea2ad65536e6d1e19889c810edc59637edd78e74',
])
def test_only_qualified_module_identities_share_original_constant_profile(monkeypatch,module):
    from agr import asset_converter,material_expressions as expressions
    monkeypatch.setattr(asset_converter,'sha',lambda path:module)
    identity=expressions.verify_expression_module(SimpleNamespace(install_path='fixture-install'))
    assert identity==dict(resource=expressions.MODULE_RESOURCE,sha256=module,semantics_sha256=expressions.MODULE_SHA256)
    expressions.validate_expression_module_identity(identity)
    assert expressions.expression_contract(authored(vector(1,2,3)))['module_sha256']==expressions.MODULE_SHA256


@pytest.mark.parametrize('install',[None,'missing-install'])
def test_absent_module_is_a_reported_unavailable_capability(install):
    from agr.material_expressions import expression_module_status
    provider=SimpleNamespace(install_path=install)
    status=expression_module_status(provider)
    assert status['status']=='requalification_required' and status['sha256'] is None
    with pytest.raises(ValueError,match='requalification'):verify_expression_module(provider)


@pytest.mark.parametrize('mutation',[
    {'sha256':'f'*64},{'sha256':[]},{'resource':'other.dll'},
    {'semantics_sha256':'f'*64},{'extra':True},
])
def test_retained_evaluator_provenance_rejects_unknown_or_malformed_identity(mutation):
    from agr import material_expressions as expressions
    identity=dict(resource=expressions.MODULE_RESOURCE,sha256=expressions.MODULE_SHA256,semantics_sha256=expressions.MODULE_SHA256)
    identity.update(mutation)
    with pytest.raises(ValueError,match='provenance'):expressions.validate_expression_module_identity(identity)
