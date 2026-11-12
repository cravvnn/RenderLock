import copy
import math
import pytest
from agr.installed_effects import UnsupportedEffect
from agr.particle_texture_animation import validate_sheet,sheet_frame,blender_rect


def sheet(clamp=True):
    return dict(schema_version=1,policy='agr_source_particle_sheet_v1',sequences=[dict(
        index=0,clamp=clamp,total_time=4,frames=[
            dict(duration=1,min=[0,0],max=[1,.5]),dict(duration=3,min=[0,.5],max=[1,1])])])


def test_manual_frame_uses_sequence_duration_and_original_uvs_not_first_image():
    s=validate_sheet(sheet());r=dict(m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES')
    first,second,blend=sheet_frame(s,0,dict(manual_frame=.125),r)
    assert first==s['sequences'][0]['frames'][0] and second==s['sequences'][0]['frames'][1]
    assert blend==.5
    assert blender_rect(first)==((0,.5),(1,.5))
    assert blender_rect(second)==((0,0),(1,.5))
    a,b,blend=sheet_frame(s,0,dict(manual_frame=1),r)
    assert a==b==second and blend==0


def test_clamped_zero_duration_terminal_frame_and_looping_uneven_durations():
    s=sheet();s['sequences'][0]['frames'].append(dict(duration=0,min=[0,0],max=[.5,.5]))
    validate_sheet(s)
    a,b,blend=sheet_frame(s,0,dict(manual_frame=1),dict(m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES'))
    assert a==b==s['sequences'][0]['frames'][-1] and blend==0
    s=sheet(False);first,last=s['sequences'][0]['frames']
    a,b,blend=sheet_frame(s,0,dict(manual_frame=.875),dict(m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES'))
    assert a==last and b==first and blend==pytest.approx(5/6)


def test_fixed_rate_fps_and_fit_lifetime_choose_the_expected_playback_time():
    s=sheet();p=dict(age=.5,life=2)
    assert sheet_frame(s,0,p,dict(m_flAnimationRate=1))[2]==0 # last frame holds in a clamped sequence
    a,b,f=sheet_frame(s,0,p,dict(m_flAnimationRate=1,m_bAnimateInFPS=True))
    assert f==.5
    a,b,f=sheet_frame(s,0,p,dict(m_flAnimationRate=1,m_nAnimationType='ANIMATION_TYPE_FIT_LIFETIME'))
    assert a==s['sequences'][0]['frames'][1] and f==0


@pytest.mark.parametrize('change',[
    lambda s:s.update(policy='unknown'),
    lambda s:s['sequences'][0].update(total_time=math.inf),
    lambda s:s['sequences'][0]['frames'][0].update(duration=-1),
    lambda s:s['sequences'][0]['frames'][0].update(min=[0,math.nan]),
    lambda s:s['sequences'][0]['frames'][0].update(max=[0,0]),
])
def test_bad_sheet_metadata_cannot_be_treated_as_a_static_success(change):
    s=copy.deepcopy(sheet());change(s)
    with pytest.raises(UnsupportedEffect):validate_sheet(s)


def test_missing_manual_input_and_unknown_animation_modes_are_explicit():
    s=sheet()
    with pytest.raises(UnsupportedEffect):sheet_frame(s,0,{},dict(m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES'))
    with pytest.raises(UnsupportedEffect):sheet_frame(s,0,dict(age=1),dict(m_nAnimationType='ANIMATION_UNKNOWN'))


def test_declared_false_like_values_are_not_typed_animation_flags():
    with pytest.raises(UnsupportedEffect,match='FPS mode'):
        sheet_frame(sheet(),0,dict(age=1),dict(m_bAnimateInFPS=0))


def test_global_frame_bound_covers_many_individually_small_sequences():
    s=sheet();frame=s['sequences'][0]['frames'][0]
    s['sequences']=[dict(index=i,clamp=False,total_time=4096,frames=[frame]*4096)for i in range(17)]
    with pytest.raises(UnsupportedEffect,match='total frame bound'):validate_sheet(s)


def test_float32_boundary_selects_following_frame_without_blending_to_hide_mismatch():
    s=sheet(False);s['sequences'][0]['total_time']=.3
    s['sequences'][0]['frames'][0]['duration']=.1
    s['sequences'][0]['frames'][1]['duration']=.2
    a,b,f=sheet_frame(s,0,dict(manual_frame=1/3),dict(m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES',m_bBlendFramesSeq0=False))
    assert a is s['sequences'][0]['frames'][1] and b is s['sequences'][0]['frames'][0]
    assert 0<=f<1e-6


def test_fit_lifetime_uses_the_reference_minimum_denominator():
    s=sheet(False)
    renderer=dict(m_nAnimationType='ANIMATION_TYPE_FIT_LIFETIME',m_flAnimationRate=1)
    assert sheet_frame(s,0,dict(age=.000025,life=.000001),renderer)==sheet_frame(s,0,dict(manual_frame=.25),dict(m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES'))


def test_behavior_gate_is_sprite_specific_and_preserves_authored_definition():
    from agr.particle_texture_animation import renderer_settings
    r=dict(_class='C_OP_RenderSprites',m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES')
    assert renderer_settings(r,{'m_nBehaviorVersion':9})['m_nAnimationType']=='ANIMATION_TYPE_FIXED_RATE'
    assert renderer_settings(r,{'m_nBehaviorVersion':10})==r
    assert r['m_nAnimationType']=='ANIMATION_TYPE_MANUAL_FRAMES'
    trail=dict(r,_class='C_OP_RenderTrails')
    assert renderer_settings(trail,{'m_nBehaviorVersion':9})==trail
    with pytest.raises(UnsupportedEffect):renderer_settings(r,{'m_nBehaviorVersion':True})


@pytest.mark.parametrize('value',[1e100,-1e100,float('nan'),True])
def test_unsupported_float_inputs_do_not_silently_saturate_the_frame(value):
    with pytest.raises(UnsupportedEffect):
        sheet_frame(sheet(),0,dict(manual_frame=value),dict(m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES'))
