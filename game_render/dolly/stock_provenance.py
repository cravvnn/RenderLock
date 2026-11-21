"""Bounded, read-only stock-config admission from the current Steam install record.

Ordinary installed cfg/GameInfo may be customized. Only current depot content
metadata is compared with Dolly's pinned stock payload, never those user files.
This consistency gate does not qualify native client layouts or authenticate an
arbitrary user-selected manifest. All cache locations come from the install and
Steam location owner.
"""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import re
import stat
import struct

from .file_ops import _plain_ancestors
from .keyvalues import _parse

CONFIGURATION_MODULES = (
    'bin/win64/engine2.dll', 'bin/win64/tier0.dll',
    'bin/win64/filesystem_stdio.dll', 'bin/win64/rendersystemdx11.dll',
)
_MANIFEST_LIMIT = 16 * 1024 * 1024
_TOTAL_LIMIT = 64 * 1024 * 1024
# Retain reviewed patch identities without rejecting a run of frequent updates.
# The provenance asset still has its independent 64 KiB read bound, and every
# mapping retains the existing 16-depot/type/identity validation below.
_MAX_REVIEWED_DEPOTS = 64
_STOCK_PATH = re.compile(r'game/(?:citadel|core)/cfg/.+|game/citadel/gameinfo\.gi')
VIDEO_RESOURCES = frozenset({f'cfg/video_defaults_{index}.txt' for index in range(4)} | {'cfg/video_version.txt'})


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, stat.S_IMODE(info.st_mode))


def _read(path, limit):
    if not path.is_absolute() or not _plain_ancestors(path):
        raise ValueError('Linked or relative stock provenance path')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
        raise ValueError('Oversized or non-file stock provenance input')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
        opened = os.fstat(stream.fileno())
    # Windows Python versions can report creation-time as path st_ctime and
    # change-time as handle st_ctime. Compare it within the path API, while the
    # opened handle independently proves device/inode/size/mtime/mode identity.
    same_open_file = _identity(before)[:4] == _identity(opened)[:4] and before.st_mode == opened.st_mode
    if len(raw) > limit or len(raw) != before.st_size or not same_open_file or _identity(before) != _identity(path.stat()):
        raise ValueError('Stock provenance input changed while reading')
    return raw, _identity(before)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate stock provenance JSON key')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique)


