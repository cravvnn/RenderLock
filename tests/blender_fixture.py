"""Completion records for orchestration tests that deliberately stub Blender."""
import json
from pathlib import Path

from agr.blender_scene import asset_spec
from agr.scene_package import read_scene


def write_blender_completion(command, **extra):
    segment = Path(command[command.index('--segment') + 1])
    output = Path(command[command.index('--output') + 1])
    assets = Path(command[command.index('--assets') + 1])
    data = read_scene(segment)
    poses_requested = data.get('coverage', {}).get('animation') != 'not_requested'
    rows = []
    for entity in data['entities']:
        spec = asset_spec(entity, assets, segment.parent)
        rows.append(dict(id=entity['id'], hero_name=entity.get('hero_name'),
            model_status='imported' if spec.get('model_file') else 'missing asset placeholder',
            pose=dict(status='baked', sample_count=len(entity['samples'])) if poses_requested
                 else dict(status='not requested; imported rest pose')))
    report = dict(schema_version=1, segment=str(segment), output=str(output),
        start_tick=data['start_tick'], end_tick=data['end_tick'], tick_rate=data['tick_rate'],
        fps=float(command[command.index('--fps') + 1]), entities=rows, warnings=[])
    report.update(extra)
    output.write_bytes(b'authored scene stub; no Blender executed')
    output.with_suffix('.scene.json').write_text(json.dumps(report))
    return report
