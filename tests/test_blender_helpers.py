"""Regression contracts for the glTF helper that AGR used to render as a ball."""
import unittest
import importlib.util
from types import SimpleNamespace

from agr.blender_helpers import exclude_gltf_bone_helpers, gltf_import_options


class Object(dict):
    def __init__(self, name="Icosphere", *, special=True, **overrides):
        super().__init__()
        self.name = name
        self.type = "MESH"
        self.parent = None
        self.children = []
        self.vertex_groups = []
        self.modifiers = []
        self.data = SimpleNamespace(materials=[], shape_keys=None)
        self.animation_data = None
        self.hide_render = False
        self.hide_viewport = False
        self.users_collection = [SimpleNamespace(
            name="glTF_not_exported" if special else "Hero", hide_render=special,
            hide_viewport=special)]
        self.__dict__.update(overrides)


class BlenderHelperTests(unittest.TestCase):
    def test_hidden_importer_display_dependency_never_becomes_model_geometry(self):
        helper, mesh = Object(), Object("Hero body", special=False)
        collections = tuple(helper.users_collection)
        assets, helpers = exclude_gltf_bone_helpers([helper, mesh])
        self.assertEqual([obj.name for obj in assets], ["Hero body"])
        self.assertEqual([obj.name for obj in helpers], ["Icosphere"])
        self.assertTrue(helper.hide_render and helper.hide_viewport)
        self.assertTrue(helper["agr_asset_hidden"])
        self.assertEqual(tuple(helper.users_collection), collections)
        self.assertIsNone(helper.parent)
        self.assertFalse(mesh.hide_render or mesh.hide_viewport)

    def test_real_geometry_named_icosphere_is_not_removed(self):
        mesh = Object(special=False)
        assets, helpers = exclude_gltf_bone_helpers([mesh])
        self.assertIs(assets[0], mesh)
        self.assertEqual(helpers, [])
        self.assertFalse(mesh.hide_render or mesh.hide_viewport)

    def test_deforming_material_and_parented_meshes_are_protected(self):
        meshes = [Object("Weighted", vertex_groups=[object()]),
                  Object("Modified", modifiers=[object()]),
                  Object("Parented", parent=object()),
                  Object("Animated", animation_data=object()),
                  Object("Material", data=SimpleNamespace(materials=[object()], shape_keys=None)),
                  Object("Morph", data=SimpleNamespace(materials=[], shape_keys=object()))]
        assets, helpers = exclude_gltf_bone_helpers(meshes)
        self.assertEqual([obj.name for obj in assets], [obj.name for obj in meshes])
        self.assertFalse(helpers)
        self.assertTrue(all(not obj.hide_render for obj in meshes))

    def test_shape_also_linked_as_visible_geometry_is_preserved(self):
        mesh = Object()
        mesh.users_collection.append(SimpleNamespace(name="Hero", hide_render=False, hide_viewport=False))
        assets, helpers = exclude_gltf_bone_helpers([mesh])
        self.assertIs(assets[0], mesh)
        self.assertFalse(helpers)

    def test_importer_setting_only_supplied_when_available(self):
        def api(properties):
            return SimpleNamespace(ops=SimpleNamespace(import_scene=SimpleNamespace(
                gltf=SimpleNamespace(get_rna_type=lambda: SimpleNamespace(properties=properties)))))
        self.assertEqual(gltf_import_options(api({"disable_bone_shape": None})), {"disable_bone_shape": True})
        self.assertEqual(gltf_import_options(api({"filepath": None})), {})


