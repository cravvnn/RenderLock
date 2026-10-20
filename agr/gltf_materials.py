"""Opt-in PBR approximations from current VRF glTF VMAT metadata.

Returns a copy, preserving the original asset. This is not Source shader parity:
NPR, self-illumination Fresnel/albedo mixing and cloak passes are not reproduced.
Glass requires explicit calibration because cloak refraction is not physical IOR.
"""
from __future__ import annotations

import copy
import math


def repair_materials(document, *, approximate_glass=False,unlit_only=False):
    result = copy.deepcopy(document)
    report = {'status': 'approximation', 'materials': [], 'limitations': [
        'Source NPR, depth/cloak passes and emission Fresnel/albedo mixing are not reproduced',
        'VMAT tint values are used directly; engine color-pipeline equivalence is unverified']}
    used = set(result.get('extensionsUsed', []))
    for index, material in enumerate(result.get('materials', [])):
        source = material.get('extras', {}).get('vmat', {})
        if source.get('ShaderName') != 'pbr.vfx':
            continue
        flags = source.get('IntParams', {})
        floats = source.get('FloatParams', {})
        vectors = source.get('VectorParams', {})
        changes, warnings = [], []
        if flags.get('F_UNLIT') == 1:
            tint = vectors.get('g_vColorTint1')
            if tint is not None and len(tint) >= 3 and all(math.isfinite(x) and 0 <= x <= 1 for x in tint[:3]):
                pbr = material.setdefault('pbrMetallicRoughness', {})
                # Set, do not multiply: repeated preparation must be idempotent.
                pbr['baseColorFactor'] = list(tint[:3]) + [pbr.get('baseColorFactor', [1, 1, 1, 1])[3]]
                material.setdefault('extensions', {})['KHR_materials_unlit'] = {}
                used.add('KHR_materials_unlit')
                material['extras'].setdefault('agr_material_repairs', {})['unlit_visibility'] = 'camera_reflection_transmission_v1'
                changes.append('unlit and source color tint')
            else:
                warnings.append('Unlit tint absent or invalid; material retained')
        if flags.get('F_SELF_ILLUM') == 1 and not unlit_only:
            scale = floats.get('g_flSelfIllumScale1')
            tint = vectors.get('g_vSelfIllumTint1')
            if (isinstance(scale, (int, float)) and math.isfinite(scale) and scale >= 0
                    and tint is not None and len(tint) >= 3
                    and all(math.isfinite(x) and 0 <= x <= 1 for x in tint[:3])
                    and 'emissiveTexture' in material):
                material['emissiveFactor'] = list(tint[:3])
                material.setdefault('extensions', {})['KHR_materials_emissive_strength'] = {'emissiveStrength': scale}
                used.add('KHR_materials_emissive_strength')
                changes.append('emission texture enabled with source tint and strength')
            else:
                warnings.append('Self-illumination parameters or converted texture absent/invalid; retained')
        if flags.get('F_GLASS') == 1 and not unlit_only:
            if approximate_glass:
                ext = material.setdefault('extensions', {})
                ext['KHR_materials_transmission'] = {'transmissionFactor': 1.0}
                ext['KHR_materials_ior'] = {'ior': 1.08}
                used.update(('KHR_materials_transmission', 'KHR_materials_ior'))
                pbr = material.setdefault('pbrMetallicRoughness', {})
                for key in ('baseColorTexture', 'metallicRoughnessTexture'):
                    pbr.pop(key, None)
                pbr.update(baseColorFactor=[.96, .98, 1, 1], roughnessFactor=.035, metallicFactor=0)
                changes.append('calibrated glass approximation (IOR 1.08, roughness .035)')
                warnings.append('Glass calibration is artistic, not a conversion of Source cloak parameters')
            else:
                warnings.append('Source glass requires explicit approximate_glass selection')
        if changes or warnings:
            report['materials'].append({'index': index, 'name': material.get('name'),
                'source': source.get('Name'), 'changes': changes, 'warnings': warnings})
    if used:
        result['extensionsUsed'] = sorted(used)
    return result, report


def repair_glb(source, output, *, approximate_glass=False,unlit_only=False):
    """Rewrite only JSON; keep every non-JSON chunk byte-for-byte intact."""
    import json
    from pathlib import Path
    import struct

    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source.parent != output.parent:
        raise ValueError('Repaired GLB must be a separate sibling of the source')
    raw = source.read_bytes()
    if len(raw) < 20:
        raise ValueError('Truncated GLB header')
    magic, version, length = struct.unpack_from('<4sII', raw)
    if magic != b'glTF' or version != 2 or length != len(raw):
        raise ValueError('Invalid GLB header')
    chunks, offset = [], 12
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError('Truncated GLB chunk header')
        size, kind = struct.unpack_from('<I4s', raw, offset)
        if size % 4 or offset + 8 + size > len(raw):
            raise ValueError('Invalid GLB chunk bounds')
        chunks.append((kind, raw[offset + 8:offset + 8 + size]))
        offset += 8 + size
    if not chunks or chunks[0][0] != b'JSON' or any(k == b'JSON' for k, _ in chunks[1:]):
        raise ValueError('Expected exactly one leading GLB JSON chunk')
    document, report = repair_materials(json.loads(chunks[0][1]), approximate_glass=approximate_glass,unlit_only=unlit_only)
    encoded = json.dumps(document, separators=(',', ':'), allow_nan=False).encode('utf-8')
    encoded += b' ' * (-len(encoded) % 4)
    chunks[0] = (b'JSON', encoded)
    body = b''.join(struct.pack('<I4s', len(data), kind) + data for kind, data in chunks)
    with output.open('xb') as stream:
        stream.write(struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body)
    return report


def main():
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--approximate-glass', action='store_true')
    args = parser.parse_args()
    source, output = args.input.resolve(), args.output.resolve()
    if source == output or source.parent != output.parent or output.suffix.lower() != '.gltf':
        parser.error('Write a separate .gltf beside the input to preserve relative asset references')
    if output.exists():
        parser.error('Output already exists; choose a new filename')
    result, report = repair_materials(json.loads(source.read_text(encoding='utf-8')),
                                      approximate_glass=args.approximate_glass)
    report.update(input=str(source), output=str(output))
    output.write_text(json.dumps(result), encoding='utf-8')
    output.with_suffix('.materials-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Prepared {len(report["materials"])} material reports: {output}')


if __name__ == '__main__':
    main()