def _path(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise ValueError('Invalid stock content path')
    value = value.replace('\\', '/')
    parts = value.split('/')
    if (any(not part or part in ('.', '..') for part in parts)
            or any(char in value for char in ':\x00\r\n')
            or PurePosixPath(value).is_absolute()):
        raise ValueError('Unsafe stock content path')
    return value


def _fields(raw):
    position = 0
    def varint():
        nonlocal position
        value = 0
        for index in range(10):
            if position >= len(raw):
                raise ValueError('Truncated depot varint')
            byte = raw[position]; position += 1
            if index == 9 and byte > 1:
                raise ValueError('Overflowing depot varint')
            value |= (byte & 127) << (7 * index)
            if byte < 128:
                return value
        raise ValueError('Oversized depot varint')
    count = 0
    while position < len(raw):
        count += 1
        if count > 1_000_000:
            raise ValueError('Depot field limit exceeded')
        tag = varint(); number, wire = tag >> 3, tag & 7
        if not 0 < number < 2**29:
            raise ValueError('Invalid depot field number')
        if wire == 0:
            value = varint()
        else:
            size = varint() if wire == 2 else 8 if wire == 1 else 4 if wire == 5 else -1
            if not 0 <= size <= len(raw) - position:
                raise ValueError('Unsupported or truncated depot field')
            value = raw[position:position + size]; position += size
        yield number, wire, value


def _scalars(raw, known):
    result = {}
    for number, wire, value in _fields(raw):
        if number not in known:
            continue
        if number in result or wire != known[number]:
            raise ValueError('Duplicate or wrongly typed depot scalar')
        result[number] = value
    return result


def _mapping(raw, depot, manifest):
    position = 0
    def frame(expected):
        nonlocal position
        if len(raw) - position < 8:
            raise ValueError('Truncated depot framing')
        magic, length = struct.unpack_from('<II', raw, position); position += 8
        if magic != expected or length > len(raw) - position:
            raise ValueError('Invalid depot framing')
        body = raw[position:position + length]; position += length
        return body
    payload = frame(0x71f617d0)
    metadata = _scalars(frame(0x1f4812be), {1: 0, 2: 0, 4: 0})
    if metadata.get(1) != int(depot) or metadata.get(2) != int(manifest) or metadata.get(4) != 0:
        raise ValueError('Depot metadata identity or plaintext-name mode mismatch')
    frame(0x1b81b817)  # Signature section; no authenticity claim is inferred.
    if raw[position:] != struct.pack('<I', 0x32c415ab):
        raise ValueError('Invalid depot end marker')
    found = {}; seen = set(); count = 0
    for number, wire, value in _fields(payload):
        count += 1
        if number != 1 or wire != 2 or count > 200_000:
            raise ValueError('Invalid or oversized depot mapping')
        row = _scalars(value, {1: 2, 2: 0, 3: 0, 5: 2, 7: 2})
        if 1 not in row:
            raise ValueError('Depot mapping has no path')
        name = _path(row[1].decode('utf-8'))
        key = name.casefold()
        if key in seen:
            raise ValueError('Duplicate depot content path')
        seen.add(key)
        if not _STOCK_PATH.fullmatch(key):
            continue
        if (7 in row or row.get(3, 0) != 0 or type(row.get(2)) is not int
                or not 0 <= row[2] <= 128 * 1024 or len(row.get(5, b'')) != 20):
            raise ValueError('Unsupported stock cfg mapping')
        found[name] = {'bytes': row[2], 'sha1': row[5].hex()}
    return found


def _one(nodes, name, *, block=False):
    values = [node for node in nodes if node.key.value.casefold() == name.casefold()]
    if len(values) != 1 or (values[0].children is not None) != block:
        raise ValueError('Missing or ambiguous Steam install record: ' + name)
    return values[0].children if block else values[0].value.value


def _video(paths, pins):
    from .panorama_assets import _read_vpk_tree, _iter_vpk_tree, read_vpk_entries
    if (not isinstance(pins, dict) or set(pins) != VIDEO_RESOURCES
            or any(not isinstance(value, str) or re.fullmatch('[a-f0-9]{64}', value) is None for value in pins.values())):
        raise ValueError('Invalid pinned stock video inventory')
    index = paths.citadel_dir/'pak01_dir.vpk'
    if not _plain_ancestors(index) or not stat.S_ISREG(index.stat().st_mode):
        raise ValueError('Linked stock video index')
    snapshots = {index: _identity(index.stat())}
    _header, _size, tree = _read_vpk_tree(index, 64 * 1024 * 1024)
    for name, _crc, _preload, archive, _offset, _length in _iter_vpk_tree(tree):
        if name in pins:
            source = index if archive == 0x7fff else index.with_name(index.name[:-8] + '_' + f'{archive:03d}' + '.vpk')
            if not _plain_ancestors(source) or not stat.S_ISREG(source.stat().st_mode):
                raise ValueError('Linked or non-file stock video archive')
            snapshots.setdefault(source, _identity(source.stat()))
    actual = read_vpk_entries(index, sorted(pins), max_payload_bytes=128 * 1024)
    if any(hashlib.sha256(data).hexdigest() != pins[name] for name, data in actual.items()):
        raise ValueError('Current stock video defaults changed; update Dolly before launching')
    if any(not _plain_ancestors(path) or _identity(path.stat()) != stamp for path, stamp in snapshots.items()):
        raise ValueError('Stock video archive changed during verification')


def verify(paths, editing_root, profile, steam_roots=(), extra_files=None):
    """Refuse changed stock provenance before journaling/deployment; return receipt.

    ``extra_files`` may add further accepted ``(bytes, sha1)`` variants per
    stock path from published build definitions. It can never remove the
    bundled snapshot, so the release remains the floor.
    """
    editing_root = Path(editing_root).absolute()
    raw, _stamp = _read(editing_root/'stock-provenance.json', 64 * 1024)
    if hashlib.sha256(raw).hexdigest() != profile.get('stock_provenance_sha256'):
        raise ValueError('Stock provenance asset failed its integrity check')
    expected = _json(raw)
    files = expected.get('files')
    if type(expected.get('format')) is not int or expected['format'] != 1 or not isinstance(files, dict) or not 1 <= len(files) <= 128:
        raise ValueError('Invalid pinned stock inventory')
    for name, row in files.items():
        if (_path(name) != name or not _STOCK_PATH.fullmatch(name) or not isinstance(row, dict)
                or set(row) != {'bytes', 'sha1'} or type(row['bytes']) is not int
                or not 0 <= row['bytes'] <= 128 * 1024
                or not isinstance(row['sha1'], str) or re.fullmatch('[a-f0-9]{40}', row['sha1']) is None):
            raise ValueError('Invalid pinned stock row')
    if files.get('game/citadel/gameinfo.gi', {}).get('sha1') != profile.get('stock_gameinfo_sha1'):
        raise ValueError('Stock GameInfo identity disagrees with editing profile')

    # The current appmanifest belongs to exactly this selected Steam install.
    library = paths.root.parent.parent.parent
    app = library/'steamapps/appmanifest_1422450.acf'
    app_raw, app_stamp = _read(app, 1024 * 1024)
    state = _one(_parse(app_raw.decode('utf-8-sig')), 'AppState', block=True)
    if _one(state, 'appid') != '1422450' or _one(state, 'StateFlags') != '4':
        raise ValueError('Steam has not completed this Deadlock installation/update')
    install = _one(state, 'installdir')
    if (not install or any(c in install for c in '/\\:') or install in ('.', '..')
            or (library/'steamapps/common'/install).absolute() != paths.root.absolute()):
        raise ValueError('Steam appmanifest belongs to a different installation')
    depots = _one(state, 'InstalledDepots', block=True)
    if not 1 <= len(depots) <= 16:
        raise ValueError('Invalid Steam depot count')
    identities = {}
    seen_depots = set()
    for depot in depots:
        ident = depot.key.value
        manifest = _one(depot.children or [], 'manifest')
        if (re.fullmatch('[1-9][0-9]{0,9}', ident) is None or ident in seen_depots
                or re.fullmatch('[1-9][0-9]{0,19}', manifest) is None
                or int(manifest) >= 2**64):
            raise ValueError('Invalid or duplicate Steam depot identity')
        seen_depots.add(ident)
        identities[ident] = manifest
    reviewed = expected.get('reviewed_depots')
    if not isinstance(reviewed, list) or not 1 <= len(reviewed) <= _MAX_REVIEWED_DEPOTS:
        raise ValueError('Missing reviewed Steam depot identity')
    for mapping in reviewed:
        if (not isinstance(mapping, dict) or not 1 <= len(mapping) <= 16
                or any(re.fullmatch('[1-9][0-9]{0,9}', ident) is None
                       or not isinstance(gid, str) or re.fullmatch('[1-9][0-9]{0,19}', gid) is None
                       or int(gid) >= 2**64 for ident, gid in mapping.items())):
            raise ValueError('Invalid reviewed Steam depot identity')
    known = identities in reviewed
    roots = list(dict.fromkeys([library, *(Path(root).absolute() for root in steam_roots)]))
    actual = {}; snapshots = {}; total = 0; receipt = []
    for ident, manifest in identities.items():
        if known:
            receipt.append({'depot': ident, 'manifest': manifest})
            continue
        candidates = [root/'depotcache'/f'{ident}_{manifest}.manifest' for root in roots]
        found = [path for path in candidates if path.exists() or path.is_symlink()]
        if not found:
            raise ValueError('Current Steam depot manifest is missing; verify Deadlock in Steam')
        chosen = None
        for path in found:
            data, stamp = _read(path, min(_MANIFEST_LIMIT, _TOTAL_LIMIT-total))
            total += len(data)
            if chosen is not None and data != chosen:
                raise ValueError('Ambiguous current Steam depot manifests')
            chosen = data; snapshots[path] = stamp
        current = _mapping(chosen, ident, manifest)
        if set(actual) & set(current):
            raise ValueError('Ambiguous stock paths across Steam depots')
        actual.update(current)
        receipt.append({'depot': ident, 'manifest': manifest, 'sha256': hashlib.sha256(chosen).hexdigest()})
    if not known and actual != files:
        published = {}
        for name, variants in (extra_files or {}).items():
            if name not in files:
                raise ValueError('Published stock inventory describes an unreviewed path')
            published[name] = tuple(variants)
        for name, row in actual.items():
            allowed = set(published.get(name, ()))
            pinned = files.get(name)
            if pinned is not None:
                allowed.add((pinned['bytes'], pinned['sha1']))
            if (row['bytes'], row['sha1']) not in allowed:
                raise ValueError('Current stock GameInfo/cfg changed; update Dolly before launching')
        if set(actual) != set(files):
            raise ValueError('Current stock GameInfo/cfg changed; update Dolly before launching')
    if extra_files and not known:
        receipt.append({'stock_snapshot': 'published definitions'})
    _video(paths, expected.get('video_resources'))
    if _read(app, 1024 * 1024) != (app_raw, app_stamp) or any(not _plain_ancestors(path) or _identity(path.stat()) != stamp for path, stamp in snapshots.items()):
        raise ValueError('Steam install changed during stock verification')
    return {'stock_provenance_sha256': profile['stock_provenance_sha256'], 'depots': receipt,
            'basis': 'reviewed_installed_depots' if known else 'current_cached_manifests'}
