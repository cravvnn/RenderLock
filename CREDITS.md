# Credits

RenderLock is maintained by [Cravvnn](https://github.com/cravvnn). It builds on the tools, shaders and research below. These credits include current dependencies and earlier experiments that helped shape the project.

**UI design and RenderLock artwork:** [officialkaraig-ux](https://github.com/officialkaraig-ux).

## Asset tools and animation

| People / project | Contribution to RenderLock |
| --- | --- |
| [Source 2 Viewer / ValveResourceFormat contributors](https://github.com/ValveResourceFormat/ValveResourceFormat/graphs/contributors) | The asset converter, Source 2 format research, animation clip decoding, and the library used by our particle and lighting helpers. The Windows portable package includes the [Source 2 Viewer CLI](https://github.com/ValveResourceFormat/ValveResourceFormat). |
| [Peter Xenopoulos](https://github.com/pnxenopoulos) and the [Boon / boon-proto](https://github.com/pnxenopoulos/boon) and [pbdems2](https://github.com/pnxenopoulos/pbdems2) contributors | Replay parsing and protocol data used by the Python application and native replay extractor. |
| Bobby Anguelov, Kirill Bazhenov, Nikita Krupitskas and the [Esoterica contributors](https://github.com/BobbyAnguelov/Esoterica) | Animation research, including bone-mask feathering and pose-operation behavior, used while developing the offline evaluator. |
| [ValveKeyValue contributors](https://github.com/ValveResourceFormat/ValveKeyValue) | KV3 text reader/writer references used in developing the map DATA reader. |

## Shaders

| Author / project | Contribution to RenderLock |
| --- | --- |
| [Elagrimm and the shader's credited collaborators](https://github.com/Elagrimm/BLENDER-Deadlock-Shader#readme) | Author of the shader used by the current character-look adapter. Users supply the qualified library separately; it is not bundled. RenderLock adapts a copy of the author's group for its partial Source-lighting bridge and preserves the project's credits and GPL-3.0 license. |
| [Toilet Horse Shoe](https://open3dlab.com/project/bb1e412b-2554-4e4e-acc4-d25b7f842e79/) | Shader author whose library supported earlier character-look development. It remains credited as historical work; the current adapter supports Elagrimm only. |

Third-party shader libraries remain separate downloads, under their authors' terms.

## Cloth and development research

| People / project | Contribution to RenderLock |
| --- | --- |
| [w1tcherrr](https://github.com/w1tcherrr) and the ValveResourceFormat contributors | [PR #1317: Decompile compiled cloth physics (FeModel)](https://github.com/ValveResourceFormat/ValveResourceFormat/pull/1317), used in the original cloth tests to recover authored proxies, cloth paint and collision data. It informed the prepared decompiled-proxy workflow. That development branch is separate from the bundled Source 2 Viewer 20.1 CLI. |
| Tom Edwards and the [Blender Source Tools contributors](https://github.com/Artfunkel/BlenderSourceTools) | The MIT-licensed `datamodel.py` reader used to parse decompiled DMX during cloth research. This was a development tool. |
| [juliancz-a / Deadlock Playground](https://github.com/juliancz-a/deadlock-playground) | Cloth and material implementations reviewed as research references. No code was ported from that review. |
| [Unicorn](https://github.com/unicorn-engine/unicorn), [Capstone](https://github.com/capstone-engine/capstone) and [pefile](https://github.com/erocarrera/pefile) contributors | Tools used for offline binary inspection and bounded numerical checks during compatibility research. They are not required to open exported Blender scenes. |

## Foundations and dependencies

- **[Blender Foundation and the Blender community](https://www.blender.org/about/):** the scene, animation, rendering and baking platform RenderLock exports to.
- **[Valve](https://www.valvesoftware.com/):** Deadlock, Source 2 and the underlying game content and formats. Replays and installed game assets are user-supplied inputs and are not included in the repository or packages.
- **[Python](https://www.python.org/), [Tcl/Tk](https://www.tcl.tk/), [Rust](https://www.rust-lang.org/) and [.NET](https://github.com/dotnet/runtime) contributors:** the languages, desktop UI and runtimes supporting the application and helper tools.
- **[NumPy](https://numpy.org/), [Polars](https://pola.rs/), [python-snappy](https://github.com/andrix/python-snappy) and [cramjam](https://github.com/milesgranger/pyrus-cramjam) contributors:** numerical arrays, replay data processing and compression.
- **[Typer](https://github.com/fastapi/typer), [Rich](https://github.com/Textualize/rich), [Pygments](https://pygments.org/), [markdown-it-py](https://github.com/executablebooks/markdown-it-py), [mdurl](https://github.com/executablebooks/mdurl), [Colorama](https://github.com/tartley/colorama), [Shellingham](https://github.com/sarugaku/shellingham) and [annotated-doc](https://github.com/fastapi/annotated-doc) contributors:** command-line and supporting dependencies included through the runtime dependency tree.
- **The Rust dependency maintainers:** the native extractor also relies on crates including anyhow, prost and serde_json. The [resolved native dependency inventory](native/licenses/0.2.1/dependencies.json) records the complete package list, upstream repositories and retained notices.

## Licenses and corrections

This page acknowledges the people and projects behind the work. Full license and provenance records are in [THIRD_PARTY.md](THIRD_PARTY.md), [animation reference notices](agr/vendor/THIRD_PARTY.md) and [native dependency notices](native/THIRD_PARTY.md). Portable packages retain their runtime and bundled-tool notices; `release_manifest.json` records the bundled dependency versions. RenderLock's MIT license does not replace third-party licenses or game-content ownership.

For a missing or incorrect credit, [open an issue](https://github.com/cravvnn/RenderLock/issues) with the preferred name, project link and a description of the contribution.
