"""One-call particle closure construction; caller owns material rollback.

No new cache, allocation transaction or playback state. Native spritecard output
factor +.5 premultiplies RGB by the full alpha and alpha by .5. Under the primary
VRF reference's ONE/INV_SRC_ALPHA framebuffer equation, a half-alpha mix with
double emission implements the same contribution. This does not prove Source
GPU/framebuffer state or lighting parity. Unsupported output modes still refuse.
"""
from .installed_effects import UnsupportedEffect

ALPHA = 'PARTICLE_OUTPUT_BLEND_MODE_ALPHA'
ADD = 'PARTICLE_OUTPUT_BLEND_MODE_ADD'
HALF_ADD = 'PARTICLE_OUTPUT_BLEND_MODE_HALF_BLEND_ADD'


def build_output_shader(nodes, links, color, alpha, mode):
    if mode not in (ALPHA, ADD, HALF_ADD):
        raise UnsupportedEffect('Unsupported particle output blend: '+str(mode))
    emit = nodes.new('ShaderNodeEmission'); links.new(color, emit.inputs['Color'])
    transparent = nodes.new('ShaderNodeBsdfTransparent')
    if mode == ADD:
        links.new(alpha, emit.inputs['Strength']); shader = nodes.new('ShaderNodeAddShader')
        links.new(transparent.outputs[0], shader.inputs[0]); links.new(emit.outputs[0], shader.inputs[1])
    else:
        shader = nodes.new('ShaderNodeMixShader')
        if mode == HALF_ADD:
            coverage = nodes.new('ShaderNodeMath'); coverage.operation = 'MULTIPLY'
            links.new(alpha, coverage.inputs[0]); coverage.inputs[1].default_value = .5
            links.new(coverage.outputs[0], shader.inputs[0]); emit.inputs['Strength'].default_value = 2.
        else:
            links.new(alpha, shader.inputs[0])
        links.new(transparent.outputs[0], shader.inputs[1]); links.new(emit.outputs[0], shader.inputs[2])
    return shader.outputs[0]
