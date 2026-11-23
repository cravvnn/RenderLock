"""Exact FE proxy identities and bounded weighted bone-fit approximation."""
from __future__ import annotations

import numpy as np


def skin_proxy_goals(proxy, bone_matrices, unit_scale=.0254):
    """Skin source-space vertices with the decompiled source inverse binds.

    Imported meshes may relocate a weapon skeleton. Its imported rest matrices
    cannot serve as inverse binds for an independently reconstructed FE proxy.
    Returned points are in the imported rig's local metre coordinate system.
    """
    return ProxyGoalSkinner(proxy, bone_matrices, unit_scale).evaluate(bone_matrices)


class ProxyGoalSkinner:
    """Prepare source bind and influence data once for an animated rig."""

    def __init__(self, proxy, bone_names, unit_scale):
        vertices = np.asarray(proxy['vertices'], dtype=float)
        count = len(vertices)
        binds = proxy.get('joint_rest_transforms')
        names, slots = proxy['joint_names'], proxy['joint_count']
        indices, weights = proxy['blend_indices'], proxy['blend_weights']
        if (vertices.shape != (count, 3) or not np.isfinite(vertices).all()
                or not isinstance(binds, list) or len(binds) != len(names)
                or len(set(names)) != len(names) or type(slots) is not int or not 1 <= slots <= 16
                or len(indices) != count * slots or len(weights) != count * slots
                or not np.isfinite(unit_scale) or unit_scale <= 0):
            raise ValueError('Invalid source inverse-bind proxy arrays')

        basis = np.array([[0., 1, 0, 0], [-1., 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        vertices = np.column_stack((vertices * unit_scale, np.ones(count))) @ basis.T
        inverse_binds = {}
        for name, row in zip(names, binds):
            if name not in bone_names:
                continue
            rest = np.array(row, dtype=float)
            if (rest.shape != (4, 4) or not np.isfinite(rest).all()
                    or abs(np.linalg.det(rest[:3, :3])) < 1e-8):
                raise ValueError('Invalid source proxy joint bind/pose transform')
            rest[:3, 3] *= unit_scale
            inverse_binds[name] = np.linalg.inv(basis @ rest)

        if any(type(index) is not int or not 0 <= index < len(names) for index in indices):
            raise ValueError('Invalid source proxy joint influence')
        weights = np.asarray(weights)
        if weights.shape != (count * slots,) or not np.isfinite(weights).all() or np.any(weights < 0):
            raise ValueError('Invalid source proxy joint influence')
        active = weights > 1e-8
        joint_indices = np.asarray(indices, dtype=np.intp)[active]
        by_name = {name: i for i, name in enumerate(inverse_binds)}
        matrix_indices = np.array([by_name.get(name, -1) for name in names], dtype=np.intp)[joint_indices]
        missing = np.flatnonzero(matrix_indices < 0)
        if missing.size:
            raise ValueError('Source proxy skin bone is absent: ' + names[joint_indices[missing[0]]])

        vertex_indices = np.flatnonzero(active) // slots
        totals = np.zeros(count)
        np.add.at(totals, vertex_indices, weights[active])
        if np.any(totals <= 1e-8) or np.any(totals > 1.001):
            raise ValueError('Source proxy skin weights have invalid total influence')

        self._names = tuple(inverse_binds)
        self._inverse_binds = np.asarray(list(inverse_binds.values()))
        self._matrix_indices = matrix_indices
        self._vertex_indices = vertex_indices
        self._vertices = vertices[vertex_indices, :, None]
        self._weights = weights[active, None]
        self._totals = totals[:, None]

    def evaluate(self, bone_matrices):
        poses = []
        for name in self._names:
            if name not in bone_matrices:
                raise ValueError('Source proxy skin bone is absent: ' + name)
            pose = np.asarray(bone_matrices[name], dtype=float)
            if pose.shape != (4, 4) or not np.isfinite(pose).all():
                raise ValueError('Invalid source proxy joint bind/pose transform')
            poses.append(pose)
        if not len(self._totals):
            return np.empty(0)

        transforms = np.asarray(poses) @ self._inverse_binds
        transformed = (transforms[self._matrix_indices] @ self._vertices)[:, :3, 0]
        points = np.zeros((len(self._totals), 3))
        np.add.at(points, self._vertex_indices, transformed * self._weights)
        return points / self._totals


def proxy_bone_outputs(proxy, physics):
    """Select compiled fits wholly covered by this exact proxy node inventory."""
    from .reconstructed_cloth import bind_proxy_node_indices
    bind_proxy_node_indices(proxy, proxy.get('fe_node_indices'), physics)
    fe = physics['m_pFeModel']; count = fe['m_nNodeCount']
    nodes = {node for node in proxy['fe_node_indices'] if node >= 0}
    if len(nodes) != sum(node >= 0 for node in proxy['fe_node_indices']):
        raise ValueError('Bone-fit proxy repeats a control-node identity')
    weights = fe.get('m_FitWeights', [])
    fits = []; begin = 0
    for fit in fe.get('m_FitMatrices', []):
        end = fit['nEnd']; dynamic = fit['nBeginDynamic']; output = fit['nNode']
        if (type(end) is not int or not begin <= dynamic <= end <= len(weights)
                or type(output) is not int or not 0 <= output < count):
            raise ValueError('Invalid FE bone-fit range/control identity')
        selected = weights[begin:end]; begin = end
        targets = {row['nNode'] for row in selected}
        if not targets & nodes:
            continue
        if not targets <= nodes or len(selected) < 3:
            raise ValueError('FE bone fit crosses proxy domains or lacks support')
        for row in selected:
            weight = row['flWeight']
            if (type(row['nNode']) is not int or not 0 <= row['nNode'] < count
                    or not np.isfinite(weight) or weight <= 0):
                raise ValueError('Invalid FE bone-fit node/weight')
        fits.append(dict(fit, weights=selected))
    if begin != len(weights) or not fits:
        raise ValueError('FE proxy lacks a complete compiled bone-fit inventory')
    reverse = [row for row in fe.get('m_ReverseOffsets', []) if row['nTargetNode'] in nodes]
    outputs = [row['nNode'] for row in fits] + [row['nBoneCtrl'] for row in reverse]
    if len(set(outputs)) != len(outputs) or any(type(i) is not int or not 0 <= i < count for i in outputs):
        raise ValueError('Ambiguous FE proxy bone outputs')
    for row in reverse:
        if np.asarray(row['vOffset']).shape != (3,) or not np.isfinite(row['vOffset']).all():
            raise ValueError('Invalid FE proxy reverse offset')
    return fits, reverse


def uniform_proxy_colliders(proxy, physics):
    """Retain masks and named selections; require uniform proxy participation.

    Blender's collision collection applies to a complete cloth object. A
    partially selected proxy must use another collision adapter, rather than
    silently broadening the source selection.
    """
    fe = physics['m_pFeModel']; count = fe['m_nNodeCount']; static = fe['m_nStaticNodes']
    dynamic = [node for node in proxy['fe_node_indices'] if node >= static]
    if not dynamic:
        raise ValueError('FE bone-fit proxy has no dynamic vertices')
    tree = fe.get('m_TreeCollisionMasks', [])
    if len(tree) != (count-static)*2-1:
        raise ValueError('FE proxy lacks its exact collision-mask leaves')
    result = []
    for key in ('m_TaperedCapsuleRigids', 'm_SphereRigids', 'm_BoxRigids'):
        for row in fe.get(key, []):
            if row.get('nFlags') != 0:
                raise ValueError('FE proxy collider flag operation is unavailable')
            selected = [(tree[node-static] & row['nCollisionMask']) != 0 for node in dynamic]
            selection = row['nVertexMapIndex']
            if selection != 65535:
                maps = fe.get('m_VertexMaps', [])
                if type(selection) is not int or not 0 <= selection < len(maps):
                    raise ValueError('FE proxy collider selection is invalid')
                mapping = maps[selection]; values = fe['m_VertexMapValues']
                base, length, offset = (mapping[key] for key in ('nVertexBase','nVertexCount','nMapOffset'))
                if min(base,length,offset) < 0 or offset+length > len(values):
                    raise ValueError('FE collider vertex-map bounds are invalid')
                selected = [flag and 0 <= node-base < length and values[offset+node-base] > 0
                            for flag,node in zip(selected,dynamic)]
            if any(selected) and not all(selected):
                raise ValueError('FE collider requires per-vertex proxy collision selection')
            if all(selected):result.append((key,row))
    return result


def weighted_rigid_delta(source, target, weights):
    """Proper weighted rigid fit, preserving reflection and rank safeguards."""
    source = np.asarray(source,dtype=float); target = np.asarray(target,dtype=float)
    weights = np.asarray(weights,dtype=float)
    if (source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3
            or len(source) < 3 or weights.shape != (len(source),)
            or not np.isfinite(source).all() or not np.isfinite(target).all()
            or not np.isfinite(weights).all() or np.any(weights <= 0)):
        raise ValueError('Invalid FE weighted bone-fit arrays')
    weights = weights/weights.sum()
    a = weights@source; b = weights@target
    covariance = (source-a).T @ ((target-b)*weights[:,None])
    u,s,v = np.linalg.svd(covariance)
    if s[1] < 1e-12:
        raise ValueError('Degenerate FE weighted bone-fit plane')
    correction = np.eye(3);correction[2,2] = 1 if np.linalg.det(v.T@u.T)>0 else -1
    rotation = v.T@correction@u.T
    result = np.eye(4);result[:3,:3] = rotation;result[:3,3] = b-rotation@a
    return result


def project_proxy_rods(proxy, physics, points, goals, unit_scale=.0254, iterations=128):
    """Retain compiled cable distances after the approximate cloth response."""
    fe=physics['m_pFeModel'];nodes={n:i for i,n in enumerate(proxy['fe_node_indices']) if n>=0}
    points=np.asarray(points,dtype=float).copy();goals=np.asarray(goals,dtype=float)
    if points.shape!=goals.shape or points.shape!=(len(proxy['vertices']),3) or not np.isfinite(points).all() or not np.isfinite(goals).all():
        raise ValueError('Invalid proxy rod-projection arrays')
    rods=[]
    for row in fe['m_Rods']:
        a,b=row['nNode']
        if a not in nodes and b not in nodes:continue
        if a not in nodes or b not in nodes:raise ValueError('Authored proxy rod crosses its exact node domain')
        values=np.asarray([row[k] for k in ('flMinDist','flMaxDist','flWeight0','flRelaxationFactor')])
        if not np.isfinite(values).all() or not 0<=values[0]<=values[1] or not 0<=values[2]<=1 or not 0<=values[3]<=1:
            raise ValueError('Invalid authored proxy rod interval/weights')
        rods.append((nodes[a],nodes[b],*values))
    dynamic=np.asarray([node>=0 and fe['m_NodeInvMasses'][node]>0 for node in proxy['fe_node_indices']])
    points[~dynamic]=goals[~dynamic]
    for _ in range(iterations):
        for a,b,minimum,maximum,weight,relax in rods:
            delta=points[b]-points[a];length=float(np.linalg.norm(delta))
            if length<1e-12:continue
            target=min(max(length,minimum*unit_scale),maximum*unit_scale)
            correction=delta*((length-target)/length*relax)
            if dynamic[a]:points[a]+=correction*weight
            if dynamic[b]:points[b]-=correction*(1-weight)
        points[~dynamic]=goals[~dynamic]
    residual=max((max(0.,float(np.linalg.norm(points[b]-points[a]))-maximum*unit_scale,
                      minimum*unit_scale-float(np.linalg.norm(points[b]-points[a])))
                  for a,b,minimum,maximum,_,_ in rods),default=0.)
    return points,float(residual)
