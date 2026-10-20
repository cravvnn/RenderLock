"""Typed static material routes; runtime shader fidelity is reported separately."""
from .asset_converter import ConversionError

NPR_ROUTE='npr_indirect'
PBR_ROUTE='generic_pbr_indirect'
UNLIT_ROUTE='converted_unlit_reference'
ROUTES={NPR_ROUTE,PBR_ROUTE,UNLIT_ROUTE}


def source_material_contract(authored,*,resolve_constants=False):
    if authored.get('m_shaderName')!='pbr.vfx':
        raise ConversionError('Material family requires a qualified Source route')
    entries=authored.get('m_intParams',[])
    flags={r['m_name']:r['m_nValue'] for r in entries}
    if len(flags)!=len(entries):raise ConversionError('Duplicate authored material flag')
    for name in ('F_USE_NPR_LIGHTING','F_UNLIT','F_NO_SPECULAR_AT_FULL_ROUGHNESS'):
        if name in flags and (type(flags[name]) is not int or flags[name] not in (0,1)):
            raise ConversionError('Invalid typed material flag: '+name)
    npr=flags.get('F_USE_NPR_LIGHTING',0)==1
    unlit=flags.get('F_UNLIT',0)==1
    if npr and unlit:raise ConversionError('Combined NPR/unlit permutation requires qualification')
    route=NPR_ROUTE if npr else UNLIT_ROUTE if unlit else PBR_ROUTE
    dynamic={key:value for key,value in authored.items() if 'dynamic' in key.lower() and value}
    expression=None;unresolved=set(dynamic)
    if resolve_constants and authored.get('m_dynamicParams'):
        from .material_expressions import expression_contract,COLOR_PARAMETERS
        try:expression=expression_contract(authored,allowed_colors=COLOR_PARAMETERS if route==NPR_ROUTE else set())
        except ValueError as exc:raise ConversionError(str(exc)) from exc
        if all(row['status']=='evaluated_constant_color' for row in expression['programs']):
            unresolved.discard('m_dynamicParams')
    gaps=[]
    for key,label in (
            ('F_GLASS','Source transmission'),('F_ADVANCED_TRANSLUCENCY','Source advanced translucency'),
            ('F_DETAIL','Source detail layers'),('F_SECONDARY_UV','Source secondary UV binding'),
            ('F_ENABLE_TEXTURE_TRANSFORMS','Source authored texture transforms'),
            ('F_SHEEN','Source sheen'),('F_USE_FRONT_FACE_NORMALS_FOR_BACK_FACES','Source front-face normal policy'),
            ('F_JITTER_VERTICES','Source jitter'),('F_SELF_ILLUM','Source runtime self illumination'),
            ('F_USE_STATUS_EFFECTS_PROXY','Source status effects'),('F_SOLID_COLOR_OUTLINE','Source solid outline')):
        if flags.get(key):gaps.append(dict(feature=key,status='not_reconstructed',scope=label))
    if unresolved:gaps.append(dict(feature='authored_dynamic_inputs',status='unbound_static_reference',fields=sorted(unresolved)))
    result=dict(route=route,no_specular_at_full_roughness=bool(flags.get('F_NO_SPECULAR_AT_FULL_ROUGHNESS',0)),
                active_features={key:value for key,value in sorted(flags.items()) if key.startswith('F_') and value},
                runtime=dict(status='unbound_static_reference' if unresolved else 'constant_authored_expressions' if expression else 'static_authored_inputs',
                             dynamic_fields=sorted(dynamic)),fidelity_gaps=gaps)
    if expression is not None:result['expression_evaluation']=expression
    return result


def material_route(row):
    """Legacy packages contain NPR-only rows."""
    return row.get('route',NPR_ROUTE)
