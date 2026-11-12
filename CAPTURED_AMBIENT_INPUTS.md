# Captured ambient effects

Advanced > Particle simulation has an optional **Captured ambient effects** file.
The ordinary CLI accepts the same input with `--captured-ambient-inputs`.
Leave it empty unless you have matching captured input. It does not launch or
read the game, collect effects automatically or enable partial export for you.

The current input profile is Venator's two ambient sprite systems and two rope
systems in the retained replay clip17920..18240, inclusive, with62earlier ticks.
It requires installed-game particle simulation, model bone rules and explicit
partial export. The original capture pins and byte-exact owner/pose binding are
checked. This is not arbitrary-replay or current-native-reader qualification.

The descriptor is JSON with these exact fields and component order:

```json
{
  "format": "renderlock-captured-ambient-inputs",
  "schema_version": 1,
  "source_scene": {"path": "original-scene.json", "sha256": "<64 lowercase hex digits>"},
  "packages": [
    {"backend": "observed_sprite_frames_v1", "file": {"path": "sprites.package.json", "sha256": "<64 lowercase hex digits>"}},
    {"backend": "observed_rope_frames_v1", "file": {"path": "ropes.package.json", "sha256": "<64 lowercase hex digits>"}}
  ]
}
```

Descriptor references can be relative to its directory. Original package asset
directories currently must remain available at their absolute paths; portable
asset relocation is unfinished. Never rewrite capture hashes or source identity
to make unrelated data fit. A digest identifies bytes, not capture authenticity.

Sprite and rope inputs retain separate capture provenance. They are not assumed
to share a Source clock because their owner motion matches. Arrays hold between
captured ticks without invented particle lifetimes, births or seeds. Geometry is
built for the prepared camera; export again after changing its path. Source
lighting, fog, depth/filtering and pixel equivalence remain incomplete.

The input can coexist with particle observations and the separate captured-slide
input. Those retain their own admission rules. Coverage reports record each
ambient component's reference/source provenance and full array-processing counts;
camera-clipped rope meshes are distinguished from unprocessed arrays. Imported
frame mesh counts do not erase recorded-event or parent/lifecycle limitations.
