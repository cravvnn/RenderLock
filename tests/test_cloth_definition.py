"""Synthetic layouts exercise actual graph checks without bundled game assets."""
import copy
import math

import pytest

from agr.cloth_definition import GARMENT_CHAINS, GARMENT_NAMES, PIN_NAMES, seven_definition
from agr.installed_cloth import UnsupportedCloth


def garment_physics():
    # Deliberately use a different node ordering from the installed fixture.
    names = sorted(PIN_NAMES) + [f'body_pin_{i}' for i in range(36)]
    names += sorted(GARMENT_NAMES-PIN_NAMES) + [f'other_dynamic_{i}' for i in range(40)]
    idx = {name:i for i,name in enumerate(names)}
    ropes = [[idx[name] for name in row] for row in GARMENT_CHAINS]
    initial = [[float(i), float(i % 3), float(i % 7), 1, 0, 0, 0, 1] for i in range(131)]
    pairs = set()
    parents = [-1]*131
    for rope in ropes:
        for a,b in zip(rope,rope[1:]):
            parents[b] = a
        for j,a in enumerate(rope):
            pairs.update(tuple(sorted((a,b))) for b in rope[j+1:j+3] if a >= 57 or b >= 57)
    rods = []
    for a,b in sorted(pairs):
        length = math.dist(initial[a][:3],initial[b][:3])
        rods.append(dict(nNode=[a,b], flMinDist=length, flMaxDist=length,
                         flWeight0=0 if a<57 else .5, flRelaxationFactor=1))
    ends=[];cursor=len(ropes)
    for rope in ropes:
        cursor += len(rope); ends.append(cursor)
    return {'m_pFeModel':{
        'm_CtrlName':names, 'm_nNodeCount':131,'m_nStaticNodes':57,'m_nRotLockStaticNodes':17,'m_nFirstPositionDrivenNode':131,
        'm_InitPose':initial, 'm_NodeInvMasses':[0]*57+[1]*74, 'm_SkelParents':parents,
        'm_NodeIntegrator':[dict(flPointDamping=0,flAnimationForceAttraction=.1,
                                flAnimationVertexAttraction=.2,flGravity=360) for _ in names],
        'm_nRopeCount':len(ropes), 'm_Ropes':ends+[i for row in ropes for i in row],
        'm_Rods':rods,'m_NodeCollisionRadii':[.5]*74,'m_TreeCollisionMasks':[15]*147,
        'm_TaperedCapsuleRigids':[dict(nNode=i,nFlags=0,nVertexMapIndex=65535,nCollisionMask=15,
                                    vSphere=[[0,0,0,1],[1,0,0,1]]) for i in range(17)],
    }}


def test_name_based_graph_allows_reordered_controls_and_preserves_source():
    source=garment_physics(); before=copy.deepcopy(source)
    definition=seven_definition(source)
    assert source == before
    assert len(definition['dynamic_nodes']) == 34
    assert len(definition['garment_nodes']) == 55
    assert len(definition['rods']) == 57
    assert len(definition['ropes']) == 17
    assert definition['dynamic_nodes'] == list(range(57,91))
    assert definition['node_collision_radii'] == [0]*57+[.5]*74
    assert definition['integrators'][0]['flAnimationVertexAttraction'] == .2


@pytest.mark.parametrize('mutation,match',[
    (lambda f:f['m_Rods'].pop(),'distance graph'),
    (lambda f:f['m_Rods'].append(copy.deepcopy(f['m_Rods'][0])),'duplicated'),
    (lambda f:f['m_Rods'][0].update(flMinDist=1,flMaxDist=1),'initial positions'),
    (lambda f:f['m_NodeInvMasses'].__setitem__(57,0),'partition'),
    (lambda f:f['m_TreeCollisionMasks'].__setitem__(0,8),'filtering'),
    (lambda f:f['m_Ropes'].__setitem__(0,99999),'boundaries'),
    (lambda f:f['m_SkelParents'].__setitem__(0,0),'Self-parented'),
    (lambda f:f.update(m_CtrlOffsets=[{}]),'unsupported FE feature'),
    (lambda f:f['m_InitPose'][0].__setitem__(0,float('nan')),'finite'),
    (lambda f:f.update(m_Twists=[dict(nNodeOrient=57,nNodeEnd=58)]),'twist/bend'),
])
def test_changed_or_corrupt_layout_is_rejected(mutation,match):
    physics=garment_physics();mutation(physics['m_pFeModel'])
    with pytest.raises(UnsupportedCloth,match=match):
        seven_definition(physics)
