"""Validated direct-bone garment inputs for an approximate Seven cloth bake.

Source FE point/rod data is retained in Source units. This describes a bounded
subset; it does not reinterpret generated control offsets as skeleton bones.
"""
from __future__ import annotations

import math

from .installed_cloth import UnsupportedCloth, _number

SEVEN_SCHEMA = 'agr_seven_garments_v1'
GARMENT_NAMES = frozenset(
    [f'waist_cloth_{i}' for i in range(9)]
    + [f'leather_strap_cloth_{i}' for i in range(18)]
    + [f'sleeve_cloth_L_{i}' for i in range(11)]
    + [f'sleeve_cloth_R_{i}' for i in range(17)]
)
# Verified authored chains, expressed by names rather than hardcoded FE indices.
GARMENT_CHAINS = (
    ('sleeve_cloth_R_4', 'sleeve_cloth_R_5', 'sleeve_cloth_R_6'),
    ('sleeve_cloth_R_11', 'sleeve_cloth_R_12', 'sleeve_cloth_R_15', 'sleeve_cloth_R_16'),
    *(tuple(f'waist_cloth_{i+j}' for j in range(3)) for i in (0, 3, 6)),
    *(tuple(f'leather_strap_cloth_{i+j}' for j in range(3)) for i in (12, 9, 6, 15, 3, 0)),
    ('sleeve_cloth_L_0', 'sleeve_cloth_L_1', 'sleeve_cloth_L_2', 'sleeve_cloth_L_3'),
    ('sleeve_cloth_L_4', 'sleeve_cloth_L_5', 'sleeve_cloth_L_6'),
    ('sleeve_cloth_R_11', 'sleeve_cloth_R_12', 'sleeve_cloth_R_13', 'sleeve_cloth_R_14'),
    ('sleeve_cloth_L_7', 'sleeve_cloth_L_8', 'sleeve_cloth_L_9', 'sleeve_cloth_L_10'),
    ('sleeve_cloth_R_0', 'sleeve_cloth_R_1', 'sleeve_cloth_R_2', 'sleeve_cloth_R_3'),
    ('sleeve_cloth_R_7', 'sleeve_cloth_R_8', 'sleeve_cloth_R_9', 'sleeve_cloth_R_10'),
)
PIN_NAMES = frozenset(
    ['waist_cloth_0', 'waist_cloth_3', 'waist_cloth_6']
    + [f'leather_strap_cloth_{i}' for i in (0, 3, 6, 9, 12, 13, 15)]
    + [f'sleeve_cloth_L_{i}' for i in (0, 1, 4, 7)]
    + [f'sleeve_cloth_R_{i}' for i in (0, 4, 6, 7, 11, 12, 16)]
)


def _index(value, count):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < count:
        raise UnsupportedCloth('FE node index is outside its control array')
    return value


def _vector(value, length):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise UnsupportedCloth('FE vector has an unexpected size')
    return [_number(v) for v in value]


def _transform(value):
    row = _vector(value, 8)
    if not math.isclose(row[3], 1, abs_tol=1e-5) or not math.isclose(sum(x*x for x in row[4:]), 1, abs_tol=1e-4):
        raise UnsupportedCloth('FE transform scale or quaternion is unsupported')
    return row


