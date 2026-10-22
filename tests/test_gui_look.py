"""Character looks preserve preferences while resolving job dependencies."""
from copy import deepcopy
from itertools import product

import pytest

from agr.gui_look import (
    LOOK_CUSTOM,
    LOOK_ELAGRIMM,
    LOOK_SOURCE,
    LOOK_STANDARD,
    migrate_look_settings,
    resolve_look_settings,
)


FLAGS = ("source_lighting", "source_hero_lighting", "source_npr_lighting")
LIBRARY = "D:/artist libraries/Elagrimm.blend"
TOOLS = {
    "world_region_probe": "D:/helpers/world/WorldRegionProbe.dll",
    "lighting_dotnet": "D:/helpers/dotnet/dotnet.exe",
    "probe_atlas_decoder": "D:/helpers/atlas/ProbeAtlasDecode.dll",
    "cubemap_decoder": "D:/helpers/cube/CubemapDecode.dll",
}


@pytest.mark.parametrize("flags", list(product(("0", "1"), repeat=3)))
@pytest.mark.parametrize("empty_mode", [None, ""])
def test_legacy_combinations_without_shader_are_preserved(flags, empty_mode):
    values = dict(zip(FLAGS, flags), character_shader="", **TOOLS)
    if empty_mode is not None:
        values["character_look"] = empty_mode
    before = deepcopy(values)
    migrated = migrate_look_settings(values)
    expected = LOOK_STANDARD if flags == ("0", "0", "0") else (
        LOOK_SOURCE if flags == ("1", "1", "0") else LOOK_CUSTOM
    )
    assert migrated == dict(before, character_look=expected)
    assert values == before


@pytest.mark.parametrize("flags", list(product(("0", "1"), repeat=3)))
def test_legacy_nonempty_library_activates_elagrimm_even_with_unusual_flags(flags):
    values = dict(zip(FLAGS, flags), character_shader=LIBRARY, **TOOLS)
    migrated = migrate_look_settings(values)
    assert migrated == dict(values, character_look=LOOK_ELAGRIMM)
    assert "character_look" not in values


@pytest.mark.parametrize("mode", [LOOK_STANDARD, LOOK_SOURCE, LOOK_ELAGRIMM, LOOK_CUSTOM])
def test_explicit_mode_overrides_legacy_inference_and_migration_is_idempotent(mode):
    values = dict(character_look=mode, character_shader=LIBRARY, **TOOLS)
    migrated = migrate_look_settings(values)
    assert migrated == values
    assert migrate_look_settings(migrated) == migrated


@pytest.mark.parametrize("path", ["", " \t "])
def test_blank_legacy_library_does_not_enable_elagrimm(path):
    values = dict(character_shader=path, source_npr_lighting="1")
    assert migrate_look_settings(values) == dict(values, character_look=LOOK_CUSTOM)


@pytest.mark.parametrize("operation", [migrate_look_settings, resolve_look_settings])
def test_empty_preferences_default_to_standard_without_mutating_input(operation):
    values = {}
    result = operation(values)
    assert result["character_look"] == LOOK_STANDARD
    assert values == {}


@pytest.mark.parametrize("operation", [migrate_look_settings, resolve_look_settings])
def test_returned_preferences_are_deep_copies_and_keep_string_value_types(operation):
    values = dict(character_look=LOOK_CUSTOM, source_lighting="0", map_enabled="1",
                  character_shader=LIBRARY, nested={"paths": [LIBRARY]}, **TOOLS)
    result = operation(values)
    assert result == values
    assert all(isinstance(result[key], str) for key in values if key != "nested")
    result["nested"]["paths"].append("new selection")
    result["character_shader"] = "different.blend"
    assert values["nested"]["paths"] == [LIBRARY]
    assert values["character_shader"] == LIBRARY


@pytest.mark.parametrize("mode,flags,shader", [
    (LOOK_STANDARD, ("0", "0", "0"), ""),
    (LOOK_SOURCE, ("1", "1", "0"), ""),
    (LOOK_ELAGRIMM, ("1", "1", "1"), LIBRARY),
])
@pytest.mark.parametrize("character_only", [False, True])
def test_presets_resolve_effective_flags_and_keep_optional_choices(mode, flags, shader, character_only):
    values = dict(character_look=mode, character_shader=LIBRARY, source_lighting="0",
                  source_hero_lighting="1", source_npr_lighting="0", map_enabled="0",
                  map_name="dl_midtown", map_margin="26", character_only="0",
                  shot_hero="", effects_enabled="0", simulate_effects="1",
                  cloth_enabled="0", material_repairs="1", partial="0",
                  partial_cloth="1", heroes="11,2", blender="D:/tools/blender.exe",
                  output="D:/scenes/character.blend", **TOOLS)
    before = deepcopy(values)
    result = resolve_look_settings(values, character_only=character_only)
    assert tuple(result[key] for key in FLAGS) == flags
    assert all(isinstance(result[key], str) for key in FLAGS)
    assert result["character_shader"] == shader
    assert result["character_look"] == mode
    for key in values.keys() - {*FLAGS, "character_shader", "map_enabled"}:
        assert result[key] == values[key]
    assert result["map_enabled"] == ("0" if mode == LOOK_STANDARD else "1")
    assert values == before


