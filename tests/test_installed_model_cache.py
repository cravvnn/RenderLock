"""Exercise the real installed-asset/cache bridge with a counted fake exporter.

These are offline fixture checks, not game-frame or Blender import parity tests.
"""
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from agr.asset_converter import AssetConverter, ConversionError, sha
from agr.cli import publish_package
from agr.installed_assets import prepare_installed_assets
from agr.scene_package import read_scene
from agr.vendor.resource_hash import resource_id
from test_game_assets import compiled, installation, write_vpk


@pytest.fixture
def installed(tmp_path, monkeypatch):
    model = 'models/hero.vmdl_c'
    resources = {
        model: compiled('models/hero.vmesh', 'models/hero.vnmskel'),
        'models/hero.vmesh_c': compiled('materials/hero.vmat'),
        'models/hero.vnmskel_c': compiled(),
        'materials/hero.vmat_c': compiled('textures/hero.vtex'),
        'textures/hero.vtex_c': compiled(),
        'shaders/vulkan/pbr_features.vcs': b'shader fixture',
    }
    root = installation(tmp_path, resources)
    converter = tmp_path / 'Source2Viewer-CLI.exe'
    converter.write_bytes(b'counted converter fixture')
    companion = tmp_path / 'libSkiaSharp.dll'
    companion.write_bytes(b'companion fixture')
    cache = tmp_path / 'shared-cache'
    calls, repairs, messages = [], [], []

    def export(command, **kwargs):
        if command[1:] == ['--help']:
            return SimpleNamespace(returncode=0, stdout=' '.join((
                '--gltf_export_format', '--gltf_export_animations', '--gltf_animation_list',
                '--gltf_export_materials', '--block')), stderr='')
        target = Path(command[command.index('-o') + 1])
        assert '--gltf_animation_list' in command
        assert command[command.index('--gltf_animation_list') + 1] == '__agr_no_source_actions__'
        tree = target.parent / 'input'
        assert all((tree / name).read_bytes() == content for name, content in resources.items())
        document = {'meshes': [{}], 'skins': [{'joints': [1]}],
                    'nodes': [{'mesh': 0, 'skin': 0}, {'name': 'root'}],
                    'images': [{'uri': 'textures/hero.png'}],
                    'materials': [{'name': 'Hero', 'extras': {'vmat': {
                        'ShaderName': 'pbr.vfx', 'IntParams': {'F_GLASS': 1}}}}]}
        encoded = json.dumps(document).encode()
        encoded += b' ' * (-len(encoded) % 4)
        target.write_bytes(struct.pack('<4sIII4s', b'glTF', 2, 20 + len(encoded),
                                       len(encoded), b'JSON') + encoded)
        image = target.parent / 'textures/hero.png'
        image.parent.mkdir()
        image.write_bytes(b'unchanged image fixture')
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='fixture exported', stderr='')

    monkeypatch.setattr('agr.asset_converter.subprocess.run', export)
    from agr import gltf_materials
    original_repair = gltf_materials.repair_glb

    def repair(*args, **kwargs):
        repairs.append(args)
        return original_repair(*args, **kwargs)

    monkeypatch.setattr(gltf_materials, 'repair_glb', repair)
    jobs = []

    def prepare(start=10, material_repairs=False, need_animation=False):
        work = tmp_path / f'job-{len(jobs)}'
        work.mkdir()
        jobs.append(work)
        segment = work / 'segment.json'
        segment.write_text(json.dumps({
            'schema_version': 1, 'tick_rate': 64, 'start_tick': start, 'end_tick': start + 1,
            'metadata': {'demo_sha256': f'replay-{start}'}, 'entities': [{
                'id': '91:755', 'hero_id': 2, 'hero_name': 'Hero 2',
                'model_handle': str(resource_id(model[:-2])),
                'samples': [{'tick': tick, 'position': [0, 0, 0], 'body_angles': [0, 0, 0]}
                            for tick in (start, start + 1)]}]}))
        pack = prepare_installed_assets(segment, work, root, cache, converter,
                                        need_animation=need_animation,
                                        material_repairs=material_repairs, log=messages.append)
        data = read_scene(segment)
        report = json.loads((work / 'installed_asset_report.json').read_text())
        return SimpleNamespace(work=work, pack=pack, segment=segment, data=data, report=report,
                               model=Path(data['entities'][0]['model_file']))

    return SimpleNamespace(prepare=prepare, resources=resources, install=root, cache=cache,
                           converter=converter, companion=companion, calls=calls,
                           repairs=repairs, messages=messages, jobs=jobs)


