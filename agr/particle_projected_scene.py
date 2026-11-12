"""Explicit zero-receiver result for the qualified world-only explosion layer.

This is an exported-scene result, never a claim that the game/map has no decals.
Any map, unknown geometry or object instancing keeps projection unsupported until
its receiving-geometry/material provider is available. Visibility is not used:
an animated hidden object may become visible later in the clip.
"""
from .particle_value_identity import digest

POLICY='agr_projected_world_no_scene_receivers_v1'
RESOURCE='particles/abilities/vampirebat/vampirebat_batswarm_explosion_proj.vpcf'
RENDERER_SHA256='9150887303ea91309fce49a373a9d2c46ffe87574068d561eea343060a55a687'
PROFILES={RESOURCE:RENDERER_SHA256,
 'particles/abilities/priest/priest_flashbang_explosion_ground_proj.vpcf':
 '03f5bdb5cae525ac9c2437bc05b4ade79f46daca2cdc6ea7f851b064ed353f04'}

def validate_profile(resource,renderer):
 # Both exact renderers omit receiver overrides: current typed native defaults
 # exclude characters and select world/water. Depth/material/color differences
 # have no receiving surface only when the complete scene check proves zero.
 if resource not in PROFILES or digest(renderer)!=PROFILES[resource]:
  raise ValueError('Projected layer requires its qualified world/water-only renderer definition')

def no_world_receivers(scene):
 """Inspect every source role, including hidden geometry and instance objects."""
 if scene is None:raise ValueError('Projected layer requires actual scene receiver classification')
 if hasattr(scene,'get') and scene.get('agr_pending_world_receivers'):
  raise ValueError('Projected world geometry is scheduled for map composition; its receiving-surface/material provider is required')
 counts=dict(characters=0,particles=0,modifier_models=0,reference_helpers=0,non_geometry=0)
 for obj in scene.objects:
  if getattr(obj,'instance_type','NONE')!='NONE':
   raise ValueError('Projected layer has unqualified instanced scene geometry: '+obj.name)
  if obj.get('agr_map_geometry'):
   raise ValueError('Projected world geometry requires its material and receiving-surface provider: '+obj.name)
  if obj.type in {'EMPTY','ARMATURE','CAMERA','LIGHT','SPEAKER','LIGHT_PROBE'}:
   counts['non_geometry']+=1;continue
  if obj.type=='CURVE' and obj.get('agr_scene_role')=='reference_grid':
   counts['reference_helpers']+=1;continue
  # Known effect meshes are a particle role, separate from the world geometry
  # selected by these exact projected profiles. Shader alpha is not used to
  # classify receivers; maps/unknowns/instances still refuse above/below.
  if isinstance(obj.get('agr_particle_resource'),str) and obj['agr_particle_resource'].startswith('particles/'):
   counts['particles']+=1;continue
  if obj.get('agr_modifier_mask_backend')=='modifier_attached_models_v1' and isinstance(obj.get('agr_mask_owner'),str):
   counts['modifier_models']+=1;continue
  current=obj;seen=set();entity=None
  while current is not None:
   if current in seen:raise ValueError('Projected receiver hierarchy contains a cycle')
   seen.add(current)
   identity=current.get('agr_entity_id')
   if isinstance(identity,str) and identity:entity=identity;break
   current=current.parent
  if entity is not None:
   counts['characters']+=1;continue
  raise ValueError('Projected layer cannot classify this scene geometry: '+obj.name)
 return dict(policy=POLICY,status='no_world_receivers_in_exported_scene',world_receivers=0,
  excluded_roles=counts,scope='No world geometry is present in this exported scene. The authored renderer excludes characters; game-map decal appearance is not established.')
