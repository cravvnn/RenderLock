import copy
import struct
import numpy as np
import pytest
from agr import observed_slide_sprite_states as s
from agr.replay_capture import canonical_sha256
from agr.simulated_effect_package import scene_identity
from agr.simulated_effect_package import validate_package as validate_normal_package
from agr.observed_slide_sprite_effects import build_observed_slide_sprite_effects,merge_observed_slide_sprite_report


@pytest.fixture
def data(tmp_path):
    entity=dict(id=s.OWNER,hero_id=1,samples=[])
    for key in ('model_file','skeleton_file','pose_file'):
        p=tmp_path/key;p.write_bytes(key.encode());entity[key]=str(p)
    return dict(start_tick=1,end_tick=2,tick_rate=64,entities=[entity],metadata=dict(demo_sha256='a'*64,coordinate_system=dict(unit_scale=.0254)))


def scalar(value=1.):
    return dict(state='literal',input_type='0x462d28',value=value,raw_hex=struct.pack('<f',value).hex())


def inputs(index=0):
    return dict(index=index,kind='C_OP_RenderSprites',context_record_raw=struct.pack('<IHH',0,48,1).hex(),animation_raw=struct.pack('<fi',.1,0).hex(),
        radius_scale=scalar(),alpha_scale=scalar(),depth_bias=scalar(0.),gamma_raw='01',
        texture_layers=[dict(accepted_index=0,source_index=0,scale_u=scalar(),scale_v=scalar())])


def point():
    return dict(position=[1.,2.,3.],radius=1.,rotation=[0.,0.,0.],color_bytes=[51,102,153,96],sequence=[0,0],native_animation_input=.5,shader_extra_data1=1.,native_random=.25)


def reference(data):
    rows=[]
    for i,name in enumerate(sorted(s.PROFILES)):
        rows.append(dict(root_ordinal=0,resource=name,child_path='root/'+str(i),state='captured',count=1,collection_time=.5,
            renderers=[dict(index=r,inputs=inputs(r),points=[point()])for r in range(s.RENDERERS[name])]))
    c=dict(schema_version=1,policy=s.POLICY,entity_id=s.OWNER,demo_sha256=data['metadata']['demo_sha256'],scene_identity=scene_identity(data,[s.OWNER]),
        start_tick=1,end_tick=2,inclusive=True,native=dict(s.NATIVE_DRIVER_DEFAULTS),profiles=s.profile_pins(),source={k:'b'*64 for k in s.SOURCE_KEYS},between_tick_policy='hold_complete_arrays',scope=s.SCOPE,
        samples=[dict(tick=1,root_count=1,owner_life_state=0,collections=rows),dict(tick=2,root_count=0,owner_life_state=1,collections=[])])
    return dict(capture=c,capture_sha256=canonical_sha256(c))


def reseal(r):r['capture_sha256']=canonical_sha256(r['capture']);return r


def test_complete_reference_distinguishes_absent_empty_positive(data):
    r=reference(data);assert s.validate_reference(r,data)['samples'][1]['collections']==[]
    row=r['capture']['samples'][0]['collections'][0];row.update(state='known_empty',count=0,renderers=[])
    assert s.validate_reference(reseal(r),data)['samples'][0]['collections'][0]['state']=='known_empty'
    row['state']='unavailable'
    with pytest.raises(ValueError,match='unavailable'):s.validate_reference(reseal(r),data)


@pytest.mark.parametrize('change', ['prefix','root_reuse','missing_child','duplicate_path','truncated_points','renderer_order','scope','source','identity','bool_count'])
def test_load_bearing_completeness_identity_and_scope(data,change):
    r=reference(data);c=r['capture'];row=c['samples'][0]['collections'][0]
    if change=='prefix':c['samples'].pop()
    elif change=='root_reuse':c['samples'][1]['collections']=copy.deepcopy(c['samples'][0]['collections'])
    elif change=='missing_child':c['samples'][0]['collections'].pop()
    elif change=='duplicate_path':c['samples'][0]['collections'][1]['child_path']=row['child_path']
    elif change=='truncated_points':row['renderers'][0]['points']=[]
    elif change=='renderer_order':row['renderers'][0]['index']=1
    elif change=='scope':c['scope']='All particles complete'
    elif change=='source':c['source'].pop(next(iter(c['source'])))
    elif change=='identity':c['scene_identity']='c'*64
    else:row['count']=True
    with pytest.raises(ValueError):s.validate_reference(reseal(r),data)


