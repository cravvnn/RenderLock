import pytest
import copy
import math
from agr.blender_simulated_effects import trail_dimensions, texture_control_value, static_texture_sequence, collect_particle_tracks
from agr.installed_effects import UnsupportedEffect
from agr.blender_simulated_effects import texture_uv_parameters, collect_system_samples

def particle(**changes):
 return dict(previous=[-10,0,0],position=[0,0,0],trail=.2,age=.1,radius=4,**changes)

def tree_states():
 return {'instances':[{'id':'shot','frames':[
  {'tick':tick,'systems':[
   {'system_path':path,'name':'page.vpcf_c','step':.1,'particles':[{'id':0,'position':[x,tick,0]}]}
   for path,x in [('root/0/0',10),('root/1/0',20)]]}
  for tick in (1,2)]}]}


def test_repeated_child_resources_keep_independent_particle_tracks():
 data=tree_states();tracks=collect_particle_tracks(data)
 assert len(tracks)==2
 for path,x in [('root/0/0',10),('root/1/0',20)]:
  samples=tracks['shot',path,'page.vpcf',0]
  assert [p['position'] for _,_,p in samples]==[[x,1,0],[x,2,0]]


def test_whole_rope_samples_preserve_live_array_order_empty_frames_and_sibling_identity():
 data=tree_states();first=data['instances'][0]['frames'][0]['systems'][0]
 first['particles']=[{'id':9},{'id':2}]
 data['instances'][0]['frames'][1]['systems'][0]['particles']=[]
 result=collect_system_samples(data)
 assert result['shot','root/0/0','page.vpcf']==[(1,[{'id':9},{'id':2}]),(2,[])]
 assert result['shot','root/1/0','page.vpcf'][0][1][0]['position']==[20,1,0]


def test_missing_simulation_tick_cannot_stretch_a_particle_track():
 data=tree_states();data['instances'][0]['frames'][1]['tick']=3
 with pytest.raises(ValueError,match='Missing particle simulation tick'):collect_particle_tracks(data)


def test_reappearing_id_cannot_make_a_ghost_particle_between_lifetimes():
 data=tree_states();frames=data['instances'][0]['frames']
 frames.append(copy.deepcopy(frames[1]));frames[-1]['tick']=3
 frames[1]['systems'][0]['particles']=[]
 with pytest.raises(ValueError,match='reappears after death'):collect_particle_tracks(data)


@pytest.mark.parametrize('field,value', [('position',[1,2,float('nan')]),
 ('previous',[0,0]),('normal',[False,0,1]),('rotation',[0,float('inf'),0]),
 ('color',[1,'1',1]),('radius',float('nan')),('alpha',True),('age',-1),('sequence',1.0)])
def test_malformed_simulation_cannot_write_nonfinite_or_coerced_blender_keys(field,value):
 from agr.blender_simulated_effects import validate_render_samples
 p=dict(position=[1,2,3],previous=[1,2,3],normal=[0,0,1],rotation=[0,0,0],color=[1,1,1],
        radius=1,alpha=1,alpha_alternate=1,age=0,trail=1,sequence=0)
 validate_render_samples({'particle':[(1,.015625,p)]})
 p[field]=value
 with pytest.raises(ValueError):validate_render_samples({'particle':[(1,.015625,p)]})


def test_legacy_unique_resources_work_but_ambiguous_children_fail():
 data=tree_states()
 for frame in data['instances'][0]['frames']:
  for system in frame['systems']:del system['system_path']
 with pytest.raises(ValueError,match='ambiguous'):collect_particle_tracks(data)
 for frame in data['instances'][0]['frames']:frame['systems'].pop()
 assert len(collect_particle_tracks(data))==1


@pytest.mark.parametrize('change,message',[
 ('duplicate_path','ambiguous'),('changed_resource','resource changed'),
 ('mixed_paths','Mixed legacy'),('invalid_path','tree path'),
 ('duplicate_particle','particle identity'),('duplicate_tick','tick order')])
