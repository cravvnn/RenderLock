# Separate particle simulation dependency

This project compiles the shared ParticleProbe commands against an isolated
simulation parser. The material/cloth extractor remains at `tools/particles`
with its qualified parser. Geometry conversion is a third, separate process.
Do not replace the material parser DLL or relax its hash pin to ship simulation
changes.

`tools/codeql_build_helpers.py prepare` creates independent material, simulation,
geometry and lighting dependency checkouts. The simulation dependency uses the
same pinned upstream commit and baseline seven patches, plus the bounded
`vrf-vector-expression-initializer.patch`. `build` compiles all six helpers.
Direct builds require `-p:ParticleSimulationVrfProject=<prepared simulation
checkout>/ValveResourceFormat/ValveResourceFormat.csproj` and .NET 10.

The additional initializer implements only ADD to Normal, replacement and no
normalization. Other modes fail explicitly. Authored expression CP writes use
the v3 provenance policy; external overwrite and missing-read guards remain.
The shared capture code now emits the actual second sequence selector. Missing
selectors in older packages are not interpreted as zero for model rendering.

This is integration source, not a complete flying-bat support declaration.
Normal-export capture/provider qualification, complete rope/shader support and
the final portable runtime package have separate acceptance gates. No GUI default
or release runtime is switched by adding this project. The geometry/helper
command and normal package transaction models are adjacent to those components;
the shared input audit is exercised by actual helper oracles and strict Python
provenance checks. Native allocation, gameplay scheduling and crash durability
are outside these offline simulator checks.

See `docs/PARTICLE_INTEGRATION.md` for the later normal-export qualification and
the complete three-helper runtime layout; portable application packaging remains
a separate gate.
