RenderLock map-builder bridge

bridge.py is a separate command-line program licensed GPL-3.0-or-later.
Copyright (c) 2026 Cravvnn. Its complete source is this file; license in COPYING.

It runs unmodified deadlockrigs map tools by Sirsyorrz, pinned to commit
0d1958ab553630df997a93fda06d3ab9ed6994c3:
https://github.com/Sirsyorrz/deadlockrigs
https://github.com/Sirsyorrz/deadlockrigs/tree/0d1958ab553630df997a93fda06d3ab9ed6994c3
The downloaded complete source archive and its LICENSE remain in the tools cache.
The bridge adds owned-path checks, bounded worker counts and disabled Blender
auto-execution, a per-job process lock and hash-verified stage checkpoints.
After an interrupted import, unchanged extracted assets can be reused. Changed
assets invalidate that checkpoint; the previous output is preserved before a
fresh build. It does not modify the upstream source files.

RenderLock invokes this helper as a separate process and reads its ordinary
prepared-map output. The RenderLock package adapter does not import the upstream
Python modules. This notice does not relicense the upstream component as MIT.

Source2Viewer CLI is a separate pinned tool from ValveResourceFormat:
https://github.com/ValveResourceFormat/ValveResourceFormat
Its archive retains THIRD_PARTY_NOTICES.txt and native dependency notices.