def test_ambiguous_or_unstable_particle_identity_fails(change,message):
 data=tree_states();frames=data['instances'][0]['frames'];s=frames[1]['systems'][0]
 if change=='duplicate_path':frames[1]['systems'][1]['system_path']=s['system_path']
 elif change=='changed_resource':s['name']='other.vpcf_c'
 elif change=='mixed_paths':del s['system_path']
 elif change=='invalid_path':s['system_path']='root/../0'
 elif change=='duplicate_particle':s['particles'].append(copy.deepcopy(s['particles'][0]))
 elif change=='duplicate_tick':frames[1]['tick']=frames[0]['tick']
 with pytest.raises(ValueError,match=message):collect_particle_tracks(data)


def test_trail_uses_authored_dt_mode_and_width_ratio():
 p=particle()
 length,radius,direction=trail_dimensions(p,{'m_bIgnoreDT':True,'m_flConstrainRadiusToLengthRatio':.5},1/64)
 assert length==2 and radius==1 and direction==[-1,0,0]
 assert trail_dimensions(p,{},1/64)[:2]==(128,4)

def test_stationary_trail_collapses_even_with_minimum_length():
 p=particle();p['previous']=[0,0,0]
 assert trail_dimensions(p,{'m_flMinLength':10},1/64)[:2]==(0,0)

def test_length_scale_fade_and_clamp():
 p=particle();p['age']=.05
 assert trail_dimensions(p,{'m_bIgnoreDT':True,'m_flLengthScale':2,'m_flLengthFadeInTime':.1,'m_flMaxLength':1.5},1/64)[0]==1.5


def test_texture_age_scrolling_and_unknown_providers():
 assert texture_control_value({'m_nType':'PF_TYPE_COLLECTION_AGE','m_nMapType':'PF_MAP_TYPE_MULT','m_flMultFactor':.25},2)==.5
 assert texture_control_value(None,3,1)==1
 with pytest.raises(UnsupportedEffect):texture_control_value({'m_nType':'PF_TYPE_RANDOM_UNIFORM'},2)


def test_uv_transform_parameters_preserve_source_units_and_wrap():
 assert texture_uv_parameters({},0)==((1,1),(.5,.5),0)
 scale,origin,rotation=texture_uv_parameters({'m_flFinalTextureScaleU':2,
  'm_flFinalTextureScaleV':-.5,'m_flFinalTextureOffsetU':1.75,
  'm_flFinalTextureOffsetV':-2.75,'m_flFinalTextureUVRotation':math.pi/2},0)
 assert scale==(2,-.5) and origin==(.25,.75) and rotation==math.pi/2
 assert texture_uv_parameters({'m_flFinalTextureOffsetU':{'m_nType':'PF_TYPE_COLLECTION_AGE'}},2.25)[1]==(.75,.5)


def test_uv_clamped_corners_and_zero_scale():
 from agr.blender_simulated_effects import texture_uv_corners
 assert texture_uv_corners({'m_bClampUVs':True,'m_flFinalTextureScaleU':.5},0)==[(0.,0.),(1.,0.),(0.,1.),(1.,1.)]
 with pytest.raises(UnsupportedEffect,match='Zero'):texture_uv_parameters({'m_flFinalTextureScaleV':0},0)


def test_each_texture_layer_wraps_against_its_own_sequence_count():
 rows={str(i):{'status':'supported','frames':[{'image':str(i)}]} for i in range(3)}
 assert static_texture_sequence({'is_sprite_sheet':True,'sequences':rows},7,'main') is rows['1']
 companion={'0':rows['0']}
 assert static_texture_sequence({'is_sprite_sheet':True,'sequences':companion},7,'glow') is rows['0']
 assert static_texture_sequence({'is_sprite_sheet':False,'sequences':companion},7,'flat') is rows['0']


def test_sequence_wrapping_does_not_hide_unsupported_or_ambiguous_sheets():
 good={'status':'supported','frames':[{}]}
 with pytest.raises(UnsupportedEffect,match='ordering'):
  static_texture_sequence({'is_sprite_sheet':True,'sequences':{'0':good,'2':good}},2,'sparse')
 with pytest.raises(UnsupportedEffect,match='unsupported'):
  static_texture_sequence({'is_sprite_sheet':True,'sequences':{'0':{'status':'unsupported','frames':[{},{}]}}},7,'animated')
 with pytest.raises(UnsupportedEffect,match='Invalid'):
  static_texture_sequence({'sequences':{'0':good}},-1,'negative')
