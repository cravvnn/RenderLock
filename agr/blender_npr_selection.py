"""Bind scene NPR packages to exact imported actor roots before material edits."""
import copy
from .source_npr_scene import validate_scene_npr_selection
from .source_hero_lighting_package import canonical_sha
from .npr_material_identity import pbr_material_resource as material_resource
from .source_material_routes import material_route,NPR_ROUTE,UNLIT_ROUTE


def actor_material_rows(actor):
    rows={row['material']:row for row in actor['package']['materials']}
    result=[]
    for material in actor['materials']:
        row=rows[material_resource(material)]
        flags=material['vmat'].get('IntParams',{})
        actual=NPR_ROUTE if flags.get('F_USE_NPR_LIGHTING')==1 else UNLIT_ROUTE if flags.get('F_UNLIT')==1 else 'generic_pbr_indirect'
        if actual!=material_route(row):raise ValueError('Selected material route differs from Source package')
        result.append((material,row))
    return result


def actor_meshes(root):
    """Particle children have their own material pipeline and coverage report."""
    meshes=set()
    for obj in root.children_recursive:
        if obj.type!='MESH':continue
        instance=obj.get('agr_particle_instance')
        if instance is not None:
            resource=obj.get('agr_particle_resource')
            if (not isinstance(instance,str) or not instance or not isinstance(resource,str)
                    or not resource.endswith(('.vpcf','.vpcf_c'))):
                raise ValueError('Incomplete child particle identity')
            continue
        meshes.add(obj)
    return meshes


def select_npr_materials(package, entities, roots, *, manifest_dir):
    packages=validate_scene_npr_selection(package,entities,manifest_dir=manifest_dir)
    selected={}
    for root in roots:
        identity=root.get('agr_entity_id')
        if identity not in packages or identity in selected:
            raise ValueError('NPR root identity/selection differs')
        selected[identity]=root
    if set(selected)!=set(packages):raise ValueError('NPR roots do not cover selected entities')
    materials=set();objects=set();rows={}
    for identity,root in selected.items():
        meshes=actor_meshes(root)
        if not meshes or meshes & objects:raise ValueError('Missing or overlapping NPR actor meshes')
        actor_materials={s.material for o in meshes for s in o.material_slots}
        if None in actor_materials:raise ValueError('NPR actor has empty material slot')
        expected={row['material']:row for row in packages[identity]['materials']}
        if {material_resource(m) for m in actor_materials}!=set(expected):
            raise ValueError('NPR actor/model material coverage differs')
        for name,row in expected.items():
            if name in rows and rows[name]!=row:
                raise ValueError('Conflicting shared NPR material packages')
            rows[name]=row
        materials.update(actor_materials);objects.update(meshes)
    # Consolidate identical materials across actor models for one all-material
    # preparation transaction, instead of committing actors one at a time.
    # The v2 texture contract also represents all legacy constant masks.
    combined=copy.deepcopy(max(packages.values(),key=lambda p:p['schema_version']))
    combined['materials']=[copy.deepcopy(rows[key]) for key in sorted(rows)]
    combined.pop('content_sha256')
    combined['content_sha256']=canonical_sha(combined)
    return combined,sorted(materials,key=lambda m:m.name),sorted(objects,key=lambda o:o.name)


def select_npr_actor_materials(package, entities, roots, *, manifest_dir):
    """Keep each validated model package within its native material budget."""
    packages=validate_scene_npr_selection(package,entities,manifest_dir=manifest_dir)
    selected={};current={e['id']:e for e in entities}
    for root in roots:
        identity=root.get('agr_entity_id')
        if identity not in packages or identity in selected:
            raise ValueError('NPR root identity/selection differs')
        selected[identity]=root
    if set(selected)!=set(packages):raise ValueError('NPR roots do not cover selected entities')
    rows=[];objects=set();owners={}
    for identity,root in selected.items():
        meshes=actor_meshes(root)
        if not meshes or meshes & objects:raise ValueError('Missing or overlapping NPR actor meshes')
        materials={slot.material for obj in meshes for slot in obj.material_slots}
        if None in materials:raise ValueError('NPR actor has empty material slot')
        expected={row['material'] for row in packages[identity]['materials']}
        if {material_resource(m) for m in materials}!=expected:
            raise ValueError('NPR actor/model material coverage differs')
        for material in materials:
            if material in owners:raise ValueError('NPR actors require distinct prepared material copies')
            owners[material]=identity
        objects.update(meshes)
        rows.append(dict(entity=current[identity],root=root,package=packages[identity],
                         materials=sorted(materials,key=lambda m:m.name),
                         objects=sorted(meshes,key=lambda o:o.name)))
        actor_material_rows(rows[-1])
    return rows
