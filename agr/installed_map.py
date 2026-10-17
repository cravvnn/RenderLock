"""Installed Source 2 world export and replay-region selection.

Source 2 Viewer 20.0 supplies the assembled world export. AGR preserves its
placed geometry and material references, then selects complete intersecting
meshes. This is a visual reference map, not reconstructed game state or physics.
See VRF 20.0 GltfModelExporter.cs, GltfModelExporter.Conversion.cs and World.cs.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time

import numpy as np

from .asset_converter import ConversionError, compiled, sha
from .game_assets import safe_resource_path, MissingResourceError
from .gltf_region import crop, local_resource
from .map_data import dump_map_data, map_data_identity


SOURCE_METRES = .0254
VISUAL_TYPES = {'.vwrld_c', '.vwnod_c', '.vents_c', '.vmdl_c', '.vmesh_c',
                '.vmat_c', '.vtex_c', '.vnmskel_c', '.vphys_c', '.vmap_c'}


def _entity_key_hash(name):
    """VRF StringToken: case-insensitive MurmurHash2, seed 0x31415926."""
    data = name.lower().encode('utf-8')
    value, offset, multiplier = 0x31415926 ^ len(data), 0, 0x5bd1e995
    while offset + 4 <= len(data):
        word = int.from_bytes(data[offset:offset + 4], 'little')
        word = word * multiplier & 0xffffffff
        word ^= word >> 24
        word = word * multiplier & 0xffffffff
        value = (value * multiplier & 0xffffffff) ^ word
        offset += 4
    if offset < len(data):
        value ^= int.from_bytes(data[offset:], 'little')
        value = value * multiplier & 0xffffffff
    value ^= value >> 13
    value = value * multiplier & 0xffffffff
    return value ^ (value >> 15)


def _entity_properties(entity):
    """Read actual typed entity properties, including pre-KV3 byte blobs.

    Mirrors VRF 20 EntityLump.ParseEntityProperties. Scanning raw bytes or
    guessing from model filenames would miss references or misclassify brushes.
    Only `model` and `classname` names are needed for the exporter traversal;
    other hashed keys remain numeric, while their values are still read by type.
    """
    if 'keyValues3Data' in entity:
        data = entity['keyValues3Data']
        if not isinstance(data, dict) or data.get('version') != 1:
            raise ConversionError('Unsupported KV3 entity data version')
        properties = {}
        for group in ('values', 'attributes'):
            fields = data.get(group) or {}
            if not isinstance(fields, dict):
                raise ConversionError('Entity values and attributes must be property mappings')
            for name, value in fields.items():
                key = name.lower()
                if key in properties:
                    raise ConversionError(f'Duplicate entity property: {key}')
                properties[key] = value
        return properties
    try:
        raw = bytes(entity['m_keyValuesData'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConversionError('Entity has no supported property data') from exc
    offset = 0

    def take(fmt):
        nonlocal offset
        size = struct.calcsize(fmt)
        if offset + size > len(raw):
            raise ConversionError('Truncated binary entity property')
        value = struct.unpack_from(fmt, raw, offset)
        offset += size
        return value[0] if len(value) == 1 else list(value)

    def string():
        nonlocal offset
        end = raw.find(b'\0', offset)
        if end < 0:
            raise ConversionError('Unterminated binary entity string')
        try:
            value = raw[offset:end].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ConversionError('Invalid UTF-8 in binary entity string') from exc
        offset = end + 1
        return value

    version, hashed_count, named_count = take('<III')
    if version != 1:
        raise ConversionError(f'Unsupported binary entity data version: {version}')
    if hashed_count + named_count > len(raw) // 8:
        raise ConversionError('Invalid binary entity property count')
    names = {_entity_key_hash(name): name for name in ('model', 'classname')}
    # Exactly the field types accepted by VRF20's EntityLump parser.
    formats = {1: '<f', 3: '<fff', 5: '<i', 6: '<?', 9: '<BBBB', 26: '<q',
               33: '<Q', 34: '<d', 37: '<I', 39: '<fff'}
    properties, seen = {}, set()
    for index in range(hashed_count + named_count):
        key_hash = take('<I')
        key = names.get(key_hash, key_hash)
        if index >= hashed_count:
            key = string()
            if _entity_key_hash(key) != key_hash:
                raise ConversionError(f'Binary entity property hash mismatch: {key}')
            key = key.lower()
        if key_hash in seen:
            raise ConversionError('Duplicate binary entity property')
        seen.add(key_hash)
        kind = take('<I')
        if kind == 30:
            value = string()
        elif kind in formats:
            value = take(formats[kind])
        else:
            raise ConversionError(f'Unsupported binary entity field type: {kind}')
        properties[key] = value
    if offset != len(raw):
        raise ConversionError('Unexpected trailing binary entity data')
    return properties


def _data_dependencies(resource, data):
    """References used by the world exporter which need not appear in RERL."""
    suffix = Path(resource).suffix
    if suffix == '.vwrld_c':
        for item in data.get('m_worldNodes', []):
            prefix = item.get('m_worldNodePrefix')
            if prefix:
                yield safe_resource_path(prefix + '.vwnod_c')
        for name in data.get('m_entityLumps', []):
            if name:
                yield safe_resource_path(compiled(name))
    elif suffix == '.vwnod_c':
        for group in ('m_sceneObjects', 'm_aggregateSceneObjects'):
            for item in data.get(group, []):
                name = item.get('m_renderableModel')
                if name:
                    yield safe_resource_path(compiled(name))
    elif suffix == '.vents_c':
        # EntityLumpTraversal registers child lumps before following templates.
        # Keep their original files so VRF also retains instance transforms.
        for name in data.get('m_childLumps', []):
            if name:
                yield safe_resource_path(compiled(name))
        for item in data.get('m_entityKeyValues', []):
            properties = _entity_properties(item)
            classname, model = properties.get('classname'), properties.get('model')
            if 'classname' in properties and classname != 'csgo_player_previewmodel' and model:
                yield safe_resource_path(compiled(model))


def replay_bounds(scene, margin_metres=26.0):
    """AABB in original Source XYZ, including every selected recorded sample."""
    if isinstance(margin_metres, bool) or not math.isfinite(float(margin_metres)) or float(margin_metres) <= 0:
        raise ConversionError('Map margin must be a finite positive number of metres')
    points = np.asarray([sample['position'] for entity in scene['entities']
                         for sample in entity['samples']], dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (3,) or not len(points) or not np.all(np.isfinite(points)):
        raise ConversionError('Map selection requires finite recorded positions')
    margin = float(margin_metres) / SOURCE_METRES
    return np.column_stack((points.min(axis=0) - margin, points.max(axis=0) + margin))


def blender_map_matrix(coordinates=None):
    """Post-import Blender matrix matching AGR root coordinates, row-major.

    VRF 20 exports Source XYZ as glTF YZX in metres. Blender's standard glTF
    import gives (source_y, -source_x, source_z)*.0254. Undo that rotation, then
    apply the scene's origin, optional unit scale and world yaw exactly once.
    """
    if coordinates == 'source2_z_up_hammer_units':
        coordinates = {}
    coordinates = coordinates or {}
    if not isinstance(coordinates, dict):
        raise ConversionError('metadata.coordinate_system must be a coordinate mapping or source2_z_up_hammer_units')
    unit = float(coordinates.get('unit_scale', SOURCE_METRES))
    origin = np.asarray(coordinates.get('origin', [0, 0, 0]), dtype=np.float64)
    angle = math.radians(float(coordinates.get('world_yaw_degrees', 0)))
    if unit <= 0 or origin.shape != (3,) or not np.all(np.isfinite([unit, angle, *origin])):
        raise ConversionError('Invalid map scene coordinate settings')
    c, s = math.cos(angle), math.sin(angle)
    yaw = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    undo = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    result = np.eye(4)
    result[:3, :3] = yaw @ undo * (unit / SOURCE_METRES)
    result[:3, 3] = yaw @ (-origin * unit)
    return result.tolist()


def selected_world(provider):
    """Require an explicit installed map, never infer DL_midtown from 'start'."""
    if not provider.map_name:
        raise ConversionError('Select an installed map name before requesting map export')
    # GameAssetProvider mounts the explicitly selected map first, ahead of stock
    # packages; search only that map so a menu/background world cannot win.
    archive = provider.archives[0]
    candidates = sorted(path for path in archive.entries if path.endswith('.vwrld_c'))
    if len(candidates) != 1:
        raise ConversionError(f'Selected map {provider.map_name!r} has {len(candidates)} compiled worlds; '
                              'a unique .vwrld_c is required: ' + ', '.join(candidates))
    return candidates[0]


def _world_dependencies(world, provider, decoder, log):
    # World prefixes are not necessarily compiled resource references in RERL.
    # VRF appends .vwnod_c itself (World.GetWorldNodeNames); retain that contract.
    data = dump_map_data(decoder, world, provider)
    roots = [world, *_data_dependencies(world, data)]
    if len(roots) == 1:
        raise ConversionError('Installed world has no world-node or entity-lump references')
    records, skipped, missing_textures = {}, set(), set()
    pending = list(roots)
    while pending:
        resource = safe_resource_path(pending.pop())
        if resource in records or resource in missing_textures:
            continue
        if len(records) >= 100000:
            raise ConversionError('Map visual dependency graph exceeds 100,000 resources')
        try:
            resolved = provider.resolve(resource)
        except MissingResourceError:
            if Path(resource).suffix.lower()!='.vtex_c':raise
            missing_textures.add(resource)
            log('Missing world texture; selected-material validation will decide: '+resource)
            continue
        records[resolved.resource_path] = resolved
        if len(records) % 250 == 0:
            log(f'Checking map dependencies: {len(records)} resources')
        dependencies = list(resolved.dependencies)
        if Path(resource).suffix in {'.vwnod_c', '.vents_c'}:
            try:
                dependencies.extend(_data_dependencies(resource, dump_map_data(decoder, resource, provider)))
            except ConversionError as exc:
                raise ConversionError(f'Map DATA dependencies for {resource}: {exc}') from exc
        for dep in dependencies:
            dep = compiled(dep)
            if Path(dep).suffix.lower() in VISUAL_TYPES:
                pending.append(dep)
            else:
                skipped.add(dep)
    # ShaderDataProvider discovers compiled shader families by filename rather
    # than material RERL. Keep this small inventory available in the isolated
    # tree and fingerprint it so shader updates invalidate material conversion.
    shaders=sorted(name for name in provider.resources if name.endswith('.vcs'))
    log(f'Including {len(shaders)} compiled shader resources for material conversion')
    for name in shaders:
        resolved=provider.resolve(name)
        records[resolved.resource_path]=resolved
    return records, sorted(skipped), roots, sorted(missing_textures)


def _check_missing_texture_usage(document, missing_textures):
    """Exporters may omit unresolved image slots; inspect authored references too."""
    missing=set(missing_textures)
    if not missing:return
    for material in document.get('materials',[]):
        authored=material.get('extras',{}).get('vmat')
        if not isinstance(authored,dict):
            raise ConversionError('Cannot exclude missing world textures: selected material lacks source VMAT metadata')
        used={compiled(name) for name in authored.get('TextureParams',{}).values() if isinstance(name,str) and name}
        absent=used & missing
        if absent:
            raise ConversionError('Selected map material '+material.get('name','<unnamed>')+' references missing textures: '+', '.join(sorted(absent)))


def _run_map_export(command, logfile, log, timeout=7200):
    """Capture full converter output and keep the AGR job log alive on big maps."""
    with logfile.open('w', encoding='utf-8') as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
        started = time.monotonic()
        try:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    elapsed = time.monotonic() - started
                    if elapsed >= timeout:
                        raise ConversionError(f'Map conversion exceeded {timeout // 60} minutes; see {logfile}')
                    log(f'Converting installed map, {int(elapsed)} seconds elapsed; converter log: {logfile}')
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait()
            raise
    # Read a bounded tail for errors; all detailed output remains in the cache.
    with logfile.open('rb') as stream:
        stream.seek(max(0, logfile.stat().st_size - 16000))
        tail = stream.read().decode('utf-8', errors='replace')
    if code:
        raise ConversionError(f'Map converter exited with code {code}: {tail[-4000:]}')


def _validate_export(path):
    document = json.loads(path.read_text(encoding='utf-8'))
    generator = document.get('asset', {}).get('generator', '')
    if not generator.startswith('Source 2 Viewer 20.'):
        raise ConversionError(f'Unverified map coordinate basis for exporter {generator!r}; use Source 2 Viewer CLI 20.x')
    if not document.get('meshes') or not document.get('nodes'):
        raise ConversionError('Map exporter returned no placed visual geometry')
    if document.get('animations'):
        raise ConversionError('Map exporter unexpectedly emitted animation; reference map must be static')
    for buffer in document.get('buffers', []):
        linked = local_resource(path.parent, buffer['uri'])
        if not linked.is_file() or linked.stat().st_size != buffer['byteLength']:
            raise ConversionError(f'Missing or truncated exported map buffer: {linked}')
    # Images are checked after region selection, so missing assets outside the
    # selected region do not prevent an otherwise complete regional map.
    return document


def _keep_failure_diagnostics(temp, decoder, stage, error):
    """Preserve small failure reports before _entry removes incomplete output."""
    paths = [path for path in (temp / 'converter.log', temp / 'region.crop_report.json') if path.is_file()]
    if not paths:
        return None
    destination = decoder.root.parent / 'map-diagnostics' / f'{stage}-{time.time_ns()}'
    try:
        destination.mkdir(parents=True, exist_ok=False)
        for path in paths:
            shutil.copyfile(path, destination / path.name)
        (destination / 'error.json').write_text(json.dumps({'stage': stage, 'error': str(error),
            'error_type': type(error).__name__}, indent=2) + '\n', encoding='utf-8')
    except OSError:
        # Disk exhaustion may itself be the failure. Preserve the original
        # exception rather than disguising it as a diagnostics-write problem.
        return None
    return destination


def prepare_installed_map(scene, provider, decoder, *, margin_metres=26.0, log=print):
    """Return a JSON-serializable map descriptor for scene['map'].

    provider is the existing GameAssetProvider with explicit map_name; decoder
    is the existing AssetConverter. Only separate map cache operations include
    these adapter hashes, preserving all existing hero/animation cache keys.
    model_file points to a verified cached glTF; caller may copy its whole parent
    directory to a portable job folder. Transform must parent imported map roots.
    """
    world = selected_world(provider)
    bounds = replay_bounds(scene, margin_metres)
    records, skipped, roots, missing_textures = _world_dependencies(world, provider, decoder, log)
    inputs = {path: resource.sha256 for path, resource in sorted(records.items())}
    adapter = {'installed_map_sha256': sha(Path(__file__)),
               'gltf_region_sha256': sha(Path(__file__).with_name('gltf_region.py')),
               **map_data_identity()}
    settings = {'format': 'gltf', 'materials': True, 'source_actions': False,
                'map_name': provider.map_name, 'world': world, 'adapter': adapter,
                'missing_texture_dependencies':missing_textures}

    def export(temp):
        tree = temp / 'input'
        tree.mkdir()
        for name, record in records.items():
            destination = tree / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(record.local_path, destination)
        gameinfo = tree / 'gameinfo.gi'
        gameinfo.write_text('"GameInfo" { "FileSystem" { "SearchPaths" { "Game" ' +
                            json.dumps(tree.resolve().as_posix()) + ' } } }\n', encoding='utf-8')
        target = temp / 'world.gltf'
        command = [str(decoder.executable), '-i', str(tree / world), '-o', str(target),
                   '-d', '--game', str(gameinfo), '--gltf_export_format', 'gltf',
                   '--gltf_export_materials', '--gltf_textures_adapt', '--gltf_export_extras']
        log(f'Exporting installed map {provider.map_name}; the first conversion can take several minutes')
        _run_map_export(command, temp / 'converter.log', log)
        if not target.is_file():
            raise ConversionError('Map converter did not create world.gltf; inspect converter.log')
        document = _validate_export(target)
        # Missing world nodes/models would otherwise produce a deceptively
        # successful incomplete map; material image completeness is crop-local.
        missing_geometry, missing_shaders = set(), set()
        with (temp / 'converter.log').open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                missing_shaders.update(re.findall(r'Failed to find shader [\"\']([^\"\']+)[\"\']',line))
                for resource in re.findall(r'Failed to load [\"\']([^\"\']+)[\"\']', line):
                    if Path(compiled(resource)).suffix in {'.vmdl_c', '.vmesh_c', '.vwnod_c', '.vents_c'}:
                        missing_geometry.add(resource)
        if missing_geometry:
            raise ConversionError('Map exporter could not resolve geometry: ' + ', '.join(sorted(missing_geometry)))
        if missing_shaders:
            raise ConversionError('Map exporter could not resolve material shaders: '+', '.join(sorted(missing_shaders)))
        (temp / 'map_export_report.json').write_text(json.dumps({
            'world': world, 'roots': roots, 'inputs': inputs, 'skipped_nonvisual_dependencies': skipped,
            'missing_texture_dependencies':missing_textures,
            'generator': document['asset']['generator'], 'source_actions': 0,
            'meshes': len(document['meshes']), 'nodes': len(document['nodes'])}, indent=2) + '\n', encoding='utf-8')
        shutil.rmtree(tree)
        # Source physics includes trigger/nav volumes and lacks complete streets.
        # It is never imported or advertised as solid collision in this stage.
        for item in temp.glob('world_physics.*'):
            item.unlink()

    def export_with_diagnostics(temp):
        try:
            export(temp)
        except Exception as exc:
            diagnostic = _keep_failure_diagnostics(temp, decoder, 'export', exc)
            if diagnostic:
                raise ConversionError(f'{exc}\nMap diagnostics retained at: {diagnostic}') from exc
            raise

    exported = decoder._entry('installed_world_gltf', inputs, settings, export_with_diagnostics)
    log('Selecting map geometry around the recorded paths')

    def select(temp):
        try:
            report = crop(exported / 'world.gltf', temp / 'region.gltf', bounds)
            if not report['selected_mesh_nodes']:
                raise ConversionError('No installed map geometry intersects the replay region')
            _check_missing_texture_usage(json.loads((temp/'region.gltf').read_text(encoding='utf-8')),missing_textures)
        except Exception as exc:
            diagnostic = _keep_failure_diagnostics(temp, decoder, 'crop', exc)
            if diagnostic:
                raise ConversionError(f'{exc}\nMap diagnostics retained at: {diagnostic}') from exc
            raise

    region = decoder._entry('installed_world_region', {'world_conversion_sha256': sha(exported / 'conversion.json')},
                            {'source_bounds_xyz': bounds.tolist(), 'adapter': adapter}, select)
    report = json.loads((region / 'region.crop_report.json').read_text(encoding='utf-8'))
    descriptor = {
        'schema_version': 1, 'status': 'ready', 'source': 'installed_game',
        'name': str(provider.map_name), 'world_resource': world,
        'model_file': str(region / 'region.gltf'), 'report_file': str(region / 'region.crop_report.json'),
        'source_bounds_xyz': bounds.tolist(), 'margin_metres': float(margin_metres),
        'blender_matrix': blender_map_matrix(scene.get('metadata', {}).get('coordinate_system')),
        'snapshot_id': provider.snapshot['snapshot_id'],
        'selected_mesh_nodes': report['selected_mesh_nodes'], 'source_actions': 0,
        'collision': False, 'physics_imported': False,
        'missing_textures_outside_selected_materials':missing_textures,
        'limits': ['Installed map version may differ from the replay version.',
                   'Complete intersecting meshes retain their placed transforms and exported PBR materials.',
                   'Map props are in their exported reference state; replay destruction and movement are not reconstructed.',
                   'No solid collision, ragdoll or cloth solver is added by visual map export.'],
    }
    log(f'Map region ready: {descriptor["selected_mesh_nodes"]} placed meshes')
    return descriptor
