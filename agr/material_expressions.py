"""Bounded constant-vector subset of the qualified Source material evaluator.

Only FLOAT, float2/3/4 and RETURN execute here. Attribute/time/feature programs
retain an explicit unbound outcome. No network field is guessed from its name.
"""
import hashlib
import math
import struct
from pathlib import Path

MODULE_SHA256='4c96327f70565633b421bfe799cd540241327e98c343fd9ff129011ed6936557'
# The original hash remains the semantic profile recorded in saved contracts.
# Both exact modules were checked against the same bounded native constant VM;
# this does not qualify runtime attributes, time, or other functions/opcodes.
QUALIFIED_MODULE_SHA256=frozenset({
    MODULE_SHA256,
    '26a029e4e33db12801ebab80ea2ad65536e6d1e19889c810edc59637edd78e74',
})
MODULE_RESOURCE='game/bin/win64/materialsystem2.dll'
COLOR_PARAMETERS={'g_vAlbedoContrastSaturationBrightness1','g_vColorTint1'}


class UnqualifiedExpression(ValueError):pass


def constant_vector(code):
    if not isinstance(code,bytes) or not 1<=len(code)<=256:
        raise ValueError('Material expression requires1..256 bytes')
    stack=[];offset=0;instructions=0
    def read(count):
        nonlocal offset
        if offset+count>len(code):raise ValueError('Truncated material expression')
        value=code[offset:offset+count];offset+=count;return value
    while offset<len(code):
        instructions+=1
        if instructions>64:raise ValueError('Material expression instruction bound exceeded')
        op=read(1)[0]
        if op==0:
            if offset!=len(code) or len(stack)!=1:raise ValueError('Invalid material expression return')
            return stack[0]
        if op==7:
            value=struct.unpack('<f',read(4))[0]
            if not math.isfinite(value):raise ValueError('Nonfinite material expression literal')
            stack.append([value]*4)
        elif op==6:
            function,check=read(2)
            if check:raise ValueError('Malformed material expression function signature')
            count={24:4,25:3,26:2}.get(function)
            if count is None:raise UnqualifiedExpression('Unqualified material expression function: '+str(function))
            if len(stack)<count:raise ValueError('Material expression stack underflow')
            values=[value[0] for value in stack[-count:]]
            del stack[-count:];stack.append(values+[0.]*(4-count))
        else:raise UnqualifiedExpression('Unbound material expression opcode: '+str(op))
        if len(stack)>32:raise ValueError('Material expression stack bound exceeded')
    raise ValueError('Missing material expression return')


def expression_contract(authored,*,allowed_colors=COLOR_PARAMETERS):
    entries=authored.get('m_dynamicParams',[])
    if not isinstance(entries,list) or len(entries)>64:raise ValueError('Bounded dynamic material parameter inventory required')
    rows=[];overrides={};seen=set()
    for entry in entries:
        name=entry.get('m_name');values=entry.get('m_value')
        if not isinstance(name,str) or not name or name in seen:raise ValueError('Distinct named material expressions required')
        seen.add(name)
        if (not isinstance(values,list) or not 1<=len(values)<=256
                or any(type(value) is not int or not 0<=value<=255 for value in values)):
            raise ValueError('Bounded material expression byte array required')
        code=bytes(values);row=dict(parameter=name,program_sha256=hashlib.sha256(code).hexdigest())
        if name not in allowed_colors:
            row.update(status='unbound_parameter_consumer',reason='Material response consumer not qualified')
        else:
            try:value=constant_vector(code)
            except UnqualifiedExpression as exc:row.update(status='unbound_runtime_expression',reason=str(exc))
            else:
                row.update(status='evaluated_constant_color',value=value)
                overrides[name]=value
        rows.append(row)
    return dict(schema_version=1,mode='bounded_source_constant_vectors',module_sha256=MODULE_SHA256,
                programs=rows,vector_overrides=overrides)


def expression_module_status(provider):
    """Report an exact module capability, independently of selected materials."""
    from .asset_converter import sha
    install=getattr(provider,'install_path',None)
    result=dict(resource=MODULE_RESOURCE,sha256=None,semantics_sha256=MODULE_SHA256,
                qualified_sha256=sorted(QUALIFIED_MODULE_SHA256),status='requalification_required',
                scope='Bounded constant colors only; selected material requirements remain unchecked.')
    if install is None:
        result['reason']='Installed game path is unavailable'
        return result
    try:
        result['sha256']=sha(Path(install)/MODULE_RESOURCE)
    except OSError as exc:
        result['reason']='Material expression evaluator module could not be read: '+type(exc).__name__
        return result
    if result['sha256'] in QUALIFIED_MODULE_SHA256:
        result['status']='ready'
    else:
        result['reason']='Material expression evaluator module requires requalification'
    return result


def verify_expression_module(provider):
    from .asset_converter import ConversionError
    identity=expression_module_status(provider)
    if identity['status']!='ready':
        error=ConversionError('Material expression evaluator module requires requalification')
        error.details=dict(code='material_expression_evaluator_unqualified',**identity)
        raise error
    return {key:identity[key] for key in ('resource','sha256','semantics_sha256')}


def validate_expression_module_identity(identity):
    """Validate retained provenance without opening a local game installation."""
    from .asset_converter import ConversionError
    if (not isinstance(identity,dict) or set(identity)!={'resource','sha256','semantics_sha256'}
            or identity['resource']!=MODULE_RESOURCE or not isinstance(identity['sha256'],str)
            or identity['sha256'] not in QUALIFIED_MODULE_SHA256
            or identity['semantics_sha256']!=MODULE_SHA256):
        raise ConversionError('Unqualified material expression evaluator provenance')


def apply_vector_overrides(parameters,contract):
    result={key:dict(value) for key,value in parameters.items()}
    result.setdefault('VectorParams',{}).update(contract['vector_overrides'])
    return result
