"""Sequential isolated Blender workers for explicit partial NPR frame renders."""
import json
from pathlib import Path
import subprocess
from .asset_converter import sha
from .blender_scene_npr import validate_npr_runtime
from .source_npr_scene import validate_scene_npr_selection


def render_sequence(blender,scene,segment,output,frames,*,allow_partial=False):
    blender,scene,segment=[Path(p).resolve(strict=True) for p in (blender,scene,segment)]
    output=Path(output).resolve();frames=list(frames)
    if not frames or any(type(f) is not int for f in frames) or len(set(frames))!=len(frames):
        raise ValueError('Distinct explicit integer frames required')
    if output.exists() or not output.parent.is_dir():
        raise ValueError('New sequence output with existing parent required')
    data=json.loads(segment.read_text(encoding='utf-8'))
    if validate_npr_runtime(data,allow_partial=allow_partial) is None:
        raise ValueError('Prepared NPR runtime segment required')
    validate_scene_npr_selection(data['npr_materials'],data['entities'],manifest_dir=segment.parent)
    scene_sha=sha(scene);segment_sha=sha(segment)
    output.mkdir()
    report=dict(status='RUNNING',requested_frames=frames,frames=[],scene_sha256=scene_sha,
                segment_sha256=segment_sha,scope='Audited partial NPR frame sequence; no replay export or engine parity claim.')
    def save():
        path=output/'sequence-report.json';temporary=path.with_suffix('.part')
        temporary.write_text(json.dumps(report,indent=2),encoding='utf-8');temporary.replace(path)
    save()
    worker=Path(__file__).resolve().parents[1]/'tools/render_npr_frame.py'
    try:
        for frame in frames:
            if sha(scene)!=scene_sha or sha(segment)!=segment_sha:
                raise ValueError('Sequence source changed between frames')
            frame_output=output/f'frame-{frame:06d}'
            request=dict(scene=str(scene),segment=str(segment),output=str(frame_output),
                         frame=frame,allow_partial=True)
            request_path=output/f'request-{frame:06d}.json'
            request_path.write_text(json.dumps(request),encoding='utf-8')
            with (output/f'frame-{frame:06d}.log').open('w',encoding='utf-8') as log:
                result=subprocess.run([str(blender),'-b','--python-exit-code','1','--python',str(worker),'--',str(request_path)],stdout=log,stderr=subprocess.STDOUT)
            if result.returncode:raise RuntimeError(f'NPR frame {frame} worker failed ({result.returncode})')
            receipt=json.loads((frame_output/'frame-report.json').read_text(encoding='utf-8'))
            if receipt.get('status')!='PASS' or receipt.get('frame')!=frame or receipt.get('scene_sha256')!=scene_sha or receipt.get('audit',{}).get('status')!='PASS':
                raise ValueError('Worker receipt does not prove requested frame')
            if receipt['image'].get('path')!='frame.png' or sha(frame_output/'frame.png')!=receipt['image']['sha256']:
                raise ValueError('Rendered frame payload changed')
            report['frames'].append(dict(frame=frame,receipt=str(frame_output/'frame-report.json'),image_sha256=receipt['image']['sha256']))
            save()
        report['status']='PASS';save();return report
    except BaseException as exc:
        report.update(status='FAIL',error=str(exc),error_type=type(exc).__name__);save();raise
