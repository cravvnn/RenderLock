from pathlib import Path
import pytest
from agr.blender_character_shader import validate_character_shader_library


def test_changed_or_missing_character_library_cannot_be_silently_selected(tmp_path):
    path=tmp_path/'shader.blend'
    with pytest.raises(ValueError,match='qualified local'):validate_character_shader_library(path)
    path.write_bytes(b'not the qualified artist library')
    with pytest.raises(ValueError,match='changed'):validate_character_shader_library(path)
    assert path.read_bytes()==b'not the qualified artist library'
