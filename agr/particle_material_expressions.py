"""Bounded particle attributes in the current Source material vector VM.

Every attribute must be explicit. This evaluates data; it does not establish
runtime attribute ownership, shader time, lighting or rendered appearance.
"""
import hashlib
import json
import math
import struct

MODULE_SHA256='8131186b65519fdc03b852e599f482a5ffa6ca0a00a9be523a71d1784968fe85'
SLIDE_MATERIAL_SHA256='d40795ca9c9a3f7e2b15e4d8df479145313d8579b23d2265c9f96f37cc0265c7'
SLIDE_PARSED_SHA256='6f710d60cc8181d5431f09df9a2d90ac5a196ded051a1fbc8dd441965565cf64'
ATTRIBUTE_TOKENS={'$COLOR':0xc4a1f8f7,'$SELFILLUM':0xbad34216,'$SCROLLSPEED':0xadfbce51}

class ParticleExpressionRefused(ValueError):pass

def _vector(value):
    if type(value)not in(list,tuple)or len(value)!=4:
        raise ParticleExpressionRefused('Explicit four-lane particle attribute required')
    try:
        if any(type(v)not in(int,float)or not math.isfinite(v)for v in value):raise ValueError
        result=struct.unpack('<4f',struct.pack('<4f',*value))
        if not all(math.isfinite(v)for v in result):raise ValueError
    except (ValueError,OverflowError,struct.error)as exc:
        raise ParticleExpressionRefused('Finite binary32 particle attribute required')from exc
    return result

def particle_vector(code,attributes):
    """FLOAT/ATTRIBUTE/float2,3,4/RETURN with strict bounds and no defaults."""
    if type(code)is not bytes or not 1<=len(code)<=256:
        raise ParticleExpressionRefused('Material program requires1..256 bytes')
    if type(attributes)is not dict or set(attributes)-ATTRIBUTE_TOKENS.keys():
        raise ParticleExpressionRefused('Unknown particle material attribute')
    values={ATTRIBUTE_TOKENS[k]:_vector(v)for k,v in attributes.items()}
    stack=[];offset=0;instructions=0
    def read(n):
        nonlocal offset
        if offset+n>len(code):raise ParticleExpressionRefused('Truncated particle material program')
        raw=code[offset:offset+n];offset+=n;return raw
    while offset<len(code):
        instructions+=1
        if instructions>64:raise ParticleExpressionRefused('Particle material instruction bound exceeded')
        op=read(1)[0]
        if op==0:
            if offset!=len(code)or len(stack)!=1:raise ParticleExpressionRefused('Invalid particle material return')
            return stack[0]
        if op==7:
            v=struct.unpack('<f',read(4))[0]
            if not math.isfinite(v):raise ParticleExpressionRefused('Nonfinite particle material literal')
            stack.append((v,)*4)
        elif op==25:
            token=struct.unpack('<I',read(4))[0]
            if token not in ATTRIBUTE_TOKENS.values():
                raise ParticleExpressionRefused('Unqualified particle material attribute: '+hex(token))
            if token not in values:raise ParticleExpressionRefused('Unbound particle material attribute: '+hex(token))
            stack.append(values[token])
        elif op==6:
            function=struct.unpack('<H',read(2))[0];count={24:4,25:3,26:2}.get(function)
            if count is None:raise ParticleExpressionRefused('Unqualified particle material function: '+str(function))
            if len(stack)<count:raise ParticleExpressionRefused('Particle material stack underflow')
            v=tuple(a[0]for a in stack[-count:])+(0.,)*(4-count)
            del stack[-count:];stack.append(v)
        else:raise ParticleExpressionRefused('Unqualified particle material opcode: '+str(op))
        if len(stack)>32:raise ParticleExpressionRefused('Particle material stack bound exceeded')
    raise ParticleExpressionRefused('Missing particle material return')

def resolved_material_variable(code,fallback,minimum,maximum,components,*,
                               primary_vectors,secondary_vectors,primary_integers,secondary_integers,
                               attribute_coverage,expression_module_sha256):
    """Evaluate a qualified artist variable with explicit complete contexts.

    Native 17e70/2ee40/2cd40/2c350 searches both vector containers before
    integers, uses the supplied stored property value when lookup fails, then
    clamps and uploads only the requested lanes. Caller must qualify the actual
    descriptor, property, primary/secondary ownership and their complete capture.
    This pure evaluator establishes none of those identities and admits no
    renderer by itself. It has no persistent state or independent lifecycle.
    """
    if (expression_module_sha256!=MODULE_SHA256
            or attribute_coverage!='complete_primary_and_secondary_v1'
            or type(components)is not int or not 1<=components<=4):
        raise ParticleExpressionRefused('Qualified module and complete material contexts required')
    default=_vector(fallback);lower=_vector(minimum);upper=_vector(maximum)
    if any(a>b for a,b in zip(lower,upper)):
        raise ParticleExpressionRefused('Invalid material variable clamp range')
    vectors=[];integers=[]
    for container in(primary_vectors,secondary_vectors):
        if type(container)is not dict or set(container)-ATTRIBUTE_TOKENS.keys():
            raise ParticleExpressionRefused('Explicit complete material vector container required')
        vectors.append({name:_vector(value)for name,value in container.items()})
    for container in(primary_integers,secondary_integers):
        if (type(container)is not dict or set(container)-ATTRIBUTE_TOKENS.keys()
                or any(type(v)is not int or not -(2**31)<=v<2**31 for v in container.values())):
            raise ParticleExpressionRefused('Explicit complete int32 material container required')
        integers.append({name:_vector((value,)*4)for name,value in container.items()})
    resolved={}
    for container in(*vectors,*integers):
        for name,value in container.items():resolved.setdefault(name,value)
    return _evaluate_material_variable(code,default,lower,upper,components,resolved)


