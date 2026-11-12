"""Bounded paired-sheet motion vectors from the reference spritecard shader.

The raw green/alpha channels are signed flow. They displace the current and
next color samples before crossfade, never enter color composition themselves.
This is a reference response; native shader/texture filtering parity is separate.
Qualification and construction are covered by MotionVectorSheet.tla. All values
come from immutable sheet metadata and existing per-tick particle samples.
"""
import math
import re
from .installed_effects import UnsupportedEffect
from .particle_texture_animation import validate_sheet

KIND='SPRITECARD_TEXTURE_ANIMMOTIONVEC'
POLICY='agr_reference_paired_sheet_motion_vectors_v1'
METADATA_POLICY='agr_source_motion_vector_metadata_v1'

def motion_vector_pair(layers,textures,renderer):
    from .particle_texture_layers import enabled_texture_layers
    enabled=enabled_texture_layers(layers)
    motion=[(i,t)for i,t in enabled if t.get('m_nTextureType')==KIND]
    if not motion:return None
    if (len(motion)!=1 or len(enabled)!=2 or enabled[1]!=motion[0]
            or enabled[0][1].get('m_nTextureType','SPRITECARD_TEXTURE_COLOR')!='SPRITECARD_TEXTURE_COLOR'
            or any(t.get('m_bReplaceTextureWithGradient')or not t.get('m_hTexture')for _,t in enabled)
            or renderer.get('m_bBlendFramesSeq0',True)is not True
            or type(renderer.get('m_bMaxLuminanceBlendingSequence0',False))is not bool
            or renderer.get('m_bMaxLuminanceBlendingSequence0',False)):
        raise UnsupportedEffect('Motion vectors require one base color sheet and one paired flow sheet with frame blending')
    sheets=[]
    for _,layer in enabled:
        sheet=textures.get(layer['m_hTexture'],{}).get('source_sheet')
        sheets.append(validate_sheet(sheet))
    base,flow=sheets;metadata=flow.get('motion_vectors',{})
    if (any(not isinstance(s.get(k),str)or not re.fullmatch('[0-9a-f]{64}',s[k])for s in sheets for k in('input_sha256','image_sha256'))
            or not isinstance(metadata,dict)or metadata.get('policy')!=METADATA_POLICY or metadata.get('metadata_present')is not True
            or type(metadata.get('max_distance'))is not int or not -32768<=metadata['max_distance']<=32767
            or not isinstance(metadata.get('metadata_sha256'),str)or not re.fullmatch('[0-9a-f]{64}',metadata['metadata_sha256'])
            or any(type(metadata.get('source_'+axis))is not int or not 1<=metadata['source_'+axis]<=16384
                   or metadata['source_'+axis]!=flow.get(axis)for axis in('width','height'))):
        raise UnsupportedEffect('Missing or unqualified original motion-vector metadata/dimensions; regenerate sheets')
    def timing(sheet):return [(s['clamp'],s['total_time'],[f['duration']for f in s['frames']])for s in sheet['sequences']]
    if timing(base)!=timing(flow):
        raise UnsupportedEffect('Motion-vector and color sheet clocks/frame counts differ')
    return dict(base_index=enabled[0][0],base=enabled[0][1],index=motion[0][0],layer=motion[0][1],sheet=flow,
                scale=(metadata['max_distance']/metadata['source_width'],metadata['max_distance']/metadata['source_height']))

def reference_flow(rgba,scale,fraction):
    if (len(rgba)!=4 or len(scale)!=2 or any(type(x)not in(int,float)or not math.isfinite(x)for x in(*rgba,*scale,fraction))
            or not 0<=fraction<=1):raise UnsupportedEffect('Invalid motion-vector sample')
    # Return a displacement in Blender's atlas UV coordinates, whose V axis
    # opposes the Source texture V axis. Current subtracts; next adds.
    return ((rgba[1]*2-1)*scale[0]*fraction,-(rgba[3]*2-1)*scale[1]*fraction,0.)

def build_motion_offsets(nodes,links,image,scale):
    from .particle_texture_layers import LayerNodes
    graph=LayerNodes(nodes,links);op=graph.op
    first=getattr(image,'current_sampler',None);second=getattr(image,'next_sampler',None)
    fraction=getattr(image,'blend_fraction',None)
    if first is None or second is None or fraction is None:
        raise UnsupportedEffect('Motion-vector sheet must retain separate current/next samplers and fraction')
    def flow(sampler,weight):
        rgba=graph.rgba(sampler)
        return graph.vector((op('MULTIPLY',op('MULTIPLY',op('SUBTRACT',op('MULTIPLY',rgba[1],2),1),scale[0]),weight),
                             op('MULTIPLY',op('MULTIPLY',op('SUBTRACT',op('MULTIPLY',rgba[3],2),1),-scale[1]),weight),0))
    return flow(first,fraction),flow(second,op('SUBTRACT',1,fraction))
