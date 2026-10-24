# Cloth / secondary motion

[All guides](docs/README.md) · [Troubleshooting](docs/TROUBLESHOOTING.md)

Enable **Advanced > Character options > Cloth / secondary motion (experimental)**
with **Installed game**. It does not require the external map or Source lighting.
Start with a successful cloth-off preview of the same characters/range.

## Requesting cloth and choosing a compromise

RenderLock checks version-matched definitions in the selected cache, then uses
the bundled extractor to prepare supported layouts from the installed model when
needed. Those supported layouts do not require a separate proxy download.

If cloth is unavailable, recognized errors can offer an explicit retry with
supported cloth only or with cloth disabled. **Advanced > Output policy > Keep
supported cloth if some cloth is unavailable** (`--partial-cloth`) accepts omitted
or failed cloth while character models and animation stay strict. The broader
**Allow partial export / movement placeholders** option is a separate decision.
Closing recovery never retries automatically.

## What can be baked

Support is checked per compiled model and definition, not guaranteed by hero name.
The compiled-model hash, resource identity, decoded physics, bounds and definition
integrity must agree. Some garment paths reconstruct authored painted proxies and
store approximate Blender motion as shape keys. Qualified imported FE-node or
bone-proxy paths can instead modify explicitly reported cloth bones; supported
multi-part bundles retain per-part outcomes.

This does not mean every FE layout or operation is supported. Unknown or
unqualified stiffening, world-collision and other mechanics can still stop a
requested cloth export. Their presence is not proof that they are inactive.
Do not remove definitions or bypass admission checks to obtain a successful label.

## Reading coverage

The report lists supported, unsupported and failed entities/parts, the actual
bake outcome, and simulation/render-influence counts. **Simulation vertices** are
not the same as **affected render vertices**. A bake reporting zero affected render
vertices does not establish visible garment influence. Read the limitations and
inspect the shot rather than assuming every completed bake visibly moves clothing.

Cloth is an approximation, not recorded Source 2 cloth playback. Pre-shot state,
self-collision, map collision, named effect activation and solver details can be
missing or approximated depending on the admitted path. The external map does
not automatically become cloth collision geometry.

Prepared cloth depends on body animation, range and FPS. Regenerate after changing
them. A source-pose verifier can also disagree with intentionally cloth-modified
bones; use a cloth-off control for recorded body animation and validate cloth
separately. A successful save or pose check is not proof of game cloth fidelity.

## Older workflows

Legacy native Blender resimulation and `Rebake_AGR_Cloth.py` were retired. Current
prepared proxy/imported-node workflows are separate implementations. Older notes
about a retired Haze prototype or single-garment tests are historical evidence,
not a current list of supported or unsupported heroes.
