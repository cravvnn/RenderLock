# Authored particle bodygroups

`particle_model_selection.py` projects independently resolved subtype0 bodygroup
arguments through qualified source inventories and primitive bindings. Native
float32 truncation and bounded group/choice behavior are preserved; the selected
mask replaces that body's bits while preserving other groups. This is a pure
calculation, with no new cache, task, replay lifecycle, persistence or retries,
so no additional TLA+ lifecycle model is required.

The native qualification executes actual particle-worker argument conversion and
the complete scene-model bodygroup setter, with authored inventory values in
private runtime-layout records. Tests exercise reordered geometry, independent
groups, invalid selectors, float32 rounding and changed/ambiguous metadata.

This does not resolve disabled attribute values or qualify other submodel types,
resource initialization, interface initialization, runtime attachments, material
controls, activation, or rendering. Callers must establish these separately.

`particle_model_bindings.authored_material_slot_document` is likewise a pure
JSON annotation of fingerprint-verified geometry. It preserves accessors and
binary geometry, retaining original VMAT/tint identity per primitive. Its import
placeholders must all be replaced with qualified materials before scene admission.
