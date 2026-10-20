"""Recorded mesh selection intersects, and never replaces, pawn lifetime."""
from .model_mesh_groups import uint64
from .source_hero_lighting_package import canonical_sha


def validate_imported_mesh_groups(objects,contract):
    content={key:value for key,value in contract.items() if key not in ('content_sha256','model_sha256')}
    if (contract.get('schema_version')!=1 or contract.get('mode')!='source_mesh_group_visibility'
            or canonical_sha(content)!=contract.get('content_sha256')):
        raise ValueError('Prepared model mesh-group metadata changed')
    expected={row['source_mesh_index']:row for row in contract['meshes']};seen=set()
    for obj in objects:
        if obj.type!='MESH':continue
        index=obj.get('agr_source_mesh_index')
        if type(index) is not int or index not in expected or index in seen:
            raise ValueError('Imported mesh/source index coverage differs')
        row=expected[index];seen.add(index)
        if (obj.get('agr_mesh_group_contract')!=contract['content_sha256']
                or obj.get('agr_mesh_group_mask')!=row['mesh_group_mask']
                or obj.get('agr_mesh_default_mask')!=contract['default_mask']
                or obj.get('agr_mesh_groups_unconditional')!=contract['unconditional']):
            raise ValueError('Imported mesh-group annotation differs from source contract')
    if seen!=set(expected):raise ValueError('Imported mesh-group coverage incomplete')


def selection_samples(obj,samples):
    observed=[sample for sample in samples if 'mesh_group_mask' in sample]
    if observed and len(observed)!=len(samples):
        raise ValueError('Recorded mesh-group mask coverage has gaps; no default substitution')
    default=uint64(obj['agr_mesh_default_mask']);mask=uint64(obj['agr_mesh_group_mask'])
    values=[(sample['tick'],uint64(sample['mesh_group_mask'])) for sample in observed]
    return mask,default,values


def selected_visibility(obj,entity,samples,data,fps,lifetime):
    mask,default,values=selection_samples(obj,samples)
    unconditional=obj.get('agr_mesh_groups_unconditional')
    if type(unconditional) is not bool:raise ValueError('Typed mesh-group policy required')
    frame=lambda tick:1+(tick-data['start_tick'])*fps/data['tick_rate']
    states={frame(tick):value for tick,value in values}
    availability=dict(lifetime);hidden=True;selected=default;rows=[]
    for current in sorted(set(states)|set(availability)):
        hidden=availability.get(current,hidden);selected=states.get(current,selected)
        state=hidden or not (unconditional or bool(mask&selected))
        if not rows or rows[-1][1]!=state:rows.append((current,state))
    return rows


def mesh_group_summary(objects,samples):
    meshes=[obj for obj in objects if obj.type=='MESH' and obj.get('agr_mesh_group_contract') is not None]
    if not meshes:return dict(status='not_prepared')
    _,default,observed=selection_samples(meshes[0],samples)
    unconditional=all(obj['agr_mesh_groups_unconditional'] for obj in meshes)
    selections=observed or [(samples[0]['tick'],default)]
    return dict(status='baked_constant_visibility',basis='absent_mesh_masks_reference' if unconditional else
                'recorded_mesh_group_masks' if observed else 'model_default_reference',
        mesh_count=len(meshes),default_mask=str(default),
        selections=[dict(tick=tick,mask=str(mask),visible_mesh_count=sum(
            obj['agr_mesh_groups_unconditional'] or bool(uint64(obj['agr_mesh_group_mask'])&mask) for obj in meshes))
            for tick,mask in selections],
        limits='Recorded masks held between samples; model default is a static reference when no mask was recorded. Skin/material-group selection is unchanged.')


def expected_mesh_group_summary(contract,samples):
    class Mesh(dict):type='MESH'
    objects=[Mesh(agr_mesh_group_contract=contract['content_sha256'],
                  agr_mesh_group_mask=row['mesh_group_mask'],agr_mesh_default_mask=contract['default_mask'],
                  agr_mesh_groups_unconditional=contract['unconditional']) for row in contract['meshes']]
    return mesh_group_summary(objects,samples)


def validate_mesh_group_consent(summary,*,poses_requested,partial):
    if poses_requested and summary.get('basis')=='model_default_reference' and not partial:
        raise ValueError('Recorded mesh-group state unavailable; animated characters require explicit --partial for a model-default reference.')
