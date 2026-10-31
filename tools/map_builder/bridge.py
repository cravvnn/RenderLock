# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Cravvnn
"""Standalone GPL map-builder bridge; invoked as a separate command-line tool.

Loads the unmodified pinned deadlockrigs source in this process. RenderLock's
application process communicates using ordinary request files and map outputs.
See COPYING and README.txt beside this file.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import uuid

COMMIT = '0d1958ab553630df997a93fda06d3ab9ed6994c3'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding='utf-8'))
    root = Path(request['root']).resolve()
    source = args.source.resolve()
    if (request['builder_commit'] != COMMIT or not (source/'LICENSE').is_file()
            or request['map'] != 'dl_midtown'):
        raise ValueError('Unsupported map-builder source or request')
    paths = {}
    for key in ('work', 'out', 's2v_cache'):
        path = Path(request[key]).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError('Map-builder working path leaves its owned job folder')
        path.mkdir(parents=True, exist_ok=True)
        paths[key] = str(path)
    threads = request.get('threads', 2)
    if type(threads) is not int or not 1 <= threads <= 8:
        raise ValueError('Invalid map-builder worker count')
    sys.path.insert(0, str(source/'tools'))
    from mapbuild import MapBuilder, MapFiles, game_stamp
    key = request['input_identity_sha256']
    if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('Missing map-builder input identity')
    state_path = root/'checkpoints.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = dict(input_identity_sha256=key,stages={})
    if state.get('input_identity_sha256') != key:
        raise ValueError('Map-builder cache belongs to another source request')

    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream,'sha256').hexdigest()

    def inventory(folder):
        folder = Path(folder).resolve()
        if not folder.is_relative_to(root):
            raise ValueError('Checkpoint output leaves its owned job folder')
        rows = []
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                if not path.resolve().is_relative_to(root):
                    raise ValueError('Checkpoint resource leaves its owned job folder')
                rows.append(dict(file=path.relative_to(root).as_posix(),
                    bytes=path.stat().st_size,sha256=digest(path)))
        return rows

    def save_state():
        temporary = state_path.with_suffix('.new.json')
        temporary.write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')
        temporary.replace(state_path)

    def checkpoint(name, folder):
        state['stages'][name] = inventory(folder)
        save_state()

    def valid(name):
        rows = state['stages'].get(name)
        if not isinstance(rows,list) or not rows:
            return False
        try:
            for row in rows:
                path = (root/row['file']).resolve()
                if (not path.is_relative_to(root) or not path.is_file()
                        or path.stat().st_size != row['bytes'] or digest(path) != row['sha256']):
                    return False
        except (OSError,KeyError,TypeError):
            return False
        return True

    def preserve(folder):
        folder = Path(folder).resolve()
        if not folder.is_relative_to(root) or folder == root:
            raise ValueError('Cannot preserve a path outside the owned job')
        if folder.exists():
            folder.rename(folder.with_name(folder.name+'.preserved-'+uuid.uuid4().hex))

    class ControlledBuilder(MapBuilder):
        def run(self, command, *positional, **keywords):
            command = list(command)
            executable = Path(command[0]).resolve()
            if executable in {Path(request['blender']).resolve(), Path(request['s2v']).resolve()}:
                if '--threads' in command:
                    command[command.index('--threads')+1] = str(threads)
                else:
                    command[1:1] = ['--threads', str(threads)]
            if executable == Path(request['blender']).resolve() and '--disable-autoexec' not in command:
                command.insert(1, '--disable-autoexec')
            return super().run(command, *positional, **keywords)

        def export(self,m):
            super().export(m);checkpoint('export',m.export)

        def rebatch(self,m):
            super().rebatch(m);checkpoint('rebatch',Path(m.rebatched).parent)

        def lighting(self,m):
            super().lighting(m);checkpoint('lighting',m.lighting)

        def import_(self,m):
            preserve(m.out)
            super().import_(m)
            checkpoint('import',m.out)

        def build_map(self,slug,force=False):
            m = MapFiles(slug,paths['work'],paths['out'])
            if not force and valid('import'):
                print('Native map cache verified; reusing the completed build.',flush=True)
                return
            if not force and all(valid(name) for name in ('export','rebatch','lighting')):
                print('Resuming map import from verified extracted assets.',flush=True)
                self.import_(m)
                Path(m.stamp_file).write_text(json.dumps(game_stamp(self.pak,slug,False)),encoding='utf-8')
                return
            preserve(m.dir)
            state['stages'] = {}
            save_state()
            return super().build_map(slug,force=True)

    # Explicit paths prevent discovery fallbacks from creating another cache or
    # picking an unrelated converter. Mods remain disabled for standard maps.
    options = SimpleNamespace(keep=bool(request.get('keep_work', False)),
        blender=request['blender'], s2v=request['s2v'], pak=request['pak'],
        python=sys.executable, exports=False, mods=False, **paths)
    for name in ('DLRIG_WORK', 'DLRIG_OUT', 'DLRIG_EXPORTS'):
        os.environ.pop(name, None)
    lock = (root/'build.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        ControlledBuilder(options).build_map(request['map'])
    finally:
        lock.close()


if __name__ == '__main__':
    main()
