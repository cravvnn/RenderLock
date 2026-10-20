import copy
import pytest
from agr.source_material_groups import (material_group_token,material_group_table,
    material_group_index,material_group_skin_token,material_group_replacements,material_group_track)


@pytest.mark.parametrize('name,expected',[('',0),('DefaULT',1977497166),('costume_BLUE',1968600058),
    ('smoke',575731315),('healing',2352586474),('ultimate',3287023105),
    ('Älpha',237818703),('βeta',183783147),('SkIn_Ω',2067355686)])
def test_tokens_match_pinned_native_hash_and_ascii_byte_case_folding(name,expected):
    assert material_group_token(name)==expected


def groups():
    return material_group_table(dict(m_materialGroups=[
        dict(m_name='default',m_materials=['models/head.vmat','models/body.vmat','models/glass.vmat']),
        dict(m_name='alternate',m_materials=['models/head_alt.vmat',None]),
        dict(m_name='ALTERNATE',m_materials=['models/other_head.vmat'])]))


def test_selection_is_per_original_slot_with_null_and_short_row_fallback():
    table=groups();before=copy.deepcopy(table);token=material_group_token('alternate')
    assert material_group_index(table,token)==1  # Native first token match.
    assert material_group_replacements(table,token)=={'models/head.vmat_c':'models/head_alt.vmat_c'}
    assert material_group_replacements(table,0)=={}
    assert material_group_replacements(table,0xffffffff)=={}
    assert material_group_replacements(table,table[0]['token'])=={}
    assert table==before


def test_no_authored_groups_or_invalid_skin_index_returns_source_token_zero():
    assert material_group_skin_token([],1)==0  # Current Wraith declaration/model layout.
    table=groups()
    assert material_group_skin_token(table,-1)==0
    assert material_group_skin_token(table,len(table))==0
    assert material_group_skin_token(table,1)==material_group_token('alternate')


def test_recorded_group_changes_and_missing_coverage_are_explicit():
    table=groups();samples=[dict(tick=1,network_render_state={'material_group':0}),
        dict(tick=2,network_render_state={'material_group':table[1]['token']})]
    result=material_group_track(table,samples)
    assert result['status']=='resolved_source_slots'
    assert result['selections'][0]['replacements']=={}
    assert result['selections'][1]['replacements']=={'models/head.vmat_c':'models/head_alt.vmat_c'}
    samples[1]['network_render_state'].pop('material_group')
    with pytest.raises(ValueError,match='coverage has gaps'):material_group_track(table,samples)
    assert material_group_track(table,[dict(tick=1)])=={'status':'not_recorded','selections':[]}
    assert material_group_track([],[dict(tick=1)])=={'status':'no_authored_groups','selections':[]}


@pytest.mark.parametrize('value',[-1,1<<32,True,1.5,'1',None])
def test_recorded_values_are_not_numeric_skin_indices_or_lossy_numbers(value):
    with pytest.raises(ValueError):material_group_index(groups(),value)


@pytest.mark.parametrize('data',[
    {'m_materialGroups':None},{'m_materialGroups':[None]},
    {'m_materialGroups':[{'m_name':'x','m_materials':None}]},
    {'m_materialGroups':[{'m_name':'x','m_materials':['../head.vmat']}]},
    {'m_materialGroups':[{'m_name':'x','m_materials':['C:/head.vmat']}]},
    {'m_materialGroups':[{'m_name':'x','m_materials':['models/head.vmat']*2}]},
    {'m_materialGroups':[{'m_name':'x'*128,'m_materials':[]}]},
])
def test_unqualified_group_tables_fail_before_resolution(data):
    with pytest.raises(ValueError):material_group_table(data)
