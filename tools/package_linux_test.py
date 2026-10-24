"""Package experimental Linux source; requires testers' own native dependencies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from package_character_beta import stage_source, write_manifest
from package_installed_release import digest, validate_archive


def archive_linux_source(stage, output):
    temporary = output.with_suffix('.zip.tmp')
    with ZipFile(temporary, 'w', ZIP_DEFLATED, compresslevel=6) as target:
        for path in sorted(stage.rglob('*')):
            if not path.is_file():
                continue
            info = ZipInfo.from_file(path, stage.name + '/' + path.relative_to(stage).as_posix())
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | (0o755 if path.suffix == '.sh' else 0o644)) << 16
            info.compress_type = ZIP_DEFLATED
            target.writestr(info, path.read_bytes())
    validate_archive(temporary, stage.name)
    temporary.replace(output)
    return dict(path=str(output), bytes=output.stat().st_size, sha256=digest(output))


def package(root, output, version):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    stage = output / f'RenderLock-{version}-Linux-test-source'
    stage.mkdir()
    excluded = stage_source(Path(root).resolve(), stage)
    # A Windows checkout can contain CRLF shell scripts. Linux needs LF and
    # Unix executable attributes even when this archive is built on Windows.
    for path in stage.rglob('*.sh'):
        path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
    write_manifest(stage, version, 'Linux-experimental-test-source')
    receipt = dict(version=version, kind='Linux-experimental-test-source',
                   runtime_bundled=False, excluded_research_tests=excluded,
                   archive=archive_linux_source(stage, output / (stage.name + '.zip')))
    (output / 'packaging-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    import sys
    sys.path.insert(0, str(root))
    from agr import __version__
    print(json.dumps(package(root, args.output, __version__), indent=2))
