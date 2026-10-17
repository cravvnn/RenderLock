"""Map selection correctness and isolated cache lifecycle, without game files."""
import json
from pathlib import Path
import shutil
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from agr.asset_converter import AssetConverter, ConversionError
from agr.game_assets import GameAssetProvider, MissingResourceError
from agr.gltf_region import AccessorReader, crop
from agr.installed_map import (blender_map_matrix, prepare_installed_map,
                                replay_bounds, selected_world, _data_dependencies,
                                _entity_properties, _world_dependencies, _check_missing_texture_usage)
from test_game_assets import compiled, installation, write_vpk


@pytest.fixture(autouse=True)
def map_data_fixture_boundary(monkeypatch):
    # These tests exercise dependency closure/export/cropping with decoded DATA
    # fixtures. test_map_text.py covers the real map DATA adapter and its cache.
    monkeypatch.setattr('agr.installed_map.dump_map_data',
                        lambda decoder, resource, provider: decoder.dump(resource, provider))


def world_fixture(directory):
    """Shared vertex data has distant unused vertices; bounds must use indices."""
    directory.mkdir(parents=True, exist_ok=True)
    points = np.array([[1000, 1000, 1000], [1001, 1000, 1000], [1000, 1001, 1000],
                       [0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype='<f4')
    # Interleave normals and positions to test stride and offset preservation.
    normal = np.tile(np.array([0, 0, 1], dtype='<f4'), (len(points), 1))
    interleaved = np.column_stack((normal, points)).astype('<f4').tobytes()
    blob = interleaved + np.array([3, 4, 5, 0, 1, 2], dtype='<u2').tobytes()
    (directory / 'world.bin').write_bytes(blob)
    (directory / 'near.png').write_bytes(b'unchanged texture fixture')
    document = {
        'asset': {'version': '2.0', 'generator': 'Source 2 Viewer 20.0'},
        'scene': 0, 'scenes': [{'nodes': [0]}],
        'nodes': [{'name': 'placement', 'translation': [10, 0, 0], 'children': [1, 2]},
                  {'name': 'retained', 'translation': [1, 0, 0], 'mesh': 0},
                  {'name': 'distant', 'mesh': 1}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 1}, 'indices': 2, 'material': 0}]},
                   {'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 1}, 'indices': 3, 'material': 1}]}],
        'accessors': [
            {'bufferView': 0, 'byteOffset': 12, 'componentType': 5126, 'count': 6, 'type': 'VEC3'},
            {'bufferView': 0, 'componentType': 5126, 'count': 6, 'type': 'VEC3'},
            {'bufferView': 1, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'},
            {'bufferView': 1, 'byteOffset': 6, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'}],
        'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(interleaved), 'byteStride': 24},
                        {'buffer': 0, 'byteOffset': len(interleaved), 'byteLength': 12}],
        'buffers': [{'uri': 'world.bin', 'byteLength': len(blob)}],
        'materials': [{'name': 'kept', 'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}},
                       'extras': {'arbitrary_original_index': 123}},
                      {'name': 'distant', 'pbrMetallicRoughness': {'baseColorTexture': {'index': 1}}}],
        'textures': [{'source': 0}, {'source': 1}],
        'images': [{'uri': 'near.png'}, {'uri': 'absent_distant.png'}],
    }
    path = directory / 'world.gltf'
    path.write_text(json.dumps(document))
    return path


def sample_scene():
    # Source (0, 450, 0) -> glTF (11.43, 0, 0), inside the near fixture.
    return {'entities': [{'samples': [{'position': [0, 450, 0]}, {'position': [1, 451, 1]}]}],
            'metadata': {'coordinate_system': {'origin': [0, 400, 0], 'unit_scale': .0254, 'world_yaw_degrees': 0}}}


def test_region_uses_indexed_placed_bounds_and_preserves_attributes(tmp_path):
    source = world_fixture(tmp_path / 'input')
    result = tmp_path / 'output/region.gltf'
    report = crop(source, result, replay_bounds(sample_scene(), 1.0))
    document = json.loads(result.read_text())
    assert report['selected_mesh_nodes'] == 1
    assert [node['name'] for node in document['nodes']] == ['placement', 'retained']
    assert document['nodes'][0]['translation'] == [10, 0, 0]
    assert document['nodes'][1]['translation'] == [1, 0, 0]
    assert len(document['materials']) == len(document['textures']) == len(document['images']) == 1
    assert document['materials'][0]['extras'] == {'arbitrary_original_index': 123}
    assert (result.parent / 'near.png').read_bytes() == (source.parent / 'near.png').read_bytes()
    assert not (result.parent / 'absent_distant.png').exists()
    assert report['geometry_compaction']['vertices_after'] == 3
    primitive = document['meshes'][0]['primitives'][0]
    reader = AccessorReader(document, result.parent)
    points = reader.read(primitive['attributes']['POSITION'])
    sequence = points[reader.read(primitive['indices']).ravel()]
    np.testing.assert_array_equal(sequence, [[0, 0, 0], [1, 0, 0], [0, 1, 0]])
    assert report['verified_buffer_view_hashes'] > 0


def test_region_rejects_missing_selected_texture(tmp_path):
    source = world_fixture(tmp_path / 'input')
    (source.parent / 'near.png').unlink()
    with pytest.raises(FileNotFoundError, match='referenced source image'):
        crop(source, tmp_path / 'output/region.gltf', replay_bounds(sample_scene(), 1.0))


@pytest.mark.parametrize('margin', [0, -1, float('nan'), float('inf'), True])
def test_invalid_margin_rejected(margin):
    with pytest.raises(ConversionError, match='margin'):
        replay_bounds(sample_scene(), margin)


def test_map_alignment_handles_origin_yaw_scale_independently():
    coordinates = {'origin': [100, -200, 512], 'unit_scale': .01, 'world_yaw_degrees': 37}
    source = np.array([1024.5, 3456.7, -1728.2])
    blender_after_gltf = np.array([source[1], -source[0], source[2]]) * .0254
    actual = (np.asarray(blender_map_matrix(coordinates)) @ np.r_[blender_after_gltf, 1])[:3]
    angle = np.deg2rad(37)
    yaw = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
    expected = yaw @ ((source - coordinates['origin']) * .01)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)


def test_map_selection_never_guesses_start_or_another_mount(tmp_path):
    root = installation(tmp_path, {'maps/menu/world.vwrld_c': compiled()})
    provider = GameAssetProvider(root, tmp_path / 'cache')
    with pytest.raises(ConversionError, match='Select an installed map'):
        selected_world(provider)
    write_vpk(root / 'game/citadel/maps/dl_midtown.vpk', {'maps/dl_midtown/world.vwrld_c': compiled()})
    selected = GameAssetProvider(root, tmp_path / 'cache', map_name='dl_midtown')
    assert selected_world(selected) == 'maps/dl_midtown/world.vwrld_c'
    with pytest.raises(MissingResourceError, match='not installed'):
        GameAssetProvider(root, tmp_path / 'cache', map_name='start')


def test_ambiguous_compiled_worlds_rejected():
    provider = SimpleNamespace(map_name='dl_midtown', archives=[SimpleNamespace(entries={
        'maps/dl_midtown/world.vwrld_c': None, 'maps/other/world.vwrld_c': None})])
    with pytest.raises(ConversionError, match='2 compiled worlds'):
        selected_world(provider)


def test_installed_map_cache_reuse_and_corrupt_region_rebuild(tmp_path, monkeypatch):
    world = 'maps/dl_midtown/world.vwrld_c'
    node = 'maps/dl_midtown/node0.vwnod_c'
    root = installation(tmp_path, {'models/map.vmdl_c': compiled()})
    # world->node is intentionally absent from RERL: DATA's prefix is authoritative.
    write_vpk(root / 'game/citadel/maps/dl_midtown.vpk', {world: compiled(), node: compiled('models/map.vmdl')})
    provider = GameAssetProvider(root, tmp_path / 'cache', map_name='dl_midtown')
    decoder = AssetConverter.__new__(AssetConverter)
    decoder.executable = tmp_path / 'Source2Viewer-CLI.exe'
    decoder.root = tmp_path / 'derived'; decoder.root.mkdir()
    decoder.identity = {'unchanged_existing_hero_converter_identity': True}
    decoder.stats = {'cache_hits': 0, 'conversions': 0}
    decoder.dump = lambda path, provider: {'m_worldNodes': [{'m_worldNodePrefix': node[:-8]}], 'm_entityLumps': []}
    calls = []

    def export(command, logfile, log):
        calls.append(command)
        target = Path(command[command.index('-o') + 1])
        tree = Path(command[command.index('-i') + 1]).parents[2]
        assert (tree / node).is_file()
        assert (tree / 'models/map.vmdl_c').is_file()
        assert '--gltf_export_animations' not in command
        world_fixture(target.parent)
        logfile.write_text('Map conversion fixture completed\n')

    monkeypatch.setattr('agr.installed_map._run_map_export', export)
    first = prepare_installed_map(sample_scene(), provider, decoder, margin_metres=1, log=lambda _: None)
    assert first['status'] == 'ready' and first['selected_mesh_nodes'] == 1
    assert not first['collision'] and not first['physics_imported']
    second = prepare_installed_map(sample_scene(), provider, decoder, margin_metres=1, log=lambda _: None)
    assert first == second
    assert len(calls) == 1 and decoder.stats == {'cache_hits': 2, 'conversions': 2}
    Path(first['model_file']).with_suffix('.bin').write_bytes(b'corrupted region')
    repaired = prepare_installed_map(sample_scene(), provider, decoder, margin_metres=1, log=lambda _: None)
    assert repaired == first and len(calls) == 1
    assert decoder.stats == {'cache_hits': 3, 'conversions': 3}
    np.testing.assert_allclose(repaired['blender_matrix'], blender_map_matrix(sample_scene()['metadata']['coordinate_system']))
    # An unsuccessful rebuild must keep its diagnostic after temporary cache
    # cleanup and must not poison the previously completed regional asset.
    exported = next(p.parent for p in decoder.root.glob('*/conversion.json')
                    if json.loads(p.read_text())['descriptor']['operation'] == 'installed_world_gltf')
    (exported / 'world.gltf').write_bytes(b'corrupted full export')
    region_bytes = Path(repaired['model_file']).read_bytes()
    def fail(command, logfile, log):
        logfile.write_text('Fixture converter reported a fatal model error\n')
        raise ConversionError('Fixture map conversion failed')
    monkeypatch.setattr('agr.installed_map._run_map_export', fail)
    with pytest.raises(ConversionError, match='Map diagnostics retained at'):
        prepare_installed_map(sample_scene(), provider, decoder, margin_metres=1, log=lambda _: None)
    diagnostic = list((decoder.root.parent / 'map-diagnostics').glob('*/converter.log'))
    assert len(diagnostic) == 1 and 'fatal model error' in diagnostic[0].read_text()
    assert Path(repaired['model_file']).read_bytes() == region_bytes
    monkeypatch.setattr('agr.installed_map._run_map_export', export)
    retried = prepare_installed_map(sample_scene(), provider, decoder, margin_metres=1, log=lambda _: None)
    assert retried['status'] == 'ready'


def test_crop_rejects_unsafe_external_buffer(tmp_path):
    source = world_fixture(tmp_path / 'input')
    document = json.loads(source.read_text())
    document['buffers'][0]['uri'] = '../escape.bin'
    source.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='escapes'):
        crop(source, tmp_path / 'output/region.gltf', replay_bounds(sample_scene(), 1.0))


