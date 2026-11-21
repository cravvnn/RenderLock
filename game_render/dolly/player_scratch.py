"""Owned per-capture scratch directories, independent of game deployment lifetime."""
from pathlib import Path
import json
import os
import re
import uuid

OWNER = '.dolly-players-owner'
MANIFEST = 'capture.json'


def checked_directory(path):
    path = Path(path).expanduser().absolute()
    if os.name == 'nt' and (len(path.drive) != 2 or path.drive[1] != ':'):
        raise ValueError('Choose a Players scratch folder on a local drive.')
    if not path.is_dir():
        raise ValueError('Choose an existing Players scratch folder.')
    for part in (path, *path.parents):
        if (part.is_symlink() or getattr(part, 'is_junction', lambda: False)()
                or getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Players scratch folders cannot use links, junctions or reparse points; choose an ordinary local folder.')
    return path.resolve()


def create(root, *, fps, frames, output=None):
    root = checked_directory(root)
    request = uuid.uuid4().hex
    folder = root / ('dolly-players-' + request)
    # Marker stays bounded for the native reader, including Unicode encoded as hex.
    if len(str(folder).encode('utf-8')) > 768:
        raise ValueError('Players scratch path is too long; choose a shorter folder.')
    folder.mkdir()
    try:
        (folder/OWNER).write_text(request, encoding='ascii')
        (folder/MANIFEST).write_text(json.dumps(dict(format='dolly-players-scratch', version=1,
            request=request, fps=fps, frames=frames, output=str(output) if output else None,
            state='pending', directory=str(folder)), indent=2), encoding='utf-8')
    except Exception:
        # No raw capture has been admitted yet. Remove only our new known files.
        for cleanup in ((folder/OWNER).unlink, (folder/MANIFEST).unlink, folder.rmdir):
            try:
                cleanup()
            except OSError:
                pass
        raise
    return folder, request


def validate(folder, request):
    folder = checked_directory(folder)
    if not re.fullmatch('[0-9a-f]{32}', request) or folder.name != 'dolly-players-' + request:
        raise ValueError('Players scratch request does not own this folder.')
    owner = folder/OWNER
    if owner.is_symlink() or owner.stat().st_size != 32 or owner.read_text(encoding='ascii') != request:
        raise ValueError('Players scratch ownership does not match this take.')
    return folder


def update(folder, state, *, error=None):
    folder = Path(folder)
    manifest = folder/MANIFEST
    if manifest.is_symlink() or manifest.stat().st_size > 16384:
        raise ValueError('Invalid Players scratch manifest.')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    validate(folder, data['request'])
    data['state'] = state
    if error: data['error'] = str(error)[:1000]
    temporary = manifest.with_name('.dolly-capture-' + uuid.uuid4().hex + '.tmp')
    stream = temporary.open('x', encoding='utf-8')
    try:
        with stream:
            json.dump(data, stream, indent=2)
        os.replace(temporary, manifest)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # An owned leftover must not hide the update result.


def from_marker(deployment):
    marker = Path(deployment)/'dolly_owner_start.txt'
    if not marker.exists(): return Path(deployment)
    text = marker.read_text(encoding='ascii')
    if not text.startswith('color-sequence-v4 '): return Path(deployment)
    match = re.search(r' request ([0-9a-f]{32}) speed \S+ scratch ([0-9a-f]+)\s*$', text)
    if not match: raise ValueError('Invalid Players scratch request.')
    return validate(Path(bytes.fromhex(match[2]).decode('utf-8')), match[1])
