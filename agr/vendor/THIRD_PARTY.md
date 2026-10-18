# References and attribution

The clip decompressor is a Python implementation of the format exposed by [ValveResourceFormat's AnimationClip](https://github.com/ValveResourceFormat/ValveResourceFormat/blob/f3e5c6dbbe484360e43e6df993fc3e4c708e253c/ValveResourceFormat/Resource/ResourceTypes/ModelAnimation2/AnimationClip.cs). Source 2 Viewer 20.0 was used to read the compiled KV3 blocks. Its MIT license is included in `licenses/ValveResourceFormat_MIT.txt`.

Mask feathering and pose-operation research used the [Esoterica animation implementation](https://github.com/BobbyAnguelov/Esoterica/tree/47e629344e569f2a8638aab97fbfddc12dcbafea/Code/Engine/Animation), under the MIT license in `licenses/Esoterica_MIT.md`. The notice is retained with the Python implementation; the upstream C++ reference files are not bundled. The supplied Deadlock binary's behavior takes precedence where numerical inspection establishes a difference.

Historical DMX and cloth checks used Tom Edwards' MIT-licensed [Blender Source Tools `datamodel.py`](https://github.com/Artfunkel/BlenderSourceTools/blob/master/io_scene_valvesource/datamodel.py). That external research tool retains its license header upstream and is not bundled with RenderLock.

Unicorn and pefile are optional installed dependencies for the numerical oracle; their binaries are not bundled. The playable Blender file needs neither dependency.

Compiled Deadlock resources, textures, character models and animation content used during development originate from user-supplied exports and installed game files. They are not bundled with RenderLock, claimed as original evaluator code or covered by the reference projects' MIT licenses.

## Binary identity used for the numerical oracle

```text
client.dll SHA256
5c021dc779c8e050f5fdf368a1586c40165d878f2c880dad712ed09530678123

Installed steam.inf: ClientVersion 6684; SourceRevision 10933105
Demo header: build 10854; recorded game directory citadel_v6679
```

These are different version fields and do not establish identical historical game content. The task mapping and numerical tests apply to the supplied files and the selected segment.
