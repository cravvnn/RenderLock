# Internal client modifier writes, version 1

`extract_replay(demo, output, start_tick, end_tick, hero_ids=None)` and the native
CLI retain their existing signatures and inclusive range. Native extraction adds
`client_modifier_writes.json`; the replay segment embeds its validated content
under `client_modifier_writes`. This is internal extraction data. It does not add
a `.crav` v1 key or alter camera ordering, shot rounding or coverage admission.

The native file has exactly these top-level fields:

- `schema_version`: integer 1; `policy`: `agr_client_modifier_writes_v1`.
- `requested_range`: `start_tick`, `end_tick`, `inclusive: true`.
- `coverage`: `availability`, nullable `reason`, `origin: "signon"`,
  `table: "ActiveModifiers"`, `table_seen`, nullable first/last observed ticks,
  `rows`, `wire_bytes`, and explanatory `scope`.
- `rows`: ordered observations, each with `tick`, `kind` (`Delta` or `Snapshot`),
  `slot`, `payload_present`, nullable `wire_bytes` and nullable `header`.

The five header fields are nullable `entry_type`, `parent`, `serial_number`,
`modifier_subclass`, and `int1`. Null preserves proto2 field absence. Do not
replace absence by the protobuf accessor defaults. Empty bytes decode to five
null fields; omitted bytes have `payload_present: false` and no header. A final
refused protobuf may retain bytes and a null header only with explicit
`unavailable/invalid_payload` coverage. Original bytes preserve all other fields.
Header values must agree with the retained protobuf; the Python reader is a
five-field reader, not a replacement for the native full-message decoder.

Capture occurs in the existing from-signon pass **before** actor output cropping,
without filtering heroes or modifier subclasses. Every pre-merge table write is
preserved, including repeated same-slot writes, removal records, omitted fields
and snapshots. This is not a merged active-state list. Boon/pbdems2 documents that
full-packet modifier snapshots can have a different capture time from entities.
Do not use Snapshot as CREATE, infer slot death from absence, or assume replay
ticks equal `creation_time` or a particle's phase. Serialized parent handles use
the replay handle format; do not substitute the live reader's index bit width.

Availability means only that this bounded raw capture reached the exact inclusive
end, observed the table and encountered no capture failure. Available zero rows,
an unobserved table, a truncated scan and an old helper without this feature are
different results. None proves that no modifiers are active. Failures are sticky;
a later valid write cannot qualify an earlier incomplete prefix.

Bounds: 65,536 rows, slots 0..65,535, 16,384 bytes per payload, 16 MiB total payload,
128 MiB serialized JSON. Row ticks are ordered from the observed signon history
through inclusive end. Native filename preflight uses `symlink_metadata`, covering
files, directories and dangling links; publication uses exclusive create after
preflight. A failed write can leave evidence, but cannot advertise successful
extraction. Python reserves the name using `lexists`, bounds the actual read,
rejects duplicate/unknown v1 keys, validates range, types, counts, byte budgets,
header agreement and the native advertisement.

The admitted row and wire-byte bounds also keep the complete serialized document
below the JSON limit: at most four decimal JSON bytes per wire byte, 512 fixed
bytes per row and 1024 document-header bytes. The native build asserts this
inequality and checks worst scalar/payload serialization. Ordinary output I/O
errors still fail extraction rather than becoming a successful empty capture.

The segment's internal envelope has `availability`, `reason`, `capture` and
`provenance`. Provenance contains the actual replay SHA-256, executed helper
SHA-256, requested start/end, inclusive flag, sidecar filename and exact sidecar
SHA-256. `capture_sha256` separately binds the embedded parsed capture using
canonical JSON (sorted keys, compact separators, finite values). Consumers
recompute this digest, check the envelope's replay/range and revalidate the
embedded schema; the original byte digest cannot by itself recheck a parsed
object with unknown original whitespace. Replay and helper bytes are checked
before/after execution and retention.
Consumers should use the embedded validated bytes; an external path alone is not
identity evidence. An old helper produces `unavailable/helper_not_advertised`,
null capture and null sidecar/capture digests. An advertised missing/inconsistent file
fails the named extraction contract instead of becoming an empty capture.

Native coverage advertises `schema_version`, `policy`, `file`, `availability`,
`reason`, `rows`, `wire_bytes`; these must agree with the sidecar. Existing replay
metadata identity keys, animation warmup, projectile and recorded-particle data
are retained. `coverage.particles` remains `not_extracted`: this raw sidecar does
not itself qualify any effects, activation, lifetime, phase, shader or lighting.
