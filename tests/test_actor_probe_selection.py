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