def test_crop_rejects_unverified_exporter_basis(tmp_path):
    source = world_fixture(tmp_path / 'input')
    document = json.loads(source.read_text())
    document['asset']['generator'] = 'Unverified exporter'
    source.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='basis'):
        crop(source, tmp_path / 'output/region.gltf', replay_bounds(sample_scene(), 1.0))


def binary_entity(model, classname='prop_dynamic'):
    """Independent v1 entity writer; hashes are known VRF StringToken values."""
    # Neither string is a RERL entry. A floating-point value between the fields
    # proves the reader walks actual typed values instead of scanning the blob.
    return {'m_keyValuesData': list(struct.pack('<III', 1, 3, 0)
            + struct.pack('<II', 0xc61b1c62, 30) + classname.encode() + b'\0'
            + struct.pack('<IIf', 0x12345678, 1, .75)
            + struct.pack('<II', 0xc8bfbc06, 30) + model.encode() + b'\0')}


def test_binary_entity_model_and_kv3_attributes_read_as_typed_properties():
    props = _entity_properties(binary_entity('maps/dl_midtown/entities/unnamed_123.vmdl'))
    assert props['classname'] == 'prop_dynamic'
    assert props['model'] == 'maps/dl_midtown/entities/unnamed_123.vmdl'
    assert props[0x12345678] == .75
    kv3 = {'keyValues3Data': {'version': 1, 'values': {'CLASSNAME': 'prop_dynamic'},
                            'attributes': {'Model': 'models/architecture/arch_church/door.vmdl'}}}
    assert list(_data_dependencies('entities.vents_c', {'m_entityKeyValues': [kv3]})) == [
        'models/architecture/arch_church/door.vmdl_c']


