import copy
import math
import pytest
from agr.installed_effects import UnsupportedEffect
from agr.particle_motion_vectors import motion_vector_pair,reference_flow,KIND,METADATA_POLICY

def fixture():
    frames=[dict(duration=1,min=[0,0],max=[1,.5]),dict(duration=1,min=[0,.5],max=[1,1])]
    sheet=dict(schema_version=1,policy='agr_source_particle_sheet_v1',width=16,height=8,input_sha256='a'*64,image_sha256='b'*64,
        sequences=[dict(index=0,clamp=False,total_time=2,frames=frames)])
    flow=copy.deepcopy(sheet);flow['motion_vectors']=dict(policy=METADATA_POLICY,metadata_present=True,max_distance=-3,
        source_width=16,source_height=8,metadata_sha256='c'*64)
    return [dict(m_hTexture='color'),dict(m_hTexture='flow',m_nTextureType=KIND)],dict(color=dict(source_sheet=sheet),flow=dict(source_sheet=flow))

def test_signed_metadata_and_pair_clock_preserve_observed_negative_scale():
    layers,textures=fixture();before=copy.deepcopy((layers,textures));pair=motion_vector_pair(layers,textures,{})
    assert pair['scale']==(-3/16,-3/8) and pair['base_index']==0 and pair['index']==1
    assert (layers,textures)==before
    assert reference_flow((0,.75,0,.25),pair['scale'],.25)==(-.0234375,-.046875,0)
    assert motion_vector_pair(layers[:1],textures,{})is None

@pytest.mark.parametrize('change',[
    lambda l,t,r:l.append(dict(m_hTexture='third')),
    lambda l,t,r:l.reverse(),
    lambda l,t,r:l[0].update(m_nTextureType='SPRITECARD_TEXTURE_UVDISTORTION'),
    lambda l,t,r:l[1].update(m_bReplaceTextureWithGradient=True),
    lambda l,t,r:r.update(m_bBlendFramesSeq0=False),
    lambda l,t,r:r.update(m_bBlendFramesSeq0=1),
    lambda l,t,r:r.update(m_bMaxLuminanceBlendingSequence0=True),
    lambda l,t,r:r.update(m_bMaxLuminanceBlendingSequence0=0),
    lambda l,t,r:t['flow']['source_sheet'].pop('motion_vectors'),
    lambda l,t,r:t['flow']['source_sheet']['motion_vectors'].update(metadata_present=False),
    lambda l,t,r:t['flow']['source_sheet']['motion_vectors'].update(max_distance=True),
    lambda l,t,r:t['flow']['source_sheet']['motion_vectors'].update(max_distance=-32769),
    lambda l,t,r:t['flow']['source_sheet']['motion_vectors'].update(source_width=15),
    lambda l,t,r:t['flow']['source_sheet']['motion_vectors'].update(metadata_sha256='bad'),
    lambda l,t,r:t['flow']['source_sheet'].update(input_sha256='bad'),
    lambda l,t,r:t['flow']['source_sheet']['sequences'][0].update(clamp=True),
    lambda l,t,r:t['flow']['source_sheet']['sequences'][0]['frames'][0].update(duration=.5),
])
def test_unknown_or_incomplete_pair_cannot_become_a_static_success(change):
    layers,textures=fixture();renderer={};change(layers,textures,renderer)
    with pytest.raises(UnsupportedEffect):motion_vector_pair(layers,textures,renderer)

def test_flow_reads_green_and_alpha_independently_at_opposite_frame_weights():
    scale=(.2,.4);current=(.9,.75,.1,.25);next=(.1,.2,.9,.8)
    assert reference_flow(current,scale,.25)==pytest.approx((.025,.05,0))
    assert reference_flow(next,scale,.75)==pytest.approx((-.09,-.18,0))
    assert reference_flow((0,.5,0,.5),scale,.4)==(0,0,0)
    for bad in [dict(rgba=(1,2,3)),dict(fraction=math.nan),dict(scale=(math.inf,1)),dict(fraction=2)]:
        values=dict(rgba=current,scale=scale,fraction=.5);values.update(bad)
        with pytest.raises(UnsupportedEffect):reference_flow(**values)


def test_actual_motion_offset_builder_keeps_two_raw_frames_and_opposite_weights(monkeypatch):
    from types import SimpleNamespace
    from agr import particle_texture_layers as layers
    from agr.particle_motion_vectors import build_motion_offsets
    class NumericGraph:
        op=staticmethod(layers.numeric)
        def __init__(self,*args):pass
        def rgba(self,sampler):return sampler
        def vector(self,items):return tuple(items)
    monkeypatch.setattr(layers,'LayerNodes',NumericGraph)
    a=(.9,.75,.1,.25);b=(.1,.2,.9,.8);scale=(.2,.4)
    image=SimpleNamespace(current_sampler=a,next_sampler=b,blend_fraction=.25)
    current,nxt=build_motion_offsets(None,None,image,scale)
    assert current==pytest.approx(reference_flow(a,scale,.25))
    assert nxt==pytest.approx(reference_flow(b,scale,.75))
    image.next_sampler=None
    with pytest.raises(UnsupportedEffect,match='separate current/next'):
        build_motion_offsets(None,None,image,scale)


def test_renderer_preflight_admits_the_pair_but_rejects_missing_metadata_and_trails():
    from agr.blender_simulated_effects import validate_renderer_classes
    layers,textures=fixture()
    renderer=dict(_class='C_OP_RenderSprites',m_vecTexturesInput=layers)
    assets=dict(definitions={'body':dict(m_Renderers=[renderer])},textures=textures)
    tracks={('instance','root','body',0):[]}
    assert validate_renderer_classes(tracks,assets)=={}
    renderer['_class']='C_OP_RenderTrails'
    with pytest.raises(UnsupportedEffect,match='only for sprite'):
        validate_renderer_classes(tracks,assets)
    renderer['_class']='C_OP_RenderSprites'
    textures['flow']['source_sheet'].pop('motion_vectors')
    with pytest.raises(UnsupportedEffect,match='motion-vector metadata'):
        validate_renderer_classes(tracks,assets)


def test_missing_texture_inventory_never_makes_a_flow_pair_static():
    from agr.blender_simulated_effects import validate_renderer_classes
    layers,_=fixture()
    assets=dict(definitions={'body':dict(m_Renderers=[dict(_class='C_OP_RenderSprites',m_vecTexturesInput=layers)])})
    with pytest.raises(UnsupportedEffect,match='original particle texture sheet'):
        validate_renderer_classes({('i','root','body',0):[]},assets)
