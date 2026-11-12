"""Authored bodygroup selection after source/geometry inventory qualification.

This pure projection matches the qualified native bodygroup setter. Callers must
separately resolve selector attributes, subtype, initial mask and model identity.
It does not admit runtime placement, material shading or other selector subtypes.
"""
import math
import re
import struct

from .installed_effects import UnsupportedEffect
from .model_mesh_groups import uint64
from .particle_model_bindings import POLICY as INVENTORY_POLICY

POLICY='agr_particle_authored_bodygroup_selection_v1'


def _require(condition, reason):
    if not condition: raise UnsupportedEffect(reason)


def _native_int(value):
    _require(type(value) in (int,float) and math.isfinite(value),
             'Finite model bodygroup selector required')
    try: value=struct.unpack('<f',struct.pack('<f',value))[0]
    except (OverflowError,struct.error) as exc:
        raise UnsupportedEffect('Model bodygroup selector exceeds float32') from exc
    _require(math.isfinite(value) and -2**31<=value<2**31,
             'Model bodygroup selector exceeds native int32 conversion')
    return math.trunc(value)


def authored_bodygroup_selection(inventory, bindings, *, initial_mask, body_group, submodel):
    """Replace one body's bits; invalid integer indices retain the initial mask.

    The disabled bodygroup attribute is not silently assumed to mean group zero.
    The caller supplies its independently resolved value. Metadata is the exact
    source inventory already bound to geometry by authored_primitive_bindings.
    """
    _require(inventory.get('policy')==bindings.get('policy')==INVENTORY_POLICY
             and type(inventory.get('schema_version')) is int and inventory['schema_version']==1,
             'Qualified authored model inventory/bindings required')
    _require(inventory.get('resource')==bindings.get('resource')
             and inventory.get('source_sha256')==bindings.get('source_sha256')
             and isinstance(inventory.get('source_sha256'),str)
             and re.fullmatch('[0-9a-f]{64}',inventory['source_sha256']),
             'Authored bodygroup source identity differs')
    names=inventory.get('mesh_groups');bodies=inventory.get('body_groups')
    _require(isinstance(names,list) and 1<=len(names)<=64 and all(isinstance(n,str) and n for n in names)
             and len(set(names))==len(names) and isinstance(bodies,list) and 1<=len(bodies)<=64,
             'Bounded authored bodygroup metadata required')
    groups=[];covered=0;body_names=set()
    for body in bodies:
        name=body.get('name');choices=body.get('choices')
        _require(isinstance(name,str) and name and name not in body_names
                 and isinstance(choices,list) and 1<=len(choices)<=64,
                 'Distinct nonempty authored bodygroups required')
        body_names.add(name);masks=[];combined=0
        for choice in choices:
            index=choice.get('group_index')
            _require(type(index) is int and 0<=index<len(names)
                     and choice.get('full_name')==names[index]
                     and isinstance(choice.get('name'),str) and choice['name'],
                     'Authored bodygroup choice identity differs')
            bit=1<<index
            _require(not combined&bit and not covered&bit,'Overlapping authored bodygroup choices')
            masks.append(bit);combined|=bit
        covered|=combined;groups.append((combined,masks))
    source_rows=inventory.get('meshes');bound_rows=bindings.get('meshes')
    _require(isinstance(source_rows,list) and isinstance(bound_rows,list)
             and 1<=len(source_rows)==len(bound_rows)<=256,'Bounded authored bodygroup meshes required')
    source={}
    for row in source_rows:
        index=row.get('source_mesh_index')
        _require(type(index) is int and 0<=index<4096 and index not in source,
                 'Distinct authored source mesh indices required')
        source[index]=uint64(row.get('mesh_group_mask'))
    seen=set();mesh_indices=set()
    for row in bound_rows:
        index=row.get('source_mesh_index');mesh=row.get('gltf_mesh_index')
        _require(type(index) is int and index in source and index not in seen
                 and type(mesh) is int and 0<=mesh<256 and mesh not in mesh_indices
                 and uint64(row.get('mesh_group_mask'))==source[index],
                 'Authored bodygroup mesh binding differs')
        seen.add(index);mesh_indices.add(mesh)
    selected=uint64(initial_mask);group=_native_int(body_group);choice=_native_int(submodel)
    if 0<=group<len(groups) and 0<=choice<len(groups[group][1]):
        combined,choices=groups[group];selected=(selected&~combined)|choices[choice]
    visible=[row for row in bound_rows if source[row['source_mesh_index']]&selected]
    return dict(policy=POLICY,selected_mask=str(selected),body_group=group,submodel=choice,
                source_mesh_indices=[row['source_mesh_index']for row in visible],
                gltf_mesh_indices=[row['gltf_mesh_index']for row in visible],
                scope='Resolved subtype0 bodygroup projection only; placement and shading are separate')
