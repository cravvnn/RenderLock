# Recorded world effect retention

`ParticleWorldSelection.tla` models `compile_particle_timeline` through two
generations of one reused control index, including late primary/secondary entity
bindings, release, immediate old destruction and final selection. `otherSerial`
includes an unselected/reused full entity handle. World retention cannot invent
an owner or admit an effect whose controls involve another entity.

The schema/type checks in `is_world_custom_origin_candidate` are pure guards,
covered by Python cases and real replay messages. The model abstracts their
result as `worldRoot`. Raw decoding, Source simulation, lifetime bounds and actual
Blender output are separate qualified interfaces; this marker admits none of them.
The existing owned provider validation remains strict. `scene_extensions` opts
into retention, and `build_recorded_effects` explicitly reports unqualified world
providers rather than silently dropping their instances. The synchronous capture
does not add a resource owner or async job.

Simplification: derive scope from the existing creation/handle sets after the
event loop. No extra mutable selected-world set or per-index lifecycle is added.
State counts and mutant results are retained with the case qualification.
