# Authored model particles

The mask descriptor, selector projection and GLB slot annotation are synchronous
bounded calculations on immutable inputs. Preparation delegates publication and
cleanup to the existing `AssetConverter._entry` owner; it adds no cache lifecycle,
retry, thread or resource-transfer protocol. Blender prototype reuse is local to
one synchronous component build. Existing package rollback owns all
new Blender data on failure. No additional lifecycle model is introduced here.

Qualification remains split: native selector/model fields and pose mathematics,
source mesh/index/VMAT identity, saved-scene checks and visual inspection. This
integration opts into static assets only for sustained modifier masks. Generic
sampled-particle admission remains unchanged; the pure sampled-selector helper
does not establish an integrated simulator path. The exact mask profile does not admit other model material variables,
activation, root seed/history, tint/alpha changes or Source lighting fidelity.
Other model particles retain their existing override-material path.
