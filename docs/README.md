# RenderLock guides

These guides describe the Windows character beta and current main source. Start
with [the README](../README.md#quick-start) or the [user guide](USER_GUIDE.md).
Published ZIPs keep the documentation from their own release; source documentation
can be newer. The [changelog](../CHANGELOG.md) records when behavior changed.

| Task | Guide |
| --- | --- |
| Install, inspect a replay, select characters and export | [User guide](USER_GUIDE.md) |
| Understand saved packages, packed textures and cache reuse | [Output files](USER_GUIDE.md#output-files-and-saved-packages) |
| Choose Source lighting or Elagrimm; understand material options | [Lighting and materials](LIGHTING.md) |
| Add the hosted map and cut it in Blender | [Map workflow](../MAP_WORKFLOW.md) |
| Use approximate cloth and understand its coverage | [Cloth workflow](../CLOTH_WORKFLOW.md) |
| Recover a stopped export and collect diagnostics | [Troubleshooting](TROUBLESHOOTING.md) |
| Use the command line | [CLI](CLI.md) |
| Update the Windows app or recover an interrupted update | [Updating](../UPDATING.md) |
| Develop, build helpers or package the app | [Building](BUILDING.md) and [Contributing](../CONTRIBUTING.md) |

## Separate development work

[Native Linux support](https://github.com/cravvnn/RenderLock/blob/feature/linux-support-20261024/LINUX.md)
is experimental work on another branch, with its own tests and pending decoder
qualification. Those changes are not silently included in the Windows release.
Other unmerged research or test results do not establish a supported user workflow.

## Which files to keep

Keep the exported `.blend`, `.agrscene` directory and `.agr-report.json` together.
For an external map, retain the downloaded `Textures` folder until you have relinked
and packed the needed images. Keep failure diagnostics until an issue is resolved.

[Credits](../CREDITS.md) · [Licenses](../THIRD_PARTY.md) · [Report a bug](TROUBLESHOOTING.md#reporting-an-issue)