@pytest.mark.parametrize('damage', ['truncated', 'count', 'type', 'version', 'trailing'])
def test_binary_entity_rejects_malformed_payload_without_guessing(damage):
    raw = bytearray(binary_entity('models/door.vmdl')['m_keyValuesData'])
    if damage == 'truncated':
        raw = raw[:-1]
    elif damage == 'count':
        struct.pack_into('<I', raw, 4, 0xffffffff)
    elif damage == 'type':
        struct.pack_into('<I', raw, 16, 0xdead)
    elif damage == 'version':
        struct.pack_into('<I', raw, 0, 99)
    elif damage == 'trailing':
        raw += b'not another resource'
    with pytest.raises(ConversionError):
        _entity_properties({'m_keyValuesData': list(raw)})


def test_named_binary_property_hash_is_verified():
    raw = struct.pack('<III', 1, 0, 1) + struct.pack('<I', 0xc8bfbc06)
    raw += b'Model\0' + struct.pack('<I', 30) + b'models/door.vmdl\0'
    assert _entity_properties({'m_keyValuesData': list(raw)})['model'] == 'models/door.vmdl'
    corrupt = bytearray(raw)
    corrupt[12] ^= 1
    with pytest.raises(ConversionError, match='hash mismatch'):
        _entity_properties({'m_keyValuesData': list(corrupt)})


