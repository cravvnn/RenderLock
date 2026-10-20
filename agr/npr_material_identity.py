"""Source identities retained by the GLB importer, independent of display names.

These checks supplement the preview checkpoint hashes; they do not qualify
arbitrary material graphs or prove runtime material overrides.
"""


def compiled_resource(value, extension):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError('Expected Source resource path')
    if '\\' in value or ':' in value or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Expected canonical relative Source resource path')
    if value.endswith(extension):
        return value + '_c'
    if value.endswith(extension + '_c'):
        return value
    raise ValueError('Unexpected Source resource extension')


def pbr_material_resource(material):
    vmat = material.get('vmat')
    if vmat is None or vmat.get('ShaderName') != 'pbr.vfx':
        raise ValueError('Expected retained pbr.vfx material metadata')
    return compiled_resource(vmat.get('Name'), '.vmat')


def material_resource(material):
    resource=pbr_material_resource(material)
    if material['vmat'].get('IntParams', {}).get('F_USE_NPR_LIGHTING') != 1:
        raise ValueError('Material does not declare NPR lighting')
    return resource


def evidence_index(rows):
    result = {}
    for row in rows:
        resource = compiled_resource(row['material'], '.vmat')
        if resource in result:
            raise ValueError('Duplicate material evidence: ' + resource)
        result[resource] = row
    return result


def bind_evidence(material, evidence, *, texture_parameter=None):
    resource = material_resource(material)
    if resource not in evidence:
        raise ValueError('Missing exact material evidence: ' + resource)
    row = evidence[resource]
    if texture_parameter is not None:
        texture = material['vmat'].get('TextureParams', {}).get(texture_parameter)
        if compiled_resource(texture, '.vtex') != compiled_resource(row.get('resource'), '.vtex'):
            raise ValueError('Material texture resource differs from evidence')
    return row


def join_rim_resources(mask_rows, texture_rows):
    """Join qualified constant-mask evidence to decoded source resource identities.

    Caller must authenticate both receipts. A hash here links their assertions;
    it does not independently verify the underlying VPK bytes or decoding.
    """
    import math
    masks, textures = evidence_index(mask_rows), evidence_index(texture_rows)
    if masks.keys() != textures.keys():
        raise ValueError('Rim material coverage differs between receipts')
    result = []
    for material, mask in masks.items():
        texture = textures[material]
        if mask['source_sha256'] != texture['sha256']:
            raise ValueError('Rim source hash differs between receipts')
        value = mask['linear_rim_mask']
        if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Invalid constant rim mask')
        result.append(dict(mask, material=material,
                           resource=compiled_resource(texture['resource'], '.vtex')))
    return result