@pytest.mark.parametrize("mode", [LOOK_STANDARD, LOOK_SOURCE])
def test_dormant_library_can_be_reused_for_elagrimm_after_resolving_another_preset(mode):
    values = dict(character_look=mode, character_shader=LIBRARY, **TOOLS)
    assert resolve_look_settings(values)["character_shader"] == ""
    assert values["character_shader"] == LIBRARY
    values["character_look"] = LOOK_ELAGRIMM
    assert resolve_look_settings(values)["character_shader"] == LIBRARY
    assert migrate_look_settings(values)["character_shader"] == LIBRARY


@pytest.mark.parametrize("map_enabled", ["0", "1"])
@pytest.mark.parametrize("character_only", [False, True])
def test_standard_prunes_map_preparation_only_for_character_only_jobs(map_enabled, character_only):
    values = dict(character_look=LOOK_STANDARD, map_enabled=map_enabled)
    result = resolve_look_settings(values, character_only=character_only)
    assert result["map_enabled"] == ("0" if character_only else map_enabled)
    assert values["map_enabled"] == map_enabled


@pytest.mark.parametrize("mode", [LOOK_SOURCE, LOOK_ELAGRIMM])
def test_source_looks_automatically_request_map_data_for_character_only_jobs(mode):
    values = dict(character_look=mode, map_enabled="0", character_shader=LIBRARY)
    assert resolve_look_settings(values, character_only=True)["map_enabled"] == "1"
    assert values["map_enabled"] == "0"


@pytest.mark.parametrize("flags", list(product(("0", "1"), repeat=3)))
@pytest.mark.parametrize("character_only", [False, True])
def test_custom_leaves_raw_flags_and_map_preparation_for_command_validation(flags, character_only):
    values = dict(zip(FLAGS, flags), character_look=LOOK_CUSTOM,
                  map_enabled="1", character_shader="", shot_hero="", **TOOLS)
    assert resolve_look_settings(values, character_only=character_only) == values


def test_custom_preserves_active_shader_without_imposing_preset_dependencies():
    values = dict(character_look=LOOK_CUSTOM, character_shader=LIBRARY, **TOOLS)
    assert resolve_look_settings(values, character_only=True) == values


@pytest.mark.parametrize("path", [None, "", " \t "])
def test_elagrimm_requires_a_chosen_shader_library_with_actionable_error(path):
    values = dict(character_look=LOOK_ELAGRIMM, character_shader=path)
    assert migrate_look_settings(values) == values
    with pytest.raises(ValueError, match=r"Elagrimm Source requires.*Choose.*\.blend"):
        resolve_look_settings(values)
    assert values == dict(character_look=LOOK_ELAGRIMM, character_shader=path)


def test_elagrimm_requires_library_when_key_is_missing():
    with pytest.raises(ValueError, match="Choose the supported Elagrimm shader"):
        resolve_look_settings(dict(character_look=LOOK_ELAGRIMM))


@pytest.mark.parametrize("path", ["does-not-exist.blend", " Z:/unused tool/library.blend "])
def test_elagrimm_resolution_does_not_validate_or_rewrite_filesystem_paths(path):
    values = dict(character_look=LOOK_ELAGRIMM, character_shader=path, **TOOLS)
    result = resolve_look_settings(values)
    assert result["character_shader"] == path
    assert all(result[key] == value for key, value in TOOLS.items())


@pytest.mark.parametrize("operation", [migrate_look_settings, resolve_look_settings])
@pytest.mark.parametrize("mode", ["future_look", "SOURCE", " ", True, [LOOK_STANDARD]])
def test_unknown_explicit_mode_is_actionably_rejected_without_mutating_settings(operation, mode):
    values = dict(character_look=mode, character_shader=LIBRARY, **TOOLS)
    before = deepcopy(values)
    with pytest.raises(ValueError, match="Unknown character look.*Choose Standard.*Custom"):
        operation(values)
    assert values == before
