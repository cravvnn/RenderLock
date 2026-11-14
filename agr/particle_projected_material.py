"""Decoded inputs for projected particle layers (C_OP_RenderProjected): the decal material's colour texture.

Review scope: only the projected_decals shader's colour texture and opacity are read. Normal, roughness and
self-illumination inputs of the game decal are not used.
"""

SHADER = 'projected_decals.vfx'


def collect_projected_materials(definitions, decoder, adapter, provider):
    """Return {material resource: descriptor}; an unreadable material is recorded, never substituted."""
    result = {}
    for name in sorted(definitions):
        for renderer in definitions[name].get('m_Renderers', []):
            if renderer.get('_class') != 'C_OP_RenderProjected':
                continue
            for row in renderer.get('m_vecProjectedMaterials', []):
                material = row.get('m_hMaterial') if isinstance(row, dict) else None
                if not isinstance(material, str) or not material or material in result:
                    continue
                try:
                    data = decoder.dump(material, provider)
                    if data.get('m_shaderName') != SHADER:
                        raise ValueError('unsupported projected shader ' + str(data.get('m_shaderName')))
                    textures = {t['m_name']: t['m_pValue'] for t in data.get('m_textureParams', [])}
                    floats = {t['m_name']: t['m_flValue'] for t in data.get('m_floatParams', [])}
                    colour = textures.get('g_tColor')
                    if not isinstance(colour, str) or not colour:
                        raise ValueError('projected material has no colour texture')
                    directory, descriptor = adapter.texture(colour, provider)
                    frames = descriptor['sequences']['0']['frames']
                    if len(frames) != 1:
                        raise ValueError('animated projected colour textures are not handled')
                    result[material] = dict(status='supported', shader=SHADER, colour_texture=colour,
                        directory=str(directory), image=frames[0]['image'], sha256=frames[0].get('sha256'),
                        opacity=float(floats.get('g_flOpacityScale', 1.0)) * float(floats.get('g_flAlbedoOpacity', 1.0)))
                except Exception as exc:  # recorded per material; the layer is then reported as not drawn
                    result[material] = dict(status='unsupported', reason=str(exc)[:300])
    return result
