"""Offline-qualified layout/tool identities; old saved packages retain their layout."""
from pathlib import Path
from .asset_converter import ConversionError, sha

WORLD_6774 = '4459031ca1fe3900c7c4516a4db552eb330369895b5b3aa228157695e5a0485b'
WORLD_6779 = '4c9948bc4dc097f99273d3077d512b2a522078808090de05c9394601aae7f8ec'
WORLD_6793 = '42872bbdc8839867fc05a4b80655c484e40f0d4fe5408dd782c2917a757c6509'
VRF_6779 = '53988fbf910caf54860479a9150545632f5ba3e10e7de5f50f294c2226311c35'
VRF_20_1 = 'feb9f96d1313661e337087a6187e2411a6464f02d1f8567ae3add7822a9a1e56'
HELPERS_6779 = {
    'ProbeAtlasDecode.dll': 'd169d1344f977379d52265e9e1dfeed4c3eece098a6d3b989e2b6022076ed746',
    'CubemapDecode.dll': '3eef46936a9247b0812d5a62989dccf0e9be275496badd3e15353fb0588d7b9c',
}
# Built against the VRF 20.1 library for map data from the October 2026
# Deadlock update; the same source with the new ValvePak/API surface.
HELPERS_20_1 = {
    'ProbeAtlasDecode.dll': '38aa17b2f064a26b71f6120fd0eb4eeff721b0fe00c19bf3f54e51e68f5a6a4a',
    'CubemapDecode.dll': 'ddf9b458f85dd155d766a728229322573635a7d2b71aa46b0bd2619179ee1876',
    'TinyBCSharp.dll': '5e71c5b4d9ee0788a5ed22601efb054d499ff01a8ebec9f6083a1cdd4a957227',
    'SkiaSharp.dll': '294300b156bb12805ec75f7b85ad454887de5448cbbb3432ebbcbeaae10036f2',
    'libSkiaSharp.dll': '0d98e29c028b3315d0e0344d02cd7aa4080abdf17fa67086847da13435320f2a',
    'ValveResourceFormat.dll': VRF_20_1,
}


def probe_layout(world_sha256):
    if world_sha256 == WORLD_6774:
        return (240, 224, 256), 232
    if world_sha256 == WORLD_6779:
        return (240, 236, 256), 191
    if world_sha256 == WORLD_6793:
        # October 2026 update (ClientVersion 6793): atlas 244x236 with a
        # 236-slice face stride, cubemap array depth 168; see the shader-chain
        # validation receipts for the header evidence.
        return (244, 236, 236), 168
    raise ConversionError('World probe layout requires requalification')


def qualified_probe_helpers(probe, legacy):
    """Require either the complete old kit or the exact locally verified new kit."""
    probe = Path(probe)
    current = sha(probe)
    required = dict(legacy)
    if current == HELPERS_6779.get(probe.name):
        required[probe.name] = HELPERS_6779[probe.name]
        required['ValveResourceFormat.dll'] = VRF_6779
    elif current == HELPERS_20_1.get(probe.name):
        for name, digest in HELPERS_20_1.items():
            if name in required or name == probe.name:
                required[name] = digest
    for name, digest in required.items():
        path = probe.parent / name
        if not path.is_file() or sha(path) != digest:
            raise ConversionError('Unqualified probe decoder companion: ' + name)
    return required
