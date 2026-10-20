"""Bounded constant-vector subset of the qualified Source material evaluator.

Only FLOAT, float2/3/4 and RETURN execute here. Attribute/time/feature programs
retain an explicit unbound outcome. No network field is guessed from its name.
"""
import hashlib
import math
import struct
from pathlib import Path

MODULE_SHA256='4c96327f70565633b421bfe799cd540241327e98c343fd9ff129011ed6936557'
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


def verify_expression_module(provider):
    from .asset_converter import sha,ConversionError
    install=getattr(provider,'install_path',None)
    if install is None or sha(Path(install)/'game/bin/win64/materialsystem2.dll')!=MODULE_SHA256:
        raise ConversionError('Material expression evaluator module requires requalification')


def apply_vector_overrides(parameters,contract):
    result={key:dict(value) for key,value in parameters.items()}
    result.setdefault('VectorParams',{}).update(contract['vector_overrides'])
    return result
