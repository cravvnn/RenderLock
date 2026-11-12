# Captured slide input transport

`read_cohort`, `bind_cohort` and `validate_bound_cohort` are read-only snapshot
and pure admission functions. They own no persistent state, retry, writer,
process, renderer allocation or cache. No additional lifecycle machine is needed.
Later rendering must stay inside the existing package allocation/rollback owner;
this change does not yet add rendering or ordinary CLI dispatch.

The descriptor names one source scene and four ordered component JSON files,
each with a SHA-256 byte digest. Its paths may be relative to the descriptor.
Reading is capped per file and in aggregate, including growing files. Duplicate
keys, nonfinite numbers, excess nesting/nodes, missing components, aliased files,
and replay/reserved-output collisions are rejected. Original asset metadata is
kept unchanged; asset directories currently must be absolute. This is **not** a
portable asset archive or a newly qualified game acquisition path.

The complete snapshot is rechecked on binding. Existing component validators
still own historical native profiles, complete arrays, material and geometry
admission. A separately validated owner binding preserves the historical scene
fingerprint and binds the fresh target. The four components must share the raw
report; sprites, lights and models must share reader/freeze/cleanup provenance
and frame/root counts. The trail's embedded sprite reference must equal the
standalone sprite reference. There is no partial batch return or empty fallback.

Digests detect changes to a snapshot; they are not capture authenticity proofs.
No recorded particle birth or prior unsupported count is removed here. Ordinary
export integration, all-component allocation rollback, a fresh normal scene,
saved-scene checks and short Cycles output remain required acceptance gates.
