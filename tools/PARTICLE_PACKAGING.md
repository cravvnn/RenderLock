# Particle runtime packaging

The portable candidate requires separate material, simulation and geometry
directories. Preflight validates parser identities, complete native/runtime
dependencies and actual helper commands before creating the candidate directory.
The source archive includes the three helper projects and their pinned patches.

`verify_isolated_particle_helpers` owns no state across calls, output files,
cache, retry or allocation transaction; it is synchronous input admission and
process result inspection. `stage_particles` reuses the existing copy stage and
the existing package/manifest/archive owner for each directory. No installer,
recovery, concurrency or crash-restart protocol is added. Their existing
updater models remain unchanged; these checks do not claim packaging crash
durability or native renderer equivalence. Simplification keeps one staging
function, one manifest builder and one archive validator instead of a second
packaging manager. Concrete missing-command/dependency and final-archive tests
cover this addition; no separate lifecycle spec is needed for the pure preflight.
