# Internal particle-owner network states

This adds optional `client_effect_owner_states.json` to normal replay extraction.
Native CLI, `extract_replay` arguments, replay identity fields, inclusive ranges
and existing outputs are preserved. It does not add any `.crav` v1 key, change
camera ordering or shot rounding, or relax strict/partial coverage admission.

Policy `agr_client_effect_owner_states_v1`, schema integer 1, has exactly
`schema_version`, `policy`, `requested_range`, `coverage`, `rows`. The requested
range contains integer start/end and `inclusive: true`. Entity state is rebuilt
from signon in the existing decoder pass; owner rows are cropped to that range.
Earlier modifier history remains in its separate raw-write capture.

Each row contains replay `tick`, canonical `entity_id`, `entity_index`,
`entity_serial`, `hero_id`, `active`, nullable `life_state`, and nullable arrays
`enabled_state_words`, `predicted_state_words`, `disabled_state_words`. Identity
uses exact index and serial, not a display name or slot. Arrays contain uint32
words in their actual fixed-array order. Never combine words by displayed field
name: all fixed-array elements can share that name. The native reader resolves
the declared uint32 array length and appends checked FieldPath indices.

Missing scalar, schema, index or value is retained as null for that component;
it is never changed into zero or a shortened array. Raw capture availability is
independent of individual field completeness. Coverage reports `availability`,
nullable `reason`, `origin: "signon"`, first/last observed ticks, row/word counts,
`incomplete_rows` and `scope`. Available means the bounded scan reached the exact
inclusive end with no capture error. Available zero rows, incomplete fields and
an unavailable/truncated stream are distinct. Neither absence nor zero rows
proves an owner has no effects. Consumers must validate each field they use.

Bounds are 200,000 rows, 128 words per array, 8,000,000 total words and 128 MiB
serialized JSON. Identity, duplicate owner/tick, ordering and budget failures
are sticky. Native preflight reserves the filename with symlink metadata;
publication reuses the existing exclusive bounded streaming writer. Python
reserves it with `lexists`, bounds the actual read, rejects duplicate/unknown v1
keys, checks counts, canonical identity, ordering, types, arrays and advertisement.

The replay segment embeds a separate `client_effect_owner_states` envelope with
availability, reason, capture and provenance. Provenance binds actual replay and
executed-helper SHA-256, requested inclusive range, fixed sidecar filename, exact
sidecar byte SHA-256 and canonical parsed capture SHA-256. Replay/helper identity
is frozen by the existing extraction checks. `validate_envelope` rechecks the
portable object without trusting its old pathname. Old helpers are explicitly
`unavailable/helper_not_advertised`; an advertised missing/inconsistent file
fails its named contract. The modifier sidecar remains unchanged.

This supplies raw inputs only. It does not infer modifier lifetime, client view
ownership, particle CREATE, seed, phase, render interpolation, shaders or lights.
For the previously qualified Windows mask code, array words 1 and 3 matter; those indices
are a separate client-specific renderer policy, not assumptions in this capture.

`validate_view` accepts a shot contained by the original capture only after
validating the complete original envelope. It leaves earlier/later rows, range
and all digests intact. Consumers select the needed owner rows separately; this
check does not infer complete semantic inputs or visibility from availability.
