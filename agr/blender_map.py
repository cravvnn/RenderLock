"""Import the selected installed visual map without changing replay coordinates."""
from __future__ import annotations

from pathlib import Path


def build_map(data, manifest_dir, scene):
    descriptor = data.get("map")
    if not isinstance(descriptor, dict) or descriptor.get("status") != "ready":
        return {"status": "not_prepared", "collision": False}
    import bpy
    from mathutils import Matrix
    from .blender_helpers import gltf_import_options, exclude_gltf_bone_helpers
    from .installed_map import blender_map_matrix

    path = Path(descriptor["model_file"])
    if not path.is_absolute():
        path = Path(manifest_dir) / path
    lighting_images = None
    if descriptor.get('lighting') is not None:
        from .source_lighting_package import validate_lighting_package
        lighting_images = validate_lighting_package(descriptor['lighting'],descriptor,path,manifest_dir)
    before = set(bpy.data.objects)
    collection = None
    try:
        bpy.ops.import_scene.gltf(filepath=str(path), import_pack_images=True, **gltf_import_options(bpy))
        objects, helpers = exclude_gltf_bone_helpers(list(set(bpy.data.objects) - before))
        for obj in list(objects):
            if obj.type == "CAMERA":
                objects.remove(obj)
                bpy.data.objects.remove(obj, do_unlink=True)
        meshes = [obj for obj in objects if obj.type == "MESH"]
        if not meshes:
            raise ValueError("Selected installed map imported without any mesh objects")
        collection = bpy.data.collections.new("AGR | installed map region")
        scene.collection.children.link(collection)
        basis = bpy.data.objects.new("AGR | map coordinate basis", None)
        collection.objects.link(basis)
        # Derive from the current coordinate contract again: a saved manifest
        # may have been sliced or moved to a different scene origin.
        basis.matrix_world = Matrix(blender_map_matrix(data.get("metadata", {}).get("coordinate_system")))
        for obj in objects:
            local = obj.matrix_basis.copy()
            for old in list(obj.users_collection):
                old.objects.unlink(obj)
            collection.objects.link(obj)
            if obj.parent not in objects:
                obj.parent = basis
                obj.matrix_parent_inverse = Matrix.Identity(4)
                obj.matrix_basis = local
            obj["agr_map_geometry"] = True
        scene["agr_map_name"] = str(descriptor["name"])
        material_repairs, material_unsupported = [], []
        if descriptor.get('material_repairs'):
            from .blender_overlay_material import repair_overlay_alpha
            materials = {slot.material for obj in meshes for slot in obj.material_slots if slot.material}
            for material in sorted(materials, key=lambda m: m.name):
                try:
                    repaired = repair_overlay_alpha(material)
                    if repaired:
                        material_repairs.append(repaired)
                except ValueError as error:
                    material_unsupported.append({'material': material.name, 'reason': str(error)})
        lighting = {"status": "not_requested"}
        if lighting_images is not None:
            from .blender_map_lighting import apply_map_lighting
            lighting = apply_map_lighting(meshes,descriptor['lighting'],lighting_images,
                data.get('metadata',{}).get('coordinate_system'))
        return {"status": "imported", "name": descriptor["name"], "lighting": lighting,
                "material_repairs": material_repairs, "material_repair_unsupported": material_unsupported,
                "collection": collection.name, "mesh_objects": len(meshes),
                "vertices": sum(len(obj.data.vertices) for obj in meshes),
                "source_bounds_xyz": descriptor.get("source_bounds_xyz"),
                "coordinate_matrix": [list(row) for row in basis.matrix_world],
                "collision": False, "limits": descriptor.get("limits", [])}
    except Exception:
        for obj in set(bpy.data.objects) - before:
            bpy.data.objects.remove(obj, do_unlink=True)
        if collection is not None:
            bpy.data.collections.remove(collection)
        raise
