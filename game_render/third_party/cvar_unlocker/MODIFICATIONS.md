# Bundled cvar unlocker modifications

- `unlocker-build-6865.patch` (apply after 6864): adds the exact reviewed 6865
  server paired with the unchanged 6864 Tier0 binary and its relocated Config001
  table and methods (`0x2b9f178`; slots `0x9abdc4`/`0x9abdd0`/`0x9b18b0`). The
  fourteen adapter slots (`cvar_slots_6864.inc`), earlier accepted pairs,
  secure-mode rejection and owned-command shutdown behavior remain intact. Three
  compiled ABI/lifecycle suites, 1221 actual-function pair checks, the 6864
  predecessor rejection and the static-CRT/export/Tier0 import audit pass. This
  is offline proof; live build 6865 acceptance is pending.

- `unlocker-build-6864.patch` (apply after6861), with `cvar_slots_6864.inc`:
  adds the exact reviewed 6864 server/Tier0 pair and its relocated Config001
  table and methods. The fourteen adapter methods kept their RVAs. Earlier
  accepted pairs, secure-mode rejection and owned-command shutdown behavior
  remain intact. Three compiled ABI/lifecycle suites, 1212 actual-function pair
  checks, the 6861 predecessor rejection and the static-CRT/export/Tier0 import
  audit pass. Bounded build6864 startup, seeking, camera/path and short
  Color/Depth/Players export sessions also pass; this does not claim exhaustive
  runtime or driver qualification.

- `unlocker-build-6861.patch` (apply after6860), with `cvar_slots_6861.inc`:
  adds the exact reviewed server/Tier0 pair and relocated Config001 methods.
  Fourteen adapter methods were independently reviewed. Existing accepted pairs,
  secure-mode rejection and owned-command shutdown behavior remain intact.
  Three compiled ABI/lifecycle suites,1203 actual-function pair checks, the
  predecessor rejection, static-CRT/export/Tier0 import audit pass. Bounded
  build6861 startup, seeking, camera/path and short Color/Depth/Players export
  sessions also pass. The historical Steam networking shutdown assertion remains
  visible; this does not claim exhaustive runtime or driver qualification.

- `unlocker-build-6806.patch` (apply after6805): local identifier
  `v0.5.2-dolly-build-6806`, retaining upstream v0.5.2 and pinned source/SDK
  provenance. Adds only the reviewed server6806/Tier0 exact pair; the named
  Source2ServerConfig001 secondary+8 table and Connect/Disconnect/GetTickInterval
  ABI remain reviewed, with all earlier pairs and shutdown guards retained.
  Three compiled ABI/lifecycle tests and512 actual gate/table checks pass; the
  current-public predecessor refuses the new pair. The static-CRT candidate
  retains its two interface exports and imports only available Tier0 symbols.
  This is offline proof; no6806 live startup/export/shutdown acceptance is claimed.
  Dolly's existing isolated Early Development warning bool remains specific to
  that stock notice; it does not skip hero announcements or weaken startup gates.