def dependency_fixture(tmp_path):
    world, node = 'maps/dl_midtown/world.vwrld_c', 'maps/dl_midtown/node0.vwnod_c'
    root_lump = 'maps/dl_midtown/entities/default.vents_c'
    child_lump = 'maps/dl_midtown/entities/template.vents_c'
    # Representative names from the reported failure, all intentionally absent
    # from RERL. Their entity classes decide what the exporter loads.
    trigger = 'maps/dl_midtown/entities/blocker_brush_73.vmdl_c'
    unknown_name = 'maps/dl_midtown/entities/unnamed_1307.vmdl_c'
    door = 'models/architecture/arch_church/arch_church_door_large.vmdl_c'
    aggregate = 'models/architecture/window.vmdl_c'
    material, texture = 'materials/door.vmat_c', 'materials/door_color.vtex_c'
    game = installation(tmp_path, {door: compiled(material[:-2]), aggregate: compiled(),
                                   material: compiled(texture[:-2]), texture: compiled()})
    write_vpk(game / 'game/citadel/maps/dl_midtown.vpk', {world: compiled(), node: compiled(),
              root_lump: compiled(), child_lump: compiled(), trigger: compiled(), unknown_name: compiled()})
    provider = GameAssetProvider(game, tmp_path / 'cache', map_name='dl_midtown')
    data = {
        world: {'m_worldNodes': [{'m_worldNodePrefix': node[:-8]}], 'm_entityLumps': [root_lump[:-2]]},
        node: {'m_sceneObjects': [], 'm_aggregateSceneObjects': [{'m_renderableModel': aggregate[:-2]}]},
        root_lump: {'m_childLumps': [child_lump[:-2]], 'm_entityKeyValues': [binary_entity(trigger[:-2], 'trigger_multiple'),
            {'keyValues3Data': {'version': 1, 'values': {'classname': 'point_template', 'entitylumpname': 'template'}}}]},
        child_lump: {'m_childLumps': [root_lump[:-2]], 'm_entityKeyValues': [binary_entity(unknown_name[:-2]),
            {'keyValues3Data': {'version': 1, 'values': {'classname': 'prop_dynamic'}, 'attributes': {'model': door[:-2]}}}]},
    }
    decoder = AssetConverter.__new__(AssetConverter)
    decoder.root = tmp_path / 'derived'; decoder.root.mkdir()
    decoder.executable = tmp_path / 'Source2Viewer-CLI.exe'
    decoder.identity = {'unchanged_existing_hero_converter_identity': True}
    decoder.stats = {'cache_hits': 0, 'conversions': 0}
    decoder.dump = lambda resource, _: data[resource]
    return world, provider, decoder, data, {world, node, root_lump, child_lump, trigger, unknown_name,
                                          door, aggregate, material, texture}


