# Pending static particle geometry component

`ParticleGeometryProbe --particle-static-model-v1` is a separate command/policy.
It requires no animation, at most one source root and no body selectors. Failed
morph validation never retries this command. Geometry remains material-free.
The existing conversion-cache caller still owns publication; the command itself
has no cache, retry, worker or game lifecycle.

`GeometryExport.tla` composes the synchronous command's argument, destination,
model-kind, exporter and output-bound admission. Its finite model includes
independent command/model kinds and a mutant for failed-morph/static fallback.
Native decoding, skin arithmetic, filesystem races and crash durability remain
outside the model. Typed command metadata is not proof of an identity rest skin.

`particle_model_geometry.read_model_geometry` is a pure bounded transformation,
so it introduces no persistent lifecycle. Its complete GLB checks still apply.
A static single-node mesh may retain the exporter's orphan JOINTS_0/WEIGHTS_0
streams after baking a root. Both streams must be present, have exact vertex
cardinality, zero joint indices and unit root weights. Animation, morph targets,
nonidentity node transforms, incomplete pairs and nontrivial weights refuse.
An actual retained skin still requires the existing complete identity-rest proof.

Current component evidence: installed flashbang sphere exported with 1 source
bone, no animation/body groups, 1,064 vertices and 1,984 triangles. The bounded
reader passes; existing bat morph bytes and metadata are unchanged. Static/morph
command mismatch, body selectors, unsafe paths and occupied outputs refuse.

The candidate now connects the exact pinned sphere renderer/material through
the shared model-asset and Blender paths. It validates complete static geometry,
model/material source hashes, the parsed material program and every sample.
Only the material's COLORTINT expression consumes the captured particle color;
other declared renderer variables are not read by this compiled material.
The Blender graph approximates translucent PBR with authored static UVs, two
opacity masks and facing fade. Packed normal/roughness, Source lighting, fog and
depth behavior remain approximate. An asset-free actual graph fixture passes
36 pixel and 6 animated-color checks per build/reopen at 24/30/60 FPS.

The bounded normal path now imports this sphere as part of the complete original
flashbang. Independent saved checks cover all20 sphere transforms/colors. Actual
geometry build and independent reopen verify1,064 vertices,1,920 visible faces,
all5,760 corner normals and UVs. The source has64 repeated-index triangles: these
zero-surface faces are removed before Blender custom-normal construction, which
otherwise crashes in Blender5.2. No distinct-index face, UV or normal is altered;
mesh.validate is deliberately not used because it can rewrite other topology.
The original1,984-face GLB and its source digest remain unchanged.

An asset-free geometry fixture also builds/saves/reopens repeated-index input.
Full normal saved/rollback and four CPU Cycles frames pass; a separately rebuilt
inspection camera visibly exercises the effect. Material/shading limits above
remain, as do strict whole-instance sibling and projected receiver gates. This
is source qualification, not a rebuilt app or general static-model admission.
