import pytest
from agr.blender_particle_skinning import evaluated_snapshot_rig


class Node:
    def __init__(self, parent=None, hidden=False, preview=False, kind='EMPTY'):
        self.parent, self.hide_viewport, self.preview, self.type = parent, hidden, preview, kind

    def get(self, key):
        return self.preview if key == 'agr_preview_hidden' else None


class Scene:
    frame_current, frame_subframe = 7, .25

    def frame_set(self, frame, *, subframe):
        self.frame_current, self.frame_subframe = frame, subframe


def test_nested_capture_keeps_outer_preview_borrow_until_outer_exit():
    scene = Scene(); root = Node(); rig = Node(root, True, True, 'ARMATURE')
    with evaluated_snapshot_rig(rig, root, scene):
        assert not rig.hide_viewport
        scene.frame_set(2, subframe=.5)
        with evaluated_snapshot_rig(rig, root, scene):
            scene.frame_set(3, subframe=0)
        assert not rig.hide_viewport and (scene.frame_current, scene.frame_subframe) == (2, .5)
    assert rig.hide_viewport and (scene.frame_current, scene.frame_subframe) == (7, .25)


def test_failed_native_frame_restore_still_restores_visibility_and_propagates():
    class FailedRestore(Scene):
        def frame_set(self, frame, *, subframe):
            raise RuntimeError('frame restoration failed')
    scene = FailedRestore(); root = Node(); rig = Node(root, True, True, 'ARMATURE')
    with pytest.raises(RuntimeError, match='frame restoration failed'):
        with evaluated_snapshot_rig(rig, root, scene):
            scene.frame_current = 3
    assert rig.hide_viewport and scene.frame_current == 3
