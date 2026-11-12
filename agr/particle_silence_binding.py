"""Explicit consumed-input equivalence; never rebind historical observations.

Both original envelopes are validated against their unchanged source witness.
Fresh output poses are acquired separately by the ordinary exporter. This bridge
compares all replay motion consumed after birth; it grants no new native profile.
"""
import copy
import hashlib
import io
from pathlib import Path
import zipfile

import numpy as np

from .particle_snapshot_provider import digest
from .particle_silence_capture import validate_silence_capture
from .particle_client_capture import validate_client_child_capture
from .captured_slide_binding import EFFECT_ASSETS_SOURCE, EFFECT_ASSETS_TARGET
from .simulated_effect_package import scene_identity

POLICY = 'agr_silence_consumed_input_equivalence_v1'
MAX_BYTES = 64 * 1024 * 1024
BASIS_FIELDS = ('id', 'hero_id', 'model_handle', 'model_yaw_degrees', 'pose_basis_yaw_degrees',
    'render_root_yaw_degrees', 'model_scale', 'model_local_transform', 'static_generated_cloth')


def require(ok, message):
    if not ok:
        raise ValueError('Silence equivalence: ' + message)


def _file(path):
    path = Path(path)
    require(path.is_file() and 0 < path.stat().st_size <= MAX_BYTES, 'missing or oversized source asset')
    with path.open('rb') as stream:
        value = stream.read(MAX_BYTES + 1)
    require(len(value) <= MAX_BYTES, 'source asset grew beyond bounds')
    return value


def _pose_slice(raw, start, end):
    """Inspect bounded NPY headers before NumPy can allocate an array."""
    fields = {'q': (3, 'f', 4), 't': (3, 'f', 3), 's': (2, 'f', None),
              'ticks': (1, 'i', None), 'fresh': (1, 'b', None)}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(len(archive.infolist()) == 5 and set(archive.namelist()) == {k + '.npy' for k in fields},
                'exact motion archive fields required')
        shapes = {}
        for key, (rank, kind, width) in fields.items():
            info = archive.getinfo(key + '.npy')
            require(0 < info.file_size <= MAX_BYTES, 'pose array exceeds bounds')
            with archive.open(info) as stream:
                version = np.lib.format.read_magic(stream)
                require(version in ((1, 0), (2, 0)), 'unqualified motion NPY version')
                reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
                shape, fortran, dtype = reader(stream, max_header_size=4096)
            require(not fortran and len(shape) == rank and all(type(n) is int and 0 < n <= 10000 for n in shape)
                and dtype.kind == kind and not dtype.hasobject and dtype.itemsize in ((4, 8) if kind == 'f' else (8,) if kind == 'i' else (1,))
                and (width is None or shape[-1] == width) and int(np.prod(shape)) <= 2_000_000
                and int(np.prod(shape)) * dtype.itemsize <= info.file_size, 'invalid bounded motion array shape/type')
            shapes[key] = shape
        count = shapes['ticks'][0]
        bones = shapes['s'][1]
        require(bones <= 4096 and shapes['q'] == (count, bones, 4) and shapes['t'] == (count, bones, 3)
            and shapes['s'] == (count, bones) and shapes['fresh'] == (count,), 'motion arrays disagree')
    with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
        ticks = archive['ticks']
        require(np.all(ticks >= 0) and np.all(ticks[1:] > ticks[:-1]), 'motion ticks must be unique and ordered')
        selected = np.flatnonzero((ticks >= start) & (ticks <= end))
        require(np.array_equal(ticks[selected], np.arange(start, end + 1)), 'complete consumed motion history required')
        values = {k: archive[k][selected].copy() for k in fields}
        require(all(np.isfinite(values[k]).all() for k in ('q', 't', 's')), 'nonfinite consumed motion')
    return values


def _owner(scene, identity):
    matches = [e for e in scene['entities'] if e.get('id') == identity]
    require(len(matches) == 1, 'missing or ambiguous owner')
    entity = matches[0]
    require(not entity.get('model_tracks') and not entity.get('secondary_motion_file'),
            'additional model/secondary motion providers require qualification')
    return entity


def _samples(entity, start, end):
    rows = entity.get('samples')
    require(isinstance(rows, list) and 0 < len(rows) <= 10000, 'bounded owner samples required')
    ticks = [r.get('tick') for r in rows]
    require(all(type(t) is int for t in ticks) and all(a < b for a, b in zip(ticks, ticks[1:])),
            'owner ticks must be unique and ordered')
    selected = [r for r in rows if start <= r['tick'] <= end]
    require([r['tick'] for r in selected] == list(range(start, end + 1))
        and all(r.get('active', True) and r.get('present', True) for r in selected), 'complete present owner history required')
    require(not any(start <= t <= end for t in entity.get('pose_unavailable_ticks', [])), 'consumed pose is unavailable')
    return selected


