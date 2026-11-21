"""Regenerate/check reviewed compatibility contracts without running Deadlock.

--check (default) detects drift without writing. --write emits reviewed data;
it never approves a build or changes the manifest. --verify-game-dir additionally
checks the saved current-module/schema/code evidence against local PE files.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))
import generate_profile as camera
import generate_sound_profile as sound

CONTRACT = 'native/profiles/runtime-contracts.json'
ANCHOR_FIELDS = ('controller', 'observer_base', 'composition', 'blend', 'blend_caller',
    'image_size', 'rig', 'manager', 'manager_table', 'camera_table', 'controller_table',
    'entity_list', 'observer_table', 'services_table', 'pawn_table', 'observer_services_offset')
ANCHOR_OPTIONAL_FIELDS = ('local_player_table',)
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z_0-9]*\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
SCENE_FIELDS = ('object', 'vtable', 'frame', 'stack', 'records', 'capacity', 'ids',
                'transforms', 'cpu_transforms', 'owner_offset', 'renderer_gpu_buffer_offset')
UNLOCKER_METADATA = 'third_party/cvar_unlocker/THIRD_PARTY.json'
UNLOCKER_ARTIFACT = 'bin/win64/server.dll'


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def rip_reference(entry, entries, image_size):
    """Bind a reviewed RVA to a pinned x64 RIP-relative MOV/LEA operand."""
    reference = entry['rip_reference']
    raw = bytes.fromhex(reference['bytes'])
    require(entry['kind'] == 'rva' and len(raw) == 7 and raw[0] in (0x48, 0x4c)
            and raw[1] in (0x89, 0x8b, 0x8d) and raw[2] & 0xc7 == 0x05,
            'Unsupported reviewed RIP-relative instruction')
    rva = bounded(reference['rva'], image_size, size=len(raw))
    target = rva + len(raw) + struct.unpack_from('<i', raw, 3)[0]
    require(target == integer(entry['value']), 'Reviewed RIP reference does not resolve to its RVA')
    require(any(integer(span['rva']) <= rva and rva + len(raw) <= integer(span['rva']) + span['size']
                for audit in entries.values() if audit['kind'] == 'spans' and audit['module'] == entry['module']
                for span in audit['value']), 'Reviewed RIP reference is outside the pinned code spans')
    return rva, raw


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_object)


def unlocker_contract(root):
    """One reviewed artifact identity for the launcher and native front factory."""
    root = Path(root).absolute()

    def read_plain(relative, limit):
        path = root / relative
        for entry in (path, *path.parents):
            info = entry.lstat()
            flags = getattr(info, 'st_file_attributes', 0)
            require(not stat.S_ISLNK(info.st_mode) and
                    not flags & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400),
                    'Unlocker contract paths must be ordinary files/directories')
            if entry == root:
                break
        before = path.stat()
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit,
                'Unlocker contract file is missing, empty or exceeds its bound')
        with path.open('rb') as handle:
            raw = handle.read(limit + 1)
        after = path.stat()
        require(len(raw) == before.st_size <= limit and
                all(getattr(before, key) == getattr(after, key)
                    for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')),
                'Unlocker contract file changed while reading')
        return raw

    metadata = json.loads(read_plain(UNLOCKER_METADATA, 64 * 1024).decode('utf-8'),
                          object_pairs_hook=unique_object)
    require(isinstance(metadata, dict) and metadata.get('bundled_file') == UNLOCKER_ARTIFACT,
            'Unlocker artifact must use the reviewed fixed bundle path')
    digest = metadata.get('bundled_file_sha256')
    size = metadata.get('bundled_file_size')
    version = metadata.get('version')
    require(isinstance(digest, str) and HASH.fullmatch(digest), 'Invalid unlocker artifact SHA-256')
    require(type(size) is int and 64 <= size <= 2 * 1024 * 1024, 'Invalid unlocker artifact size')
    require(isinstance(version, str) and re.fullmatch(r'v[0-9][A-Za-z0-9_.-]{0,79}', version),
            'Invalid unlocker artifact version')
    artifact = read_plain('third_party/cvar_unlocker/' + UNLOCKER_ARTIFACT, 2 * 1024 * 1024)
    require(len(artifact) == size and hashlib.sha256(artifact).hexdigest() == digest,
            'Unlocker artifact differs from its reviewed metadata')
    return {'SHA256': digest, 'SIZE': size, 'VERSION': version}


def integer(value):
    require(type(value) is int or isinstance(value, str) and re.fullmatch(r'0x[0-9a-f]+', value),
            'Expected integer or lowercase hexadecimal value')
    return int(value, 16) if isinstance(value, str) else value


def bounded(value, end, *, size=1):
    value = integer(value)
    require(type(size) is int and 0 < size <= 0x100000 and 0 < value <= end - size,
            'Reviewed address/offset span is outside its module or structure')
    return value


def profile_at(root, name, manifest):
    require(isinstance(name, str) and Path(name).name == name and name in manifest['profiles'],
            'Profile must be an explicitly listed manifest file')
    return read_json(root / 'native/profiles' / name)


def byte_reference(entry, entries, image_size):
    """Bind a runtime byte guard to its named address, including relocations."""
    name = entry.get('at')
    require(entry.get('kind') == 'bytes' and isinstance(name, str) and name in entries,
            'Reviewed bytes require a named address')
    target = entries[name]
    require(target.get('kind') == 'rva' and target.get('module') == entry.get('module'),
            'Reviewed byte address must belong to the same module')
    offset = integer(entry.get('at_offset', 0))
    require(0 <= offset <= 4096, 'Invalid reviewed byte address offset')
    raw = bytes.fromhex(entry['value'])
    require(0 < len(raw) < 4096, 'Invalid reviewed byte span')
    return bounded(integer(target['value']) + offset, image_size, size=len(raw)), raw


def verify_byte_reference(image, entry, entries, image_size):
    rva, expected = byte_reference(entry, entries, image_size)
    require(image.read(rva, len(expected)) == expected,
            'Reviewed runtime guard bytes differ from the installed module')


def load_contract(root=ROOT):
    root = Path(root)
    data = read_json(root / CONTRACT)
    require(data['format'] == 1, 'Unsupported runtime contract format')
    manifest = read_json(root / 'native/profiles/manifest.json')
    require(len(set(manifest['profiles'])) == len(manifest['profiles']) and
            all(isinstance(name,str) and Path(name).name == name for name in manifest['profiles']),
            'Manifest profile names must be unique filenames')
    modules = {}
    for name, entry in data['modules'].items():
        require(IDENTIFIER.fullmatch(name), 'Invalid module name')
        path = entry['path']
        require(path in manifest['modules'], 'Module is absent from compatibility manifest')
        if 'profile' in entry:
            section = profile_at(root, entry['profile'], manifest)[entry['section']]
            digest, size = section[entry['hash_key']], integer(section[entry['size_key']])
        else:
            digest, size = entry['sha256'], integer(entry['image_size'])
        require(HASH.fullmatch(digest) and digest in manifest['modules'][path]['accepted'],
                'Runtime module identity must already be reviewed in the manifest')
        require(0 < size < 0x80000000, 'Invalid reviewed module size')
        modules[name] = dict(sha256=digest, image_size=size, path=path)
    require(manifest['modules'][modules['client']['path']]['feature_pins']['automatic preload']
            == [modules['client']['sha256']], 'Automatic preload pin differs from its runtime contract')
    for name, field in data['schema'].items():
        require(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\.m_[A-Za-z_0-9]+', name), 'Invalid named schema field')
        module = modules[field['module']]
        bounded(field['offset'], 0x8000)
        bounded(field['record_rva'], module['image_size'], size=20)
        require(field.get('evidence') and len(field['getters']) >= 2, 'Schema field needs independent getter evidence')
        for getter in field['getters']:
            bounded(getter['rva'], module['image_size'], size=17)
            bounded(getter['slot'], 0x1000, size=8)
            expected = (bytes.fromhex('48 8b 89') + struct.pack('<I', integer(field['offset']))
                        + bytes.fromhex('48 8b 01 48 ff a0') + struct.pack('<I', integer(getter['slot'])))
            require(bytes.fromhex(getter['bytes']) == expected, 'Schema offset conflicts with its saved stock getter')
            evidence = [span for entries in data['groups'].values() for entry in entries.values()
                        if entry['kind'] == 'spans' and entry['module'] == field['module']
                        for span in entry['value'] if integer(span['rva']) == integer(getter['rva'])
                        and span['size'] == len(expected)]
            require(evidence and all(span['sha256'] == hashlib.sha256(expected).hexdigest() for span in evidence),
                    'Schema getter is absent from or contradicts the runtime code audit')
    require(set(data['groups']) == {'Preload','ReplayCamera','FollowCapabilities','FollowTarget','AttachRuntime'},
            'Runtime contract groups are incomplete or unknown')
    values = {}
    for group, entries in data['groups'].items():
        require(IDENTIFIER.fullmatch(group), 'Invalid generated group name')
        values[group] = {}
        for name, entry in entries.items():
            require(IDENTIFIER.fullmatch(name), 'Invalid generated constant name')
            module = modules[entry['module']]
            kind, value = entry['kind'], entry.get('value')
            if kind == 'module_hash':
                value = module['sha256']
            elif kind == 'module_size':
                value = module['image_size']
            elif kind == 'schema_offset':
                field = data['schema'][entry['field']]
                require(field['module'] == entry['module'], 'Schema field belongs to another module')
                value = integer(field['offset'])
            elif kind in ('rva', 'offset'):
                value = bounded(value, module['image_size'] if kind == 'rva' else 0x8000)
            elif kind == 'rvas':
                value = tuple(bounded(v, module['image_size']) for v in value)
                require(value and len(set(value)) == len(value), 'Duplicate/empty reviewed addresses')
            elif kind == 'rva_map':
                require(value and all(IDENTIFIER.fullmatch(key) for key in value), 'Invalid named references')
                value = {key: bounded(v, module['image_size']) for key, v in value.items()}
            elif kind == 'spans':
                require(value, 'Empty code audit')
                spans = []
                for span in value:
                    require(HASH.fullmatch(span['sha256']), 'Code span lacks saved evidence digest')
                    spans.append((bounded(span['rva'], module['image_size'], size=span['size']), span['size']))
                require(len(set(spans)) == len(spans), 'Duplicate code audit span')
                value = tuple(spans)
            elif kind == 'bytes':
                _rva, value = byte_reference(entry, entries, module['image_size'])
            else:
                raise ContractError('Unknown contract value kind: ' + str(kind))
            values[group][name] = value
        for entry in entries.values():
            if 'rip_reference' in entry:
                rip_reference(entry, entries, modules[entry['module']]['image_size'])
    fields = data['attach_fields']
    require(fields and len({f['purpose'] for f in fields}) == len(fields), 'Duplicate/empty attach field contract')
    for f in fields:
        require(all(isinstance(f[key], str) and IDENTIFIER.fullmatch(f[key])
                    for key in ('purpose', 'layout_class', 'field')), 'Invalid named attach field')
        require(type(f['required']) is bool and isinstance(f['expected_type'], str) and f['expected_type'],
                'Attach fields need explicit type and required/optional status')
    wire = data['attach_wire_fields']
    require(wire == ['scene_node','owner','player_origin','player_angles','eye_offset','eye_angles','scene_child','scene_sibling'],
            'Attach ABI field order must remain unchanged')
    components = data['view_offset_components']
    require(len(components) == 3 and all(type(v) is int and 0 <= v < 128 for v in components)
            and components == sorted(set(components)), 'Invalid view-offset components')
    seen = set()
    for anchor in data['follow_anchors']:
        require(re.fullmatch(r'\d+', anchor['build']), 'Invalid anchor build identifier')
        profile = profile_at(root, anchor['profile'], manifest)['client']
        digest, size = profile['client_sha256'], profile['size_of_image']
        require(digest not in seen and digest in manifest['modules'][modules['client']['path']]['accepted'],
                'Duplicate or unreviewed Follow anchor profile')
        seen.add(digest)
        require(set(ANCHOR_FIELDS) <= set(anchor['values']) <= set(ANCHOR_FIELDS + ANCHOR_OPTIONAL_FIELDS),
                'Follow anchor layout is incomplete or has unknown members')
        require(integer(anchor['values']['image_size']) == size, 'Follow image size differs from its reviewed profile')
        for key, value in anchor['values'].items():
            if key != 'image_size':
                bounded(value, 0x8000 if key.endswith('_offset') else size)
        require(anchor['schema_field'] == 'C_BasePlayerPawn.m_pObserverServices' and anchor.get('evidence'),
                'Follow observer field needs explicit named review')
        if digest == modules['client']['sha256']:
            require(integer(anchor['values']['observer_services_offset']) ==
                    integer(data['schema'][anchor['schema_field']]['offset']),
                    'Current Follow anchor observer field differs from the reviewed schema')
            if 'local_player_table' in anchor['values']:
                require(integer(anchor['values']['local_player_table']) == values['FollowTarget']['PLAYER_PAWN_VTABLE'],
                        'Current Follow local player type differs from the reviewed target profile')
        require(anchor['spans'], 'Follow anchor needs exact code evidence')
        covered = set()
        caller = integer(anchor['values']['blend_caller'])
        blend = integer(anchor['values']['blend'])
        caller_verified = False
        for span in anchor['spans']:
            raw = bytes.fromhex(span['bytes'])
            rva = bounded(span['rva'], size, size=len(raw))
            require(rva not in covered, 'Duplicate Follow code span')
            covered.add(rva)
            offset = caller - rva
            if 5 <= offset <= len(raw) and raw[offset - 5] == 0xe8:
                destination = caller + int.from_bytes(raw[offset - 4:offset], 'little', signed=True)
                caller_verified |= destination == blend
        require(all(integer(anchor['values'][name]) in covered for name in ('composition','blend')),
                'Follow hook entry is absent from exact code evidence')
        require(caller_verified, 'Follow blend caller lacks an exact reviewed call to the blend hook')
        anchor['_sha256'] = digest
    return data, modules, values


def cpp_value(name, value):
    if isinstance(value, str):
        return f'inline constexpr char {name}[] = {json.dumps(value)};'
    if type(value) is int:
        return f'inline constexpr std::uintptr_t {name} = {hex(value)};'
    if isinstance(value, bytes):
        return f'inline constexpr unsigned char {name}[] = {{' + ', '.join(hex(v) for v in value) + '};'
    if isinstance(value, dict):
        return f'inline constexpr NamedRva {name}[] = {{' + ', '.join(
            '{' + json.dumps(k) + ', ' + hex(v) + '}' for k,v in value.items()) + '};'
    if value and isinstance(value[0], tuple):
        return f'inline constexpr CodeSpan {name}[] = {{' + ', '.join(
            '{' + hex(rva) + ', ' + str(size) + '}' for rva,size in value) + '};'
    return f'inline constexpr std::uintptr_t {name}[] = {{' + ', '.join(hex(v) for v in value) + '};'


def render_runtime(data, values):
    py = ['"""Generated by tools/generate_compatibility.py; edit reviewed profiles, not this file."""', '']
    cpp = ['// Generated by tools/generate_compatibility.py. Do not edit by hand.', '#pragma once',
           '#include <cstddef>', '#include <cstdint>', 'namespace dolly { namespace reviewed {',
           'struct CodeSpan { std::uintptr_t rva; std::size_t size; };',
           'struct NamedRva { const char* name; std::uintptr_t rva; };']
    for group, entries in values.items():
        py.append(f'class {group}:')
        cpp.append(f'namespace {group} {{')
        for name, value in entries.items():
            py.append(f'    {name} = {repr(value)}')
            cpp.append(cpp_value(name, value))
        py += ['', '']
        cpp.append('}')
    specs = tuple(tuple(f[k] for k in ('purpose','layout_class','field','expected_type','required')) for f in data['attach_fields'])
    py += ['ATTACH_FIELD_SPECS = ' + repr(specs), 'ATTACH_WIRE_FIELDS = ' + repr(tuple(data['attach_wire_fields'])),
           'VIEW_OFFSET_VALUE_OFFSETS = ' + repr(tuple(data['view_offset_components'])), '']
    cpp += ['enum class AttachFieldIndex : unsigned { ' + ', '.join(data['attach_wire_fields']) + ' };',
            'inline constexpr std::size_t kViewOffsetComponents[] = {' + ', '.join(map(str,data['view_offset_components'])) + '};',
            '} } // namespace dolly::reviewed', '']
    return '\n'.join(py), '\n'.join(cpp)


def render_follow(data):
    lines = ['// Generated by tools/generate_compatibility.py. Do not edit by hand.', '#pragma once',
             '// Included inside the bridge scope; no nested dolly namespace.',
             'struct FollowCodeSpan { std::uintptr_t rva; const unsigned char* bytes; std::size_t size; };']
    for anchor in data['follow_anchors']:
        build = anchor['build']
        for span in anchor['spans']:
            raw = bytes.fromhex(span['bytes'])
            name = 'kFollowCode' + build + '_' + format(integer(span['rva']), 'x')
            lines.append(f'constexpr unsigned char {name}[] = {{')
            for index in range(0,len(raw),16):
                lines.append('    ' + ', '.join(f'0x{b:02x}' for b in raw[index:index+16]) + ',')
            lines.append('};')
        lines.append(f'constexpr FollowCodeSpan kFollowCode{build}Spans[] = {{')
        for span in anchor['spans']:
            name = 'kFollowCode' + build + '_' + format(integer(span['rva']), 'x')
            lines.append('    {' + span['rva'] + f', {name}, sizeof({name})' + '},')
        lines.append('};')
    lines += ['struct FollowAnchorProfile {', '    const char* hash;']
    lines += [f'    std::uintptr_t {name};' for name in ANCHOR_FIELDS + ANCHOR_OPTIONAL_FIELDS]
    lines += ['    const FollowCodeSpan* spans;', '    std::size_t count;', '};',
              'constexpr FollowAnchorProfile kFollowAnchorProfiles[] = {']
    for anchor in data['follow_anchors']:
        name = 'kFollowCode' + anchor['build'] + 'Spans'
        lines.append('    {' + json.dumps(anchor['_sha256']) + ', ' + ', '.join(
            anchor['values'].get(key, '0x0') for key in ANCHOR_FIELDS + ANCHOR_OPTIONAL_FIELDS) +
            f', {name}, sizeof({name}) / sizeof(*{name})' + '},')
    lines += ['};', '']
    return '\n'.join(lines)


def player_scenes(root, data):
    """Keep the producer ABI, complete scene layout and renderer pair together."""
    manifest = read_json(root / 'native/profiles/manifest.json')
    result, seen = [], set()
    require(data.get('player_scenes'), 'Missing reviewed player scene profiles')
    for entry in data['player_scenes']:
        profile = profile_at(root, entry['profile'], manifest)
        require(profile['profile_format'] == 1 and profile['module'] == 'scenesystem.dll',
                'Invalid player scene profile')
        digest = profile['sha256']
        require(HASH.fullmatch(digest) and digest not in seen and
                digest in manifest['modules']['bin/win64/scenesystem.dll']['accepted'],
                'Duplicate or unreviewed player scene identity')
        seen.add(digest)
        renderer = entry['renderer_sha256']
        require(HASH.fullmatch(renderer) and
                renderer in manifest['modules']['bin/win64/rendersystemdx11.dll']['accepted'],
                'Unreviewed player renderer identity')
        size, renderer_size = integer(profile['size_of_image']), integer(entry['renderer_size'])
        require(0 < size < 0x80000000 and 0 < renderer_size < 0x80000000, 'Invalid player module size')
        producer, layout = profile['producer'], profile['layout']
        rva = bounded(producer['rva'], size, size=32)
        raw = bytes.fromhex(producer['prologue'])
        # These are the two reviewed entry/ABI shapes. Another ABI requires a
        # reviewed forwarding implementation, not just a new profile value.
        prologues = {
            9: bytes.fromhex('488bc44c89482048895010488948085553488d68e84881ec08010000488970e8'),
            10: bytes.fromhex('488bc44c8948204c89401848895010488948085553488d68e84881ec08010000'),
        }
        require(type(producer['arguments']) is int and raw == prologues.get(producer['arguments']),
                'Player producer entry and forwarding ABI disagree')
        require(layout['record_stride'] == 32 and type(layout['record_stride']) is int,
                'Player record stride differs from CPU/GPU ownership guards')
        require(profile.get('validation'), 'Player scene profile lacks review evidence')
        fields = [bounded(layout[key], 0x8000 if key.endswith('_offset') else size)
                  for key in SCENE_FIELDS]
        if 'end_rva' in producer:
            end = integer(producer['end_rva'])
            bounded(rva, size, size=end-rva)
            require(HASH.fullmatch(producer['sha256']), 'Player producer span lacks evidence digest')
        result.append(dict(profile=profile, scene_hash=digest, scene_size=size,
                           renderer_hash=renderer, renderer_size=renderer_size,
                           arguments=producer['arguments'], fields=fields, rva=rva, prologue=raw))
    return result


def render_player_scenes(rows):
    lines = ['// Generated by tools/generate_compatibility.py. Do not edit by hand.', '#pragma once',
             '#include <cstdint>', '#include <cstring>', 'namespace dolly::player_capture {',
             'struct SceneLayout {',
             '    std::uintptr_t object, table, producer, frame, stack, records, capacity, ids, transforms;',
             '    std::uintptr_t cpu_transforms, owner, gpu_buffer;', '};',
             'struct SceneProfile {',
             '    const char* scene_hash; std::uint32_t scene_size;',
             '    const char* renderer_hash; std::uint32_t renderer_size;',
             '    bool september; SceneLayout layout; unsigned char prologue[32];', '};',
             'inline constexpr SceneProfile kSceneProfiles[] = {']
    for row in rows:
        values = row['fields'][:2] + [row['rva']] + row['fields'][2:]
        lines += ['    {' + json.dumps(row['scene_hash']) + ', ' + hex(row['scene_size']) + ',',
                  '     ' + json.dumps(row['renderer_hash']) + ', ' + hex(row['renderer_size']) + ',',
                  '     ' + ('true' if row['arguments'] == 10 else 'false') + ', {' +
                  ', '.join(hex(v) for v in values) + '},',
                  '     {' + ', '.join(f'0x{b:02x}' for b in row['prologue']) + '}},']
    lines += ['};',
              'inline const SceneProfile* reviewed_scene_profile(const char* scene_hash, std::uint32_t scene_size,',
              '                                                  const char* renderer_hash, std::uint32_t renderer_size) noexcept {',
              '    if (!scene_hash || !renderer_hash) return nullptr;',
              '    for (const auto& profile : kSceneProfiles)',
              '        if (scene_size == profile.scene_size && renderer_size == profile.renderer_size &&',
              '            std::strcmp(scene_hash, profile.scene_hash) == 0 &&',
              '            std::strcmp(renderer_hash, profile.renderer_hash) == 0)',
              '            return &profile;',
              '    return nullptr;', '}', '} // namespace dolly::player_capture', '']
    return '\n'.join(lines)


def generated_outputs(root=ROOT):
    root = Path(root)
    unlocker = unlocker_contract(root)
    data, modules, values = load_contract(root)
    # Check the consumer API before emitting anything. Removing/renaming a
    # reviewed symbol cannot silently produce a broken startup monitor.
    for module, group in (('preload','Preload'), ('replay_camera','ReplayCamera'),
                          ('follow_capabilities','FollowCapabilities'), ('follow_target','FollowTarget')):
        tree = ast.parse((root / 'dolly' / (module + '.py')).read_text(encoding='utf-8'))
        required = {node.attr for node in ast.walk(tree) if isinstance(node,ast.Attribute)
                    and isinstance(node.value,ast.Name) and node.value.id == '_profile'}
        require(required <= values[group].keys(), f'{group}: missing symbols required by its runtime consumer')
    py, cpp = render_runtime(data, {**values, 'Unlocker': unlocker})
    outputs = {'dolly/_runtime_generated.py': py, 'native/include/dolly_runtime_generated.hpp': cpp,
               'native/src/dolly_follow_anchor_generated.hpp': render_follow(data),
               'native/include/dolly_player_scene_generated.hpp': render_player_scenes(player_scenes(root, data))}
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / 'generated.hpp'
        manifest = read_json(root / 'native/profiles/manifest.json')
        entries = camera.collect_profile_entries(root / 'native/profiles', None, profile_names=manifest['profiles'])
        accepted = manifest['modules'][modules['client']['path']]['accepted']
        require(all(profile['client']['client_sha256'] in accepted for profile,_ in entries),
                'Camera profile hash is not reviewed in the manifest')
        camera.emit_header(entries, target)
        outputs['native/src/dolly_compat_generated.hpp'] = target.read_text(encoding='utf-8')
        current = profile_at(root, sound.PROFILE.name, manifest)
        legacy = profile_at(root, sound.LEGACY_PROFILE.name, manifest)
        previous = profile_at(root, sound.PREVIOUS_PROFILE.name, manifest)
        accepted_sound = manifest['modules']['bin/win64/soundsystem.dll']['accepted']
        require(all(profile['sha256'] in accepted_sound for profile in (current,legacy,previous)),
                'Sound profile hash is not reviewed in the manifest')
        sound.emit_sound_header(current, target, legacy_profile=legacy, previous_profile=previous)
        outputs['native/src/dolly_sound_compat_generated.hpp'] = target.read_text(encoding='utf-8')
    return outputs


def check_generated(root=ROOT):
    root = Path(root)
    stale = [name for name, text in generated_outputs(root).items()
             if not (root/name).is_file() or (root/name).read_text(encoding='utf-8') != text]
    require(not stale, 'Generated compatibility drift: ' + ', '.join(stale)
            + '. Review profiles, then run tools/generate_compatibility.py --write.')


def verify_game(root, game_dir):
    data, modules, values = load_contract(root)
    game = camera.resolve_game_dir(Path(game_dir))
    images = {}
    report = {'method':'offline exact-module/code/schema verification', 'modules':{}, 'schema':{}}
    try:
        for name, module in modules.items():
            image = images[name] = camera.Image(game / module['path'])
            require(image.sha256 == module['sha256'] and image.image_size == module['image_size'],
                    f'{name}: installed module differs from the reviewed runtime contract')
            report['modules'][name] = dict(sha256=image.sha256, image_size=image.image_size, code_spans=0)
        for entries in data['groups'].values():
            for entry in entries.values():
                if entry['kind'] == 'bytes':
                    verify_byte_reference(images[entry['module']], entry, entries,
                                          modules[entry['module']]['image_size'])
                if 'rip_reference' in entry:
                    rva, expected = rip_reference(entry, entries, modules[entry['module']]['image_size'])
                    require(images[entry['module']].read(rva, len(expected)) == expected,
                            'Reviewed RIP reference instruction differs from the installed module')
                if entry['kind'] != 'spans':
                    continue
                image = images[entry['module']]
                for span in entry['value']:
                    raw = image.read(integer(span['rva']), span['size'])
                    require(hashlib.sha256(raw).hexdigest() == span['sha256'], 'Saved code span evidence differs')
                    report['modules'][entry['module']]['code_spans'] += 1
        for name, field in data['schema'].items():
            report['schema'][name] = verify_named_field(images[field['module']], name, field)
        for anchor in data['follow_anchors']:
            if anchor['_sha256'] == modules['client']['sha256']:
                report['follow_anchor'] = verify_follow_anchor(images['client'], anchor)
        scene = images['player_scene'] = camera.Image(game / 'bin/win64/scenesystem.dll')
        renderer = images['player_renderer'] = camera.Image(game / 'bin/win64/rendersystemdx11.dll')
        matches = [row for row in player_scenes(Path(root), data)
                   if row['scene_hash'] == scene.sha256 and row['scene_size'] == scene.image_size
                   and row['renderer_hash'] == renderer.sha256 and row['renderer_size'] == renderer.image_size]
        require(len(matches) == 1, 'Installed player scene/renderer pair is not reviewed')
        row = matches[0]
        require(scene.read(row['rva'], 32) == row['prologue'], 'Player producer prologue differs')
        producer = row['profile']['producer']
        if 'end_rva' in producer:
            require(hashlib.sha256(scene.read(row['rva'], integer(producer['end_rva'])-row['rva'])).hexdigest()
                    == producer['sha256'], 'Player producer body differs from saved evidence')
        report['player_scene'] = dict(scene_sha256=scene.sha256, renderer_sha256=renderer.sha256,
                                     producer_rva=row['rva'], arguments=row['arguments'], live_verified=False)
        return report
    finally:
        for image in images.values():
            image.close()


def verify_follow_anchor(image, anchor):
    """Check the matching correction profile against installed bytes, read-only."""
    require(image.sha256 == anchor['_sha256'] and
            image.image_size == integer(anchor['values']['image_size']),
            'Follow anchor module differs from the reviewed profile')
    for span in anchor['spans']:
        expected = bytes.fromhex(span['bytes'])
        require(image.read(integer(span['rva']), len(expected)) == expected,
                'Follow anchor code differs from saved evidence')
    return dict(build=anchor['build'], code_spans=len(anchor['spans']), live_verified=False)


def verify_named_field(image, name, field):
    """Prove the named record and independent getter consumers agree, read-only."""
    record = image.read(integer(field['record_rva']), 20)
    require(len(record) == 20, 'Truncated named schema record')
    address = struct.unpack_from('<Q', record)[0]
    label = name.split('.')[1].encode() + b'\0'
    require(image.read(address-image.base, len(label)) == label, 'Named schema record differs')
    offset = struct.unpack_from('<I', record, 16)[0]
    require(offset == integer(field['offset']), 'Named schema field offset differs')
    for getter in field['getters']:
        expected = bytes.fromhex(getter['bytes'])
        require(image.read(integer(getter['rva']), len(expected)) == expected, 'Stock schema getter differs')
    return dict(offset=offset, getters=len(field['getters']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--write', action='store_true')
    parser.add_argument('--verify-game-dir', type=Path)
    args = parser.parse_args(argv)
    try:
        # Validate/render everything before touching any generated output.
        if args.write:
            outputs = generated_outputs()
            for name, text in outputs.items():
                (ROOT/name).write_text(text, encoding='utf-8')
            print(f'Generated {len(outputs)} compatibility files from saved reviews.')
        else:
            check_generated()
            print('Generated compatibility files match the saved reviews.')
        if args.verify_game_dir:
            print(json.dumps(verify_game(ROOT,args.verify_game_dir),indent=2))
    except (ContractError, camera.ProfileError, KeyError, TypeError, OSError, ValueError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
