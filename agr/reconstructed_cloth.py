"""Bounded installed-model cloth reconstruction; game-derived results stay in cache."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .asset_converter import ConversionError, compiled, sha
from .particle_runtime import particle_command, particle_environment
from .vendor.kv3_text import parse_kv3


def cloth_module_hashes():
    return {name:sha(Path(__file__).with_name(name+'.py')) for name in (
        'fe_bone_motion','fe_node_rotations','fe_rod_constraints','fe_hinge_limits','fe_triangle_constraints','fe_quad_constraints','fe_collision_planes','fe_soft_offsets','fe_control_offsets','fe_kelager_bends','fe_body_collisions',
        'fe_proxy_bones','blender_fe_nodes','blender_fe_proxy_bones','blender_fe_proxy','prepared_cloth')}


def discover_extractor():
    root = Path(__file__).resolve().parents[1]
    override = os.environ.get('AGR_MODEL_EXTRACTOR')
    probe = Path(override).expanduser().resolve() if override else next((p for p in (root/'tools/particles/ParticleProbe.dll',
                              root/'research/ParticleProbe/bin/Release/net10.0/ParticleProbe.dll') if p.is_file()), None)
    executable = 'dotnet.exe' if sys.platform == 'win32' else 'dotnet'
    dotnet = next((p for p in (root/'tools/dotnet'/executable, root/'.toolchains/dotnet'/executable) if p.is_file()), None)
    if dotnet is None:
        found = shutil.which(executable)
        dotnet = Path(found) if found else None
    if probe is None or dotnet is None:
        raise ConversionError('Automatic cloth reconstruction requires the bundled RenderLock model extractor')
    if probe.suffix.lower() != '.dll' or not probe.is_file():
        raise ConversionError('AGR_MODEL_EXTRACTOR must select an existing managed ParticleProbe.dll')
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
    result['joint_rest_transforms'] = read_joint_rest_transforms(document)
    count = len(positions)*result['joint_count']
    if not 1 <= result['joint_count'] <= 16 or len(result['blend_indices']) != count or len(result['blend_weights']) != count:
        raise ValueError('Cloth skin array dimensions differ')
    result['cloth_enable$0'] = paint['cloth_enable$0']
    return result


def read_joint_rest_transforms(document):
    """DMX bind frames, before a mesh importer relocates weapon attachments."""
    import numpy as np
    from .vendor.pose_math import rotate, normalize
    world = {}; visiting = set()
    def visit(element,parent):
        if id(element) in visiting:
            raise ValueError('Cycle in decompiled cloth joint hierarchy')
        if 'transform' not in element:return
        visiting.add(id(element));row=element['transform']
        position=np.asarray(row['position'],dtype=float);rotation=normalize(row['orientation'])
        if position.shape!=(3,) or not np.isfinite(position).all():
            raise ValueError('Invalid decompiled cloth joint bind frame')
        matrix=np.eye(4);matrix[:3,:3]=np.column_stack([rotate(rotation,axis) for axis in np.eye(3)])
        matrix[:3,3]=position;matrix=parent@matrix
        if id(element) in world and not np.allclose(world[id(element)],matrix,atol=1e-8,rtol=0):
            raise ValueError('Ambiguous decompiled cloth joint carrier')
        world[id(element)]=matrix
        for child in element.get('children',[]):visit(child,matrix)
        visiting.remove(id(element))
    visit(document.root['model'],np.eye(4))
    joints=list(document.root['model']['jointList'])
    if any(id(joint) not in world for joint in joints):
        raise ValueError('Decompiled cloth joint bind inventory differs from its hierarchy')
    return [world[id(joint)].tolist() for joint in joints]


def read_source_bone_rest(path):
    """Bind frames for actual skeleton joints, excluding model/mesh DAG nodes."""
    from .vendor import datamodel
    document=datamodel.load(str(path));joints=document.root['model']['jointList']
    transforms=read_joint_rest_transforms(document);result={}
    for joint,matrix in zip(joints,transforms):
        if joint.type!='DmeJoint':continue
        if joint.name in result:raise ValueError('Duplicate source skeleton joint identity')
        result[joint.name]=matrix
    if not result:raise ValueError('Decompiled cloth model lacks source skeleton bind frames')
    return result


def select_surface_proxy(modeldoc, proxy_files):
    """Separate generated surface cloth from authored bone-backsolve proxies.

    These are distinct reconstruction routes. A bone-backsolve proxy must not
    become the Surface Deform target for a different painted garment merely
    because both proxies belong to the same model.
    """
    rows = {}

    def visit(value, vertex_map=None):
        if isinstance(value, dict):
            if value.get('_class') == 'ClothVertexMap':
                vertex_map = value.get('name')
            if value.get('_class') == 'ClothProxyMeshFile':
                filename = value.get('filename')
                if not isinstance(filename, str):
                    raise ValueError('Cloth proxy lacks its modeldoc filename')
                name = Path(filename).name
                if name in rows:
                    raise ValueError('Duplicate modeldoc cloth proxy identity')
                rows[name] = dict(value, vertex_map=vertex_map)
            for child in value.values():
                visit(child, vertex_map)
        elif isinstance(value, list):
            for child in value:
                visit(child, vertex_map)

    visit(modeldoc)
    if set(rows) != set(proxy_files) or len(rows) != len(proxy_files):
        raise ValueError('Modeldoc cloth proxy inventory differs from extracted files')
    surfaces, pending = [], []
    for filename in proxy_files:
        row = rows[filename]
        if (row.get('add_bones_to_render_mesh') is True
                and row.get('back_solve_joints') is False
                and row.get('back_solve_joints_drive_meshes') is False):
            if not isinstance(row.get('vertex_map'), str) or not row['vertex_map'].strip():
                raise ValueError('Generated cloth proxy lacks an explicit vertex-map identity')
            surfaces.append((filename, row.get('vertex_map')))
        elif (row.get('add_bones_to_render_mesh') is False
              and row.get('back_solve_joints') is True
              and row.get('back_solve_joints_drive_meshes') is True):
            pending.append(filename + ': authored bone-backsolve cloth requires the bone simulation route')
        else:
            raise ValueError('Unqualified modeldoc cloth proxy deformation route: ' + filename)
    if len(surfaces) != 1:
        raise ValueError('Generated cloth surfaces require one qualified proxy-to-garment mapping')
    return surfaces[0], pending


def bind_proxy_node_indices(proxy, indices, physics):
    """Verify the native extractor's exact FE identity for every faced vertex."""
    fe = physics['m_pFeModel']
    rest = fe['m_InitPose']
    count = fe['m_nNodeCount']
    if (not isinstance(indices,list) or len(indices)!=len(proxy['vertices'])
            or len(rest)!=count or len(fe['m_CtrlName'])!=count):
        raise ValueError('Cloth proxy FE identity count differs from its mesh')
    faced = {vertex for face in proxy['faces'] for vertex in face}
    for vertex,node in enumerate(indices):
        if type(node) is not int or not -1<=node<count or (node<0 and vertex in faced):
            raise ValueError('Cloth proxy vertex lacks a valid FE control-node identity')
        if node<0:
            continue
        delta = [float(a)-float(b) for a,b in zip(proxy['vertices'][vertex],rest[node][:3])]
        if len(delta)!=3 or not all(math.isfinite(v) for v in delta) or sum(v*v for v in delta)>1e-6:
            raise ValueError('Cloth proxy FE node rest position differs from its mesh')
    proxy['fe_node_indices'] = list(indices)
    return proxy


