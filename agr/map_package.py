"""Verified, portable prepared-map package shared by hosted and local builds."""
from __future__ import annotations

import hashlib
import copy
import json
from pathlib import Path, PurePosixPath
import re
import shutil

FORMAT = 'renderlock-map-v2'
BUILDER_COMMIT = '0d1958ab553630df997a93fda06d3ab9ed6994c3'
MANIFEST = 'map-package.json'
LIBRARY = 'RenderLockMap.blend'
COLLECTION = 'RenderLockMap'
PROXY_COLLECTION = 'RenderLockProxy'
NAMESPACE = 'RenderLock map | '
CONVERTER_SHA256 = frozenset({
    'd440ca0c62a885665702b35bf005dc19f3ad4736e45afab4d000f74448c87de7',
    '9142f59e64dc97b7afab4cca3c5cbb4197d38beccb1f7d7f4ffbd691a053e707',
})
_SHA = re.compile(r'^[0-9a-f]{64}$')
HOSTED_MANIFESTS = frozenset({'92c52ac4f56f132e752a609b4163b7b1384aebca456676484d5ca28e85c0101b'})
_VERIFIED_PACKAGES = {}


def _fail(message):
    return ValueError('External map: ' + message)


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative_path(value):
    if not isinstance(value, str) or not value or chr(92) in value or ':' in value:
        raise _fail('The map package contains an invalid resource path.')
    name = PurePosixPath(value)
    if (name.is_absolute() or name.as_posix() != value or any(p in {'.', '..', ''} for p in name.parts)):
        raise _fail('The map package resource path leaves its folder.')
    return name


def _positive(value, maximum):
    return type(value) is int and 0 < value <= maximum


