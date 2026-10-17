"""Keep glTF importer's bone-display meshes out of AGR render geometry.

The importer creates a unit Icosphere in its hidden ``glTF_not_exported``
collection. AGR must not move that display dependency into a hero collection.
The object name is not evidence: real model geometry may be named Icosphere.
This module deliberately has no bpy import so portable tests can import AGR.
"""
from __future__ import annotations


def gltf_import_options(bpy_module):
    """Disable generated bone display geometry when the importer supports it.

    The setting affects custom viewport bone shapes only, not the skeleton,
    mesh, skin weights, or transforms. Inspect RNA to retain compatibility with
    Blender installations whose bundled importer predates this option.
    """
    properties = bpy_module.ops.import_scene.gltf.get_rna_type().properties
    return {"disable_bone_shape": True} if "disable_bone_shape" in properties else {}


def exclude_gltf_bone_helpers(imported_objects):
    """Return (asset objects, helpers), before AGR relinks imported objects.

    Only nondeforming, unparented meshes confined to the importer's reserved,
    hidden collection qualify. Keep the helper datablocks and their existing
    collection links so pose-bone custom-shape references remain valid. They
    receive no replay parenting, lifetime animation, or geometry processing.

    This function is for objects returned by a fresh glTF import. It is not a
    name-based cleanup utility for arbitrary user-authored Blender scenes.
    """
    assets, helpers = [], []
    for obj in imported_objects:
        collections = tuple(obj.users_collection)
        importer_only = bool(collections) and all(
            collection.name == "glTF_not_exported"
            and collection.hide_viewport and collection.hide_render
            for collection in collections
        )
        # The glTF importer creates exactly a bare display mesh. These guards
        # leave any weighted, parented, material-bearing or animated geometry
        # alone even if an extension puts it in the reserved collection.
        display_mesh = (
            obj.type == "MESH" and obj.parent is None
            and not obj.children and not obj.vertex_groups and not obj.modifiers
            and not obj.data.materials and obj.data.shape_keys is None
            and obj.animation_data is None
        )
        if importer_only and display_mesh:
            obj.hide_render = True
            obj.hide_viewport = True
            obj["agr_asset_hidden"] = True
            obj["agr_import_helper"] = "gltf_bone_display"
            helpers.append(obj)
        else:
            assets.append(obj)
    return assets, helpers
