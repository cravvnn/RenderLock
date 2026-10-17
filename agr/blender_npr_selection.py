"""Bind scene NPR packages to exact imported actor roots before material edits."""
import copy
from .source_npr_scene import validate_scene_npr_selection
from .source_hero_lighting_package import canonical_sha
from .npr_material_identity import material_resource


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
    combined=copy.deepcopy(next(iter(packages.values())))
    combined['materials']=[copy.deepcopy(rows[key]) for key in sorted(rows)]
    combined.pop('content_sha256')
    combined['content_sha256']=canonical_sha(combined)
    return combined,sorted(materials,key=lambda m:m.name),sorted(objects,key=lambda o:o.name)
