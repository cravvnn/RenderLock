import json
import pytest
from agr.cli import read_blender_completion


def completion(tmp_path,**stages):
    segment=tmp_path/'segment.json';output=tmp_path/'scene.blend';path=tmp_path/'scene.scene.json'
    data=dict(start_tick=10,end_tick=12,tick_rate=64,coverage={'animation':'not_requested'},
              entities=[dict(id='actor',hero_id=15)],
              npr_materials=dict(entities=[dict(entity_id='actor',model_sha256='model')],
                 models={'model':{'materials':[{'material':'body.vmat_c'},{'material':'eyes.vmat_c'}]}}))
    report=dict(schema_version=1,segment=str(segment),output=str(output),start_tick=10,end_tick=12,
                tick_rate=64,fps=64,warnings=[],entities=[dict(id='actor',model_status='imported',pose={'status':'not requested'})],**stages)
    path.write_text(json.dumps(report))
    return path,dict(segment=segment,output=output,data=data,fps=64)


@pytest.mark.parametrize('stage',['character_shader','map','map_lighting'])
def test_requested_flag_cannot_become_missing_completion_stage(tmp_path,stage):
    path,job=completion(tmp_path)
    with pytest.raises(RuntimeError,match='lacks requested'):
        read_blender_completion(path,**job,partial=True,requested_stages=[stage])


@pytest.mark.parametrize('stage',['npr_lighting','character_shader'])
def test_actor_presence_cannot_hide_dropped_selected_material(tmp_path,stage):
    row={'status':'partial'}
    if stage=='npr_lighting':
        material={'source_material':'body.vmat_c'}
        row['preparation']={'actors':[{'entity_id':'actor','materials':[material]}],'materials':[material]}
    else:row['materials']=[{'entity_id':'actor','material':'body.vmat_c'}]
    path,job=completion(tmp_path,**{stage:row})
    with pytest.raises(RuntimeError,match='coverage'):
        read_blender_completion(path,**job,requested_stages=[stage])


def test_follow_camera_must_cover_requested_range_and_actor(tmp_path):
    path,job=completion(tmp_path,camera={'mode':'hero follow','hero_id':'15','entity_id':'actor','frames':1})
    with pytest.raises(RuntimeError,match='follow camera'):
        read_blender_completion(path,**job,shot_hero=15)


@pytest.mark.parametrize('partial',[False,True])
def test_mesh_group_completion_requires_exact_recorded_selection_even_partial(tmp_path,partial):
    from agr.blender_mesh_groups import expected_mesh_group_summary
    path,job=completion(tmp_path)
    entity=job['data']['entities'][0]
    entity.update(samples=[dict(tick=10,mesh_group_mask='1'),dict(tick=12,mesh_group_mask='2')],
        model_mesh_groups=dict(content_sha256='contract',default_mask='1',unconditional=False,
            meshes=[dict(mesh_group_mask='1'),dict(mesh_group_mask='2')]))
    expected=expected_mesh_group_summary(entity['model_mesh_groups'],entity['samples'])
    document=json.loads(path.read_text())
    # A valid report identifies both selections, not only the default/first frame.
    document['entities'][0]['mesh_groups']=expected
    path.write_text(json.dumps(document))
    assert read_blender_completion(path,**job,partial=partial)
    for mutation in ('missing','last_selection','visible_count','basis'):
        actual=json.loads(json.dumps(expected))
        if mutation=='missing':document['entities'][0].pop('mesh_groups')
        else:
            if mutation=='last_selection':actual['selections'].pop()
            elif mutation=='visible_count':actual['selections'][-1]['visible_mesh_count']=2
            else:actual['basis']='model_default_reference'
            document['entities'][0]['mesh_groups']=actual
        path.write_text(json.dumps(document))
        with pytest.raises(RuntimeError,match='exact mesh-group visibility coverage'):
            read_blender_completion(path,**job,partial=partial)


def test_model_default_reference_does_not_consent_to_missing_animated_render_state(tmp_path):
    from agr.blender_mesh_groups import expected_mesh_group_summary
    path,job=completion(tmp_path);entity=job['data']['entities'][0]
    entity.update(samples=[dict(tick=10),dict(tick=12)],model_mesh_groups=dict(
        content_sha256='contract',default_mask='1',unconditional=False,meshes=[dict(mesh_group_mask='1')]))
    document=json.loads(path.read_text());document['entities'][0]['mesh_groups']=expected_mesh_group_summary(
        entity['model_mesh_groups'],entity['samples']);path.write_text(json.dumps(document))
    assert read_blender_completion(path,**job)  # Explicit static reference.
    job['data']['coverage']['animation']='evaluated'
    document['entities'][0]['pose']=dict(status='baked',sample_count=2);path.write_text(json.dumps(document))
    with pytest.raises(RuntimeError,match='mesh-group state unavailable'):
        read_blender_completion(path,**job)
    assert read_blender_completion(path,**job,partial=True)


@pytest.mark.parametrize('stage',['npr_lighting','character_shader'])
def test_mixed_material_completion_cannot_claim_generic_as_npr_or_drop_dynamic_status(tmp_path,stage):
    rows=[dict(source_material='body.vmat_c',material='body.vmat_c',entity_id='actor',route='npr_indirect',
               runtime={'status':'static_authored_inputs'},fidelity_gaps=[],no_specular_at_full_roughness=False,artist_shader_applied=True),
          dict(source_material='eyes.vmat_c',material='eyes.vmat_c',entity_id='actor',route='generic_pbr_indirect',
               runtime={'status':'unbound_static_reference'},fidelity_gaps=[{'feature':'authored_dynamic_inputs'}],artist_shader_applied=False)]
    result=dict(status='partial',materials=rows,preparation=dict(materials=rows,actors=[dict(entity_id='actor',materials=rows)]))
    path,job=completion(tmp_path,**{stage:result})
    model=job['data']['npr_materials']['models']['model'];model['schema_version']=3
    model['materials']=[{k:v for k,v in row.items() if k in ('route','runtime','fidelity_gaps','no_specular_at_full_roughness','material')} for row in rows]
    assert read_blender_completion(path,**job,requested_stages=[stage])
    rows[1]['route']='npr_indirect'
    document=json.loads(path.read_text());document[stage]=result;path.write_text(json.dumps(document))
    with pytest.raises(RuntimeError,match='route/runtime'):
        read_blender_completion(path,**job,requested_stages=[stage])
