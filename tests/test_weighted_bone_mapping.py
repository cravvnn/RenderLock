"""Verify visible skin weights cannot silently hang from an unmapped rig root."""
from agr.blender_scene import asset_spec, weighted_bone_coverage


def test_weighted_helpers_report_the_nearest_pose_driven_ancestor():
    parents = {"root": None, "spine": "root", "coat": "spine", "coat_tip": "coat"}
    result = weighted_bone_coverage(parents, {"spine", "coat_tip"}, {"root", "spine"})
    assert result["mapped_weighted_bones"] == ["spine"]
    assert result["inherited_weighted_bones"] == {"coat_tip": "spine"}
    assert not result["unmapped_weighted_bones"]


def test_disconnected_weighted_root_is_detected_despite_other_matching_bones():
    parents = {"root": None, "spine": "root", "other_root": None, "weighted_tip": "other_root"}
    result = weighted_bone_coverage(parents, {"spine", "weighted_tip"}, {"root", "spine"})
    assert result["mapped_weighted_bones"] == ["spine"]
    assert result["unmapped_weighted_bones"] == ["weighted_tip"]


def test_empty_weights_and_parent_cycles_do_not_assert_coverage():
    empty = weighted_bone_coverage({"root": None}, [], ["root"])
    assert empty["weighted_bones"] == 0
    cycle = weighted_bone_coverage({"a": "b", "b": "a"}, ["a"], ["root"])
    assert cycle["unmapped_weighted_bones"] == ["a"]


def test_installed_guard_flag_passes_through_entity_spec(tmp_path):
    spec = asset_spec({"hero_id": 13, "require_weighted_bone_mapping": True,
                       "static_generated_cloth": True}, tmp_path, tmp_path)
    assert spec["require_weighted_bone_mapping"] is True
    assert spec["static_generated_cloth"] is True
    assert "require_weighted_bone_mapping" not in asset_spec({"hero_id": 13}, tmp_path, tmp_path)


def test_generated_cloth_requires_explicit_policy_and_reports_static_pose():
    parents = {"root": None, "$cloth_m1p0": None, "$cloth_m2p83": None}
    weights = {"root", "$cloth_m1p0", "$cloth_m2p83"}
    rejected = weighted_bone_coverage(parents, weights, {"root"})
    assert rejected["unmapped_weighted_bones"] == ["$cloth_m1p0", "$cloth_m2p83"]
    allowed = weighted_bone_coverage(parents, weights, {"root"}, static_generated_cloth=True)
    assert allowed["unmapped_weighted_bones"] == []
    assert allowed["static_generated_cloth_bone_count"] == 2
    assert allowed["static_generated_cloth_bones"] == ["$cloth_m1p0", "$cloth_m2p83"]
    assert allowed["cloth"] == "rest_pose_no_simulation"
    assert allowed["mapped_weighted_bones"] == ["root"]
    assert allowed["inherited_weighted_bones"] == {}


def test_static_cloth_policy_does_not_allow_other_disconnected_weights():
    invalid = {"other_root", "cloth_m1p0", "$cloth_m1px", "$cloth_m1p0_extra", "$cloth_m1p0\n"}
    parents = dict.fromkeys({"root", "$cloth_m1p0", *invalid})
    result = weighted_bone_coverage(parents, {"$cloth_m1p0", *invalid}, {"root"}, static_generated_cloth=True)
    assert result["static_generated_cloth_bones"] == ["$cloth_m1p0"]
    assert result["unmapped_weighted_bones"] == sorted(invalid)