def seven_definition(physics, *, resource='', source_sha256=''):
    """Accept the verified direct-bone waist, straps and sleeves FE topology.

    Dispatch depends on source control names and graph relations, never a hero
    display name. Unrelated dynamic controls stay in their evaluated animation.
    """
    fe = physics.get('m_pFeModel') or {}
    names = fe.get('m_CtrlName', [])
    count, static = fe.get('m_nNodeCount'), fe.get('m_nStaticNodes')
    if (count != 131 or static != 57 or not isinstance(names, list) or len(names) != count
            or not all(isinstance(n, str) and n for n in names) or len(set(names)) != count
            or not GARMENT_NAMES.issubset(names)
            or {n for n in names if 'cloth' in n} != GARMENT_NAMES):
        raise UnsupportedCloth('This model does not have the verified Seven direct-bone garment layout')
    if fe.get('m_nRotLockStaticNodes') != 17:
        raise UnsupportedCloth('FE rotation-locked anchor partition changed')
    if fe.get('m_nFirstPositionDrivenNode') != count:
        raise UnsupportedCloth('Position-driven FE output mapping changed')
    for key in ('m_InitPose', 'm_NodeInvMasses', 'm_NodeIntegrator', 'm_SkelParents'):
        if len(fe.get(key, [])) != count:
            raise UnsupportedCloth('FE array size changed: ' + key)
    # These features would need a different mapping or constraint adapter.
    for key in ('m_Tris', 'm_Quads', 'm_CtrlOffsets', 'm_CtrlOsOffsets', 'm_CtrlSoftOffsets',
                'm_FitMatrices', 'm_FitWeights', 'm_FollowNodes', 'm_NodeBases',
                'm_LockToGoal', 'm_SphereRigids', 'm_CollisionPlanes', 'm_HingeLimits',
                'm_AxialEdges', 'm_DynKinLinks', 'm_NodeStrayBoxes', 'm_SpringIntegrator',
                'm_TaperedCapsuleStretches', 'm_JiggleBones', 'm_GoalDampedSpringIntegrators',
                'm_SDFRigids', 'm_VertexMaps', 'm_SelfCollisionLayers'):
        if fe.get(key):
            raise UnsupportedCloth('Garment inputs now require an unsupported FE feature: ' + key)
    initial = [_transform(row) for row in fe['m_InitPose']]
    masses = [_number(v) for v in fe['m_NodeInvMasses']]
    if any(v != 0 for v in masses[:static]) or any(v <= 0 for v in masses[static:]):
        raise UnsupportedCloth('FE inverse masses do not match static/dynamic partition')
    garment_nodes = [i for i,n in enumerate(names) if n in GARMENT_NAMES]
    selected = set(garment_nodes)
    dynamic = [i for i in garment_nodes if i >= static]
    if {names[i] for i in garment_nodes if i < static} != PIN_NAMES:
        raise UnsupportedCloth('Garment animated anchor partition changed')
    parents = []
    for i,value in enumerate(fe['m_SkelParents']):
        if value != -1:
            _index(value, count)
        if value == i:
            raise UnsupportedCloth('Self-parented FE control')
        parents.append(value)
    for i in range(count):
        seen = {i}; parent = parents[i]
        while parent != -1:
            if parent in seen:
                raise UnsupportedCloth('Cyclic FE control parents')
            seen.add(parent); parent = parents[parent]
    # The prefix contains absolute end offsets into this same flat array.
    rope_count = fe.get('m_nRopeCount')
    flat = fe.get('m_Ropes', [])
    if not isinstance(rope_count, int) or isinstance(rope_count, bool) or not 0 < rope_count < len(flat):
        raise UnsupportedCloth('Invalid FE rope table')
    ropes = []; cursor = rope_count
    for end in flat[:rope_count]:
        if not isinstance(end, int) or isinstance(end, bool) or not cursor + 2 <= end <= len(flat):
            raise UnsupportedCloth('Invalid FE rope boundaries')
        ids = [_index(i, count) for i in flat[cursor:end]]; cursor = end
        if len(set(ids)) != len(ids):
            raise UnsupportedCloth('Repeated FE rope node')
        if set(ids) & selected:
            if not set(ids) <= selected:
                raise UnsupportedCloth('Garment rope is connected to an unsupported control')
            ropes.append(ids)
    if cursor != len(flat) or {tuple(names[i] for i in row) for row in ropes} != set(GARMENT_CHAINS):
        raise UnsupportedCloth('Authored garment rope topology changed')
    expected_rods = set()
    for rope in ropes:
        for j,a in enumerate(rope):
            for b in rope[j+1:j+3]:
                if a >= static or b >= static:
                    expected_rods.add(tuple(sorted((a,b))))
    rods = []; pairs = set()
    for row in fe.get('m_Rods', []):
        pair = row.get('nNode', [])
        if len(pair) != 2:
            raise UnsupportedCloth('Invalid FE rod shape')
        a,b = [_index(i,count) for i in pair]
        if not {a,b} & selected:
            continue
        if not {a,b} <= selected or a == b or tuple(sorted((a,b))) in pairs:
            raise UnsupportedCloth('Garment rod is duplicated or connected to an unsupported control')
        lo,hi = _number(row['flMinDist']), _number(row['flMaxDist'])
        weight,relax = _number(row['flWeight0']),_number(row['flRelaxationFactor'])
        if not 0 < lo <= hi or not 0 <= weight <= 1 or not 0 <= relax <= 1:
            raise UnsupportedCloth('Invalid FE rod parameters')
        rest = math.dist(initial[a][:3], initial[b][:3])
        if rest < lo-1e-4 or rest > hi+1e-4:
            raise UnsupportedCloth('Garment rod does not match its authored initial positions')
        pairs.add(tuple(sorted((a,b))))
        rods.append({'nodes':[a,b], 'min':lo, 'max':hi, 'weight0':weight, 'relaxation':relax})
    if pairs != expected_rods:
        raise UnsupportedCloth('Authored garment distance graph changed')
    integrators = []
    for row in fe['m_NodeIntegrator']:
        integrators.append({k:_number(row[k]) for k in ('flPointDamping', 'flAnimationForceAttraction',
                                                       'flAnimationVertexAttraction','flGravity')})
    ndyn = count-static
    radii = [_number(v) for v in fe.get('m_NodeCollisionRadii',[])]
    if len(radii) != ndyn or any(v < 0 for v in radii):
        raise UnsupportedCloth('FE collision radii do not match the dynamic partition')
    tree_masks = fe.get('m_TreeCollisionMasks',[])
    if len(tree_masks) != 2*ndyn-1 or any(v != 15 for v in tree_masks):
        raise UnsupportedCloth('Nonuniform FE collision filtering needs a new mapping')
    capsules = []
    for row in fe.get('m_TaperedCapsuleRigids',[]):
        node = _index(row['nNode'],count)
        if node >= static or row.get('nFlags') != 0 or row.get('nVertexMapIndex') != 65535:
            raise UnsupportedCloth('Garment capsule binding or vertex filtering changed')
        ends = row.get('vSphere',[])
        if len(ends) != 2:
            raise UnsupportedCloth('Invalid FE capsule endpoints')
        ends = [_vector(v,4) for v in ends]
        mask = row['nCollisionMask']
        if any(v[3] <= 0 for v in ends) or not isinstance(mask,int) or not 0 <= mask <= 15:
            raise UnsupportedCloth('Invalid FE capsule radius or filter')
        capsules.append({'node':node,'ends':ends,'mask':mask})
    if len(capsules) != 17:
        raise UnsupportedCloth('Authored garment body collision layout changed')
    boxes = []
    for row in fe.get('m_BoxRigids',[]):
        node = _index(row['nNode'],count); size = _vector(row['vSize'],3)
        if (node >= static or any(v <= 0 for v in size) or row.get('nFlags') != 0
                or row.get('nVertexMapIndex') != 65535 or row.get('nCollisionMask') != 15):
            raise UnsupportedCloth('Unsupported FE box binding, size or collision filter')
        boxes.append({'node':node,'frame':_transform(row['tmFrame2']),'size':size,'mask':15})
    stray = []
    for row in fe.get('m_AnimStrayRadii',[]):
        pair = row['nNode']
        if len(pair) != 2 or pair[0] != pair[1] or pair[0] not in dynamic:
            raise UnsupportedCloth('Unsupported animation-stray constraint topology')
        radius = _number(row['flMaxDist']); relax = _number(row['flRelaxationFactor'])
        if radius < 0 or relax != 1:
            raise UnsupportedCloth('Unsupported animation-stray bound')
        stray.append({'node':pair[0], 'radius':radius, 'relaxation':relax})
    locks = []
    for row in fe.get('m_LockToParent',[]):
        a,b = _index(row['nCtrlParent'],count),_index(row['nCtrlChild'],count)
        if {a,b} & selected:
            if not {a,b} <= selected or parents[b] != a:
                raise UnsupportedCloth('Garment rotation-lock parent relationship changed')
            locks.append({'parent':a,'child':b,'offset':_vector(row['vOffset'],3)})
    for key,fields in (('m_Twists',('nNodeOrient','nNodeEnd')),('m_KelagerBends',('nNode',))):
        for row in fe.get(key,[]):
            nodes = []
            for field in fields:
                value = row[field]; nodes.extend(value if isinstance(value,list) else [value])
            if set(nodes) & selected:
                raise UnsupportedCloth('Selected garments now use unimplemented FE twist/bend operations')
    return {
        'schema':SEVEN_SCHEMA, 'resource':resource, 'source_sha256':source_sha256,
        'control_names':names, 'initial_pose':initial, 'static_count':static,
        'rotation_locked_static_count':17,
        'garment_nodes':garment_nodes, 'dynamic_nodes':dynamic, 'parents':parents,
        'inverse_masses':masses, 'rods':rods, 'ropes':ropes,
        'gravity':[x['flGravity'] for x in integrators], 'integrators':integrators,
        'node_collision_radii':[0.0]*static+radii, 'collision_masks':[0]*static+[15]*ndyn,
        'capsules':capsules, 'boxes':boxes, 'stray_radii':stray, 'lock_to_parent':locks,
        'scope':'Seven waist panels: native Blender surface cloth; sleeves and straps remain in recorded animation',
        'surface_selection_names':[names[i] for i in dynamic if names[i].startswith('waist_cloth_')],
        'solver':'Blender native surface cloth, with source FE used for garment identification and body capsule placement',
        'limitations':[
            'Only Seven waist-cloth surfaces are simulated in this release; source sleeves, straps, cables and other hero garments are unsupported.',
            'Historical simulation state is unavailable; Blender computes a new simulation from the first sampled pose.',
            'Blender cloth settings are chosen for this reconstruction and do not reproduce Source 2 FE integration or source attraction coefficients.',
            'Low-resolution rounded capsule meshes approximate the authored body capsules; their tapered joins are approximated.',
            'Box collision, self-collision, world collision and game-driven wind are not implemented.',
            'Body action, rig bones and replay roots are preserved; only the extracted waist surface deforms.'
        ],
        'excluded_dynamic_controls':[names[i] for i in range(static,count) if i not in selected],
        'source_feature_counts':{k:len(fe.get(k,[])) for k in
            ('m_Twists','m_KelagerBends','m_LockToParent','m_Effects','m_BoxRigids','m_WorldCollisionNodes')},
    }
