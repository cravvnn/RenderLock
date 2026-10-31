"""Manage the optional standalone map builder and its verified local cache."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile

from .map_package import BUILDER_COMMIT, LIBRARY, file_hash, library_identity, load_package

SOURCE_URL = f'https://codeload.github.com/Sirsyorrz/deadlockrigs/zip/{BUILDER_COMMIT}'
SOURCE_SHA256 = 'c5b43b0a572edb8058eea34adc60c96b096306ce7aef90e7cf39b2ab1828a17a'
S2V_TAG = 's2v-f030654ff'
S2V = {
    'win-x64': dict(archive='0ae6a2c4c4285dfbce9a47eb70e55b6dc6747850e6b70bfd133ef3bf5161c7f3',
                    cli='d440ca0c62a885665702b35bf005dc19f3ad4736e45afab4d000f74448c87de7', name='Source2Viewer-CLI.exe'),
    'linux-x64': dict(archive='3e49187542d9f2696aeb3234281176afe1ed7c759cbdec8c8c8606b423faf493',
                      cli='9142f59e64dc97b7afab4cca3c5cbb4197d38beccb1f7d7f4ffbd691a053e707', name='Source2Viewer-CLI'),
}
ADAPTER_FILES = ('blender_prepared_map.py','blender_prepared_proxy.py','map_package.py')


def adapter_identity():
    return {name:file_hash(Path(__file__).with_name(name)) for name in ADAPTER_FILES}


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _extract_archive(archive, output):
    """Inspect every ZIP member before writing any file."""
    with zipfile.ZipFile(archive) as packed:
        entries = [];seen = set();total = 0
        for entry in packed.infolist():
            name = entry.orig_filename.rstrip('/')
            relative = PurePosixPath(name)
            if (not name or chr(0) in name or relative.is_absolute() or ':' in name or chr(92) in name
                    or relative.as_posix() != name or '..' in relative.parts
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or name.casefold() in seen):
                raise ValueError('Map tools: archive contains an unsafe or duplicate member.')
            seen.add(name.casefold());total += entry.file_size
            target = output.joinpath(*relative.parts).resolve()
            if not target.is_relative_to(output.resolve()) or total > 1024**3 or len(seen) > 20_000:
                raise ValueError('Map tools: archive exceeds its extraction bounds.')
            entries.append((entry, target))
        for entry, target in entries:
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with packed.open(entry) as source, target.open('xb') as stream:
                    shutil.copyfileobj(source, stream)


def _component(cache, name, url, expected):
    cache.mkdir(parents=True, exist_ok=True)
    folder = cache / name
    archive = cache / (name+'.zip')
    if (folder.exists() and not folder.resolve().is_relative_to(cache.resolve())
            or archive.exists() and not archive.resolve().is_relative_to(cache.resolve())):
        raise ValueError('Map tools: component path leaves its cache.')
    marker = folder/'renderlock-component.json'
    if marker.is_file():
        try:
            doc = json.loads(marker.read_bytes())
            files = {p.relative_to(folder).as_posix():p for p in folder.rglob('*') if p.is_file() and p != marker}
            if (doc['archive_sha256'] == expected and set(files) == set(doc['files'])
                    and all(p.resolve().is_relative_to(folder.resolve())
                            and file_hash(p) == doc['files'][relative] for relative,p in files.items())):
                return folder
        except (OSError, ValueError, KeyError):
            pass
    if folder.exists():
        if folder.is_symlink() or not folder.resolve().is_relative_to(cache.resolve()):
            raise ValueError('Map tools: component folder leaves its cache.')
        folder.rename(cache/(name+'.preserved-'+uuid.uuid4().hex))
    if not archive.is_file() or file_hash(archive) != expected:
        if archive.exists():
            archive.rename(cache/(name+'.preserved-'+uuid.uuid4().hex+'.zip'))
        temporary = cache/(name+'.download-'+uuid.uuid4().hex)
        print('Downloading map tools: '+url, flush=True)
        with urllib.request.urlopen(url, timeout=45) as response, temporary.open('xb') as stream:
            total = 0
            while chunk := response.read(1024**2):
                total += len(chunk)
                if total > 512*1024**2:
                    raise ValueError('Map tools: download exceeds its size limit.')
                stream.write(chunk)
        if file_hash(temporary) != expected:
            raise ValueError('Map tools: download identity differs from the pinned source.')
        temporary.replace(archive)
    stage = cache/(name+'.extract-'+uuid.uuid4().hex)
    stage.mkdir()
    _extract_archive(archive, stage)
    files = {p.relative_to(stage).as_posix():file_hash(p) for p in stage.rglob('*') if p.is_file()}
    _write_json(stage/'renderlock-component.json', dict(archive_sha256=expected,files=files))
    stage.rename(folder)
    return folder


def tools(cache):
    machine = platform.machine().lower()
    rid = ('win' if sys.platform == 'win32' else 'linux' if sys.platform.startswith('linux') else sys.platform)
    rid += '-x64' if machine in {'amd64','x86_64','x64'} else '-'+machine
    if rid not in S2V:
        raise ValueError('Local map building is currently supported on Windows x64 and Linux x64. Use the hosted map on other platforms.')
    tool_cache = Path(cache)/'map-tools'
    if not tool_cache.resolve().is_relative_to(Path(cache).resolve()):
        raise ValueError('Map tools: helper cache leaves the selected cache folder.')
    source = _component(tool_cache, 'deadlockrigs-'+BUILDER_COMMIT, SOURCE_URL, SOURCE_SHA256)
    source = source/('deadlockrigs-'+BUILDER_COMMIT)
    if not (source/'LICENSE').is_file():
        raise ValueError('Map tools: source archive has no upstream license.')
    cli_info = S2V[rid]
    viewer = _component(tool_cache, S2V_TAG+'-'+rid,
        f'https://github.com/Sirsyorrz/deadlockrigs/releases/download/{S2V_TAG}/{rid}.zip', cli_info['archive'])
    cli = viewer/cli_info['name']
    if not cli.is_file() or file_hash(cli) != cli_info['cli']:
        raise ValueError('Map tools: converter identity does not match the pinned release.')
    if os.name != 'nt':
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    return source,cli


def installed_fingerprint(install):
    from .game_assets import normalize_install_path
    install = normalize_install_path(install)
    game = install/'game';citadel = game/'citadel'
    archives = list(citadel.glob('*.vpk')) + list((game/'core').glob('*.vpk'))
    selected = citadel/'maps/dl_midtown.vpk'
    if not selected.is_file():
        raise ValueError('Midtown is not installed in the selected game folder.')
    archives.append(selected)
    rows = []
    for path in sorted(archives):
        info = path.stat()
        rows.append(dict(file=path.relative_to(game).as_posix(),bytes=info.st_size,
                         mtime_ns=info.st_mtime_ns,inode=info.st_ino))
    version = next((line.split('=',1)[1].strip() for line in (citadel/'steam.inf').read_text(encoding='utf-8-sig').splitlines()
                    if line.startswith('ClientVersion=')), '')
    if not version.isdigit():
        raise ValueError('Could not read the installed game version.')
    return install,dict(game_version=version,archives=rows,
        map_archive_sha256=file_hash(selected),root_archive_sha256=file_hash(citadel/'pak01_dir.vpk'))


def _run(command, environment):
    with subprocess.Popen([str(part) for part in command], env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
        for line in process.stdout:
            print(line, end='', flush=True)
        code = process.wait()
    if code:
        raise subprocess.CalledProcessError(code, command)


def native_request_identity(fingerprint, capabilities):
    bridge = Path(__file__).resolve().parents[1]/'tools/map_builder/bridge.py'
    return dict(source=fingerprint,builder_commit=BUILDER_COMMIT,
        bridge_sha256=file_hash(bridge),blender_version=capabilities['version'],mods=False,threads=2,
        source2viewer_tag=S2V_TAG,platform=sys.platform,machine=platform.machine())


def prepare_map(install, blender, cache, *, map_name='dl_midtown', keep_work=False):
    from .blender_runtime import check_blender
    if map_name != 'dl_midtown':
        raise ValueError('New full-map preparation currently supports Midtown (dl_midtown).')
    cache = Path(cache).expanduser().resolve()
    # The pinned native image loader does not accept long Windows paths. Its
    # qualified longest extracted resource adds 147 characters beneath this root.
    if os.name == 'nt' and len(str(cache)) > 96:
        raise ValueError('Map building needs a shorter cache folder path on Windows. Choose a folder such as D:\\RenderLockCache in Setup, or use the hosted map.')
    install,fingerprint = installed_fingerprint(install)
    if cache == install or cache.is_relative_to(install):
        raise ValueError('Map cache must stay outside the installed game folder.')
    cache.mkdir(parents=True,exist_ok=True)
    capabilities = check_blender(str(blender))
    if capabilities.get('version',[])[:2] != [5,2]:
        raise ValueError('Select Blender 5.2 LTS in Setup to build the new maps.')
    program = Path(__file__).resolve().parents[1]
    bridge = program/'tools/map_builder/bridge.py'
    native_request = native_request_identity(fingerprint, capabilities)
    native_key = _digest(native_request)
    adapter = adapter_identity()
    prepared_key = _digest(dict(native=native_key,adapter=adapter))
    destination = (cache/'p'/prepared_key[:16]).resolve()
    if not destination.is_relative_to(cache):
        raise ValueError('Map output leaves the chosen cache.')
    if (destination/LIBRARY).is_file():
        try:
            identity = library_identity(destination/LIBRARY)
            if load_package(destination)['document']['source'].get('build_request_sha256') == prepared_key:
                print('RENDERLOCK_MAP_READY='+identity['path'],flush=True)
                return identity
        except ValueError:
            pass
    if destination.exists():
        if not destination.resolve().is_relative_to(cache):
            raise ValueError('Map output leaves the chosen cache.')
        destination.rename(destination.with_name(destination.name+'.preserved-'+uuid.uuid4().hex))
    if shutil.disk_usage(cache).free < 32*2**30:
        raise ValueError('A first full-map build needs at least 32 GB of free working space. Choose a roomier cache folder or use the hosted map.')
    source,cli = tools(cache)
    job = (cache/'m'/native_key[:12]).resolve()
    if not job.is_relative_to(cache):
        raise ValueError('Map working folder leaves the chosen cache.')
    job.mkdir(parents=True,exist_ok=True)
    identity_file = job/'input-identity.json'
    if identity_file.exists():
        try:
            if json.loads(identity_file.read_bytes()) != native_request:
                raise ValueError('Map cache identifiers collided. Choose a fresh cache folder.')
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('The map cache identity is damaged. Choose a fresh cache folder.') from exc
    request = dict(root=str(job),builder_commit=BUILDER_COMMIT,map=map_name,
        work=str(job/'work'),out=str(job/'native'),s2v_cache=str(job/'s2v-cache'),
        blender=str(Path(blender).absolute()),s2v=str(cli),pak=str(install/'game/citadel/pak01_dir.vpk'),
        threads=2,keep_work=bool(keep_work),input_identity_sha256=native_key)
    _write_json(job/'request.json',request)
    _write_json(identity_file,native_request)
    env = dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
    temporary = (cache/'Temp').resolve()
    if not temporary.is_relative_to(cache):
        raise ValueError('Map temporary folder leaves the chosen cache.')
    temporary.mkdir(exist_ok=True)
    env.update(TEMP=str(temporary),TMP=str(temporary))
    print('Building Midtown from installed game files. The first build can take a while; later exports reuse the prepared package.',flush=True)
    _run([sys.executable,'-B',bridge,'--source',source,'--request',job/'request.json'],env)
    if adapter_identity() != adapter:
        raise ValueError('RenderLock map preparation changed during building. The native map is retained; run Build from game again to prepare it with the current version.')
    if installed_fingerprint(install)[1] != fingerprint:
        raise ValueError('The game changed during map building. Let Steam finish updating before starting another build.')
    native = job/'native/maps/dl_midtown/dl_midtown.blend'
    provenance = dict(kind='installed_game',game_version=fingerprint['game_version'],
        build_request_sha256=prepared_key,map_archive_sha256=fingerprint['map_archive_sha256'],
        root_archive_sha256=fingerprint['root_archive_sha256'],native_blend_sha256=file_hash(native),
        source2viewer_sha256=file_hash(cli),adapter_sha256=adapter)
    _write_json(job/'provenance.json',provenance)
    script = Path(__file__).with_name('blender_prepared_map.py')
    print('Preparing portable textures and the cached viewport proxy...',flush=True)
    _run([blender,'--background','--factory-startup','--disable-autoexec','--threads','2',
        '--python-exit-code','1','--python',script,'--','--source',native,'--output',destination,
        '--provenance',job/'provenance.json'],env)
    identity = library_identity(destination/LIBRARY)
    if installed_fingerprint(install)[1] != fingerprint:
        raise ValueError('The game changed before map preparation finished. The candidate was retained for diagnostics; rebuild after updating.')
    print('RENDERLOCK_MAP_READY='+identity['path'],flush=True)
    return identity