def test_data_entity_models_nested_lumps_and_material_closure_are_staged(tmp_path, monkeypatch):
    world, provider, decoder, data, expected = dependency_fixture(tmp_path)
    records, skipped, roots, missing = _world_dependencies(world, provider, decoder, lambda _: None)
    assert missing==[]
    assert set(records) == expected and not skipped
    calls = []

    def export(command, logfile, log):
        calls.append(command)
        target = Path(command[command.index('-o') + 1])
        tree = target.parent / 'input'
        for resource in expected:
            assert (tree / resource).read_bytes() == provider.resolve(resource).local_path.read_bytes()
        world_fixture(target.parent)
        logfile.write_text('All entity and world-node fixture resources resolved\n')

    monkeypatch.setattr('agr.installed_map._run_map_export', export)
    first = prepare_installed_map(sample_scene(), provider, decoder, margin_metres=1, log=lambda _: None)
    assert first['status'] == 'ready'
    second = prepare_installed_map(sample_scene(), provider, decoder, margin_metres=1, log=lambda _: None)
    assert second == first and len(calls) == 1
    assert decoder.identity == {'unchanged_existing_hero_converter_identity': True}


def test_missing_real_entity_model_still_fails_before_export(tmp_path):
    world, provider, decoder, data, expected = dependency_fixture(tmp_path)
    root_lump = 'maps/dl_midtown/entities/default.vents_c'
    data[root_lump]['m_entityKeyValues'].append(binary_entity('models/architecture/missing_door.vmdl'))
    with pytest.raises(MissingResourceError, match='missing_door'):
        _world_dependencies(world, provider, decoder, lambda _: None)


def test_cli_missing_geometry_still_fails_even_after_successful_export(tmp_path, monkeypatch):
    world, provider, decoder, data, expected = dependency_fixture(tmp_path)

    def export(command, logfile, log):
        target = Path(command[command.index('-o') + 1])
        world_fixture(target.parent)
        # A brush-ish filename is still an unresolved model: no broad filtering.
        logfile.write_text('Failed to load "maps/dl_midtown/entities/blocker_brush_73.vmdl_c"\n')

    monkeypatch.setattr('agr.installed_map._run_map_export', export)
    with pytest.raises(ConversionError, match='blocker_brush_73'):
        prepare_installed_map(sample_scene(), provider, decoder, margin_metres=1, log=lambda _: None)
    assert not list(decoder.root.glob('*/conversion.json'))


def test_missing_texture_is_deferred_but_preserved_in_dependency_report(tmp_path,monkeypatch):
    world,provider,decoder,data,expected=dependency_fixture(tmp_path)
    original=provider.resolve
    missing='materials/door_color.vtex_c'
    def resolve(resource):
        if resource==missing:raise MissingResourceError('fixture missing texture')
        return original(resource)
    monkeypatch.setattr(provider,'resolve',resolve)
    records,skipped,roots,absent=_world_dependencies(world,provider,decoder,lambda _:None)
    assert absent==[missing] and missing not in records
    assert set(records)==expected-{missing}


def test_selected_authored_texture_reference_cannot_be_hidden_by_exporter():
    missing=['materials/missing.vtex_c']
    unrelated={'materials':[{'name':'road','extras':{'vmat':{'TextureParams':{'g_tColor':'materials/road.vtex'}}}}]}
    _check_missing_texture_usage(unrelated,missing)
    selected={'materials':[{'name':'hair','extras':{'vmat':{'TextureParams':{'g_tMetal':'materials/missing.vtex'}}}}]}
    with pytest.raises(ConversionError,match='hair references missing textures'):
        _check_missing_texture_usage(selected,missing)
    with pytest.raises(ConversionError,match='lacks source VMAT metadata'):
        _check_missing_texture_usage({'materials':[{'name':'unknown'}]},missing)


def test_compiled_shader_inventory_is_copied_and_fingerprinted(tmp_path):
    world,provider,decoder,data,expected=dependency_fixture(tmp_path)
    name='shaders/vfx/environment_blend_pc_50_features.vcs'
    shader=SimpleNamespace(resource_path=name,sha256='shader-version',dependencies=[])
    original=provider.resolve
    provider.resources[name]=object()
    provider.resolve=lambda resource:shader if resource==name else original(resource)
    records,_,_,_=_world_dependencies(world,provider,decoder,lambda _:None)
    assert records[name] is shader


def test_shader_fallback_is_not_a_successful_map_export(tmp_path,monkeypatch):
    world,provider,decoder,data,expected=dependency_fixture(tmp_path)
    def export(command,logfile,log):
        target=Path(command[command.index('-o')+1])
        world_fixture(target.parent)
        logfile.write_text('Failed to find shader "environment_blend.vfx"\n')
    monkeypatch.setattr('agr.installed_map._run_map_export',export)
    with pytest.raises(ConversionError,match='material shaders: environment_blend.vfx'):
        prepare_installed_map(sample_scene(),provider,decoder,margin_metres=1,log=lambda _:None)
