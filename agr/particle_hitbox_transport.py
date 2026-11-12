"""Stateless preparation of explicit, identity-bound CP hitbox request frames.

Engine model selection and evaluated pose acquisition are separate obligations.
The caller supplies the exact authored inventory and a qualified pose snapshot.
"""
import hashlib
import json
import re

from .particle_hitbox_shapes import model_hitbox_shapes


POLICY = 'agr_explicit_cp_hitbox_frames_v1'


def prepare_hitbox_input(inventory, pose, *, entity_id, tick, control_point):
    """Return binding header and frame entry for one explicit control point.

    Header identity is independent of frame time/pose values. Every frame entry
    repeats that identity; the backend checks bounds, layout and frame continuity.
    Full replay/rig/pose provenance remains bound by the containing export package.
    """
    sets = {}
    for selected in inventory.get('hitbox_sets', []):
        records = model_hitbox_shapes(inventory, pose, entity_id=entity_id,
            tick=tick, control_point=control_point, hitbox_set=selected['name'])
        sets[selected['name']] = [dict(min=low.tolist(), max=high.tolist(),
            transform=transform.tolist()) for low, high, transform in zip(
                records['mins'], records['maxs'], records['transforms'])]
    if not sets:
        raise ValueError('Explicit hitbox input requires authored sets')
    resource = records['resource']
    if (not re.fullmatch(r'[0-9]+:[0-9]+', entity_id)
            or resource.startswith('/') or '..' in resource or ':' in resource
            or '\\' in resource or any(not ' ' <= c <= '~' for c in resource)):
        raise ValueError('Hitbox transport requires an exact entity ID and relative model resource')
    names = json.dumps(inventory['model_bone_names'], separators=(',', ':'),
                       ensure_ascii=True).encode('utf-8')
    header = dict(control_point=control_point, entity_id=entity_id,
        model=records['resource']+'_c', model_sha256=records['resource_sha256'],
        bone_order_sha256=hashlib.sha256(names).hexdigest())
    # This field order is the shared C#/Python binding contract. Each component
    # has already been checked; no field contains a newline separator.
    canonical = '\n'.join(str(header[key]) for key in ('control_point', 'entity_id',
        'model', 'model_sha256', 'bone_order_sha256'))
    header['binding_id'] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return header, dict(control_point=control_point,
        binding_id=header['binding_id'], sets=sets)
