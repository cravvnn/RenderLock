import copy
import itertools
import numpy as np
import pytest
from agr.source_probe_blending import contributing_actor_probes,probe_weights


def probe(identity,low,high,priority=0,fade=0):
    return dict(hammeruniqueid=str(identity),array_index=identity,indoor_outdoor_level=priority,
                origin=[0,0,0],angles=[0,0,0],box_mins=low,box_maxs=high,edge_fade_dists=[fade]*3)


def coverage(*frames):
    return dict(status='verified_integer_frame_bounds',scope='per_actor_convex_bounds',
                frames=[dict(frame=i+1,actors=actors) for i,actors in enumerate(frames)])


def actor(identity,low,high):return dict(entity_id=identity,low=low,high=high)


def test_partial_predecessor_and_equal_priority_sibling_order_are_preserved():
    partial=probe(1,[0,0,0],[5,10,10],fade=1)
    full=probe(2,[-10]*3,[10]*3)
    later=probe(3,[-10]*3,[10]*3)
    outside=probe(4,[100]*3,[110]*3,priority=5)
    probes=[later,full,outside,partial]
    bounds=actor('a',[1,1,1],[8,8,8])
    selected=contributing_actor_probes(probes,coverage([bounds]))['a']
    assert [p['hammeruniqueid'] for p in selected]==['1','2']
    # Compare exact reference blend, including points inside partial fades and
    # triangle interiors. Removed later siblings have identically zero weight.
    points=np.array(list(itertools.product(*[[1,4.49,5.01,8]]*3)))
    original,left=probe_weights(probes,points);culled,newleft=probe_weights(selected,points)
    expected={r['id']:r['weight'] for r in original}
    for row in culled:np.testing.assert_array_equal(row['weight'],expected[row['id']])
    assert all(not np.any(row['weight']) for row in original if row['id'] not in {'1','2'})
    np.testing.assert_array_equal(left,newleft)


def test_distant_actors_and_moving_actor_keep_their_own_union():
    left=probe(1,[-10]*3,[10]*3);right=probe(2,[90,-10,-10],[110,10,10])
    result=contributing_actor_probes([left,right],coverage(
        [actor('moving',[-1]*3,[1]*3),actor('right',[99,-1,-1],[101,1,1])],
        [actor('moving',[99,-1,-1],[101,1,1]),actor('right',[99,-1,-1],[101,1,1])]))
    assert [p['hammeruniqueid'] for p in result['moving']]==['1','2']
    assert [p['hammeruniqueid'] for p in result['right']]==['2']


@pytest.mark.parametrize('mutation',['coverage','missing_frame_actor','missing_full','duplicate_frame'])
def test_unproven_or_incomplete_coverage_cannot_prune(mutation):
    data=coverage([actor('a',[1]*3,[2]*3)],[actor('a',[1]*3,[2]*3)])
    probes=[probe(1,[0]*3,[10]*3)]
    if mutation=='coverage':data['status']='decoded'
    if mutation=='missing_frame_actor':data['frames'][1]['actors']=[]
    if mutation=='missing_full':data['frames'][1]['actors'][0]['high']=[11]*3
    if mutation=='duplicate_frame':data['frames'][1]['frame']=1
    with pytest.raises(ValueError):contributing_actor_probes(probes,data)


def hidden(identity):return dict(entity_id=identity,visibility='hidden_model_track',surface_checks=0)


def test_hidden_model_track_form_keeps_identity_and_visible_probe_union():
    # A split transformation keeps one actor identity per form. The form that is
    # hidden at a frame has no surface bounds but must stay in the actor set.
    first=actor('77:961',[1]*3,[2]*3)
    second=actor('77:961@model:80366',[3]*3,[4]*3)
    data=coverage([first,hidden('77:961@model:80366')],[hidden('77:961'),second])
    data['actor_count']=2
    probes=[probe(1,[0]*3,[2.5]*3),probe(2,[2.5]*3,[10]*3)]
    selected=contributing_actor_probes(probes,data)
    assert [p['hammeruniqueid'] for p in selected['77:961']]==['1']
    assert [p['hammeruniqueid'] for p in selected['77:961@model:80366']]==['2']


@pytest.mark.parametrize('mutation',['missing_count','extra_field','bad_count','never_visible'])
def test_invalid_or_unused_hidden_model_track_rows_are_rejected(mutation):
    data=coverage([actor('a',[1]*3,[2]*3),hidden('b')])
    data['actor_count']=2
    if mutation=='missing_count':data['frames'][0]['actors'][1].pop('surface_checks')
    if mutation=='extra_field':data['frames'][0]['actors'][1]['low']=[0]*3
    if mutation=='bad_count':data['frames'][0]['actors'][1]['surface_checks']=1
    if mutation=='never_visible':
        data['frames'][0]['actors']=[hidden('a')]
        data['frames'].append(dict(frame=2,actors=[hidden('a')]))
    with pytest.raises(ValueError):contributing_actor_probes([probe(1,[-10]*3,[10]*3)],data)


class _Mesh:
    type='MESH'
    def get(self,key,default=None):return default


class _Root:
    def __init__(self,identity):
        self.identity=identity;self.children_recursive=[_Mesh()]
    def get(self,key,default=None):
        return self.identity if key=='agr_entity_id' else default


def test_reused_coverage_requires_the_same_builder_actors():
    from agr.blender_hero_lighting import reuse_surface_coverage
    roots=[_Root('a')]
    data=coverage([actor('a',[1]*3,[2]*3)]);data['actor_count']=1
    assert reuse_surface_coverage(data,roots) is data
    with pytest.raises(ValueError):reuse_surface_coverage(data,[_Root('a'),_Root('b')])
    with pytest.raises(ValueError):reuse_surface_coverage(data,[_Root('b')])
    broken=copy.deepcopy(data);broken.pop('actor_count')
    with pytest.raises(ValueError):reuse_surface_coverage(broken,roots)
