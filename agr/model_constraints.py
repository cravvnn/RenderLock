"""Strict authored model constraint binding for the validated bone-only subset.

Explicitly selected by normal exports and research providers.
One instance belongs to one entity and one chronological pose stream.
"""
from copy import deepcopy
import numpy as np
from .installed_map import _entity_key_hash
from .model_constraint_math import aim_single, orient_single, point_single, tilt_twist
from .vendor.pose_math import inv, mul, rotate


class ModelConstraints:
    def __init__(self, names, parents, definitions):
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
        def bind(record):
            if record.get('m_bIsAttachment'):
                raise ValueError('Constraint attachment binding is unsupported')
            token = (_entity_key_hash(record['m_sName']) if record.get('m_sName')
                     else record['m_nBoneHash'])
            if token not in lookup:
                raise ValueError('Constraint bone hash is absent from model')
            if not np.isfinite(record['m_flWeight']):
                raise ValueError('Nonfinite model constraint weight')
            return lookup[token]
        for definition in self._definitions:
            kind = definition['_class']
            if kind not in ('CAimConstraint', 'COrientConstraint', 'CTiltTwistConstraint', 'CPointConstraint'):
                raise ValueError('Unsupported model constraint '+kind)
            slaves, targets = definition['m_slaves'], definition['m_targets']
            if len(slaves) != 1 or len(targets) not in ((1, 2) if kind == 'CAimConstraint' else (1,)):
                raise ValueError('Unsupported model constraint target/slave count')
            bone = bind(slaves[0])
            target_indices = tuple(bind(t) for t in targets)
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
        self.affected_names = frozenset(self.names[i] for bone, _ in self._bindings
                                       for i in (bone, *self._descendants[bone]))
        required = set(self.affected_names)
        for bone, targets in self._bindings:
            for index in (bone, *targets):
                required.add(self.names[index])
                if self.parents[index] >= 0:
                    required.add(self.names[self.parents[index]])
        self.required_names = frozenset(required)

    @staticmethod
    def _target(values, index, record):
        result = values[index].copy()
        q, p = np.array(record['m_qOffset']), np.array(record['m_vOffset'])
        near = (np.all(abs(p) <= 1e-4) and np.all(abs(q[:3]) <= .1)
                and min(abs(q[3]-1), abs(q[3]+1)) <= .1)
        if not near:
            result[:3] += rotate(result[4:], p)*result[3]
            result[4:] = mul(result[4:], q)
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
        for index, (definition, (bone, targets)) in enumerate(zip(self._definitions,self._bindings)):
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
            elif kind == 'CPointConstraint':
                values = point_single(values,bone,children,self._target(values,targets[0],records[0])[:3],
                    target_weight=records[0]['m_flWeight'],slave_weight=slave['m_flWeight'])
            elif kind == 'COrientConstraint':
                values = orient_single(values,bone,children,self._target(values,targets[0],records[0])[4:],
                    target_weight=records[0]['m_flWeight'],slave_weight=slave['m_flWeight'])
            else:
                values = aim_single(values,bone,children,self._target(values,targets[0],records[0])[:3],
                    bone_parent=parent,up_type=definition['m_nUpType'],up_vector=definition['m_vUpVector'],
                    up_target=self._target(values,targets[-1],records[-1]) if len(targets)>1 else None,
                    aim_offset=definition['m_qAimOffset'],target_weight=records[0]['m_flWeight'],
                    slave_weight=slave['m_flWeight'])
        self._history, self._last_tick = history, tick
        return values