def validate_document(doc):
    """Check format and supported builder ABI before reading asset payloads."""
    from .blender_prepared_proxy import ALGORITHM
    try:
        if (doc['format'] != FORMAT or type(doc['schema_version']) is not int or doc['schema_version'] != 2
                or doc['builder']['commit'] != BUILDER_COMMIT or doc['builder']['map_builder'] != 14
                or doc['map_name'] != 'dl_midtown' or doc['blender_min'] != [5, 2, 0]
                or doc['source_frame'] != 'vrf_blender_metres'
                or doc['collection'] != COLLECTION or doc['world'] != 'Midtown sky'
                or doc['namespace'] != NAMESPACE):
            raise _fail('The prepared map uses an unsupported builder or coordinate format.')
        source = doc['source']
        if (source['kind'] not in {'hosted_snapshot', 'installed_game'}
                or not isinstance(source['game_version'], str) or not source['game_version'].isdigit()
                or not _SHA.fullmatch(source['native_blend_sha256'])):
            raise _fail('The prepared map has incomplete source identity.')
        if source['kind'] == 'installed_game' and (any(not _SHA.fullmatch(source.get(key, '')) for key in
                ('build_request_sha256','map_archive_sha256','root_archive_sha256'))
                or source.get('source2viewer_sha256') not in CONVERTER_SHA256):
            raise _fail('The locally built map lacks its installed-source or converter identity.')
        if source['kind'] == 'installed_game':
            adapter = source.get('adapter_sha256', {})
            if (not isinstance(adapter,dict) or set(adapter) !=
                    {'blender_prepared_map.py','blender_prepared_proxy.py','map_package.py'}
                    or any(not isinstance(value,str) or not _SHA.fullmatch(value) for value in adapter.values())):
                raise _fail('The locally built map lacks its package-preparation identity.')
        inventory = doc['inventory']
        if (set(inventory) != {'objects','meshes','vertices','faces','file_images','geometry_sha256'}
                or not _positive(inventory['objects'], 100_000)
                or not _positive(inventory['meshes'], 50_000)
                or not _positive(inventory['vertices'], 200_000_000)
                or not _positive(inventory['faces'], 200_000_000)
                or not _positive(inventory['file_images'], 10_000)
                or not _SHA.fullmatch(inventory['geometry_sha256'])):
            raise _fail('The prepared map has an invalid geometry inventory.')
        proxy = doc['proxy']
        if (proxy['status'] != 'ready' or proxy['algorithm'] != ALGORITHM
                or proxy['collection'] != PROXY_COLLECTION
                or any(not _positive(proxy[key], 200_000_000) for key in
                       ('source_meshes', 'source_vertices', 'source_faces', 'proxy_meshes', 'proxy_vertices', 'proxy_faces'))
                or proxy['source_meshes'] > inventory['meshes']
                or proxy['source_vertices'] > inventory['vertices']
                or proxy['source_faces'] > inventory['faces']
                or proxy['proxy_faces'] >= proxy['source_faces']
                or proxy['proxy_vertices'] > proxy['source_vertices']
                or proxy['proxy_meshes'] > proxy['source_meshes']
                or proxy['full_map_hidden_in_viewport'] is not True
                or proxy['proxy_hidden_in_render'] is not True):
            raise _fail('The prepared map has an invalid viewport proxy.')
        library = doc['library']
        if library['file'] != LIBRARY or not _positive(library['bytes'], 16 * 2**30) or not _SHA.fullmatch(library['sha256']):
            raise _fail('The prepared map library identity is invalid.')
        files = doc['files']
        if not isinstance(files, list) or not 2 <= len(files) <= 10_000:
            raise _fail('The prepared map resource inventory is invalid.')
        names = set(); total = 0
        for row in files:
            name = relative_path(row['file']).as_posix()
            if (name.casefold() in names or not _positive(row['bytes'], 16 * 2**30)
                    or not _SHA.fullmatch(row['sha256'])):
                raise _fail('The prepared map has duplicate or invalid resource entries.')
            if name != LIBRARY and (len(PurePosixPath(name).parts) < 2
                    or PurePosixPath(name).parts[0] not in {'textures', 'lightmaps', 'volumes'}
                    or PurePosixPath(name).suffix.lower() not in {'.png', '.exr', '.vdb'}):
                raise _fail('The prepared map contains an unsupported resource file.')
            names.add(name.casefold());total += row['bytes']
        matching = [row for row in files if row['file'] == LIBRARY]
        if (len(matching) != 1 or matching[0] != library or total > 64 * 2**30
                or not isinstance(doc['package_id'], str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,99}', doc['package_id'])):
            raise _fail('The prepared map library and resource inventory disagree.')
    except (KeyError, TypeError, AttributeError) as exc:
        raise _fail('The prepared map manifest is incomplete or malformed.') from exc
    return doc


def load_package(path):
    """Require every declared byte; no basename search or caller count overrides."""
    library = Path(path).expanduser().resolve()
    folder = library if library.is_dir() else library.parent
    manifest = folder / MANIFEST
    try:
        if manifest.stat().st_size > 4 * 1024**2:
            raise _fail('The prepared map manifest is too large.')
        raw = manifest.read_bytes()
        manifest_sha256 = hashlib.sha256(raw).hexdigest()
        cached = _VERIFIED_PACKAGES.get((str(folder), manifest_sha256))
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if cached is not None:
            try:
                if all(identity(Path(name).stat()) == expected for name, expected in cached['identities']):
                    return copy.deepcopy(cached['package'])
            except OSError:
                pass
        doc = validate_document(json.loads(raw))
        identities = []
        for row in doc['files']:
            target = folder.joinpath(*relative_path(row['file']).parts).resolve()
            if not target.is_relative_to(folder):
                raise _fail('A prepared map resource leaves its folder.')
            before = target.stat()
            if not target.is_file() or before.st_size != row['bytes'] or file_hash(target) != row['sha256']:
                raise _fail('A prepared map resource is missing or changed: ' + row['file'])
            after = target.stat()
            if identity(before) != identity(after):
                raise _fail('A prepared map resource changed during validation: ' + row['file'])
            identities.append((str(target), identity(after)))
        if not all(identity(Path(name).stat()) == expected for name, expected in identities):
            raise _fail('Prepared map resources changed while validating the package.')
        package = dict(folder=folder, document=doc, manifest_sha256=manifest_sha256)
        # Repeated checks within one job reuse verified bytes only while every
        # file identity is unchanged. Never persist this shortcut between jobs.
        if len(_VERIFIED_PACKAGES) >= 8:
            _VERIFIED_PACKAGES.clear()
        _VERIFIED_PACKAGES[(str(folder), manifest_sha256)] = dict(package=copy.deepcopy(package), identities=identities)
        return package
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise _fail('Keep the complete extracted map package together, including textures, lightmaps and map-package.json.') from exc


def library_identity(path):
    package = load_package(path)
    doc = package['document']
    if doc['source']['kind'] == 'hosted_snapshot' and package['manifest_sha256'] not in HOSTED_MANIFESTS:
        raise _fail('This hosted map snapshot is not qualified by this RenderLock version.')
    selected = Path(path).expanduser().resolve()
    if selected != package['folder'] / LIBRARY:
        raise _fail('Choose RenderLockMap.blend from the prepared map package.')
    return dict(path=str(selected), sha256=doc['library']['sha256'], package_id=doc['package_id'],
                format=FORMAT, manifest_sha256=package['manifest_sha256'])


def library_definition(library):
    package = load_package(library['path'])
    doc = package['document']
    if (library.get('sha256') != doc['library']['sha256']
            or library.get('manifest_sha256') != package['manifest_sha256']):
        raise _fail('The prepared map identity changed after it was selected.')
    return dict(package_id=doc['package_id'], bytes=doc['library']['bytes'],
                **doc['inventory'], proxy=doc['proxy'], format=FORMAT,
                manifest_sha256=package['manifest_sha256'], world=doc['world'], source=doc['source'],
                namespace=doc['namespace'], resource_files=len(doc['files'])-1)


def install_project_assets(package, scene_output):
    """Copy once per map/project folder; never share mutable hardlinked images."""
    parent = Path(scene_output).resolve().parent
    destination = (parent / 'RenderLockMapAssets' / package['manifest_sha256'][:16]).resolve()
    if not destination.is_relative_to(parent):
        raise _fail('The project map-assets folder leaves the output folder.')
    marker = destination / 'map-assets.json'
    pending = destination / 'map-assets-pending.json'
    identity = dict(format=FORMAT, manifest_sha256=package['manifest_sha256'])
    complete = marker.is_file()
    if destination.exists():
        owned = marker if complete else pending
        try:
            if json.loads(owned.read_bytes()) != identity:
                raise _fail('The project map assets belong to a different package.')
        except (OSError, ValueError) as exc:
            raise _fail('The project map-assets folder is not a managed RenderLock folder.') from exc
    else:
        destination.mkdir(parents=True)
        pending.write_text(json.dumps(identity), encoding='utf-8')
    files = [row for row in package['document']['files'] if row['file'] != LIBRARY]
    short_names = {}
    for index, row in enumerate(files):
        relative = relative_path(row['file'])
        short = row['sha256'][:24] + relative.suffix.lower()
        if short in short_names and short_names[short] != row['sha256']:
            raise _fail('Project asset identifiers collided; choose a fresh output folder.')
        short_names[short] = row['sha256']
        target = (destination / short).resolve()
        if not target.is_relative_to(destination):
            raise _fail('A project asset path leaves its folder.')
        valid = target.is_file() and target.stat().st_size == row['bytes'] and file_hash(target) == row['sha256']
        if not valid:
            if complete:
                raise _fail('Project map assets were changed. Keep edits separately or choose a fresh output folder: ' + row['file'])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package['folder'].joinpath(*relative.parts), target)
            if target.stat().st_size != row['bytes'] or file_hash(target) != row['sha256']:
                raise _fail('Could not verify the project map asset copy: ' + row['file'])
        if index % 100 == 0:
            print(f'RenderLock map assets: {index+1}/{len(files)} verified', flush=True)
    if not complete:
        pending.replace(marker)
    return destination


def project_asset_path(assets, row):
    return Path(assets) / (row['sha256'][:24] + relative_path(row['file']).suffix.lower())