@pytest.mark.parametrize('material_repairs', [False, True])
def test_repeat_and_new_replay_ranges_share_static_files_without_export_copies(installed, material_repairs):
    first = installed.prepare(material_repairs=material_repairs)
    before = {p: sha(p) for p in first.model.parent.rglob('*') if p.is_file()}
    second = installed.prepare(material_repairs=material_repairs)
    later = installed.prepare(start=300, material_repairs=material_repairs)
    assert len(installed.calls) == 1  # Actual model-export subprocess calls.
    assert len(installed.repairs) == int(material_repairs)
    assert first.model == second.model == later.model
    assert first.model.is_relative_to(installed.cache)
    assert all(not (job / 'installed-assets/models').exists() for job in installed.jobs)
    assert before == {p: sha(p) for p in first.model.parent.rglob('*') if p.is_file()}
    assert later.report['status'] == 'prepared'
    assert later.report['model_cache']['reused'] == 1
    assert later.report['model_cache']['converted'] == 0
    assert later.report['models'][0]['cache']['status'] == 'reused'
    assert any('Reusing cached installed model and textures' in message for message in installed.messages)
    assert (later.model.parent / 'textures/hero.png').read_bytes() == b'unchanged image fixture'
    if material_repairs:
        assert later.report['model_cache']['material_repairs_reused'] == 1
        assert later.report['model_cache']['material_repairs_built'] == 0
        assert not (first.model.parent / 'model.glb').exists()  # Base is retained in its own entry.


@pytest.mark.parametrize('changed', [
    'models/hero.vmdl_c', 'models/hero.vmesh_c', 'models/hero.vnmskel_c',
    'materials/hero.vmat_c', 'textures/hero.vtex_c',
    'shaders/vulkan/pbr_features.vcs', 'converter', 'companion'])
def test_changed_model_dependency_or_converter_rebuilds_static_cache(installed, changed):
    first = installed.prepare(material_repairs=True)
    original = first.model.read_bytes()
    if changed in ('converter', 'companion'):
        path = getattr(installed, changed)
        path.write_bytes(path.read_bytes() + b' changed')
    else:
        payload = installed.resources[changed]
        if changed.endswith('_c'):
            # Independent compiled-resource version, retaining valid v12/RERL.
            payload = payload[:6] + struct.pack('<H', 2) + payload[8:]
        else:
            payload += b' changed'
        installed.resources[changed] = payload
        write_vpk(installed.install / 'game/citadel/pak01_dir.vpk', installed.resources)
    second = installed.prepare(start=300, material_repairs=True)
    assert len(installed.calls) == len(installed.repairs) == 2
    assert second.model != first.model
    assert first.model.read_bytes() == original  # Prior verified inputs are retained.
    assert second.report['model_cache']['converted'] == 1


@pytest.mark.parametrize('corrupt', ['base-model', 'base-texture', 'repaired-model', 'repaired-texture'])
def test_corrupt_output_is_verified_and_rebuilt_before_reuse(installed, corrupt):
    first = installed.prepare(material_repairs=True)
    original = first.model.read_bytes()
    base = next(p.parent for p in (installed.cache / 'derived-v1').glob('*/conversion.json')
                if json.loads(p.read_text())['descriptor']['operation'] == 'hero_glb')
    if corrupt.startswith('base'):
        target = base / ('model.glb' if corrupt.endswith('model') else 'textures/hero.png')
    else:
        target = first.model if corrupt.endswith('model') else first.model.parent / 'textures/hero.png'
    target.write_bytes(b'corrupt fixture')
    second = installed.prepare(start=300, material_repairs=True)
    assert second.model == first.model and second.model.read_bytes() == original
    assert len(installed.calls) == (2 if corrupt.startswith('base') else 1)
    assert (second.model.parent / 'textures/hero.png').read_bytes() == b'unchanged image fixture'


def test_failed_animation_keeps_successful_static_cache_for_retry(installed, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('unsupported replay rig fixture')
    monkeypatch.setattr('agr.installed_assets.prepare_animation_assets', fail)
    with pytest.raises(ValueError, match='unsupported replay rig fixture'):
        installed.prepare(need_animation=True)
    failed = installed.jobs[0]
    assert json.loads((failed / 'installed_asset_report.json').read_text())['status'] == 'failed'
    retry = installed.prepare()
    assert len(installed.calls) == 1
    assert retry.report['model_cache']['reused'] == 1


def test_material_variant_rejects_bytes_changed_during_copy(installed, monkeypatch):
    first = installed.prepare()
    texture = first.model.parent / 'textures/hero.png'
    from agr import installed_assets
    copyfile = installed_assets.shutil.copyfile

    def changed(source, target, **kwargs):
        result = copyfile(source, target, **kwargs)
        if Path(source) == texture:
            Path(target).write_bytes(b'changed during copy fixture')
        return result

    monkeypatch.setattr(installed_assets.shutil, 'copyfile', changed)
    with pytest.raises(ConversionError, match='changed during material preparation'):
        installed.prepare(material_repairs=True)
    assert texture.read_bytes() == b'unchanged image fixture'
    manifests = list((installed.cache / 'derived-v1').glob('*/conversion.json'))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())['descriptor']['operation'] == 'hero_glb'