The bundled `bin/win64/server.dll` is a locally built modification of
[cvar-unhide-s2-citadel v0.5.2](https://github.com/Artemon121/cvar-unhide-s2-citadel),
which is MIT licensed (see `LICENSE.md`). The upstream project is not
affiliated with Deadlock Dolly and does not endorse this build.

## Why

The upstream plugin registers two of its own console commands during
`Connect` but never removes them on `Disconnect`. During process teardown, the
game's command registry can then reach a command object whose SDK reference was
already invalidated, producing an access violation on normal game exit.

## Changes

- `unlocker-build-6785.patch` (apply after the 6782 patch): adds the reviewed
  build6785 server and its configuration interface. Builds6782 and6785 use
  the same tier0 binary, so both exact server pairs remain supported. Unknown
  and crossed pairs are refused. Existing secure-mode and shutdown code is
  unchanged. Offline checks passed; live acceptance is recorded separately.

- `unlocker-build-6753.patch` (apply after the 6746 patch): adds the exact
  October5 server hash; the reviewed Source2ServerConfig001 secondary table
  moved +0x4240 while all three method bodies and the this-8 thunks are
  unchanged from 6746. Unknown module identities remain refused. Import,
  offline ABI and lifecycle checks pass; build6753 live acceptance pending.

- `unlocker-build-6746.patch` (apply after the 6745 patch): adds the exact
  October4 server hash; the reviewed Source2ServerConfig001 secondary table
  moved +0x1000 while all three method bodies and the this-8 thunks are
  unchanged from 6745. Unknown module identities remain refused. Import,
  offline ABI and lifecycle checks pass; build6746 live acceptance pending.

- `unlocker-build-6745.patch` (apply after the 6742 patch): adds the exact
  October2 hotfix server hash; the reviewed Source2ServerConfig001 vtable and
  all three method bodies are pure relocations of 6742 (slots +0x70). Connect
  and Disconnect keep the this-8 thunk ABI and the tick method still returns a
  float. Unknown module identities remain refused. Import, offline ABI and
  lifecycle checks pass; build6745 live acceptance pending.

- `unlocker-build-6742.patch` (apply after the 6739 patch): adds the exact
  October2 hotfix server hash; the reviewed Source2ServerConfig001 vtable and
  all three method bodies are unchanged from 6739. Connect/Disconnect keep the
  this-8 thunk ABI and the tick method still returns a float. Unknown module
  identities remain refused. Import, offline ABI and lifecycle checks pass;
  build6742 live acceptance pending.

- `unlocker-build-6739.patch` (apply after the 6731 patch): adds the exact
  October2 server hash and its relocated Source2ServerConfig001 vtable and
  methods (all three reviewed method bodies relocate byte-identically).
  Connect/Disconnect keep the this-8 thunk ABI and the tick method still
  returns a float. Unknown module identities remain refused. Import, offline
  ABI and lifecycle checks pass; build6739 live acceptance pending.

- `unlocker-build-6731.patch` (apply after the 6728 patch): adds the exact
  October1 server hash and its relocated Source2ServerConfig001 vtable and
  methods. Connect/Disconnect keep the this-8 thunk ABI and the tick method
  still returns a float. Unknown module identities remain refused. Import,
  offline ABI and lifecycle checks pass; build6731 live acceptance pending.

- `unlocker-build-6730.patch` (apply after the 6728 patch): adds the exact
  October1 server hash and its relocated Source2ServerConfig001 vtable and
  methods. Connect/Disconnect keep the this-8 thunk ABI and the tick method
  still returns a float. Unknown module identities remain refused. Import,
  offline ABI and lifecycle checks pass; build6730 live acceptance pending.

- `unlocker-build-6728.patch` (apply after the 6726 patch): adds the exact
  October1 server hash and its relocated Source2ServerConfig001 vtable and
  methods. Connect/Disconnect keep the this-8 thunk ABI and the tick method
  still returns a float. Unknown module identities remain refused. Import,
  offline ABI and lifecycle checks pass; build6728 live acceptance pending.

- `unlocker-build-6723.patch` (apply after the 6722 patch): adds the exact
  September30 server hash and reviewed secondary configuration interface.
  Connect/Disconnect retain their this-8 thunk ABI and the tick method still
  returns a float. Unknown module identities remain refused. The current
  tier0 imports, offline ABI and lifecycle tests pass; build6723 live startup
  and replay/shutdown acceptance are pending.

- `unlocker-build-6722.patch` (apply after the 6712 patch): retain the exact
  ICvar adapter and add the reviewed hotfix server hash/config vtable tuple.
  Version6722 preserves the Connect/Disconnect this-8 thunk ABI and float tick
  interval method. Unknown server hashes still refuse before registration.
  Dolly also checks the editing profile's server fingerprint before mounting,
  so a future server-only update produces a launcher error before game startup.

- `unlocker-shutdown.patch` (upstream commit
  `505547d58e4b66001406a8fa618548364f7cdb4d`): track a successful `Connect`,
  avoid registering after a failed one, hook the server-config `Disconnect` to
  unregister the two commands owned by this exact source while `ICvar` is still
  connected, invalidate those same objects, and pair `ConVar_Unregister` and
  `DisconnectInterfaces` with startup.
- `sdk-disconnect.patch` (alliedmodders `hl2sdk` commit
  `7ae489c1722dfb117ff080d6403d85e01e9c53c9`): `DisconnectInterfaces` clears
  the pointed-to interface storage instead of overwriting its own bookkeeping
  pointer.

- `unlocker-build-6712.patch` (apply after the source shutdown patch): adapt the
  pinned SDK to Deadlock build 6712's changed ICvar layout. Only the unlocker
  DLL's own interface pointers use the adapter; the engine's ICvar object and
  vtable remain intact. Exact tier0 and server hashes, the server-config vtable,
  and the used ICvar target addresses must match before registration. The
  existing reviewed legacy tier0 builds retain their original SDK path.
- The same patch removes a mandatory import of the deleted optional
  `g_bUpdateStringTokenDatabase` export. Legacy builds resolve their original
  flag by name; the reviewed new build disables that optional debug database
  tracking. Token hashing and cvar definitions are unchanged.
- Preserve the owned-command shutdown fix and secure-mode rejection; reject
  unknown layouts, clean up after adapter failure, avoid hooking repeated
  requests for the same server-config object, and protect the exact vtable
  slot address when installing the existing config hooks.

The plugin retains the same cvar unhiding operation. Camera and rendering
behavior are unchanged. Static layout facts are deliberately tied to the
reviewed module hashes; they are not a general compatibility fallback.

## Build

Built from the patched sources with MSVC 19.38.33134.0, x64 Release, static
CRT, C++20, with MASM for the adapter tail transfers, against the pinned SDK
commit above. The result is pinned by
SHA-256 in `THIRD_PARTY.json` and verified by the launcher and native bridge
before use. The unmodified upstream release binary hash is recorded there for
provenance.

Apply `unlocker-shutdown.patch` to the pinned upstream source and
`sdk-disconnect.patch` to its pinned SDK first, then apply the build patches
in the order listed in `THIRD_PARTY.json`, through `unlocker-build-6785.patch`.
The 6712 patch includes `CMakeLists.txt`
and offline tests. With the SDK at `sdk/`:

```text
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release --parallel 1
ctest --test-dir build -C Release --output-on-failure
```

The resulting `build/Release/unlocker_shutdown_candidate.dll` is bundled as
`bin/win64/server.dll`. Compiler/linker output is validated before the manifest
and launcher/native hash pins are updated; never change pins to accept an
unreviewed binary.

Offline build, actual assembled adapter ABI tests, synthetic shutdown/lifecycle
tests and an installed-file PE import/export audit passed for this candidate.
Build 6712 startup, command registration and game shutdown remain pending a
bounded Dolly-owned replay test. No live compatibility claim follows from the
offline results.

## License

The upstream MIT license and copyright notice are preserved in `LICENSE.md`.

- `unlocker-build-6726.patch` follows6723. Adds the exact6726 tier0/server pair and secondary interface tuple. Unknown or mixed6726 pairs fail closed; existing reviewed builds retain their paths.

## Build 6801 complete shim and qualification

The current MIT-licensed compatibility shim is included as `build_compat.cpp`.
Its exact source SHA-256 is
`21c9faa03b7c992763fb139aa423fc5a6867d47d1b1d19313637ba57ed85fbdd`.
This is plugin source, not a Valve game binary. It adds the independently
reviewed 6801 tier0/server pair and configuration-interface tuple while
preserving earlier guards, the ICvar adapter and owned-command teardown.

For source reconstruction, use the upstream and SDK commits pinned above,
retain `unlocker-shutdown.patch`, `sdk-disconnect.patch` and the existing build
patches through 6793 for the supporting adapter/CMake sources, then replace
their `build_compat.cpp` with this complete file. Keep all historical patches,
including `unlocker-build-6796.patch` and `unlocker-build-6801.patch`, as change
records. The 6796 file describes a hash substitution rather than a standard
replayable unified hunk; the complete shim avoids requiring that old step.
Do not apply the 6796/6801 incremental changes again after copying the complete
shim. Verify the source hash above before building.

The qualified build used MSVC 19.38.33134.0, toolset v143 14.38.33130,
Windows SDK 10.0.22621.0, MASM, C++20 and the static CRT, against SDK commit
`7ae489c1722dfb117ff080d6403d85e01e9c53c9`. With the patched SDK at `sdk/`,
the established CMake build can be selected explicitly:

```text
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 -T "v143,version=14.38.33130" -DCMAKE_SYSTEM_VERSION=10.0.22621.0
cmake --build build --config Release --parallel 2
ctest --test-dir build -C Release --output-on-failure
```

Qualification built this exact shim with the retained supporting sources and
patched pinned SDK; a fresh upstream checkout/reconstruction was not repeated
for this update. `shutdown_lifecycle_tests` and `cvar_adapter_tests` passed.
The additional maintainer `pair_tests` harness passed 462 checks using the
actual build/configuration guard functions, including historical and unknown
module pairs, both optional-export states, and corrupted/null configuration
tables. Its `pair_tests_legacy_mutant` control failed the intended new-pair
admission case. These two extra harness targets are qualification fixtures,
not targets supplied by the historical upstream CMake patch.

Offline review also verified the named server configuration class, all three
complete configuration methods and all 14 complete ICvar adapter function
families. The built DLL's PE imports/exports passed: x64, static CRT, both
`CreateInterface` exports, and tier0 imports present in the complete installed
export table. These results do not establish 6801 live startup, replay,
rendering or shutdown behavior; those require a separate bounded Dolly-owned
test. Keep the binary hash and launcher pins tied to the reviewed build.

## Build6805 exact pair

Adds the reviewed6805 server to the existing6801 Tier0 family without changing historical pair/table guards. Config001 named registration, constructor-owned instance, secondary interface and complete lifecycle bodies are qualified separately from camera profiles. Fresh static-CRT build,487 actual-function pair/table checks, three ABI/lifecycle tests and import/export audit pass offline; in-game results are recorded separately.


## Build6807 exact pair

The current complete compatibility source `build_compat.cpp` has SHA-256
`7fbf2c6cfa316e569946c0453ac6ff6cb7179d000d42c3639f44842011099ed1`. Additive `unlocker-build-6807.patch` records the change from the retained6806 shim. All earlier pairs, table guards, adapter/lifecycle sources and upstream v0.5.2/SDK provenance remain. The actual named Config001 secondary+8 table and three tuple entries retain their reviewed values; relocated registration, constructed object, registry and complete consumers were requalified.

One private MSVC/static-CRT build and all three owning ABI/lifecycle/pair tests passed, including537 actual-function pair/table checks and an intended failing exact6806 predecessor control. The actual DLL has306176 bytes and SHA-256 `6175a79b88eb8e76dc8e7d9ca6eda01cc37f24c3a2519f9e170d56b1e429c25f`; both PE exports/imports and current Tier0 import subset passed. The established patched source/SDK method was reused; a fresh upstream reconstruction was not repeated. These are offline facts; no6807 game load or runtime qualification is claimed.

## Build6819 exact pair

Adds the reviewed 6819 server hash while retaining the 6817 Source2ServerConfig001 table and method bodies (table 0x2b5a1b0; Connect/Disconnect/Interval 0x99d9e4/0x99d9f0/0x99ab40). The candidate passed the three ABI/lifecycle tests and 712 actual-function pair/table checks, and the exact6817 predecessor mutant rejected the new pair. The built DLL is 307200 bytes with SHA-256 `a304dc87f22b1a276463a83ceb0410494b288b92d1f0aef7ef9b4b35df1016b2`. Offline only; no6819 live startup/export/shutdown acceptance is claimed.

## Builds 6853 and 6854 (Ranked Beta Season 2)

Adds only the reviewed new Tier0/server pair and its named Config001 table.
All fourteen ICvar adapter slots retain their calling conventions. The stock
callback containers moved within CCvar; the shim continues to use its methods,
without writing those private fields. Earlier exact pairs and SDK pins remain.
Compiled lifecycle, adapter, pair and predecessor-negative checks pass.

Build6858 adds the exact reviewed server fingerprint to the existing6853 Tier0/Config001 layout. Complete Connect/Disconnect/Interval bodies, adjustor thunks, named registration and vtable tuple remain exact. Prior pairs and teardown guards retained; no6858 runtime claim.
