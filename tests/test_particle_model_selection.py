import copy
import pytest
from agr.installed_effects import UnsupportedEffect
from agr.particle_model_bindings import POLICY
from agr.particle_model_selection import authored_bodygroup_selection


def inputs():
    # Source/GLTF ordering is deliberately different from bodygroup choice order.
    groups=['team_@0_#&hidden','team_@1_#&arch','extra_@0_#&off','extra_@1_#&on']
    inv=dict(schema_version=1,policy=POLICY,resource='models/mask.vmdl',source_sha256='a'*64,
        mesh_groups=groups,body_groups=[dict(name='team',choices=[dict(group_index=i,name=n,full_name=groups[i])for i,n in ((0,'hidden'),(1,'arch'))]),
            dict(name='extra',choices=[dict(group_index=i,name=n,full_name=groups[i])for i,n in ((2,'off'),(3,'on'))])],
        meshes=[dict(source_mesh_index=i,mesh_group_mask=str(m))for i,m in ((0,2),(1,1),(2,8),(3,4))])
    bound=dict(policy=POLICY,resource=inv['resource'],source_sha256='a'*64,
        meshes=[dict(row,gltf_mesh_index=3-row['source_mesh_index'])for row in inv['meshes']])
    return inv,bound


def select(inv,bound,group=0,choice=1,mask=5):
    return authored_bodygroup_selection(inv,bound,initial_mask=mask,body_group=group,submodel=choice)


def test_choice_uses_authored_bits_and_preserves_other_bodygroup():
    inv,bound=inputs();before=copy.deepcopy((inv,bound))
    result=select(inv,bound)
    assert result['selected_mask']=='6' and result['source_mesh_indices']==[0,3]
    assert result['gltf_mesh_indices']==[3,0] and (inv,bound)==before
    assert select(inv,bound,group=1,choice=1)['selected_mask']=='9'


@pytest.mark.parametrize('group,choice',[(0,-1),(0,2),(-1,0),(2,0)])
def test_invalid_native_indices_keep_initial_mask(group,choice):
    inv,bound=inputs();assert select(inv,bound,group,choice)['selected_mask']=='5'


def test_native_truncation_happens_after_float32_rounding():
    inv,bound=inputs()
    assert select(inv,bound,choice=-.2)['submodel']==0
    assert select(inv,bound,choice=1.9)['submodel']==1
    assert select(inv,bound,choice=.999999999)['submodel']==1


@pytest.mark.parametrize('value',[True,None,float('nan'),float('inf'),1e40,2**31])
def test_unknown_and_unbounded_attributes_are_not_defaulted(value):
    inv,bound=inputs()
    with pytest.raises(UnsupportedEffect):select(inv,bound,group=value)


@pytest.mark.parametrize('tamper',['fingerprint','choice_name','duplicate_choice','overlap','mesh_mask','mesh_duplicate'])
def test_changed_or_ambiguous_metadata_is_refused(tamper):
    inv,bound=inputs()
    if tamper=='fingerprint':bound['source_sha256']='b'*64
    elif tamper=='choice_name':inv['body_groups'][0]['choices'][0]['full_name']='other'
    elif tamper=='duplicate_choice':inv['body_groups'][0]['choices'][1]=copy.deepcopy(inv['body_groups'][0]['choices'][0])
    elif tamper=='overlap':inv['body_groups'][1]['choices'][0]=copy.deepcopy(inv['body_groups'][0]['choices'][0])
    elif tamper=='mesh_mask':bound['meshes'][0]['mesh_group_mask']='1'
    elif tamper=='mesh_duplicate':bound['meshes'][0]['gltf_mesh_index']=bound['meshes'][1]['gltf_mesh_index']
    with pytest.raises(UnsupportedEffect):select(inv,bound)
