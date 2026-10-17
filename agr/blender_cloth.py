"""Deterministic, baked Haze hose secondary motion; no garment cloth claim.

The solver uses source rod rest lengths, source gravity, source animation-stray
bounds and the source arm capsules. It is an approximate position-based solver,
not Source 2's FE solver or a replay of historical cloth state. Scalar constraints
are used once; their SIMD duplicates are deliberately ignored.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .installed_cloth import HOSE_NAMES, SCHEMA, UnsupportedCloth


def _capsule_contact(point, ends):
    """Closest member of a tapered capsule's swept family of spheres."""
    a, b = ends[0, :3], ends[1, :3]
    ra, rb = ends[:, 3]
    axis = b - a
    length = float(np.linalg.norm(axis))
    cone = None
    if length < 1e-12 or abs(rb - ra) >= length:
        center, radius = (a, ra) if ra >= rb else (b, rb)
    else:
        direction = axis / length
        along = float(np.dot(point - a, direction))
        radial = point - a - along * direction
        slope = (rb - ra) / length
        closest = along + slope * float(np.linalg.norm(radial)) / math.sqrt(1 - slope*slope)
        t = min(1.0, max(0.0, closest / length))
        center, radius = a + axis*t, ra + (rb - ra)*t
        cone = (direction, slope, t)
    delta = point - center
    distance = float(np.linalg.norm(delta))
    if distance > 1e-12:
        normal = delta / distance
    else:
        # A stable perpendicular is necessary even at an exact capsule axis.
        direction = axis / length if length > 1e-12 else np.array([0., 0., 1.])
        basis = np.eye(3)[int(np.argmin(abs(direction)))]
        normal = np.cross(direction, basis)
        normal /= np.linalg.norm(normal)
        if cone is not None:
            direction, slope, t = cone
            if t <= 0:
                normal = -direction
            elif t >= 1:
                normal = direction
            else:
                normal = normal*math.sqrt(1-slope*slope) - direction*slope
    return float(radius - distance), normal


def _basis(points, row):
    x = points[row['x1']] - points[row['x0']]
    y = points[row['y1']] - points[row['y0']]
    nx = float(np.linalg.norm(x))
    if nx < 1e-9:
        raise UnsupportedCloth('A Haze hose has coincident anchors')
    x /= nx
    z = np.cross(x, y)
    nz = float(np.linalg.norm(z))
    if nz < 1e-9:
        raise UnsupportedCloth('A Haze hose output basis is degenerate')
    z /= nz
    y = np.cross(z, x)
    return np.column_stack((x, y, z))


