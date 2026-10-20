"""Source material-group tokens and per-original-material slot remapping.

Mesh-group visibility and skeletal skin/joint indices are independent contracts.
This module resolves identities only; it does not convert or apply a material.
"""
from .npr_material_identity import compiled_resource
from .game_assets import safe_resource_path


def material_group_token(name):
    if not isinstance(name,str) or '\x00' in name:raise ValueError('Material-group name required')
    data=name.encode('utf-8')
    if len(data)>127:raise ValueError('Material-group name exceeds qualified byte bound')
    if not data:return 0
    # CBufferString::ToLowerFast folds ASCII bytes, not Unicode code points.
    data=bytes(value+32 if 65<=value<=90 else value for value in data)
    value=0x31415926^len(data);mask=0xffffffff;multiplier=0x5bd1e995;at=0
    while len(data)-at>=4:
        word=int.from_bytes(data[at:at+4],'little');word=word*multiplier&mask
        word^=word>>24;word=word*multiplier&mask
        value=(value*multiplier&mask)^word;at+=4
    if at<len(data):
        value^=int.from_bytes(data[at:],'little');value=value*multiplier&mask
    value^=value>>13;value=value*multiplier&mask;value^=value>>15
    return value


def material_group_table(data):
    groups=data.get('m_materialGroups',[])
    if not isinstance(groups,list) or len(groups)>64:raise ValueError('Bounded material-group array required')
    result=[]
    for group in groups:
        if not isinstance(group,dict):raise ValueError('Typed material-group row required')
        name=group.get('m_name');token=material_group_token(name);materials=group.get('m_materials')
        if not isinstance(materials,list) or len(materials)>512:raise ValueError('Bounded material-group slots required')
        slots=[]
        for material in materials:
            if material is None or material=='':slots.append(None);continue
            resource=compiled_resource(material,'.vmat');safe_resource_path(resource);slots.append(resource)
        result.append(dict(name=name,token=token,materials=slots))
    if result:
        base=[material for material in result[0]['materials'] if material is not None]
        if len(base)!=len(set(base)):raise ValueError('Duplicate base material slots require native map insertion qualification')
    return result


def material_group_index(groups,token):
    if type(token) is not int or not 0<=token<=0xffffffff:raise ValueError('Recorded material group must be uint32')
    return next((index for index,group in enumerate(groups) if group['token']==token),-1)


def material_group_skin_token(groups,index):
    if type(index) is not int or not -(1<<31)<=index<1<<31:raise ValueError('Typed Source skin index required')
    return groups[index]['token'] if 0<=index<len(groups) else 0


def material_group_replacements(groups,token):
    index=material_group_index(groups,token)
    if index<0:return {}
    base=groups[0]['materials'];target=groups[index]['materials']
    return {source:replacement for source,replacement in zip(base,target)
            if source is not None and replacement is not None and replacement!=source}


def material_group_track(groups,samples):
    observed=[sample for sample in samples if 'material_group' in sample.get('network_render_state',{})]
    if observed and len(observed)!=len(samples):
        raise ValueError('Recorded material-group coverage has gaps; no default substitution')
    if not observed:return dict(status='not_recorded' if groups else 'no_authored_groups',selections=[])
    return dict(status='resolved_source_slots',selections=[dict(tick=sample['tick'],token=sample['network_render_state']['material_group'],
        group_index=material_group_index(groups,sample['network_render_state']['material_group']),
        replacements=material_group_replacements(groups,sample['network_render_state']['material_group'])) for sample in observed])