def bind_equivalence(source, target, instance, birth, child):
    """Return a new bridge proof retaining source and original packet identities."""
    try:
        source_instances = [i for i in source['particle_instances'] if i.get('id') == instance['id']]
        target_instances = [i for i in target['particle_instances'] if i.get('id') == instance['id']]
        require(len(source_instances) == 1 and digest(source_instances[0]) == digest(instance), 'recorded events/instance changed')
        require(len(target_instances) == 1 and digest(target_instances[0]) == digest(instance), 'fresh recorded instance is missing or ambiguous')
        original = source_instances[0]
        validate_silence_capture(birth, source, original)
        validate_client_child_capture(child, source, original)
        # Deliberately narrow first profile: equal shot endpoints. Added earlier
        # warmup is allowed only outside this effect's complete consumed span.
        require(all(type(s[k]) is int for s in (source, target) for k in ('start_tick', 'end_tick'))
            and digest({k: source[k] for k in ('start_tick', 'end_tick', 'tick_rate')})
                == digest({k: target[k] for k in ('start_tick', 'end_tick', 'tick_rate')}), 'shot range/rate changed')
        require(source['metadata']['demo_sha256'] == target['metadata']['demo_sha256']
            and digest(source['metadata'].get('coordinate_system')) == digest(target['metadata'].get('coordinate_system')),
            'replay or world basis changed')
        start, end = instance['created_tick'], target['end_tick']
        require(type(start) is int and 0 <= start <= end and end - start < 10000, 'bounded consumed span required')
        a, b = _owner(source, instance['entity_id']), _owner(target, instance['entity_id'])
        require(digest({k:a.get(k) for k in BASIS_FIELDS}) == digest({k:b.get(k) for k in BASIS_FIELDS}), 'owner identity/basis changed')
        roots = _samples(a, start, end)
        require(digest(roots) == digest(_samples(b, start, end)), 'consumed root samples changed')
        left, right = copy.deepcopy(a['model_constraints']), copy.deepcopy(b['model_constraints'])
        lp, rp = left.pop('preparation_dependencies', {}), right.pop('preparation_dependencies', {})
        if digest(lp) != digest(rp):
            require(lp.pop('effect_assets.py', None) == EFFECT_ASSETS_SOURCE
                and rp.pop('effect_assets.py', None) == EFFECT_ASSETS_TARGET and digest(lp) == digest(rp),
                'unqualified preparation dependency change')
        require(digest(left) == digest(right), 'authored model/constraints changed')
        files = {}
        for key in ('model_file', 'skeleton_file'):
            x, y = _file(a[key]), _file(b[key])
            require(x == y, 'model/skeleton asset bytes changed')
            files[key] = hashlib.sha256(x).hexdigest()
        source_raw, target_raw = _file(a['pose_file']), _file(b['pose_file'])
        x, y = _pose_slice(source_raw, start, end), _pose_slice(target_raw, start, end)
        require(all(x[k].dtype == y[k].dtype and np.array_equal(x[k], y[k]) for k in x), 'consumed pose rows changed')
        # Validate the historical raw acquisition semantics against the target,
        # without constructing or returning a newly bound observation packet.
        from .particle_silence_capture import _validate
        from .particle_client_capture import _validate_capture
        _validate(birth['capture'], target, instance)
        _validate_capture(child['capture'], target, instance)
        witness = dict(schema_version=source.get('schema_version', 1),
            **{k:copy.deepcopy(source[k]) for k in ('start_tick', 'end_tick', 'tick_rate', 'metadata')},
            entities=[copy.deepcopy(a)], particle_instances=[copy.deepcopy(original)])
        for key in ('particle_history', 'network_projectiles'):
            if key in source:
                witness[key] = copy.deepcopy(source[key])
        value = dict(schema_version=1, policy=POLICY, instance_id=instance['id'], source_scene=witness,
            target_scene_identity=scene_identity(target, [instance['entity_id']]),
            birth_content_sha256=birth['content_sha256'], child_content_sha256=child['content_sha256'],
            consumed_range_inclusive=[start, end], model_files=files,
            source_pose_sha256=hashlib.sha256(source_raw).hexdigest(), target_pose_sha256=hashlib.sha256(target_raw).hexdigest(),
            consumed_pose_sha256={k:hashlib.sha256(x[k].tobytes()).hexdigest() for k in x},
            root_samples_sha256=digest(roots), constraints_sha256=digest(left),
            scope='Exact consumed replay/root/pose/model equivalence only; original historical native observations unchanged; no current native acquisition or renderer parity')
        return dict(value, content_sha256=digest(value))
    except (KeyError, TypeError, IndexError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError('Silence equivalence inputs are incomplete or invalid') from exc


def validate_equivalence(proof, target, instance, birth, child):
    require(isinstance(proof, dict) and proof.get('policy') == POLICY, 'missing explicit equivalence proof')
    expected = bind_equivalence(proof.get('source_scene', {}), target, instance, birth, child)
    require(digest(proof) == digest(expected), 'bridge proof or inputs changed')
    return expected['source_scene']
