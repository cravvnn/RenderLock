# Third-party components and asset provenance

The native replay helper uses Boon 0.15.0, boon-proto, pbdems2, and their Rust dependencies. Exact resolved versions are retained in `native/Cargo.lock`; license notices are in `native/licenses` and described in `native/THIRD_PARTY.md`.

The offline animation implementation uses research and format descriptions from ValveResourceFormat and Esoterica. Applicable MIT notices and references are retained in `agr/vendor/licenses` and `agr/vendor/THIRD_PARTY.md`.

Source installations use `requirements.txt`. The Windows portable distribution bundles Python and its runtime dependencies, including their license files and package metadata. Exact bundled versions are recorded in `release_manifest.json`; Python's notice is at `runtime/LICENSE.txt`, and package notices remain in `runtime/Lib/site-packages`. Native helper notices are also copied to `notices/native` in the portable distribution. Blender is a separate tool. The portable also bundles the Source 2 Viewer CLI, a .NET runtime and the character-look helper tools; see the bundled-tools section below. The normal converter does not need the earlier diagnostic DLL-emulation harness.

The historical prepared model, texture, skeleton and animation assets derive from the user's supplied Deadlock exports. They are not included in this installed-game distribution and are not represented as original AGR code or relicensed under the parser/evaluator licenses.

## 0.1.3 installed-game distribution

This standalone 0.1.3 package excludes the historical `assets/` and game-scene `examples/` directories described above. Source 2 Viewer is installed separately and invoked as an external converter: https://github.com/ValveResourceFormat/ValveResourceFormat . Its executable and supporting libraries are not redistributed in this package. The VPK reader follows the publicly documented archive layout and is exercised against unchanged user-supplied resource bytes in local, unshipped fixtures.

## 0.1.8 entity dependency parsing

The typed binary and KV3 entity-property reader follows [ValveResourceFormat 20.0 EntityLump](https://github.com/ValveResourceFormat/ValveResourceFormat/blob/20.0/ValveResourceFormat/Resource/ResourceTypes/EntityLump.cs) and its StringToken key hashing. The existing ValveResourceFormat MIT notice remains in `agr/vendor/licenses/ValveResourceFormat_MIT.txt`. Validation downloads upstream fixtures separately; no compiled entity fixtures or game assets are included in these ZIPs.

## Map DATA text parsing (0.1.9)

The map text reader is an AGR implementation of the text format emitted by Source 2 Viewer CLI 20. Format references were the upstream ValveKeyValue [writer](https://github.com/ValveResourceFormat/ValveKeyValue/blob/master/ValveKeyValue/ValveKeyValue/Serialization/KeyValues3/KV3TextSerializer.cs) and [reader](https://github.com/ValveResourceFormat/ValveKeyValue/blob/master/ValveKeyValue/ValveKeyValue/Deserialization/KeyValues3/KV3TokenReader.cs). These references were retrieved from the mutable master branch; their exact snapshot hashes are recorded in `validation/0.1.9/source_provenance.json`. Compiled fixture checks independently verified behavior against the official CLI 20 binary. The upstream C# source and compiled game fixtures are not bundled.

## 0.3.0b1 bundled tools

The Windows portable candidate bundles these components under `tools/`:

- **Source 2 Viewer CLI 20.1** (`tools/source2viewer`), from
  https://github.com/ValveResourceFormat/ValveResourceFormat, used as the
  asset converter. MIT License; the notice and the exact executable SHA-256
  are in `tools/source2viewer/NOTICE.txt`, and the MIT text is retained at
  `agr/vendor/licenses/ValveResourceFormat_MIT.txt`.
- **Microsoft .NET runtime** (`tools/dotnet`), shared runtime used to
  execute the helper tools. License and third-party notices are included in
  the same folder.
- **Character-look helper tools** (`tools/lighting`) and the **particle
  simulation helper** (`tools/particles`), built locally from the project's
  own sources against the ValveResourceFormat library.

Replays, game assets and third-party shader libraries remain excluded from
both archives.