def solve_hoses(definition, targets, capsules, fps, *, unit_scale=.0254,
                 substeps=4, iterations=12, damping=3.0, settling_seconds=.2):
    """Solve world-space points, returning samples and explicit constraint errors.

    targets: [frame, 16, xyz]; capsules: [frame, 4, 2, xyz+radius], in metres.
    Pin/static positions are copied from the baked animation without tolerance.
    The source's maximum stray radius is a hard final displacement bound. Rods
    are elastic in this approximation; any resulting residual is reported.
    """
    targets = np.asarray(targets, dtype=np.float64)
    capsules = np.asarray(capsules, dtype=np.float64)
    if definition.get('schema') != SCHEMA or tuple(definition.get('control_names', ())) != HOSE_NAMES:
        raise UnsupportedCloth('Unknown secondary-motion definition')
    if (targets.ndim != 3 or targets.shape[1:] != (16, 3) or not len(targets)
            or capsules.shape != (len(targets), 4, 2, 4)):
        raise UnsupportedCloth('Secondary-motion target/capsule sample shape mismatch')
    if (not np.isfinite(targets).all() or not np.isfinite(capsules).all()
            or not math.isfinite(fps) or not 1 <= fps <= 240
            or not math.isfinite(unit_scale) or unit_scale <= 0):
        raise UnsupportedCloth('Invalid secondary-motion samples or timing')
    if not 1 <= substeps <= 32 or not 1 <= iterations <= 100:
        raise UnsupportedCloth('Unsupported solver iteration count')
    h = 1.0 / (fps * substeps)
    radii = np.asarray(definition['stray_radii']) * unit_scale
    acceleration = np.zeros((4, 3))
    acceleration[:, 2] = -np.asarray(definition['gravity'][12:]) * unit_scale
    rods = [(r['nodes'][0], r['nodes'][1], r['min']*unit_scale, r['max']*unit_scale)
            for r in definition['rods']]
    output = np.empty_like(targets)
    points = targets[0].copy()
    velocity = np.zeros((4, 3))
    restarts = []
    max_penetration = max_rod_error = 0.0

    def project(goal, caps):
        nonlocal points
        points[:12] = goal[:12]
        for _ in range(iterations):
            for a, b, lo, hi in rods:
                delta = points[b] - points[a]
                length = float(np.linalg.norm(delta))
                if length > 1e-12:
                    distance = min(hi, max(lo, length))
                    # Explicit elastic PBD setting, not a claimed mapping of
                    # the engine's undocumented relaxation coefficient.
                    points[b] -= .75 * (length - distance) * delta / length
            for i in range(4):
                for cap in caps:
                    depth, normal = _capsule_contact(points[12+i], cap)
                    if depth > 0:
                        points[12+i] += normal * depth
            # Preserve the authored hard animation-stray limit. If collision
            # and this limit cannot coexist, validation rejects the bake.
            delta = points[12:] - goal[12:]
            lengths = np.linalg.norm(delta, axis=1)
            factors = np.minimum(1.0, radii / np.maximum(lengths, 1e-12))
            points[12:] = goal[12:] + delta * factors[:, None]

    def step(goal, caps):
        nonlocal velocity, points
        before = points[12:].copy()
        velocity *= math.exp(-damping*h)
        velocity += acceleration*h
        points[12:] += velocity*h
        project(goal, caps)
        velocity = (points[12:] - before)/h

    for _ in range(round(settling_seconds/h)):
        step(targets[0], capsules[0])
    # Start with sampled carrier motion after settling, rather than a large
    # artificial drag impulse from an actor's existing scene translation.
    if len(targets) > 1:
        velocity += (targets[1, 12:] - targets[0, 12:])*fps
    for frame in range(len(targets)):
        if frame:
            displacement = np.linalg.norm(targets[frame, :12] - targets[frame-1, :12], axis=1)
            if float(displacement.max()) > 3.0:
                points = targets[frame].copy()
                velocity[:] = 0
                restarts.append(frame)
            else:
                for sub in range(substeps):
                    alpha = (sub+1)/substeps
                    goal = targets[frame-1]*(1-alpha) + targets[frame]*alpha
                    caps = capsules[frame-1]*(1-alpha) + capsules[frame]*alpha
                    step(goal, caps)
        output[frame] = points
        for a, b, lo, hi in rods:
            length = float(np.linalg.norm(points[b]-points[a]))
            max_rod_error = max(max_rod_error, lo-length, length-hi)
        for point in points[12:]:
            for cap in capsules[frame]:
                max_penetration = max(max_penetration, _capsule_contact(point, cap)[0])
    if not np.isfinite(output).all():
        raise UnsupportedCloth('Secondary-motion solve produced a non-finite result')
    displacement = np.linalg.norm(output[:, 12:] - targets[:, 12:], axis=-1)
    diagnostics = {'max_displacement_m': float(displacement.max()),
                   'max_stray_excess_m': float(np.maximum(displacement-radii, 0).max()),
                   'max_capsule_penetration_m': max_penetration,
                   'max_rod_residual_m': max_rod_error,
                   'pinned_control_error_m': float(abs(output[:, :12]-targets[:, :12]).max()),
                   'reset_sample_indices': restarts, 'substeps': substeps,
                   'constraint_iterations': iterations, 'damping_per_second': damping,
                   'settling_seconds': settling_seconds,
                   'historical_simulation_state': 'unavailable; settled from first sampled pose'}
    return output, diagnostics


