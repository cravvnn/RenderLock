"""Apply a validated single-frame rim stage to caller-owned NPR materials.

Call immediately before rendering an unchanged baseline frame; do not save this
as an animation-ready rim stage. Geometry/visibility edits are not tracked.
"""
import numpy as np
from .blender_rim_depth import load_current_depth,current_identity
from .blender_rim_projection import create_projection_group
from .blender_rim_depth_lookup import create_depth_lookup_group
from .blender_npr_rim import create_rim_group
from .blender_material_emission import add_converted_emission
from .npr_material_identity import material_resource


def assert_rim_frame(scene_path,coordinates,contract):
    if current_identity(scene_path,coordinates)!=contract:
        raise ValueError('Frame-specific rim projection changed; recapture/reapply required')


def apply_frame_rim(scene_path,coordinates,depth_path,manifest_path,materials,masks,*,allow_partial=False):
    if allow_partial is not True:raise ValueError('Explicit partial frame rim required')
    materials=list(materials)
    if not materials or len(set(materials))!=len(materials):raise ValueError('Distinct NPR materials required')
    if {material_resource(m) for m in materials}!=set(masks):raise ValueError('Exact rim material mask coverage required')
    plans=[]
    for mat in materials:
        mask=masks[material_resource(mat)]
        if np.ndim(mask) or not np.isfinite(mask) or not 0<=mask<=1:raise ValueError('Finite constant rim mask required')
        nodes=mat.node_tree.nodes
        if nodes.get('AGR frame rim') is not None:raise ValueError('Frame rim already applied')
        npr=nodes.get('AGR NPR indirect preview');probe=nodes.get('AGR hero probes')
        bsdfs=[n for n in nodes if n.type=='BSDF_PRINCIPLED']
        outputs=[n for n in nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
        if npr is None or probe is None or len(bsdfs)!=1 or len(outputs)!=1:
            raise ValueError('Prepared NPR graph required')
        if len(probe.inputs['Position'].links)!=1 or any(len(npr.inputs[k].links)!=1 for k in ('Normal','View','Material AO')):
            raise ValueError('Prepared NPR position/material links required')
        surface=outputs[0].inputs['Surface']
        if len(surface.links)!=1 or surface.links[0].from_node.type!='EMISSION':raise ValueError('NPR emission surface required')
        plans.append((mat,npr,probe,bsdfs[0],surface.links[0].from_node,mask))
    depth,contract=load_current_depth(scene_path,coordinates,depth_path,manifest_path)
    import bpy
    height,width=depth.shape
    image=bpy.data.images.new(f"Frame{contract['frame']} axial Source depth",width=width,height=height,alpha=True,float_buffer=True)
    image.colorspace_settings.name='Non-Color';image.alpha_mode='CHANNEL_PACKED'
    pixels=np.ones((height,width,4),dtype='f4');pixels[:,:,:3]=depth[::-1,:,None]
    image.pixels.foreach_set(pixels.reshape(-1));image.pack()
    projection=create_projection_group(world_to_clip=contract['source_row_matrix'],sample_distance=.5,translation=[0,0,0,0],uv_scale=[1,1])
    lookup=create_depth_lookup_group(image)
    rimtree=create_rim_group(wrap=.7,falloff=.7,strength=2,up_ramp=[.2,.2],equal_ramp_policy='strict_step_gpu_reference',depth_enabled=True,tolerance_min=2.5,tolerance_range=.5)
    audits=[]
    for mat,npr,probe,bsdf,emit,mask in plans:
        nodes,links=mat.node_tree.nodes,mat.node_tree.links
        project=nodes.new('ShaderNodeGroup');project.node_tree=projection
        links.new(probe.inputs['Position'].links[0].from_socket,project.inputs['Position']);links.new(npr.inputs['Normal'].links[0].from_socket,project.inputs['Normal'])
        depths=nodes.new('ShaderNodeGroup');depths.node_tree=lookup
        for name in ('Current UV','Sample UV'):links.new(project.outputs[name],depths.inputs[name])
        rim=nodes.new('ShaderNodeGroup');rim.node_tree=rimtree;rim.name='AGR frame rim'
        for name in ('Normal','View','Material AO'):links.new(npr.inputs[name].links[0].from_socket,rim.inputs[name])
        for name in ('Current Depth','Sampled Depth'):links.new(depths.outputs[name],rim.inputs[name])
        links.new(npr.outputs['Irradiance'],rim.inputs['Indirect']);rim.inputs['Direct'].default_value=(0,0,0);rim.inputs['Mask'].default_value=mask
        base=add_converted_emission(nodes,links,bsdf,npr.outputs['Total'])
        total=nodes.new('ShaderNodeVectorMath');total.operation='ADD';links.new(base,total.inputs[0]);links.new(rim.outputs['Rim'],total.inputs[1]);links.new(total.outputs[0],emit.inputs['Color'])
        audits.append(dict(material=mat,project=project,depths=depths,rim=rim,npr=npr,total=total,base=base))
    return contract,audits