def _evaluate_material_variable(code,default,lower,upper,components,resolved):
    # Validate the entire supported program before a missing binding can use its
    # fallback. Unknown or malformed later instructions remain errors.
    particle_vector(code,{name:(0.,)*4 for name in ATTRIBUTE_TOKENS})
    try:value=particle_vector(code,resolved)
    except ParticleExpressionRefused as error:
        if not str(error).startswith('Unbound particle material attribute: '):raise
        value=default
    output=[]
    for v,lo,hi in zip(value[:components],lower,upper):
        # MAXSS/MINSS choose the second operand on equality, including signed0.
        v=v if v>lo else lo
        output.append(v if v<hi else hi)
    return tuple(output)


def resolved_material_query_variable(code,fallback,minimum,maximum,components,*,
                                    queries,attribute_coverage,expression_module_sha256):
    """Evaluate complete per-token lookup queries without inventing whole maps.

    Each token retains the exact queried prefix in Pvector/Svector/Pint/Sint
    order. An absent token must prove all four stages; skipped later maps are
    never described as empty. The caller must separately qualify source identity,
    current material/property/program, replay time and before/after CPU cohort.
    These numeric inputs alone do not authorize a renderer or a stored default.
    """
    if (expression_module_sha256!=MODULE_SHA256
            or attribute_coverage!='native_order_token_queries_v1'
            or type(components)is not int or not 1<=components<=4
            or type(queries)is not dict or set(queries)!=set(ATTRIBUTE_TOKENS)):
        raise ParticleExpressionRefused('Qualified module and complete token queries required')
    default=_vector(fallback);lower=_vector(minimum);upper=_vector(maximum)
    if any(a>b for a,b in zip(lower,upper)):
        raise ParticleExpressionRefused('Invalid material variable clamp range')
    resolved={}
    for name,query in queries.items():
        if (type(query)is not dict or type(query.get('present'))is not bool
                or type(query.get('origin'))is not int or not 0<=query['origin']<=4):
            raise ParticleExpressionRefused('Explicit typed material query required')
        origin=query['origin'];checked=query.get('checked_stages')
        if (query['present']!=bool(origin) or type(checked)is not list
                or any(type(stage)is not int for stage in checked)
                or checked!=list(range(1,(origin or 4)+1))):
            raise ParticleExpressionRefused('Incomplete native material lookup prefix')
        fields={'present','origin','checked_stages'}
        if not origin:
            if set(query)!=fields:raise ParticleExpressionRefused('Absent query contains a value')
            continue
        if origin<=2:
            bits=query.get('raw_bits')
            if (set(query)!=fields|{'kind','raw_bits'} or query.get('kind')!='vector'
                    or type(bits)is not list or len(bits)!=4
                    or any(type(value)is not int or not 0<=value<2**32 for value in bits)):
                raise ParticleExpressionRefused('Explicit binary32 vector query required')
            resolved[name]=_vector(struct.unpack('<4f',struct.pack('<4I',*bits)))
        else:
            value=query.get('value')
            if (set(query)!=fields|{'kind','value'} or query.get('kind')!='integer'
                    or type(value)is not int or not -(2**31)<=value<2**31):
                raise ParticleExpressionRefused('Explicit int32 query required')
            resolved[name]=_vector((value,)*4)
    return _evaluate_material_variable(code,default,lower,upper,components,resolved)


def model_material_attribute(field,values,*,scale):
    """Pack one explicit model-var field; runtime binding/scale must be observed.

    Current slide material uses vector6 and scalar18/26. Native RGB packing has
    a zero fourth lane; scalar packing replicates its scaled value four times.
    """
    if type(field)is not int or field not in(6,18,26):
        raise ParticleExpressionRefused('Unqualified model material field')
    if type(values)not in(list,tuple)or len(values)!=(3 if field==6 else 1):
        raise ParticleExpressionRefused('Complete model material field required')
    coefficient=_vector((scale,)*4)[0]
    source=_vector(tuple(values)+(0.,)*(4-len(values)))
    result=_vector(tuple(source[i]*coefficient for i in range(len(values)))+(0.,)*(4-len(values)))
    return result if field==6 else(result[0],)*4

def slide_material_variables(authored,attributes,*,material_sha256,expression_module_sha256):
    """Evaluate the pinned slide material; missing hot COLOR remains unbound.

    Callers must qualify their actual attribute sources separately. Supplying a
    white COLOR for an unobserved renderer binding is not done by this function.
    """
    if material_sha256!=SLIDE_MATERIAL_SHA256 or expression_module_sha256!=MODULE_SHA256:
        raise ParticleExpressionRefused('Particle material/evaluator requires qualification')
    try:digest=hashlib.sha256(json.dumps(authored,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    except (ValueError,TypeError)as exc:raise ParticleExpressionRefused('Invalid authored particle material')from exc
    if digest!=SLIDE_PARSED_SHA256:raise ParticleExpressionRefused('Slide material source changed')
    return {entry['m_name']:particle_vector(bytes(entry['m_value']),attributes)for entry in authored['m_dynamicParams']}
