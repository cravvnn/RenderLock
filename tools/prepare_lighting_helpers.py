"""Prepare a local development helper kit without changing an existing release.

Run from the project root with python -m tools.prepare_lighting_helpers.
The .NET runtime remains an explicit external dependency. No game data is copied.
"""
import argparse
import json
from pathlib import Path
import shutil

from agr.asset_converter import ConversionError, sha
from agr.source_region import regional_command
from agr.source_hero_lighting import validate_hero_decoder_paths
from agr.helper_files import helper_file_inventory


def prepare_helpers(world, atlas, cube, dotnet, output):
    world = Path(world).expanduser().resolve()
    atlas, cube, dotnet = validate_hero_decoder_paths(atlas, cube, dotnet)
    _, regional_identity = regional_command(world, dotnet)
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ConversionError('Helper destination already exists; choose a new directory')
    planned = {}
    for role, helper in [('world', world), ('atlas', atlas), ('cube', cube)]:
        files = helper_file_inventory(helper.parent)
        for suffix in ('.deps.json', '.runtimeconfig.json'):
            if helper.stem + suffix not in files:
                raise ConversionError('Missing helper runtime metadata: ' + helper.stem + suffix)
        planned[role] = (helper, files)
    if planned['world'][1] != regional_identity:
        raise ConversionError('Regional helper inventory changed during planning')
    # Only a new destination is written. A failed partial copy is retained for
    # diagnosis and has no completed manifest; never remove an existing kit.
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version': 1, 'status': 'verified_local_development_kit',
                'dotnet': str(dotnet), 'helpers': {},
                'limits': ['Not a published release or a bundled .NET runtime.',
                           'Source lighting remains explicitly partial.']}
    for role, (helper, files) in planned.items():
        for name, digest in files.items():
            target = output / role / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(helper.parent / name, target)
            if sha(target) != digest:
                raise ConversionError('Helper changed during copy: ' + name)
        manifest['helpers'][role] = {'entrypoint': f'{role}/{helper.name}', 'files': files}
    _, copied_identity = regional_command(output / 'world' / world.name, dotnet)
    if copied_identity != regional_identity:
        raise ConversionError('Copied regional helper identity changed')
    validate_hero_decoder_paths(output / 'atlas' / atlas.name, output / 'cube' / cube.name, dotnet)
    manifest['cli_arguments'] = ['--source-lighting', '--world-region-probe',
        str(output / 'world' / world.name), '--lighting-dotnet', str(dotnet),
        '--source-hero-lighting', '--probe-atlas-decoder', str(output / 'atlas' / atlas.name),
        '--cubemap-decoder', str(output / 'cube' / cube.name)]
    (output / 'helper-kit.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('world', 'atlas', 'cube', 'dotnet', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    manifest = prepare_helpers(**vars(args))
    print(json.dumps({'status': manifest['status'], 'output': str(args.output.resolve()),
                      'cli_arguments': manifest['cli_arguments']}, indent=2))


if __name__ == '__main__':
    main()
