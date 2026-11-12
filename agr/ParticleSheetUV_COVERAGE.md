# Spritecard sheet UV qualification

`particle_sheet_uv.collect_shader_inputs`, `validate_shader_inputs` and
`sheet_random_offset_bypass` are stateless. They qualify immutable installed
shader identities and the existing complete texture sequence inventory.
`particle_render_assets.prepare_render_assets` adds that qualification to each
new asset packet; `simulated_effect_package.validate_package` checks it again.
`blender_simulated_effects` uses the sheet path without adding the ordinary
texture's substitute random displacement and reports the affected resources.

No RNG state, playback cursor, lifetime transition, retry or new cache is added.
The existing content-verified asset cache and static sequence decoder remain
unchanged. This change needs no new state model. Ordinary texture random offsets
retain their prior substitute behavior and warning. Legacy packets without the
new provenance retain their existing path until regenerated.

The current installed four-layer spritecard VS contains a sheet table lookup
branch and an ordinary texture branch. The latter contains the per-particle
random offset term; the sheet lookup branch does not. The installed features,
vertex and pixel VCS hashes are checked independently. Texture classification
uses the converter's verified sheet sequence inventory, not a filename guess.
This establishes a rendering-path correction, not recovery of native random
seeds, every constant-buffer binding, all shader variants or Source pixel parity.

The retained smoke experiments keep particle simulation frames, alpha and
lifetimes unchanged. Original atlas/crop changes alone leave the artifact;
removing only the sheet's added offset eliminates it while ordinary texture
noise remains. Native resource metadata, rendered comparisons and the normal
frontend are separate evidence. No artificial card-edge fade is introduced.

This integration currently uses complete dense legacy static sequence inventories.
Typed original animated-sheet sampling is a separate unintegrated capability;
an explicit source_sheet packet refuses instead of treating it as a static crop.
The known fresh muzzle smoke profile uses six supported static sequences. Shader
identity validation runs before every supported backend's package dispatch.
Declared null/changed proof cannot become legacy absence; legacy packets which
never declared this provenance retain their old renderer behavior.