def reconstruct(resource, provider, decoder, physics, *, command=None):
    """Generate a version-bound definition; never bundle game-derived proxy data."""
    resolved = provider.resolve(compiled(resource))
    if command is None:
        command, environment = discover_extractor()
    else:
        command, environment = list(command), None
    if not command or not Path(command[-1]).is_file():
        raise ConversionError('Cloth extractor is unavailable')
    tool = Path(command[-1])
    library = tool.with_name('ValveResourceFormat.dll')
    settings = {'adapter':sha(Path(__file__)), 'parser':sha(Path(__file__).parent/'vendor/datamodel.py'),
                'probe':sha(tool), 'vrf':sha(library) if library.is_file() else None,
                'physics':hashlib.sha256(json.dumps(physics,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()}
    settings['cloth_modules']=cloth_module_hashes()
    name = resource[:-2] if resource.endswith('_c') else resource
    def build(temp):
        output = temp/'extracted'; output.mkdir()
        proc = subprocess.run(command+['--decompile-cloth',str(provider.install_path/'game/citadel/gameinfo.gi'),
            name,str(output)],env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
        if proc.returncode:
            raise ConversionError('Automatic cloth extraction failed: '+proc.stderr[-1600:])
        report = json.loads((output/'cloth-extraction.json').read_text())
        if report.get('resource') != name or report.get('schema_version') != 1 or report.get('animation_files') != 0:
            raise ConversionError('Cloth extractor identity/format changed')
        model=physics.get('m_pFeModel')
        expected=0 if model is None else model.get('m_nNodeCount') if isinstance(model,dict) else None
        actual=report.get('fe_node_count')
        if (type(expected) is not int or not 0<=expected<=4096
                or type(actual) is not int or actual!=expected):
            raise ConversionError('Cloth extractor FE node count differs from decoded installed physics')
        if actual == 0:
            (temp/'result.json').write_text(json.dumps({'status':'not_applicable','reason':'Installed model has no FE cloth nodes'}))
            return
        files = {}
        for row in report['files']:
            p = output/row['path']
            if Path(row['path']).name != row['path'] or p.stat().st_size != row['bytes'] or sha(p) != row['sha256']:
                raise ConversionError('Cloth extractor file integrity mismatch')
            files[row['path']] = p
        proxies = report['proxy_files']
        if not proxies:
            from .fe_bone_motion import validate_motion_physics
            validate_motion_physics(physics)
            meshes=report.get('render_meshes',[])
            if not meshes or meshes[0].get('file') not in files:
                raise ValueError('Imported cloth bones lack their decompiled skeleton source')
            definition = dict(schema='agr_imported_fe_nodes_v1', physics=physics,
                source_bone_rest=read_source_bone_rest(files[meshes[0]['file']]),
                bone_basis_yaw_degrees=-90.,
                source_sha256={r['path']:r['sha256'] for r in report['files']},
                extractor=settings,
                scope='Installed FE control nodes and rods driving exact imported cloth bones; offline solver approximation',
                limitations=['Fixed-step node/rod approximation, not recorded Source cloth motion',
                             'Exponential animation attraction approximates Source goal forces',
                             'No map collision, wind or pre-shot simulation history'])
            (temp/'result.json').write_text(json.dumps({'status':'prepared','definition':definition},allow_nan=False))
            return
        if any(p not in files for p in proxies):
            raise ValueError('Multiple or missing cloth proxies require an explicit surface mapping')
        pending = []
        modeldoc_path = output/'model.vmdl'
        source_hashes = {r['path']:r['sha256'] for r in report['files']}
        modeldoc_identity = report.get('modeldoc')
        if modeldoc_identity is not None:
            if (modeldoc_identity.get('path') != 'model.vmdl'
                    or modeldoc_path.stat().st_size != modeldoc_identity.get('bytes')
                    or sha(modeldoc_path) != modeldoc_identity.get('sha256')):
                raise ConversionError('Cloth modeldoc metadata integrity mismatch')
            source_hashes['model.vmdl'] = modeldoc_identity['sha256']
        if len(proxies) == 1:
            proxy_file, vertex_map = proxies[0], None
        else:
            if not 0 < modeldoc_path.stat().st_size <= 8*1024*1024:
                raise ValueError('Cloth modeldoc exceeds supported size')
            (proxy_file, vertex_map), pending = select_surface_proxy(
                parse_kv3(modeldoc_path.read_text(encoding='utf-8-sig')), proxies)
            source_hashes['model.vmdl'] = sha(modeldoc_path)
        proxy = read_painted_mesh(files[proxy_file])
        if proxy is None:
            raise ValueError('Decompiled proxy has no cloth paint')
        node_rows = report.get('proxy_nodes')
        if node_rows is not None:
            if (not isinstance(node_rows,list) or len(node_rows)!=len(proxies)
                    or any(not isinstance(row,dict) for row in node_rows)
                    or {row.get('file') for row in node_rows} != set(proxies)):
                raise ValueError('Cloth proxy FE inventory differs from extracted files')
            row = next(row for row in node_rows if row['file']==proxy_file)
            bind_proxy_node_indices(proxy,row.get('node_indices'),physics)
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
                source_proxy_file=proxy_file,source_vertex_map=vertex_map,
                scope='Automatically reconstructed painted garment; Blender cloth approximation, not Source solver parity'))
        if not parts:
            raise ValueError('No painted render surface matches the reconstructed cloth proxy')
        # Back-solved proxies drive existing rig bones, rather than another
        # garment's generated FE surface. Native node identities bind the route.
        if node_rows is not None:
            from .fe_proxy_bones import proxy_bone_outputs, uniform_proxy_colliders
            for reason in list(pending):
                filename = reason.split(':',1)[0]
                bone_proxy = read_painted_mesh(files[filename])
                if bone_proxy is None:
                    raise ValueError('Back-solved proxy has no authored cloth paint')
                indices = next(row['node_indices'] for row in node_rows if row['file']==filename)
                bind_proxy_node_indices(bone_proxy,indices,physics)
                proxy_bone_outputs(bone_proxy,physics)
                uniform_proxy_colliders(bone_proxy,physics)
                parts.append(dict(schema='agr_decompiled_fe_bone_proxy_v1',
                    proxy=bone_proxy,physics=physics,source_proxy_file=filename,
                    bone_basis_yaw_degrees=-90.,blender_goal_pins='authored_strength',
                    scope='Authored back-solved proxy driving exact imported cloth bones; weighted rigid-fit approximation'))
                pending.remove(reason)
        if physics['m_pFeModel'].get('m_Effects'):
            pending.append('Named FE cloth effects: activation/default state is not recovered from replay data')
        definition=dict(schema='agr_decompiled_fe_proxy_bundle_v1',parts=parts,physics=physics,
            source_sha256=source_hashes,unsupported_parts=pending,limitations=pending,
            scope='Version-matched automatic cloth reconstruction; per-garment Blender approximation',
            extractor=settings)
        (temp/'result.json').write_text(json.dumps({'status':'prepared','definition':definition},allow_nan=False))
    entry = decoder._entry('automatic_cloth_v1',{compiled(resource):resolved.sha256},settings,build)
    return json.loads((entry/'result.json').read_text())