def bake_secondary_motion(rig, definition_path, frames, fps, collection=None, *, unit_scale=.0254):
    """Bake only the four supported controls; never edit the replay body action.

    Four render-invisible Empty targets contain ordinary transform keyframes.
    COPY_TRANSFORMS constraints affect only the source-declared free controls.
    All samples are computed and checked before these objects are created.
    """
    import bpy
    from mathutils import Matrix
    from .blender_scene import _action_channels, _curve, _init_blender

    _init_blender()

    definition = json.loads(Path(definition_path).read_text(encoding='utf-8'))
    if definition.get('schema') == 'agr_decompiled_fe_proxy_v1':
        from .blender_fe_proxy import bake_fe_proxy
        return bake_fe_proxy(rig, definition, frames, fps, collection, unit_scale=unit_scale)
    if definition.get('schema') == 'agr_seven_garments_v1':
        from .blender_surface_cloth import bake_surface_cloth
        return bake_surface_cloth(rig, definition, frames, fps, collection, unit_scale=unit_scale)
    if definition.get('schema') != SCHEMA or tuple(definition.get('control_names', ())) != HOSE_NAMES:
        raise UnsupportedCloth('Unknown secondary-motion definition')
    frames = list(frames)
    if not frames or any(b-a != 1 for a, b in zip(frames, frames[1:])):
        raise UnsupportedCloth('Hose bake requires consecutive scene frames')
    names = definition['control_names']
    missing = set(names)-set(rig.pose.bones.keys())
    if missing:
        raise UnsupportedCloth('Hose controls missing from imported rig: ' + ', '.join(sorted(missing)))
    for name in names[12:]:
        bone = rig.pose.bones[name]
        if bone.constraints or bone.children:
            raise UnsupportedCloth('Hose output control has constraints or descendants: ' + name)
    # This checks the installed GLB against the actual PHYS input, instead of
    # assuming a matching hero/model name proves a matching rig rest layout.
    source = np.asarray(definition['initial_pose'], dtype=float)[:, :3] * unit_scale
    rest = np.asarray([tuple(rig.data.bones[n].head_local) for n in names])
    expected_distances = np.linalg.norm(source[:, None]-source[None, :], axis=-1)
    actual_distances = np.linalg.norm(rest[:, None]-rest[None, :], axis=-1)
    rest_error = float(abs(expected_distances-actual_distances).max())
    if rest_error > .002:
        raise UnsupportedCloth(f'Installed hose rest positions do not match PHYS ({rest_error:.6f} m)')
    scene = bpy.context.scene
    previous_frame = scene.frame_current
    transforms = np.empty((len(frames), 16, 4, 4))
    targets = np.empty((len(frames), 16, 3))
    capsules = np.empty((len(frames), 4, 2, 4))
    try:
        for f, frame in enumerate(frames):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            for n, name in enumerate(names):
                matrix = rig.matrix_world @ rig.pose.bones[name].matrix
                scale = matrix.to_scale()
                if max(abs(s-1) for s in scale) > .001:
                    raise UnsupportedCloth('Scaled hose controls require an unimplemented capsule scale mapping')
                transforms[f, n] = np.array(matrix)
                targets[f, n] = tuple(matrix.translation)
            for c, cap in enumerate(definition['capsules']):
                matrix = transforms[f, cap['node']]
                for end, values in enumerate(cap['ends']):
                    point = matrix @ np.array([*(np.array(values[:3])*unit_scale), 1.])
                    capsules[f, c, end] = [*point[:3], values[3]*unit_scale]
        solved, diagnostics = solve_hoses(definition, targets, capsules, fps, unit_scale=unit_scale)
        failures = []
        if diagnostics['max_capsule_penetration_m'] > .002:
            failures.append(f"{diagnostics['max_capsule_penetration_m']:.6f} m capsule penetration")
        if diagnostics['max_rod_residual_m'] > .002:
            failures.append(f"{diagnostics['max_rod_residual_m']:.6f} m rod error")
        if failures:
            raise UnsupportedCloth('Hose authored constraints conflict; bake withheld: ' + '; '.join(failures),
                                   diagnostics=diagnostics)
        output = {}
        for base in definition['bases']:
            n = base['node']
            rows = []
            prior = None
            for f in range(len(frames)):
                delta = _basis(solved[f], base) @ _basis(targets[f], base).T
                rotation = Matrix((delta @ transforms[f, n, :3, :3]).tolist()).to_quaternion()
                rotation.normalize()
                if prior is not None and rotation.dot(prior) < 0:
                    rotation.negate()
                prior = rotation.copy()
                rows.append([*solved[f, n], *rotation])
            output[n] = np.asarray(rows)
        # No scene mutation before validation above succeeds.
        collection = collection or rig.users_collection[0]
        objects = []
        for n, rows in output.items():
            target = bpy.data.objects.new(rig.name[:30] + ' | simulated ' + names[n], None)
            collection.objects.link(target)
            target.empty_display_type = 'PLAIN_AXES'
            target.empty_display_size = .01
            target.hide_render = True
            target.rotation_mode = 'QUATERNION'
            target['agr_secondary_motion'] = 'Haze hose bake v1'
            target['agr_source_sha256'] = definition.get('source_sha256', '')
            target['agr_entity_id'] = rig.get('agr_entity_id', '')
            action, channels = _action_channels(target, target.name)
            resets = {i-1 for i in diagnostics['reset_sample_indices'] if i > 0}
            for prop, offset, count in (('location', 0, 3), ('rotation_quaternion', 3, 4)):
                for component in range(count):
                    _curve(channels, target, prop, component, frames, rows[:, offset+component], steps=resets)
            action['agr_source'] = 'Approximate secondary motion from installed PHYS and evaluated replay motion'
            constraint = rig.pose.bones[names[n]].constraints.new('COPY_TRANSFORMS')
            constraint.name = 'AGR baked Haze hose motion'
            constraint.target = target
            constraint.target_space = 'WORLD'
            constraint.owner_space = 'WORLD'
            target.hide_set(True)
            objects.append(target.name)
        return {'status': 'baked', 'scope': 'Haze four hose midpoint controls only; no garment cloth',
                'dynamic_controls': names[12:], 'sample_count': len(frames),
                'source_resource': definition.get('resource'), 'source_sha256': definition.get('source_sha256'),
                'body_action_edited': False, 'rest_distance_error_m': rest_error,
                'baked_targets': objects, 'solver': definition.get('solver'), **diagnostics}
    finally:
        scene.frame_set(previous_frame)
