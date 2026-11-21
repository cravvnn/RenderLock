"""Reviewed Panorama stock assets and a bounded read-only VPK entry reader.

Read the small directory tree and only named payload spans from its paired
archives. No extraction, resource installation, compilation or process control.
"""
from pathlib import Path, PurePosixPath
import binascii
import struct
import hashlib

# These current stock inputs were reviewed alongside the content-preserving
# loading layout. A game UI update must refresh the override and these pins.
STOCK_UI_SHA256 = {
    'panorama/layout/citadel_prewarm_assets_status.vxml_c': '274ef6908d928a3821c2875b2f705d989074b1bc10b7efc817107db90163de8d',
    'panorama/styles/citadel_prewarm_assets_status.vcss_c': '597b44d3847ed9d8d8763317046aa04ff8ebfd6362bf0dcb1dcb9575e7ff0c9c',
    'panorama/styles/citadel_status_message_shared.vcss_c': '502a96f42007d7a239e5452e82dce08555248b236c25c3c85a111ed2fdd12363',
    'panorama/layout/hud_hideout.vxml_c': '54b3b57c81ce0dad21fc1866be8a8a4a0a053ad7452d9fe5456b0971b62554f4',
    'panorama/styles/hud_hideout.vcss_c': 'fa22a8beb701a5d5c6f9ad001da79d718ba97e869d3b10498ad26d69c57c2374',
}

OVERRIDE_RESOURCES = frozenset((
    'panorama/layout/citadel_prewarm_assets_status.vxml_c',
    'panorama/layout/hud_hideout.vxml_c',
    'panorama/styles/citadel_prewarm_assets_status.vcss_c',
    'panorama/styles/citadel_client_status.vcss_c',
    'panorama/styles/citadel_hud_and_db_overlay.vcss_c',
))

def verify_stock_ui_assets(citadel_dir):
    """Refuse changed stock layouts before preparing a new native deployment."""
    try:
        actual = read_vpk_entries(Path(citadel_dir) / 'pak01_dir.vpk', STOCK_UI_SHA256)
        changed = [name for name, payload in actual.items()
                   if hashlib.sha256(payload).hexdigest() != STOCK_UI_SHA256[name]]
        if changed:
            raise ValueError('Changed stock resource: ' + ', '.join(sorted(changed)))
    except (OSError, ValueError) as exc:
        raise ValueError("Deadlock's shader loading UI could not be verified. "
                         "Use a Dolly build reviewed for this game update. " + str(exc)) from exc

def _read_vpk_tree(index_path, max_tree_bytes):
    """Read one validated VPK directory tree: (header size, tree size, tree)."""
    index = Path(index_path)
    if not index.name.casefold().endswith('_dir.vpk'):
        raise ValueError('Expected a VPK directory index.')
    with index.open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise ValueError('Truncated VPK header.')
        signature, version, tree_size = struct.unpack('<III', header)
        if signature != 0x55AA1234 or version not in (1, 2) or not 1 <= tree_size <= max_tree_bytes:
            raise ValueError('Unsupported or invalid VPK header.')
        header_size = 28 if version == 2 else 12
        if version == 2 and len(stream.read(16)) != 16:
            raise ValueError('Truncated VPK v2 header.')
        tree = stream.read(tree_size)
        if len(tree) != tree_size:
            raise ValueError('Truncated VPK directory tree.')
    return header_size, tree_size, tree


def _iter_vpk_tree(tree):
    """Yield one ``(key, crc, preload, archive, offset, length)`` per entry."""
    position = 0
    def word():
        nonlocal position
        try:
            end = tree.index(b'\0', position)
        except ValueError as exc:
            raise ValueError('Unterminated VPK tree string.') from exc
        result = tree[position:end].decode('utf-8')
        position = end + 1
        return result
    while extension := word():
        while directory := word():
            while name := word():
                if position + 18 > len(tree):
                    raise ValueError('Truncated VPK entry.')
                crc, preload_size, archive, offset, length, terminator = struct.unpack_from('<IHHIIH', tree, position)
                position += 18
                if terminator != 0xffff or position + preload_size > len(tree):
                    raise ValueError('Invalid VPK entry.')
                preload = tree[position:position+preload_size]
                position += preload_size
                key = (directory + '/' if directory != ' ' else '') + name + '.' + extension
                yield key, crc, preload, archive, offset, length
    if position != len(tree):
        raise ValueError('VPK tree length mismatch.')


def list_vpk_entries(index_path, *, prefixes=(), suffixes=(), max_tree_bytes=64*1024*1024,
                     max_entries=200_000):
    """Return matching directory entries without reading any payload.

    ``{virtual_path: (archive, offset, length)}``; ``archive`` is ``0x7fff``
    for payloads stored after the tree in the index file. Directory-only
    entries are skipped. Filters are required so a caller cannot accidentally
    materialise a whole game archive; the tree itself is always walked in full
    so malformed indexes still fail closed.
    """
    index = Path(index_path)
    if not prefixes and not suffixes:
        raise ValueError('Expected a prefix or suffix filter.')
    _header, _tree_size, tree = _read_vpk_tree(index, max_tree_bytes)
    found = {}
    for key, _crc, _preload, archive, offset, length in _iter_vpk_tree(tree):
        if prefixes and not key.startswith(tuple(prefixes)):
            continue
        if suffixes and not key.endswith(tuple(suffixes)):
            continue
        if archive == 0x7fff and length == 0 and not _preload:
            continue
        if key in found:
            raise ValueError('Duplicate named VPK resource.')
        if len(found) >= max_entries:
            raise ValueError('VPK entry limit exceeded.')
        found[key] = (archive, offset, length)
    return found


def read_vpk_entries(index_path, virtual_paths, *, max_tree_bytes=64*1024*1024,
                     max_payload_bytes=16*1024*1024):
    index = Path(index_path)
    requested = tuple(virtual_paths)
    if not requested or len(set(requested)) != len(requested):
        raise ValueError('Expected distinct named VPK resources.')
    for name in requested:
        path = PurePosixPath(name)
        if not isinstance(name, str) or path.is_absolute() or '..' in path.parts or '\\' in name or str(path) != name:
            raise ValueError('Invalid VPK resource path.')
    header_size, tree_size, tree = _read_vpk_tree(index, max_tree_bytes)
    matches = {}
    for key, crc, preload, archive, offset, length in _iter_vpk_tree(tree):
        if key not in requested:
            continue
        preload_size = len(preload)
        if key in matches or length + preload_size > max_payload_bytes:
            raise ValueError('Duplicate or oversized named VPK resource.')
        source = index if archive == 0x7fff else index.with_name(index.name[:-8] + '_' + f'{archive:03d}' + '.vpk')
        start = offset + header_size + tree_size if archive == 0x7fff else offset
        if start + length > source.stat().st_size:
            raise ValueError('Truncated VPK resource payload.')
        with source.open('rb') as stream:
            stream.seek(start)
            payload = preload + stream.read(length)
        if len(payload) != preload_size + length or binascii.crc32(payload) & 0xffffffff != crc:
            raise ValueError('VPK payload CRC mismatch.')
        matches[key] = payload
    if set(matches) != set(requested):
        raise ValueError('Named VPK resource missing: ' + ', '.join(sorted(set(requested)-set(matches))))
    return matches

def read_vpk_entry(index_path, virtual_path, **limits):
    return read_vpk_entries(index_path, (virtual_path,), **limits)[virtual_path]
