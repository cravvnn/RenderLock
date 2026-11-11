# Texture channel selection

Pure enum selection and arithmetic; no cross-step state, cache or ownership is
introduced, so no separate lifecycle model is needed. Blender allocation stays
inside the existing effect-package rollback boundary. Default RGBA is unchanged.
The exact bat core's RGBA_RGBALPHA uses the existing RenderLock layer equation
(.299R+.587G+.114B). Both shaders continue to use the same RGB sample. This is
not complete spritecard lighting, gamma, blending or game-pixel parity. Other
selectors refuse until independently integrated and checked. No GPL source was
copied from the inspected third-party package.