@unittest.skipUnless(importlib.util.find_spec("bpy"), "Blender bpy is not installed")
class BlenderHelperImportTests(unittest.TestCase):
    def test_real_gltf_import_retains_skin_and_named_geometry_without_render_helpers(self):
        import base64
        import json
        import struct
        import tempfile
        from pathlib import Path
        import bpy

        # A skinned triangle genuinely named Icosphere, with two joint matrices.
        # This distinguishes actual asset geometry from the importer-created ball.
        data = bytearray()
        views, accessors = [], []

        def accessor(values, fmt, count, kind, component, **bounds):
            while len(data) % 4:
                data.append(0)
            start = len(data)
            data.extend(struct.pack("<" + fmt, *values))
            views.append({"buffer": 0, "byteOffset": start, "byteLength": len(data) - start})
            accessors.append({"bufferView": len(views) - 1, "componentType": component,
                              "count": count, "type": kind, **bounds})
            return len(accessors) - 1

        position = accessor([0, 0, 0, 1, 0, 0, 0, 1, 0], "9f", 3, "VEC3", 5126,
                            min=[0, 0, 0], max=[1, 1, 0])
        joints = accessor([0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0], "12H", 3, "VEC4", 5123)
        weights = accessor([1, 0, 0, 0] * 3, "12f", 3, "VEC4", 5126)
        indices = accessor([0, 1, 2], "3H", 3, "SCALAR", 5123)
        identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        tip = list(identity)
        tip[13] = -1
        inverse = accessor(identity + tip, "32f", 2, "MAT4", 5126)
        gltf = {
            "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 2]}],
            "nodes": [{"name": "FixtureRoot", "children": [1]},
                      {"name": "FixtureTip", "translation": [0, 1, 0]},
                      {"name": "Icosphere", "mesh": 0, "skin": 0}],
            "skins": [{"joints": [0, 1], "skeleton": 0, "inverseBindMatrices": inverse}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": position,
                "JOINTS_0": joints, "WEIGHTS_0": weights}, "indices": indices}]}],
            "buffers": [{"byteLength": len(data), "uri": "data:application/octet-stream;base64,"
                         + base64.b64encode(data).decode("ascii")}],
            "bufferViews": views, "accessors": accessors,
        }

        def signature(objects):
            return {
                "meshes": sorted((tuple(tuple(v.co) for v in obj.data.vertices),
                                  tuple(tuple((g.group, g.weight) for g in v.groups)
                                        for v in obj.data.vertices))
                                 for obj in objects if obj.type == "MESH"),
                "rigs": sorted(tuple((bone.name, tuple(value for row in bone.matrix_local for value in row))
                                     for bone in obj.data.bones)
                               for obj in objects if obj.type == "ARMATURE"),
                "transforms": sorted(tuple(value for row in obj.matrix_world for value in row)
                                     for obj in objects),
            }

        before_all = set(bpy.data.objects)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "skinned_triangle.gltf"
                path.write_text(json.dumps(gltf))
                bpy.ops.import_scene.gltf(filepath=str(path), disable_bone_shape=False)
                imported = list(set(bpy.data.objects) - before_all)
                original_geometry = signature([obj for obj in imported if obj.type != "MESH" or obj.vertex_groups])
                assets, helpers = exclude_gltf_bone_helpers(imported)
                self.assertEqual(len(helpers), 1)
                self.assertEqual(sum(obj.type == "MESH" for obj in assets), 1)
                self.assertEqual(len(next(obj for obj in assets if obj.type == "MESH").data.vertices), 3)
                self.assertTrue(helpers[0].hide_render and helpers[0].hide_viewport)
                baseline = signature(assets)
                self.assertEqual(original_geometry, baseline)
                before_second = set(bpy.data.objects)
                bpy.ops.import_scene.gltf(filepath=str(path), **gltf_import_options(bpy))
                second = list(set(bpy.data.objects) - before_second)
                clean, second_helpers = exclude_gltf_bone_helpers(second)
                self.assertEqual(second_helpers, [])
                self.assertEqual(baseline, signature(clean))
        finally:
            for obj in set(bpy.data.objects) - before_all:
                bpy.data.objects.remove(obj, do_unlink=True)


if __name__ == "__main__":
    unittest.main()
