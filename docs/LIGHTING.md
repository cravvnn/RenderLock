# Lighting and materials

[All guides](README.md) · [Troubleshooting](TROUBLESHOOTING.md)

The default converted materials and optional character looks are different paths.
Start with **Standard materials**, then choose a look in **Lighting lab**.

| Character look | What it does | What you supply |
| --- | --- | --- |
| **Standard materials** | Converted character materials; Source lighting off | Normal installed-game/converter setup |
| **Source lighting** | Partial character diffuse/probe lighting and reflections | Installed map lighting data and lighting-limit consent |
| **Elagrimm + Source** | The author's shader through the current Source/NPR bridge, with an artistic rim | Source inputs, qualified shader library, selected follow target and lighting consent |
| **Custom** | Existing manual lighting settings | All prerequisites for the selected options |

Source presets prepare their linked settings together. You do not need to chase
independent checkboxes or enable scene map geometry. The character beta reads the
installed lighting region while omitting installed map geometry.

## Source lighting

1. Use **Installed game** in Setup and choose the characters/range.
2. Choose **Source lighting** or **Elagrimm + Source**.
3. Check the installed map name in **Map**. The default is `dl_midtown`; a replay
   label such as `start` does not identify its installed map archive automatically.
4. Review and select **Accept partial lighting limits for this export**.
   Relevant input/settings changes reset that acceptance.
5. Export and read the material/surface coverage and limitations.

The external downloaded `.blend` supplies no probe/NPR data. It can be included
independently, but cannot replace the installed lighting prerequisites.

Lighting-limit consent does not allow missing character animation or models.
Leave the broader **Allow partial export / movement placeholders** off unless
those additional compromises are intended.

## Elagrimm library

Download the supported
[BLENDER-Deadlock-Shader.blend](https://github.com/Elagrimm/BLENDER-Deadlock-Shader/blob/e18af8b1c9c1d34b4af72002fb1f4136aa159515/BLENDER-Deadlock-Shader.blend)
and select it in **Elagrimm shader .blend**. This is a separate GPL-3.0 library,
not an application asset. Supported revision:
`e18af8b1c9c1d34b4af72002fb1f4136aa159515`, SHA-256
`018bb08347dcd9cf48da317f5b4c2bcee9e0dcc4fecd4f32c3be3a3225ec1891`.
Earlier Toilet Horse Shoe library selections are no longer supported.

Choose **Elagrimm + Source**, select a named hero in **Follow camera**, and include
that hero in the character selection. The current bridge needs those Source/NPR
and camera inputs; standalone Elagrimm Studio is not implemented.

The library picker appears for Elagrimm + Source and Custom. Saving its location
does not enable it. Switching looks retains saved library/tool paths.

Successful appending embeds materials/node groups inside the exported scene.
No separate Elagrimm file appears in the output folder. Select a character mesh
in Blender to inspect its material slots; a selected replay-root Empty has none.

## What the decoder overrides mean

The portable package discovers its tools automatically. **Advanced tool overrides**
contains paths for people using a different prepared tool kit:

- **Probe atlas decoder** reads the installed baked indirect-light probe data.
- **Cubemap decoder** reads reflection-environment images.
- **World region helper / .NET** read the installed lighting region and run the
  managed helper assemblies.

Finding these files is not a current-game qualification check. Do not replace
their binaries or disable identity checks to work around a game update.

## Artistic limits and scene editing

These are partial reference looks, not full Source 2 rendering. Direct lighting,
shadows, animated material inputs, per-instance behavior and complete NPR/rim
fidelity can be missing or approximated. The saved report and `AGR_README` describe
the actual result. Static installed-map lightmaps have a separate qualification
gate; their refusal does not by itself invalidate a supported character look.

Elagrimm uses Eevee and keeps editable `AGR | Key` and `AGR | Fill` preview lights.
Follow exports animate these relative to the character/view. Adjust their power
or **Additional Light Influence** for the shot. They are artistic Blender lights,
not reconstructed game direct lights. Rebuild dependent lighting/effect work
after changing the camera or animation.

Probe selection follows each character's movement region. Selection and surface
coverage remain checked, including bounded decode batches. More characters and
larger regions can require substantially more processing/cache space. A dark look
is not evidence that all materials need the same emission or light multiplier.

## Emission, glass and normal maps

**Advanced > Character options > Emission and glass repairs (experimental)** can
restore supported static emission from converted textures and authored tint/scale,
and applies an artistic glass approximation. It remains optional and limited;
it does not recover every glow, animated mask or game shader. Elagrimm is not
required for converted emission.

A present normal texture with unexpected shading needs a reproducible scene and
the matching converted inputs. No blanket green-channel flip or universal normal
repair is qualified. See [material troubleshooting](TROUBLESHOOTING.md#materials-or-lighting-look-wrong).

## Game updates and recovery

RenderLock checks the exact selected NPR material set before expensive animation,
effects and probe preparation. Only the qualified constant-expression subset and
known evaluator identities are accepted. A general compatibility report saying
resources are ready still requires selected-material and animation checks.

Recognized failures can offer **Continue without NPR** and its dependent Elagrimm
shader, retaining Source diffuse/probe lighting and strict character data. Other
failures can require a different look or an app compatibility update. Review the
actual choice before retrying; a retry can repeat preparation and scene creation.
