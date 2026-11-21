"""Portable contracts for external Blender jobs; no Blender import is required."""
import json
from pathlib import Path
import tempfile
import unittest

from agr.blender_scene import asset_spec, tick_frame, visibility_changes


class BlenderSceneContractTests(unittest.TestCase):

    def test_fractional_endpoint_is_retained_at_different_output_fps(self):
        self.assertEqual(tick_frame(112704, 112704, 64, 30), 1)
        self.assertEqual(tick_frame(112719, 112704, 64, 30), 8.03125)
        self.assertEqual(tick_frame(112704 + 64, 112704, 64, 30), 31)

    def test_entity_pose_path_and_pack_model_use_different_bases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, segment = root / "pack", root / "job"
            pack.mkdir()
            segment.mkdir()
            (pack / "assets.json").write_text(json.dumps({"heroes": {"13": {
                "model_file": "models/heroes.blend", "asset_objects": ["haze_ARM"],
                "skeleton_file": "skeletons/Haze.json", "model_yaw_degrees": 180}}}))
            actual = asset_spec({"id": "17:2", "hero_id": 13, "hero_name": "Haze",
                                 "pose_file": "evaluated/17_2.npz"}, pack, segment)
            self.assertEqual(actual["pose_file"], str(segment / "evaluated/17_2.npz"))
            self.assertEqual(actual["model_file"], str(pack / "models/heroes.blend"))
            self.assertEqual(actual["skeleton_file"], str(pack / "skeletons/Haze.json"))
            self.assertEqual(actual["model_yaw_degrees"], 180)
            self.assertEqual(actual["asset_objects"], ["haze_ARM"])

    def test_disappeared_serial_is_hidden_and_replacement_serial_appears(self):
        data = {"start_tick": 100, "end_tick": 110, "tick_rate": 64}
        old = {"id": "17:1", "observed_intervals": [{"start_tick": 100, "end_tick_exclusive": 102}]}
        new = {"id": "17:2", "observed_intervals": [{"start_tick": 105, "end_tick_exclusive": 111}]}
        self.assertEqual(visibility_changes(old, [{"tick": 100}, {"tick": 101}], data, 64), [(1, False), (3, True)])
        # Death keeps the observed replacement body's held skeletal pose visible.
        self.assertEqual(visibility_changes(new, [{"tick": 105, "alive": True}, {"tick": 106, "alive": False}], data, 64), [(1, True), (6, False)])

    def test_blender_stage_output_is_retained_beside_the_scene(self):
        import io
        import sys
        from agr.blender_scene import attach_blender_log
        stdout, stderr = sys.stdout, sys.stderr
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = attach_blender_log(Path(directory) / "scene.blend")
                print("blender stage diagnostics")
                sys.stdout.flush()
                sys.stdout.log.close()
                sys.stdout, sys.stderr = stdout, stderr
                self.assertEqual(path.name, "scene.blender.log")
                text = path.read_text(encoding="utf-8")
                self.assertIn("blender stage diagnostics", text)
                self.assertIn("RenderLock Blender scene log", text)
        finally:
            sys.stdout, sys.stderr = stdout, stderr


if __name__ == "__main__":
    unittest.main()
