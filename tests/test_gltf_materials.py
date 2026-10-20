from agr.gltf_materials import repair_materials
import json
import struct
import pytest
from agr.gltf_materials import repair_glb


def material(flags, **params):
    return {'materials': [{'name': 'test', 'extras': {'vmat': {
        'ShaderName': 'pbr.vfx', 'IntParams': flags, **params}}}]}


def test_unlit_black_does_not_become_white_and_input_is_preserved():
    original = material({'F_UNLIT': 1}, VectorParams={'g_vColorTint1': [0, 0, 0, 0]})
    fixed, _ = repair_materials(original)
    assert fixed['materials'][0]['pbrMetallicRoughness']['baseColorFactor'] == [0, 0, 0, 1]
    assert 'pbrMetallicRoughness' not in original['materials'][0]
    assert fixed['materials'][0]['extras']['agr_material_repairs']['unlit_visibility'] == 'camera_reflection_transmission_v1'
    assert repair_materials(fixed)[0] == fixed


def test_npr_unlit_dependency_changes_only_unlit_surface():
    original=material({'F_UNLIT':1},VectorParams={'g_vColorTint1':[0,0,0,0]})
    other=material({'F_GLASS':1,'F_SELF_ILLUM':1},FloatParams={'g_flSelfIllumScale1':3},VectorParams={'g_vSelfIllumTint1':[1,0,0,0]})['materials'][0]
    other['emissiveTexture']={'index':0}
    original['materials'].append(other)
    fixed,report=repair_materials(original,unlit_only=True)
    assert fixed['materials'][1]==other
    assert fixed['materials'][0]['extensions']=={'KHR_materials_unlit':{}}
    assert len(report['materials'])==1


def test_invalid_unlit_tint_does_not_opt_in_to_blender_repair():
    original = material({'F_UNLIT': 1}, VectorParams={'g_vColorTint1': [-1, 0, 0, 0]})
    fixed, report = repair_materials(original)
    assert fixed == original
    assert report['materials'][0]['warnings']


def test_emission_preserves_texture_and_uses_source_strength():
    original = material({'F_SELF_ILLUM': 1}, FloatParams={'g_flSelfIllumScale1': 2.25},
                        VectorParams={'g_vSelfIllumTint1': [1, .8, .3, 0]})
    original['materials'][0]['emissiveTexture'] = {'index': 7, 'texCoord': 1}
    fixed, _ = repair_materials(original)
    m = fixed['materials'][0]
    assert m['emissiveTexture'] == {'index': 7, 'texCoord': 1}
    assert m['emissiveFactor'] == [1, .8, .3]
    assert m['extensions']['KHR_materials_emissive_strength']['emissiveStrength'] == 2.25


def test_glass_requires_explicit_approximation_and_incomplete_emission_is_reported():
    original = material({'F_GLASS': 1, 'F_SELF_ILLUM': 1})
    fixed, report = repair_materials(original)
    assert fixed == original
    assert len(report['materials'][0]['warnings']) == 2
    fixed, report = repair_materials(original, approximate_glass=True)
    assert fixed['materials'][0]['extensions']['KHR_materials_transmission']['transmissionFactor'] == 1
    assert any('artistic' in w for w in report['materials'][0]['warnings'])


def test_glb_repair_preserves_binary_chunks_and_refuses_overwrite(tmp_path):
    d = material({'F_UNLIT': 1}, VectorParams={'g_vColorTint1': [0, 0, 0, 0]})
    encoded = json.dumps(d).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary = bytes(range(256))
    tail = struct.pack('<I4s', len(binary), b'BIN\0') + binary
    body = struct.pack('<I4s', len(encoded), b'JSON') + encoded + tail
    raw = struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body
    source, output = tmp_path / 'source.glb', tmp_path / 'fixed.glb'
    source.write_bytes(raw)
    repair_glb(source, output)
    repaired = output.read_bytes()
    assert source.read_bytes() == raw
    assert repaired.endswith(tail)
    assert struct.unpack_from('<I', repaired, 8)[0] == len(repaired)
    size = struct.unpack_from('<I', repaired, 12)[0]
    assert json.loads(repaired[20:20 + size])['materials'][0]['pbrMetallicRoughness']['baseColorFactor'] == [0, 0, 0, 1]
    with pytest.raises(FileExistsError):
        repair_glb(source, output)
    source.write_bytes(raw[:-1])
    with pytest.raises(ValueError, match='header'):
        repair_glb(source, tmp_path / 'bad.glb')
    assert not (tmp_path / 'bad.glb').exists()