def test_native_random_scalar_is_bounded_by_context_and_actual_float32(data):
    r=reference(data);value=dict(state='constant_random',input_type='0x462d28',value=1.,provider_raw=struct.pack('<ffB3xiii',.75,1.25,0,-1,0,48).hex(),slot_offset=48,slot_seed_raw=struct.pack('<i',-4097).hex(),native_unit_random=.5)
    r['capture']['samples'][0]['collections'][0]['renderers'][0]['inputs']['texture_layers'][0]['scale_u']=value
    s.validate_reference(reseal(r),data)
    for key,bad in (('slot_offset',52),('native_unit_random',2.),('value',1.1),('input_type','0x462c88')):
        candidate=copy.deepcopy(r);candidate['capture']['samples'][0]['collections'][0]['renderers'][0]['inputs']['texture_layers'][0]['scale_u'][key]=bad
        with pytest.raises(ValueError):s.validate_reference(reseal(candidate),data)


def raw_node(count,constant,life=2.):
    fields={}
    for field,components in s.FIELDS.items():
        stride=0 if constant else components*4;last=min(count-1,3)if constant else(count-1)%4
        raw=bytearray(((count-1)//4*stride+(components-1)*4+last+1)*4)
        for i in range(count):
            slot=i%4 if constant else i
            for c in range(components):
                value=([1.,2.,3.][c]+slot)if field==0 else (.2,.4,.6)[c]if field==6 else {1:life,3:1.,7:.5,8:.5,9:0.,13:0.,16:.75,38:.25,39:1.}.get(field,0.)
                struct.pack_into('<i'if field==11 else'<f',raw,((i//4)*stride+c*4+i%4)*4,int(value)if field==11 else value)
        fields[str(field)]=dict(components=components,stride=stride,raw_hex=raw.hex())
    clocks=bytearray(56);struct.pack_into('<4f',clocks,0,1.5,1.5,1.5,1.5)
    return dict(particles=count,fields=fields,clock_40_78_raw=clocks.hex(),native_random_values=[.25]*count)


@pytest.mark.parametrize('count',[1,3,4,5,9])
@pytest.mark.parametrize('constant',[False,True])
def test_raw_partial_and_constant_arrays_use_native_gather_equations(count,constant):
    n=raw_node(count,constant)
    for mode,animation in ((0,1.),(1,.5),(2,.25)):
        p=s._points(n,mode);assert len(p)==count
        for i,value in enumerate(p):
            assert value['position']==[1.+(i%4 if constant else i),2.+(i%4 if constant else i),3.+(i%4 if constant else i)]
            assert value['native_animation_input']==animation and value['native_random']==.25 and value['color_bytes']==[51,102,153,96]
    assert s._points(raw_node(count,constant,0.),1)[0]['native_animation_input']==0.


def test_not_selected_does_not_allocate_or_prepare_assets():
    report=build_observed_slide_sprite_effects({},None,None,{}, {},None,None,None)
    assert report['status']=='not_selected' and report['objects']==0 and report['unsupported']==[]


def test_collection_meshes_do_not_rewrite_recorded_birth_coverage():
    report=dict(objects=10,point_lights=2,rendered_instances=97,verified_empty_instances=1,unsupported_by_birth_scope={'history':28})
    before=copy.deepcopy(report)
    observed=dict(objects=754,point_lights=0,coverage_scope=s.SCOPE,unsupported=[dict(resource=s.ROOT,occurrences=1)])
    merge_observed_slide_sprite_report(report,observed)
    assert report['objects']==764 and report['point_lights']==2
    for key in ('rendered_instances','verified_empty_instances','unsupported_by_birth_scope'):assert report[key]==before[key]


def test_normal_package_routes_reference_validation_and_copies_inputs(data,monkeypatch):
    from agr.particle_sheet_uv import POLICY,SHADERS
    definitions={n:dict(m_Renderers=[dict(_class='C_OP_RenderSprites',m_vecTexturesInput=[dict(m_hTexture='flat')])for _ in range(s.RENDERERS[n])])for n in s.PROFILES}
    monkeypatch.setattr(s,'PROFILES',{n:('d'*64,canonical_sha256(d))for n,d in definitions.items()})
    r=reference(data)
    assets=dict(models={},definitions=definitions,textures={'flat':{}},provenance=dict(input_sha256={n:'d'*64 for n in (*definitions,'flat')},spritecard_sheet_uv=dict(policy=POLICY,sha256=SHADERS)))
    package=s.prepare_package(data,r,assets);validate_normal_package(package,data)
    r['capture']['samples'][0]['collections'][0]['renderers'][0]['points'][0]['radius']=9.
    assert package['reference']['capture']['samples'][0]['collections'][0]['renderers'][0]['points'][0]['radius']==1.
    changed=copy.deepcopy(package);changed['reference']['capture']['samples'].pop();reseal(changed['reference'])
    with pytest.raises(ValueError,match='dense'):validate_normal_package(changed,data)
    changed=copy.deepcopy(package);changed['assets']['definitions'][next(iter(definitions))]['m_Renderers'][0]['_class']='OtherRenderer'
    changed['assets_sha256']=canonical_sha256(changed['assets'])
    with pytest.raises(ValueError,match='definition'):validate_normal_package(changed,data)