@pytest.mark.parametrize('injected_path', [
    '../outside.bin', r'..\outside.bin', r'nested\..\..\outside.bin',
    '/outside.bin', r'\outside.bin', r'C:\outside.bin', r'C:outside.bin',
    r'\\server\share\outside.bin', 'absolute-fixture-path'])
def test_material_variant_rejects_manifest_paths_tampered_after_model_validation(
        installed, monkeypatch, injected_path):
    outside = installed.cache.parent / 'outside-sentinel.bin'
    outside.write_bytes(b'outside cache; must remain unchanged')
    copy_calls = []
    from agr import installed_assets
    original_model = AssetConverter.model
    original_copy = installed_assets.shutil.copyfile

    def copy(source, destination, **kwargs):
        copy_calls.append((Path(source), Path(destination)))
        return original_copy(source, destination, **kwargs)

    def tamper_after_validation(decoder, *args, **kwargs):
        model, details = original_model(decoder, *args, **kwargs)
        copy_calls.clear()  # Exclude legitimate initial converter input copies.
        manifest = model.parent / 'conversion.json'
        record = json.loads(manifest.read_text())
        relative = str(outside) if injected_path == 'absolute-fixture-path' else injected_path
        record['files'][relative] = sha(outside)
        manifest.write_text(json.dumps(record))
        return model, details

    monkeypatch.setattr(installed_assets.shutil, 'copyfile', copy)
    monkeypatch.setattr(AssetConverter, 'model', tamper_after_validation)
    with pytest.raises(ConversionError, match='Unsafe cached model dependency path'):
        installed.prepare(material_repairs=True)
    assert not copy_calls  # Reject the whole inventory before copying any file.
    assert not installed.repairs
    assert outside.read_bytes() == b'outside cache; must remain unchanged'
    manifests = list((installed.cache / 'derived-v1').glob('*/conversion.json'))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())['descriptor']['operation'] == 'hero_glb'


def test_material_variant_rejects_source_symlink_injected_after_model_validation(installed, monkeypatch):
    outside = installed.cache.parent / 'outside-sentinel.bin'
    outside.write_bytes(b'outside cache; must remain unchanged')
    from agr import installed_assets
    original_model = AssetConverter.model
    copy_calls = []
    original_copy = installed_assets.shutil.copyfile

    def copy(source, destination, **kwargs):
        copy_calls.append((Path(source), Path(destination)))
        return original_copy(source, destination, **kwargs)

    def tamper_after_validation(decoder, *args, **kwargs):
        model, details = original_model(decoder, *args, **kwargs)
        copy_calls.clear()
        linked = model.parent / 'textures/linked.bin'
        try:
            linked.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f'Test account cannot create symlinks: {exc}')
        manifest = model.parent / 'conversion.json'
        record = json.loads(manifest.read_text())
        record['files']['textures/linked.bin'] = sha(outside)
        manifest.write_text(json.dumps(record))
        return model, details

    monkeypatch.setattr(installed_assets.shutil, 'copyfile', copy)
    monkeypatch.setattr(AssetConverter, 'model', tamper_after_validation)
    with pytest.raises(ConversionError, match='escapes its cache directory'):
        installed.prepare(material_repairs=True)
    assert not copy_calls and not installed.repairs
    assert outside.read_bytes() == b'outside cache; must remain unchanged'


def test_published_scene_rebinds_shared_model_without_saving_cache_dependency(installed):
    first = installed.prepare(material_repairs=True)
    destination = first.work.parent / 'scene.agrscene'
    publish_package(first.segment, destination)
    published = read_scene(destination / 'manifest.json')
    assert 'model_file' not in published['entities'][0]
    assert published['entities'][0]['model_handle'] == first.data['entities'][0]['model_handle']
    assert not list(destination.rglob('*.glb'))
    assert not list(destination.rglob('*.png'))
    # The next installed import resolves the same recorded resource ID to the
    # persistent cache, instead of relying on a deleted per-export model copy.
    retry = installed.prepare(start=300, material_repairs=True)
    assert retry.model == first.model and len(installed.calls) == 1
