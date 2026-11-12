"""Stateless qualification of the installed spritecard atlas UV branch.

Its sheet-lookup path omits the random-offset term used by the ordinary texture
path. This does not recover random inputs for ordinary textures or certify GPU
filtering, constant-buffer packing, all variants or complete Source parity.
"""
from .installed_effects import UnsupportedEffect

POLICY = 'agr_spritecard_sheet_uv_no_random_offset_v1'
SHADERS = {
    'shaders/vfx/spritecard_vulkan_50_features.vcs': '13cdf76158cdb452bc726d5fcd778a167f0e8cdc5daf12b04b5523505d193351',
    'shaders/vfx/spritecard_vulkan_50_vs.vcs': 'b7f92a8201b9867e9a2d06e7c3520aa781b58eeb2120ba7e7a73aa84fbe5b7d3',
    'shaders/vfx/spritecard_vulkan_50_ps.vcs': 'a099be69b84c9435b309216165e75b5917506ceddb45b98d663216286f0981dc',
}


def validate_shader_inputs(proof):
    if (not isinstance(proof, dict) or set(proof) != {'policy', 'sha256'}
            or proof.get('policy') != POLICY or proof.get('sha256') != SHADERS):
        raise UnsupportedEffect('Unqualified spritecard sheet UV shader inputs')
    return proof


def collect_shader_inputs(provider):
    actual = {name: provider.resolve(name).sha256 for name in SHADERS}
    return validate_shader_inputs(dict(policy=POLICY, sha256=actual))


def sheet_random_offset_bypass(renderer, texture, provenance):
    if renderer.get('_class') != 'C_OP_RenderSprites' or texture.get('is_sprite_sheet') is not True:
        return False
    if 'spritecard_sheet_uv' not in provenance:
        # Preserve legacy prepared packages; only freshly qualified assets use
        # the corrected branch. Do not erase an unverified input silently.
        return False
    validate_shader_inputs(provenance['spritecard_sheet_uv'])
    if texture.get('source_sheet') is not None:
        raise UnsupportedEffect('Original animated sheet UV rendering is not integrated')
    sequences = texture.get('sequences')
    if (type(texture.get('schema_version')) is not int or texture['schema_version'] != 1 or texture.get('atlas') is not True
            or not isinstance(sequences, dict) or not 1 <= len(sequences) <= 256
            or set(sequences) != {str(i) for i in range(len(sequences))}
            or any(not isinstance(s,dict) or s.get('status') != 'supported'
                   or not isinstance(s.get('frames'),list) or not 1 <= len(s['frames']) <= 4096 for s in sequences.values())):
        raise UnsupportedEffect('Spritecard sheet UV requires the complete verified sheet sequence inventory')
    return True
