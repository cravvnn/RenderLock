"""Package experimental Linux source; requires testers' own native dependencies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from package_character_beta import archive, stage_source, write_manifest


def package(root, output, version):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    stage = output / f'RenderLock-{version}-Linux-test-source'
    stage.mkdir()
    excluded = stage_source(Path(root).resolve(), stage)
    write_manifest(stage, version, 'Linux-experimental-test-source')
    receipt = dict(version=version, kind='Linux-experimental-test-source',
                   runtime_bundled=False, excluded_research_tests=excluded,
                   archive=archive(stage, output / (stage.name + '.zip')))
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
