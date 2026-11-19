"""Identity of the installed game's particle module.

Several effect inputs (rope seed/blend parameters, default light arithmetic,
texture-control constructor defaults) were measured on specific builds of
particles.dll. On a build not listed here the same measured values are used and
the effect is labelled: nothing about the new module has been checked. Add a
digest to VERIFIED only after the build verification pass.
"""
from pathlib import Path

VERIFIED = {
    '5d5e29757162d86a4bb940c0f0b0189fbf12598d4238a7d27ec4082e9740ff7b': 'Deadlock client 6860',
    '46ae70beaef7935e27794d2148406407605994b59fa5ff19f9a37c8ba68c129f': 'Deadlock client 6861',
    '424aa9b6c5f5458108de4f4fa71852cf0bf9f5fa22fe1849710ce0e3ed3837f5': 'Deadlock client 6864',
}
RELATIVE_PATH = 'game/bin/win64/particles.dll'
NOTE = ('native particle defaults were measured on an earlier game build '
        'and are not verified against the installed one')


def installed_digest(provider):
    """SHA-256 of the installed particle module, or None when it is absent."""
    from .asset_converter import sha
    module = Path(provider.install_path) / RELATIVE_PATH
    return sha(module) if module.is_file() else None


def is_digest(value):
    return (isinstance(value, str) and len(value) == 64
            and all(character in '0123456789abcdef' for character in value))


def is_verified(digest):
    return digest in VERIFIED


def unverified_digests(provenance):
    """Digests in a prepared effect's provenance that are not a verified build."""
    found = set()
    for key in ('native_rope_shader', 'native_standard_light', 'texture_control_defaults'):
        row = provenance.get(key) if isinstance(provenance, dict) else None
        digest = row.get('particles_sha256') if isinstance(row, dict) else None
        if is_digest(digest) and not is_verified(digest):
            found.add(digest)
    return found


def status(provider):
    digest = installed_digest(provider)
    return dict(path=RELATIVE_PATH, sha256=digest, verified=is_verified(digest),
                build=VERIFIED.get(digest),
                effect='ropes, particle lights and extended sprite layers' if digest else None,
                behaviour=('verified' if is_verified(digest) else
                           'built with values measured on an earlier build; labelled unverified' if digest else
                           'unavailable; these effect layers are refused'))
