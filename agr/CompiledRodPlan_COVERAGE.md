# Cloth optimization scope

`CompiledRodPlan.tla` models validation, immutable call-local preparation,
repeated use and disposal between two differently identified hero/export calls.
Its default configuration uses the production 16 ordinary solver iterations;
four mutants exercise metadata admission, point dimensions, cross-call reuse and
caller-owned array aliasing. The bounded model does not prove float arithmetic.

`RodProjectionGroups.tla` checks two adjacent compiled blocks with four lanes and
two endpoints. Batching is allowed only across disjoint node domains. Shared
nodes keep a group boundary; repeated compiler pad lanes retain the last native
write. Both grouping and last-write mutants must break native-order equivalence.
Numerical tests compare against the pre-optimization scalar block loop, including
random overlapping/repeated node indices and the supplied Violet cloth inputs.

`fe_rod_constraints.py` retains immutable metadata only. Each
`simulate_node_motion` invocation creates its own plan after validating physics;
mutable point/velocity/collider history remains local to that existing solver.
Frame/substep clocks, iteration counts, friction-history advancement and residual
checks are unchanged. The optional progress callback observes completed samples;
it adds no worker, queue, retry, persistent cache or new ownership lifetime.
Concurrent mutation of solver inputs is not supported.

`blender_fe_proxy.py` and `blender_fe_proxy_bones.py` change coordinate transfers
to Blender bulk APIs only. They introduce no new persistent state, shape keys or
action lifecycle. The existing bake/rollback paths remain in charge; headless
Blender mesh/shape-key and saved-scene checks qualify the API transfer. No model
claims Blender cloth parity, full renderer coverage, or Source simulation parity.

The earlier capsule batching applies to every imported-node hero using that
operation. Proxy garments use Blender cloth instead; bulk coordinate transfers
apply across those generated-surface and back-solved garment paths. No hero name
or catalog bypass is used to enable an optimization or admit unsupported cloth.
