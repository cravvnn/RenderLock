"""Strict authored model constraint binding for qualified bone/attachment subsets.

Explicitly selected by normal exports and research providers.
One instance belongs to one entity and one chronological pose stream.
"""
from copy import deepcopy
import numpy as np
from .installed_map import _entity_key_hash
from .model_constraint_math import (aim_single, orient_single, orient_two_targets,
    point_single, point_two_targets, tilt_twist, compose_constraint_rotation,
    parent_targets, compose_constraint_transform, single_bone_attachment_transform)
from .vendor.pose_math import inv, rotate


class ModelConstraints:
    def __init__(self, names, parents, definitions, *, attachments=None, binding_policy='strict'):
        if binding_policy not in ('strict','native_disabled_missing_slave_v1'):
            raise ValueError('Unsupported model constraint binding policy')
        self.names = tuple(names)
        self.parents = tuple(parents)
        self._history = {}
        self._last_tick = None
        if not self.names or len(self.names) != len(self.parents):
            raise ValueError('Invalid model skeleton dimensions')
        if any(not isinstance(n, str) or not n for n in self.names):
            raise ValueError('Invalid model bone name')
        lookup = {_entity_key_hash(n): i for i, n in enumerate(self.names)}
        if len(lookup) != len(self.names):
            raise ValueError('Duplicate model names or bone hash collision')
        if any(type(p) is not int or not -1 <= p < len(self.names) for p in self.parents):
            raise ValueError('Invalid model parent index')
        descendants = [[] for _ in self.names]
        for i in range(len(self.names)):
            seen = {i}
            parent = self.parents[i]
            while parent >= 0:
                if parent in seen:
                    raise ValueError('Cyclic model hierarchy')
                descendants[parent].append(i)
                seen.add(parent)
                parent = self.parents[parent]
        self._descendants = tuple(tuple(v) for v in descendants)
        self._definitions = deepcopy(definitions)
        self._bindings = []
        disabled = []
        self._attachment_offsets = {}
        attachment_lookup = {}
        if attachments is not None:
            if not isinstance(attachments, list):
                raise ValueError('Invalid constraint attachment table')
            for attachment in deepcopy(attachments):
                if not isinstance(attachment, dict) or not isinstance(attachment.get('m_name'), str) or not attachment['m_name']:
                    raise ValueError('Invalid constraint attachment name')
                token = _entity_key_hash(attachment['m_name'])
                if token in attachment_lookup:
                    raise ValueError('Ambiguous constraint attachment name/hash')
                attachment_lookup[token] = attachment
        def bind(record, *, attachment_allowed=False):
            if not np.isfinite(record['m_flWeight']):
                raise ValueError('Nonfinite model constraint weight')
            token = (_entity_key_hash(record['m_sName']) if record.get('m_sName')
                     else record['m_nBoneHash'])
            if record.get('m_bIsAttachment'):
                if not attachment_allowed:
                    raise ValueError('Constraint attachment slave/operation is unsupported')
                if token not in attachment_lookup:
                    raise ValueError('Constraint attachment is absent from model table')
                attachment = attachment_lookup[token]
                try:
                    influence_names = attachment['m_influenceNames']
                    rotations = attachment['m_vInfluenceRotations']
                    positions = attachment['m_vInfluenceOffsets']
                    weights = attachment['m_influenceWeights']
                    roots = attachment['m_bInfluenceRootTransform']
                    if (type(attachment['m_nInfluences']) is not int or attachment['m_nInfluences'] != 1
                            or attachment['m_bIgnoreRotation'] is not False
                            or any(not isinstance(v,list) or len(v)!=3 for v in (influence_names,rotations,positions,weights,roots))
                            or weights != [1.,0.,0.] or any(v is not False for v in roots)
                            or not isinstance(influence_names[0],str) or not influence_names[0]
                            or any(influence_names[1:])):
                        raise ValueError('Unsupported constraint attachment influence mode')
                    influence = _entity_key_hash(influence_names[0])
                    if influence not in lookup or self.names[lookup[influence]] != influence_names[0]:
                        raise ValueError('Constraint attachment influence bone is absent from model')
                    q,p = np.asarray(rotations[0],dtype=float),np.asarray(positions[0],dtype=float)
                    if (q.shape!=(4,) or p.shape!=(3,) or not np.isfinite(q).all() or not np.isfinite(p).all()
                            or abs(np.linalg.norm(q)-1)>1e-5):
                        raise ValueError('Invalid constraint attachment offset')
                except (KeyError,TypeError,IndexError) as exc:
                    raise ValueError('Incomplete constraint attachment definition') from exc
                self._attachment_offsets[token] = np.array([*p,1.,*q])
                return lookup[influence]
            if token not in lookup:
                raise ValueError('Constraint bone hash is absent from model')
            return lookup[token]
        for definition_index,definition in enumerate(self._definitions):
            kind = definition['_class']
            if kind not in ('CAimConstraint', 'COrientConstraint', 'CTiltTwistConstraint', 'CPointConstraint', 'CParentConstraint'):
                raise ValueError('Unsupported model constraint '+kind)
            slaves, targets = definition['m_slaves'], definition['m_targets']
            if len(slaves) != 1 or len(targets) not in ((1, 2) if kind in ('CAimConstraint','CPointConstraint','COrientConstraint','CParentConstraint') else (1,)):
                raise ValueError('Unsupported model constraint target/slave count')
            slave = slaves[0]
            if (binding_policy == 'native_disabled_missing_slave_v1'
                    and kind in ('CAimConstraint','COrientConstraint','CTiltTwistConstraint')
                    and not slave.get('m_sName') and not slave.get('m_bIsAttachment')
                    and type(slave.get('m_nBoneHash')) is int
                    and 0 <= slave['m_nBoneHash'] <= 0xffffffff
                    and slave['m_nBoneHash'] not in lookup):
                if not np.isfinite(slave['m_flWeight']):
                    raise ValueError('Nonfinite model constraint weight')
                # Original fresh binding fails before target resolution; its
                # readiness gate makes Evaluate a pose-preserving no-op.
                disabled.append(dict(index=definition_index,kind=kind,
                    reason_code='native_disabled_missing_slave',bone_hash=slave['m_nBoneHash'],
                    reason='Authored slave bone is absent; native binding disables this whole constraint.'))
                self._bindings.append(None)
                continue
            bone = bind(slaves[0])
            target_indices = tuple(bind(t,attachment_allowed=kind in ('CAimConstraint','CPointConstraint','COrientConstraint')) for t in targets)
            for target in targets:
                q, p = np.asarray(target['m_qOffset']), np.asarray(target['m_vOffset'])
                if (q.shape != (4,) or p.shape != (3,) or not np.isfinite(q).all()
                        or not np.isfinite(p).all() or abs(np.linalg.norm(q)-1) > 1e-5):
                    raise ValueError('Invalid model constraint offset')
            if kind == 'CAimConstraint':
                if self.parents[bone] < 0:
                    raise ValueError('Unparented aim binding is unsupported')
                up_type = definition['m_nUpType']
                if type(up_type) is not int or up_type not in (0,1,2,3):
                    raise ValueError('Invalid aim up type')
                if up_type in (0,2) and len(targets) != 2:
                    raise ValueError('Aim requires an up target')
            self._bindings.append((bone, target_indices))
        self.disabled_constraints = tuple(disabled)
        active_bindings = [binding for binding in self._bindings if binding is not None]
        self.active_constraint_count = len(active_bindings)
        self.affected_names = frozenset(self.names[i] for bone, _ in active_bindings
                                       for i in (bone, *self._descendants[bone]))
        required = set(self.affected_names)
        for bone, targets in active_bindings:
            for index in (bone, *targets):
                required.add(self.names[index])
                if self.parents[index] >= 0:
                    required.add(self.names[self.parents[index]])
        self.required_names = frozenset(required)

    def _target(self, values, index, record, *, full_transform=False):
        result = values[index].copy()
        q, p = np.array(record['m_qOffset']), np.array(record['m_vOffset'])
        if record.get('m_bIsAttachment'):
            token = (_entity_key_hash(record['m_sName']) if record.get('m_sName') else record['m_nBoneHash'])
            result = single_bone_attachment_transform(result,self._attachment_offsets[token])
            # Native attachment target dispatch always composes/normalizes,
            # including an identity authored target offset.
            return compose_constraint_transform(result,[*p,1.,*q])
        near = (np.all(abs(p) <= 1e-4) and np.all(abs(q[:3]) <= .1)
                and min(abs(q[3]-1), abs(q[3]+1)) <= .1)
        if not near:
            if full_transform:
                return compose_constraint_transform(result,[*p,1.,*q])
            result[:3] += rotate(result[4:], p)*result[3]
            result[4:] = compose_constraint_rotation(result[4:], q)
        return result

    def evaluate(self, pose, *, tick, names):
        if tuple(names) != self.names:
            raise ValueError('Constraint pose bone identity/order mismatch')
        if type(tick) is not int or (self._last_tick is not None and tick <= self._last_tick):
            raise ValueError('Constraint history requires strictly increasing ticks')
        values = np.asarray(pose, dtype=float)
        if values.shape != (len(self.names), 8):
            raise ValueError('Constraint pose dimensions mismatch')
        values = orient_single(values, 0, [], (0.,0.,0.,1.), target_weight=0.)
        history = dict(self._history)
        for index, (definition, binding) in enumerate(zip(self._definitions,self._bindings)):
            if binding is None:
                continue
            bone, targets = binding
            kind = definition['_class']
            slave = definition['m_slaves'][0]
            records = definition['m_targets']
            parent = self.parents[bone]
            children = self._descendants[bone]
            if kind == 'CTiltTwistConstraint':
                values, history[index] = tilt_twist(values,bone,children,targets[0],
                    target_parent=self.parents[targets[0]],bone_parent=parent,
                    inverse_offset=inv(records[0]['m_qOffset']),target_axis=definition['m_nTargetAxis'],
                    bone_axis=definition['m_nSlaveAxis'],weight=slave['m_flWeight'],previous=history.get(index))
            elif kind == 'CParentConstraint':
                values = parent_targets(values,bone,children,
                    [self._target(values,target,record,full_transform=True) for target,record in zip(targets,records)],
                    [record['m_flWeight'] for record in records],slave_weight=slave['m_flWeight'])
            elif kind == 'CPointConstraint':
                if len(targets)==1:
                    values = point_single(values,bone,children,self._target(values,targets[0],records[0])[:3],
                        target_weight=records[0]['m_flWeight'],slave_weight=slave['m_flWeight'])
                else:
                    values = point_two_targets(values,bone,children,
                        [self._target(values,target,record)[:3] for target,record in zip(targets,records)],
                        [record['m_flWeight'] for record in records],slave_weight=slave['m_flWeight'])
            elif kind == 'COrientConstraint':
                if len(targets)==1:
                    values = orient_single(values,bone,children,self._target(values,targets[0],records[0])[4:],
                        target_weight=records[0]['m_flWeight'],slave_weight=slave['m_flWeight'])
                else:
                    values = orient_two_targets(values,bone,children,
                        [self._target(values,target,record)[4:] for target,record in zip(targets,records)],
                        [record['m_flWeight'] for record in records],slave_weight=slave['m_flWeight'])
            else:
                values = aim_single(values,bone,children,self._target(values,targets[0],records[0])[:3],
                    bone_parent=parent,up_type=definition['m_nUpType'],up_vector=definition['m_vUpVector'],
                    up_target=self._target(values,targets[-1],records[-1]) if len(targets)>1 else None,
                    aim_offset=definition['m_qAimOffset'],target_weight=records[0]['m_flWeight'],
                    slave_weight=slave['m_flWeight'])
        self._history, self._last_tick = history, tick
        return values
