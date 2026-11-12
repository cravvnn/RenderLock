# Client modifier write capture

`ClientModifierWrites.tla` covers the complete additive capture/publication path
called by `main.rs`: reserved path check, signon observations, bounded row
retention, sticky capture error, finish/availability, competing file creation,
exclusive publication, flush/close failure and consumer acceptance after a
successful process result. The adjacent default and existing-file configurations
bound observation to two ticks and a one-row budget. Eleven mutants cover each
load-bearing guard; their expected invariant violations must be found.

The checker is the project's pinned tla-ts 0.0.8 in D: local tools. Run:

    node local-data/Tools/tla-skill/check.mjs native/src

The integration model has 134 default states and 2 existing-file states. The
earlier component model had 114 default states; the added states distinguish a
retained malformed diagnostic row from a failure before copying. Actor progress
and a frozen failed prefix are now explicit invariants. New mutants reject
blocking actor callbacks, retaining more writes after failure and admitting a
malformed diagnostic as available. No retry loop, cache, independent publication status
machine or modifier-state merger was added. Simplification keeps one sticky
failure and derives availability from existing observations, instead of storing
an independently mutable availability flag. The model's available variable is
the returned observation of that calculation. `faultSeen` is a checker witness,
not an extra production state. No prior implementation/state count is claimed
for the original component. The integration reuses main's existing exclusive
reserved-path check instead of retaining a second identical implementation.

Admitted rows cannot exhaust the serialized writer budget: decimal payload bytes
take at most four bytes each, fixed scalar row metadata fits within 512 bytes,
and the fixed document header within 1024 bytes. At the admitted row/payload
bounds their sum is below 128 MiB. A compile-time assertion and concrete worst
scalar/payload serialization checks enforce that bound. Output write, flush and
sync errors remain fatal; raw capture data failures remain explicitly unavailable.

Concrete tests cover proto presence, ordered repeated-slot removal/reuse and
snapshot writes, malformed data, bounds before copy, empty/unavailable states and
exclusive writes. Python validation is synchronous over bounded immutable bytes;
its byte parsing, schema/type/range checks and digest calculations have no
separate retained lifecycle. `agr/replay.py` performs identity checks around its
existing synchronous subprocess and file retention. The model composes through
successful-process/complete-file admission. It does not prove arbitrary filesystem
or adversarial process replacement atomicity, protobuf implementation correctness,
all existing exporter lifecycles, merged modifier state or particle activation.

See `agr/ClientModifierWrites_CONTRACT.md`. No `.crav` v1 change or strict/partial
gate change belongs to this feature. Snapshot capture time relative to entity
state and modifier-to-client-effect behavior require independent qualification.
