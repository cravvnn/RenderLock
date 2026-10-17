"""Content-verified Source 2 Viewer conversion of resolved installed resources.

The converter reads an AGR-owned resource tree. It never exports into the game.
Source 2 Viewer: https://github.com/ValveResourceFormat/ValveResourceFormat
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
from urllib.parse import unquote

import numpy as np

from .vendor.kv3_text import parse_kv3
from .vendor.build_clip_cache import decode_frames


class ConversionError(ValueError):
    pass


def compiled(path):
    path = str(path).replace('\\', '/')
    return path if path.endswith('_c') else path + '_c'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def _json(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def read_glb(path):
    with Path(path).open('rb') as f:
        header = f.read(20)
        if len(header) != 20:
            raise ConversionError('Truncated GLB output')
        magic, version, length, size, kind = struct.unpack('<4sIII4s', header)
        if magic != b'glTF' or version != 2 or kind != b'JSON' or length != Path(path).stat().st_size:
            raise ConversionError('Invalid GLB output')
        return json.loads(f.read(size))


class AssetConverter:
    def __init__(self, executable, cache_dir, log=print):
        self.executable = Path(executable).resolve()
        self.root = Path(cache_dir).resolve() / 'derived-v1'
        self.root.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.stats = {'cache_hits': 0, 'conversions': 0}
        proc = subprocess.run([str(self.executable), '--help'], capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=30)
        required = ['--gltf_export_format', '--gltf_export_animations', '--gltf_animation_list',
                    '--gltf_export_materials', '--block']
        if proc.returncode or any(x not in proc.stdout for x in required):
            raise ConversionError('Select the Source2Viewer-CLI executable with glTF and DATA-block export support.')
        companions = [p for p in self.executable.parent.iterdir() if p.is_file() and
                      p.name.lower().startswith(('libskiasharp', 'libspirv-cross', 'valveresourceformat'))]
        self.identity = {'executable_sha256': sha(self.executable),
                         'companions': {p.name: sha(p) for p in sorted(companions)},
                         'adapter_sha256': sha(Path(__file__)),
                         'kv3_parser_sha256': sha(Path(__file__).parent / 'vendor/kv3_text.py'),
                         'decoder_sha256': sha(Path(__file__).parent / 'vendor/build_clip_cache.py')}

    def _entry(self, operation, inputs, settings, build):
        descriptor = {'operation': operation, 'inputs': inputs, 'settings': settings,
                      'converter': self.identity}
        key = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()
        dest = self.root / key
        manifest = dest / 'conversion.json'
        def valid_record():
            if not manifest.is_file():
                return False
            try:
                record = json.loads(manifest.read_text(encoding='utf-8'))
                return bool(record['descriptor'] == descriptor and record['files'] and all(
                    not Path(p).is_absolute() and '..' not in Path(p).parts and
                    (dest / p).is_file() and sha(dest / p) == h for p, h in record['files'].items()))
            except (ValueError, KeyError, OSError):
                return False
        if valid_record():
            self.stats['cache_hits'] += 1
            return dest
        temp = Path(tempfile.mkdtemp(prefix=key[:12] + '-', dir=self.root))
        try:
            build(temp)
            files = {p.relative_to(temp).as_posix(): sha(p) for p in sorted(temp.rglob('*')) if p.is_file()}
            if not files:
                raise ConversionError('Converter produced no files')
            _json(temp / 'conversion.json', {'descriptor': descriptor, 'files': files})
            if valid_record():
                self.stats['cache_hits'] += 1
                return dest  # another completed writer already published this key
            if dest.exists():
                shutil.rmtree(dest)  # only this owned, invalid derived-cache entry
            os.replace(temp, dest)
            self.stats['conversions'] += 1
            return dest
        finally:
            if temp.exists():
                shutil.rmtree(temp)

    def model_constraints(self, resource_path, provider):
        """Decode authored constraints with the nested-string-capable KV3 parser."""
        from .map_text import parse_map_kv3
        from .model_constraints import ModelConstraints
        resolved = provider.resolve(compiled(resource_path))
        def build(temp):
            proc = subprocess.run([str(self.executable), '-i', str(resolved.local_path), '-b', 'DATA'],
                                  capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
            if proc.returncode:
                raise ConversionError('Model constraint DATA decode failed: '+proc.stderr[-2000:])
            data = parse_map_kv3(proc.stdout)
            skeleton = data['m_modelSkeleton']
            keys = parse_map_kv3(data['m_modelInfo']['m_keyValueText'])
            spec = {'schema_version':1, 'names':skeleton['m_boneName'],
                    'parents':skeleton['m_nParent'], 'definitions':keys.get('BoneConstraintList',[]),
                    'resource':compiled(resource_path), 'resource_sha256':resolved.sha256}
            ModelConstraints(spec['names'],spec['parents'],spec['definitions'])
            _json(temp / 'constraints.json', spec)
        dest = self._entry('model_constraints_v1', {compiled(resource_path):resolved.sha256},
                           {'parser_sha256':sha(Path(__file__).parent/'map_text.py')}, build)
        result = json.loads((dest/'constraints.json').read_text(encoding='utf-8'))
        ModelConstraints(result['names'],result['parents'],result['definitions'])
        return result

    def dump(self, resource_path, provider):
        resolved = provider.resolve(compiled(resource_path))
        def build(temp):
            proc = subprocess.run([str(self.executable), '-i', str(resolved.local_path), '-b', 'DATA'],
                                  capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
            if proc.returncode:
                raise ConversionError(f'DATA decode failed for {resource_path}: {proc.stderr[-2000:]}')
            try:
                data = parse_kv3(proc.stdout)
            except (ValueError, IndexError) as exc:
                raise ConversionError(f'Unsupported DATA for {resource_path}: {exc}') from exc
            _json(temp / 'data.json', data)
        dest = self._entry('DATA', {compiled(resource_path): resolved.sha256}, {}, build)
        return json.loads((dest / 'data.json').read_text(encoding='utf-8'))

    def decode_clip(self, resource_path, provider):
        resolved = provider.resolve(compiled(resource_path))
        data = self.dump(resource_path, provider)
        skeleton = provider.resolve(compiled(data['m_skeleton']))
        sk = self.dump(data['m_skeleton'], provider)
        if data.get('m_secondaryAnimations'):
            raise ConversionError(f'Secondary animation tracks are unsupported: {resource_path}')
        def build(temp):
            try:
                q, t, s = decode_frames(data)
            except (AssertionError, IndexError, ValueError) as exc:
                raise ConversionError(f'Unsupported compressed clip {resource_path}: {exc}') from exc
            if len(sk['m_boneIDs']) != q.shape[1]:
                raise ConversionError(f'Clip/skeleton bone count differs: {resource_path}')
            np.savez_compressed(temp / 'poses.npz', q=q, t=t, s=s)
        dest = self._entry('numeric_clip', {compiled(resource_path): resolved.sha256,
                                          compiled(data['m_skeleton']): skeleton.sha256}, {}, build)
        return data, dest / 'poses.npz'

    def model(self, resource_path, provider):
        """Convert geometry, rig and materials; sampled animations are decoded separately."""
        visual = {'.vmdl_c', '.vmesh_c', '.vmat_c', '.vtex_c', '.vnmskel_c'}
        queue = [compiled(resource_path)]
        records, skipped = {}, set()
        while queue:
            path = queue.pop()
            if path in records:
                continue
            resolved = provider.resolve(path)
            records[path] = resolved
            for dep in resolved.dependencies:
                dep = compiled(dep)
                if Path(dep).suffix.lower() in visual:
                    queue.append(dep)
                else:
                    skipped.add(dep)
        # Material texture channels are discovered from compiled shader files,
        # not just RERL dependencies. Without these, VRF can silently omit normal
        # textures and mispack roughness while still producing a valid GLB.
        shaders = sorted(p for p in provider.resources if p.endswith('.vcs'))
        for path in shaders:
            resolved = provider.resolve(path)
            records[resolved.resource_path] = resolved
        inputs = {p: r.sha256 for p, r in sorted(records.items())}
        settings = {'format': 'glb', 'materials': True, 'rig': True,
                    'source_actions': False, 'lod': 'exporter_default', 'textures_adapt': True,
                    'compiled_shader_inventory': True}
        def build(temp):
            # Flat extraction preserves paths, with a private search configuration.
            tree = temp / 'input'
            tree.mkdir()
            for p, resolved in records.items():
                target = tree / p
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(resolved.local_path, target)
            gameinfo = tree / 'gameinfo.gi'
            # VRF's --game loader does not expand this engine token for loose
            # cache trees. An explicit private path works on both drive layouts.
            gameinfo.write_text('"GameInfo" { "FileSystem" { "SearchPaths" { "Game" ' +
                                json.dumps(tree.resolve().as_posix()) + ' } } }\n', encoding='utf-8')
            out = temp / 'model.glb'
            command = [str(self.executable), '-i', str(tree / compiled(resource_path)),
                       '-o', str(out), '-d', '--game', str(gameinfo),
                       '--gltf_export_format', 'glb', '--gltf_export_materials',
                       '--gltf_export_animations', '--gltf_animation_list', '__agr_no_source_actions__',
                       '--gltf_textures_adapt', '--gltf_export_extras']
            self.log(f'Converting installed model: {resource_path}')
            proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', timeout=600)
            output = proc.stdout + '\n' + proc.stderr
            (temp / 'converter.log').write_text(output, encoding='utf-8')
            if proc.returncode or not out.is_file():
                raise ConversionError(f'Model conversion failed for {resource_path}: {output[-3000:]}')
            missing_shaders = re.findall(r'Failed to find shader [\"\']([^\"\']+)[\"\']', output)
            if missing_shaders:
                raise ConversionError('Model exporter could not resolve material shaders: ' +
                                      ', '.join(sorted(set(missing_shaders))))
            missing = re.findall(r'Failed to load [\"\']([^\"\']+)[\"\']', output)
            missing_visual = [p for p in missing if Path(p).suffix.lower() in visual]
            if missing_visual:
                raise ConversionError('Converter could not resolve visual dependencies: ' + ', '.join(sorted(set(missing_visual))))
            document = read_glb(out)
            if not document.get('meshes') or not document.get('skins'):
                raise ConversionError(f'Converted hero has no usable geometry or skin: {resource_path}')
            nodes = document.get('nodes', [])
            skinned = [n for n in nodes if 'mesh' in n and 'skin' in n]
            if not skinned:
                raise ConversionError('Converted hero has no mesh node bound to its skeleton')
            for node in skinned:
                if not (isinstance(node['skin'], int) and 0 <= node['skin'] < len(document['skins'])
                        and isinstance(node['mesh'], int) and 0 <= node['mesh'] < len(document['meshes'])):
                    raise ConversionError('Invalid skin/mesh node index in converted model')
                joints = document['skins'][node['skin']].get('joints', [])
                if not joints or len(set(joints)) != len(joints) or any(
                        not isinstance(j, int) or not 0 <= j < len(nodes) for j in joints):
                    raise ConversionError('Invalid joint binding in converted model')
            if document.get('animations'):
                raise ConversionError('Model exporter ignored the source-animation filter')
            for entry in document.get('images', []) + document.get('buffers', []):
                uri = entry.get('uri')
                if not uri or uri.startswith('data:'):
                    continue
                linked = (temp / unquote(uri)).resolve()
                if not linked.is_relative_to(temp.resolve()) or not linked.is_file():
                    raise ConversionError(f'Missing or unsafe exported file: {uri}')
            _json(temp / 'model_report.json', {'resource': compiled(resource_path),
                  'inputs': inputs, 'skipped_nonvisual_dependencies': sorted(skipped),
                  'compiled_shader_resources': len(shaders),
                  'meshes': len(document['meshes']), 'skins': len(document['skins']),
                  'images': len(document.get('images', [])), 'source_actions': 0,
                  'remaining_converter_missing': [p for p in missing if p not in missing_visual]})
            shutil.rmtree(tree)
            # Physics export is not a simulation implementation; retain no extra scene.
            for physics in temp.glob('model_physics.*'):
                physics.unlink()
        dest = self._entry('hero_glb', inputs, settings, build)
        return dest / 'model.glb', json.loads((dest / 'model_report.json').read_text())
