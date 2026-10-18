"""Bounded installed-model cloth reconstruction; game-derived results stay in cache."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from .asset_converter import compiled, sha
from .particle_runtime import particle_command, particle_environment


def discover_extractor():
    root = Path(__file__).resolve().parents[1]
    probe = next((p for p in (root/'tools/particles/ParticleProbe.dll',
                              root/'research/ParticleProbe/bin/Release/net10.0/ParticleProbe.dll') if p.is_file()), None)
    executable = 'dotnet.exe' if sys.platform == 'win32' else 'dotnet'
    dotnet = next((p for p in (root/'tools/dotnet'/executable, root/'.toolchains/dotnet'/executable) if p.is_file()), None)
    if dotnet is None:
        found = shutil.which(executable)
        dotnet = Path(found) if found else None
    if probe is None or dotnet is None:
        raise ValueError('Automatic cloth reconstruction requires the bundled RenderLock model extractor')
    return particle_command(probe, dotnet), particle_environment(probe, dotnet)


def read_painted_mesh(path):
    from .vendor import datamodel
    if not 0 < path.stat().st_size <= 64*1024*1024:
        raise ValueError('Cloth DMX file exceeds supported size')
    document = datamodel.load(str(path))
    meshes = [e for e in document.elements if e.type == 'DmeMesh'
              and 'cloth_enable$0' in (e['bindState'] or e['currentState'])]
    if not meshes:
        return None
    if len(meshes) != 1:
        raise ValueError('Ambiguous painted cloth surface in DMX')
    mesh = meshes[0]
    state = mesh['bindState'] or mesh['currentState']
    positions, indices = state['position$0'], state['position$0Indices']
    if not 0 < len(positions) <= 1_000_000 or len(indices) > 6_000_000:
        raise ValueError('Cloth surface exceeds array bounds')
    faces = []
    for face_set in mesh['faceSets']:
        face = []
        for corner in face_set['faces']:
            if corner == -1:
                if not 3 <= len(face) <= 64:
                    raise ValueError('Invalid cloth polygon')
                faces.append(face); face = []
            else:
                if not 0 <= corner < len(indices) or not 0 <= indices[corner] < len(positions):
                    raise ValueError('Cloth face index outside surface')
                face.append(int(indices[corner]))
        if face:
            raise ValueError('Unterminated cloth polygon')
    paint = {}
    for key in state:
        if not key.startswith('cloth_') or key.endswith('Indices'):
            continue
        values = [None]*len(positions)
        mapping = state[key+'Indices']
        if len(mapping) != len(indices):
            raise ValueError('Cloth paint corner count mismatch')
        for pos, attribute in zip(indices, mapping):
            if not 0 <= pos < len(values) or not 0 <= attribute < len(state[key]):
                raise ValueError('Cloth paint index outside array')
            value = float(state[key][attribute])
            if values[pos] is not None and abs(values[pos]-value) > 1e-6:
                raise ValueError('Split cloth paint at shared position')
            values[pos] = value
        if any(v is None for v in values):
            if len(state[key]) != len(positions) or list(mapping) != list(indices):
                raise ValueError('Unmapped cloth vertex')
            values = list(map(float, state[key]))
        paint[key] = values
    result = dict(mesh_name=mesh.name, vertices=[list(v) for v in positions], faces=faces,
        paint=paint, joint_count=int(state['jointCount']),
        joint_names=[e.name for e in document.root['model']['jointList']],
        blend_indices=list(state['blendindices$0']), blend_weights=list(state['blendweights$0']))
    count = len(positions)*result['joint_count']
    if not 1 <= result['joint_count'] <= 16 or len(result['blend_indices']) != count or len(result['blend_weights']) != count:
        raise ValueError('Cloth skin array dimensions differ')
    result['cloth_enable$0'] = paint['cloth_enable$0']
    return result


def reconstruct(resource, provider, decoder, physics, *, command=None):
    """Generate a version-bound definition; never bundle game-derived proxy data."""
    resolved = provider.resolve(compiled(resource))
    if command is None:
        command, environment = discover_extractor()
    else:
        command, environment = list(command), None
    if not command or not Path(command[-1]).is_file():
        raise ValueError('Cloth extractor is unavailable')
    tool = Path(command[-1])
    library = tool.with_name('ValveResourceFormat.dll')
    settings = {'adapter':sha(Path(__file__)), 'parser':sha(Path(__file__).parent/'vendor/datamodel.py'),
                'probe':sha(tool), 'vrf':sha(library) if library.is_file() else None,
                'physics':hashlib.sha256(json.dumps(physics,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()}
    name = resource[:-2] if resource.endswith('_c') else resource
    def build(temp):
        output = temp/'extracted'; output.mkdir()
        proc = subprocess.run(command+['--decompile-cloth',str(provider.install_path/'game/citadel/gameinfo.gi'),
            name,str(output)],env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
        if proc.returncode:
            raise ValueError('Automatic cloth extraction failed: '+proc.stderr[-1600:])
        report = json.loads((output/'cloth-extraction.json').read_text())
        if report.get('resource') != name or report.get('schema_version') != 1 or report.get('animation_files') != 0:
            raise ValueError('Cloth extractor identity/format changed')
        if report.get('fe_node_count') == 0:
            (temp/'result.json').write_text(json.dumps({'status':'not_applicable','reason':'Installed model has no FE cloth nodes'}))
            return
        files = {}
        for row in report['files']:
            p = output/row['path']
            if Path(row['path']).name != row['path'] or p.stat().st_size != row['bytes'] or sha(p) != row['sha256']:
                raise ValueError('Cloth extractor file integrity mismatch')
            files[row['path']] = p
        proxies = report['proxy_files']
        if len(proxies) != 1 or proxies[0] not in files:
            raise ValueError('Multiple or missing cloth proxies require an explicit surface mapping')
        proxy = read_painted_mesh(files[proxies[0]])
        if proxy is None:
            raise ValueError('Decompiled proxy has no cloth paint')
        parts = []
        for row in report['render_meshes']:
            render = read_painted_mesh(files[row['file']])
            if render is None or not any(v > 0 for v in render['cloth_enable$0']):
                continue
            mesh = row['name']
            if not isinstance(mesh,str) or not mesh or '/' in mesh or '\\' in mesh:
                raise ValueError('Invalid decompiled cloth mesh identity')
            parts.append(dict(schema='agr_decompiled_fe_proxy_v1',mesh_suffix='.'+mesh,
                blender_goal_pins='authored_strength',proxy=proxy,render=render,physics=physics,
                scope='Automatically reconstructed painted garment; Blender cloth approximation, not Source solver parity'))
        if not parts:
            raise ValueError('No painted render surface matches the reconstructed cloth proxy')
        definition=dict(schema='agr_decompiled_fe_proxy_bundle_v1',parts=parts,physics=physics,
            source_sha256={r['path']:r['sha256'] for r in report['files']},
            scope='Version-matched automatic cloth reconstruction; per-garment Blender approximation',
            extractor=settings)
        (temp/'result.json').write_text(json.dumps({'status':'prepared','definition':definition},allow_nan=False))
    entry = decoder._entry('automatic_cloth_v1',{compiled(resource):resolved.sha256},settings,build)
    return json.loads((entry/'result.json').read_text())
