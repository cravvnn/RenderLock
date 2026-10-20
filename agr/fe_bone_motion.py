"""Bounded node/rod cloth approximation for imported bone layouts.

Names, rest transforms, rods, collision layers and node-basis identities come
from the installed FE model. This is an offline position solver, not a port of
Source's integrator. Animation attraction is an explicit per-second exponential
approximation; authored gravity and distances retain their physical units.
"""
from __future__ import annotations

import math
import numpy as np


UNSUPPORTED_OPERATIONS = (
    'm_Prisms',
    'm_FollowNodes', 'm_FitMatrices', 'm_BoneMergeLinks', 'm_DynKinLinks',
    'm_WorldCollisionNodes', 'm_AntiTunnelBytecode',
    'm_SpringIntegrator', 'm_GoalDampedSpringIntegrators', 'm_Effects',
    'm_SDFRigids', 'm_TaperedCapsuleStretches',
)


def _finite(value, name, shape=None):
    result = np.asarray(value, dtype=np.float64)
    if not np.isfinite(result).all() or (shape is not None and result.shape != shape):
        raise ValueError('Invalid FE ' + name)
    return result


def validate_motion_physics(physics):
    fe = physics['m_pFeModel']
    names = fe.get('m_CtrlName')
    count = fe.get('m_nNodeCount')
    if (type(count) is not int or not 1 <= count <= 4096 or not isinstance(names, list)
            or len(names) != count or len(set(names)) != count
            or any(not isinstance(n, str) or not n for n in names)):
        raise ValueError('Invalid FE control-node identities')
    pending = [key for key in UNSUPPORTED_OPERATIONS if fe.get(key)]
    if pending:
        raise ValueError('Imported-bone solver requires additional authored operations: ' + ', '.join(pending))
    flags=fe.get('m_nDynamicNodeFlags',0)
    if type(flags) is not int or flags<0 or flags&0x8000:
        raise ValueError('Per-control FE constraint length-scale mapping is not qualified')
    rest = _finite(fe['m_InitPose'], 'rest transforms', (count, 8))
    if np.any(rest[:, 3] <= 0) or np.any(np.linalg.norm(rest[:, 4:8], axis=1) < 1e-8):
        raise ValueError('Invalid FE rest scale/rotation')
    if fe.get('m_CtrlOffsets') or fe.get('m_CtrlSoftOffsets') or fe.get('m_CtrlOsOffsets'):
        from .fe_control_offsets import validate_preparation_layout
        validate_preparation_layout(physics)
    masses = _finite(fe['m_NodeInvMasses'], 'inverse masses', (count,))
    if np.any(masses < 0):
        raise ValueError('Invalid FE inverse mass')
    static = fe['m_nStaticNodes']
    if type(static) is not int or not 0 <= static < count or np.any(masses[:static] != 0):
        raise ValueError('Invalid FE static-node interval')
    integrators = fe['m_NodeIntegrator']
    if not isinstance(integrators, list) or len(integrators) != count:
        raise ValueError('Invalid FE integrator count')
    values = _finite([[r[k] for k in ('flPointDamping', 'flAnimationForceAttraction',
                                    'flAnimationVertexAttraction', 'flGravity')]
                      for r in integrators], 'integrators', (count, 4))
    if np.any(values[:, :3] < 0):
        raise ValueError('Negative FE damping/attraction')

    def index(value):
        if type(value) is not int or not 0 <= value < count:
            raise ValueError('FE constraint index outside control nodes')
        return value

    if len(fe['m_Rods']) > 100000:
        raise ValueError('Excessive FE rods')
    for rod in fe['m_Rods']:
        if len(rod['nNode']) != 2 or index(rod['nNode'][0]) == index(rod['nNode'][1]):
            raise ValueError('Invalid FE rod endpoints')
        minimum, maximum, weight, relax = _finite(
            [rod[k] for k in ('flMinDist', 'flMaxDist', 'flWeight0', 'flRelaxationFactor')], 'rod')
        if not 0 <= minimum <= maximum or not 0 <= weight <= 1 or not 0 <= relax <= 1:
            raise ValueError('Invalid FE rod interval/weight')
    if fe.get('m_SimdRods'):
        from .fe_rod_constraints import project_compiled_rods
        project_compiled_rods(rest[:,:3],fe['m_SimdRods'])
    if fe.get('m_SimdRodsAnim'):
        from .fe_rod_constraints import project_animated_rods
        project_animated_rods(rest[:,:3],fe['m_SimdRodsAnim'],rest[:,:3])
    if fe.get('m_Quads') or fe.get('m_SimdQuads'):
        from collections import Counter
        from .fe_quad_constraints import project_compiled_quads
        raw=fe.get('m_Quads',[]);blocks=fe.get('m_SimdQuads',[])
        one,two=fe.get('m_nQuadCount1'),fe.get('m_nQuadCount2')
        simd_one,simd_two=fe.get('m_nSimdQuadCount1'),fe.get('m_nSimdQuadCount2')
        if (not raw or not blocks or len(raw)>100000
                or any(type(n) is not int for n in (one,two,simd_one,simd_two))
                or not 0<=two==one<=len(raw) or not 0<=simd_two==simd_one<=len(blocks)):
            raise ValueError('Ordered FE quad prefixes with no one-pinned layouts required')
        declared=[];compiled=[]
        for number,row in enumerate(raw):
            nodes=row['nNode'];shape=_finite(row['vShape'],'quad shape',(4,4))
            slack=_finite(row['flSlack'],'quad slack')
            pins=2 if number<two else 0
            if len(nodes)!=4 or len(set(index(i) for i in nodes))!=4 or slack!=0:
                raise ValueError('Distinct FE quad nodes and zero authored slack required')
            if pins==2:
                if (np.any(shape[:2,3]!=0) or np.any(shape[2:,3]<0)
                        or abs(shape[2:,3].sum()-1)>2e-5
                        or any(masses[nodes[i]]!=0 for i in (0,1))
                        or any(masses[nodes[i]]<=0 for i in (2,3))):
                    raise ValueError('FE quad requires two pinned nodes and two dynamic endpoints')
            elif (np.any(shape[:,3]<=0) or abs(shape[:,3].sum()-1)>2e-5
                    or any(masses[n]<=0 for n in nodes)):
                raise ValueError('Free FE quad requires four positive weights and dynamic endpoints')
            declared.append((pins,)+tuple(nodes)+(float(slack),)+tuple(shape.ravel()))
        project_compiled_quads(rest[:,:3],blocks,two_pin_count=simd_two,one_pin_count=simd_one)
        for number,row in enumerate(blocks):
            pins=2 if number<simd_two else 0
            shape=np.asarray(row['vShape']).reshape(4,3,4)
            for lane in range(4):
                compiled.append((pins,)+tuple(row['nNode'][i][lane] for i in range(4))+(row['f4Slack'][lane],)+tuple(
                    value for i in range(4) for value in (*shape[i,:,lane],row['f4Weights'][i][lane])))
        if Counter(declared)-Counter(compiled) or set(compiled)-set(declared):
            raise ValueError('Compiled FE quads differ from the complete scalar declaration inventory')
    if fe.get('m_Tris') or fe.get('m_SimdTris'):
        from collections import Counter
        from .fe_triangle_constraints import project_compiled_triangles
        raw=fe.get('m_Tris',[]);blocks=fe.get('m_SimdTris',[])
        one,two=fe.get('m_nTriCount1'),fe.get('m_nTriCount2')
        simd_one,simd_two=fe.get('m_nSimdTriCount1'),fe.get('m_nSimdTriCount2')
        if (not raw or not blocks or len(raw)>100000
                or any(type(n) is not int for n in (one,two,simd_one,simd_two))
                or not 0<=two<=one<=len(raw) or not 0<=simd_two<=simd_one<=len(blocks)):
            raise ValueError('Complete ordered FE triangle pin-count prefixes required')
        declared=[];compiled=[]
        for number,row in enumerate(raw):
            nodes=row['nNode']
            if len(nodes)!=3 or len(set(index(i) for i in nodes))!=3:
                raise ValueError('Invalid FE triangle control identities')
            values=_finite([row['w1'],row['w2'],row['v1x'],*row['v2']], 'triangle geometry', (5,))
            pins=2 if number<two else 1 if number<one else 0
            if values[2]<=0 or values[4]<0:
                raise ValueError('Positive FE triangle edge and nonnegative altitude required')
            if pins==2:
                if (values[0]!=0 or values[1]!=1 or masses[nodes[0]]!=0
                        or masses[nodes[1]]!=0 or masses[nodes[2]]<=0):
                    raise ValueError('FE triangle requires two pinned nodes and one dynamic endpoint')
            elif pins==1:
                if (np.any(values[:2]<=0) or abs(values[:2].sum()-1)>2e-5
                        or masses[nodes[0]]!=0 or any(masses[n]<=0 for n in nodes[1:])):
                    raise ValueError('One pinned node and two weighted dynamic FE triangle endpoints required')
            elif (np.any(values[:2]<=0) or values[:2].sum()>=1 or any(masses[n]<=0 for n in nodes)):
                raise ValueError('Three weighted dynamic FE triangle endpoints required')
            declared.append((pins,)+tuple(nodes)+tuple(values))
        project_compiled_triangles(rest[:,:3],blocks,two_pin_count=simd_two,one_pin_count=simd_one)
        for number,row in enumerate(blocks):
            pins=2 if number<simd_two else 1 if number<simd_one else 0
            for lane in range(4):
                compiled.append((pins,)+tuple(row['nNode'][i][lane] for i in range(3))+tuple(
                    row[key][lane] for key in ('w1','w2','v1x'))+(row['v2']['x'][lane],row['v2']['y'][lane]))
        if Counter(declared)-Counter(compiled) or set(compiled)-set(declared):
            raise ValueError('Compiled FE triangles differ from the complete scalar declaration inventory')
    if fe.get('m_HingeLimits'):
        from .fe_hinge_limits import project_hinge_limits
        project_hinge_limits(rest[:,:3],masses,fe['m_HingeLimits'])
    if fe.get('m_KelagerBends'):
        from .fe_kelager_bends import project_kelager_bends
        project_kelager_bends(rest[:,:3],fe['m_KelagerBends'])
        for row in fe['m_KelagerBends']:
            if any(masses[node]==0 and weight!=0 for node,weight in zip(row['nNode'],row['flWeight'])):
                raise ValueError('FE bend coefficients cannot move a zero-mass control')
    if fe.get('m_CollisionPlanes'):
        from .fe_collision_planes import project_collision_planes
        project_collision_planes(rest[:,:3],rest,fe['m_CollisionPlanes'])
        for row in fe['m_CollisionPlanes']:
            if row['nCtrlParent']>=static or masses[row['nChildNode']]<=0:
                raise ValueError('FE collision planes require static parents and dynamic child nodes at the qualified phase')
    if fe.get('m_LockToParent'):
        from .fe_node_rotations import apply_parent_locks
        apply_parent_locks(rest,rest,fe['m_LockToParent'])
    if fe.get('m_LockToGoal'):
        from .fe_node_rotations import apply_goal_locks
        apply_goal_locks(rest,rest,rest[:,:3],fe['m_LockToGoal'])
    for basis in fe.get('m_NodeBases', []):
        for key in ('nNode', 'nNodeX0', 'nNodeX1', 'nNodeY0', 'nNodeY1'):
            index(basis[key])
    for row in fe.get('m_Twists', []):
        index(row.get('nNodeOrient'));index(row.get('nNodeEnd'))
        relax=_finite([row.get('flTwistRelax'),row.get('flSwingRelax')], 'twist relaxation', (2,))
        if np.any(relax<0) or np.any(relax>1):
            raise ValueError('FE twist relaxation must be in 0..1')
    for row in fe.get('m_ReverseOffsets', []):
        index(row['nBoneCtrl']); index(row['nTargetNode'])
        _finite(row['vOffset'], 'reverse offset', (3,))
    for row in fe.get('m_AnimStrayRadii', []):
        if len(row['nNode']) != 2:
            raise ValueError('Invalid FE animation stray endpoints')
        index(row['nNode'][0]); index(row['nNode'][1])
        radius, relax = _finite([row['flMaxDist'], row['flRelaxationFactor']], 'animation stray')
        if radius < 0 or not 0 <= relax <= 1:
            raise ValueError('Invalid FE animation stray interval')
    for key in ('m_TaperedCapsuleRigids', 'm_SphereRigids', 'm_BoxRigids'):
        for row in fe.get(key, []):
            index(row['nNode'])
            if row.get('nFlags') != 0:
                raise ValueError('FE collider requires its authored flag/selection operation')
            from .fe_body_collisions import collider_node_selection
            collider_node_selection(fe,row)
            if key=='m_BoxRigids':
                from .fe_body_collisions import world_box
                world_box(row.get('tmFrame2'),[0,0,0,1,0,0,0,1],row.get('vSize'))
    return fe


def node_basis(points, row):
    """Orthonormal frame from the authored four-node orientation declaration."""
    x = points[row['nNodeX0']] - points[row['nNodeX1']]
    y = points[row['nNodeY0']] - points[row['nNodeY1']]
    length = float(np.linalg.norm(x))
    if length < 1e-9:
        raise ValueError('Degenerate FE node-basis X axis')
    x = x / length
    z = np.cross(x, y)
    length = float(np.linalg.norm(z))
    if length < 1e-9:
        raise ValueError('Degenerate FE node-basis plane')
    z /= length
    return np.column_stack((x, np.cross(z, x), z))


def rod_residuals(points, rods, scale):
    return np.asarray([max(0., float(np.linalg.norm(points[r['nNode'][1]] - points[r['nNode'][0]]))
                          - r['flMaxDist'] * scale,
                          r['flMinDist'] * scale - float(np.linalg.norm(points[r['nNode'][1]] - points[r['nNode'][0]])))
                       for r in rods])


def _plane_frames(targets, parents, unit_scale):
    """Exact output goal rotations; interpolate these as quaternions per step."""
    result=np.zeros((*targets.shape[:2],8));result[:,:,3]=1;result[:,:,7]=1
    result[:,:,:3]=targets[:,:,:3,3]/unit_scale
    for frame in range(len(targets)):
        for node in parents:
            rotation=targets[frame,node,:3,:3]
            if (np.max(abs(rotation.T@rotation-np.eye(3)))>2e-5
                    or abs(np.linalg.det(rotation)-1)>2e-5):
                raise ValueError('FE collider/plane parent goals require unit instance/control scale')
            xx,xy,xz=rotation[0];yx,yy,yz=rotation[1];zx,zy,zz=rotation[2]
            symmetric=np.array([[xx-yy-zz,xy+yx,xz+zx,zy-yz],
                [xy+yx,yy-xx-zz,yz+zy,xz-zx],[xz+zx,yz+zy,zz-xx-yy,yx-xy],
                [zy-yz,xz-zx,yx-xy,xx+yy+zz]])
            result[frame,node,4:]=np.linalg.eigh(symmetric)[1][:,-1]
    return result


def _collide(points, transforms, fe, dynamic, radii, scale, *,
             previous_points, rigid_controls, rigid_history):
    from .fe_body_collisions import world_sphere,world_box,relative_collision_frame,project_sphere,project_capsule,project_box,collider_node_selection
    coefficients=fe.get('m_DynNodeFriction',[])
    friction=None
    if coefficients:
        friction=np.r_[np.zeros(fe['m_nStaticNodes']),coefficients]
    for key in ('m_TaperedCapsuleRigids', 'm_SphereRigids', 'm_BoxRigids'):
        for number,row in enumerate(fe.get(key, [])):
            selected=collider_node_selection(fe,row)
            selected=selected[dynamic[selected]]
            control=rigid_controls[row['nNode']]
            kwargs={}
            if friction is not None:
                cached=rigid_history['controls'].get((key,number),control)
                kwargs=dict(previous=previous_points/scale,friction=friction,
                    relative_transform=relative_collision_frame(control,cached,
                        history_valid=rigid_history['valid']))
                # Source advances history even if no point hits this rigid.
                rigid_history['controls'][key,number]=control.copy()
            if key=='m_SphereRigids':
                shape=world_sphere(row['vSphere'],control);operation=project_sphere
            elif key=='m_TaperedCapsuleRigids':
                shape=np.asarray([world_sphere(sphere,control) for sphere in row['vSphere']]);operation=project_capsule
            else:
                frame,half=world_box(row['tmFrame2'],control,row['vSize'])
                points[:]=project_box(points/scale,frame,half,radii/scale,selected,**kwargs)*scale
                continue
            points[:]=operation(points/scale,shape,radii/scale,selected,**kwargs)*scale
    rigid_history['valid']=True


def simulate_node_motion(physics, goal_transforms, fps, unit_scale=.0254, *, iterations=16,
                         sample_times=None):
    """Sample an explicit approximation using <=1/256-second steps.

    Targets are world-space metre transforms for every exact FE control node.
    Ordinary rig animation supplies these targets and is not evaluated here.
    Optional sample_times are explicit simulation seconds for those targets;
    output video FPS cannot supply that clock after a shot has been retimed.
    Repeated/reversed timestamps require a separate pause/seek state policy.
    """
    fe = validate_motion_physics(physics)
    count = fe['m_nNodeCount']
    targets = _finite(goal_transforms, 'animated targets')
    if (targets.ndim != 4 or targets.shape[1:] != (count, 4, 4) or not 1 <= len(targets) <= 20000
            or not math.isfinite(fps) or not 1 <= fps <= 240
            or not math.isfinite(unit_scale) or unit_scale <= 0
            or type(iterations) is not int or not 1 <= iterations <= 128):
        raise ValueError('Invalid FE motion timing/target dimensions')
    if sample_times is None:
        intervals = np.full(len(targets)-1, 1/fps)
    else:
        times = _finite(sample_times, 'sample times', (len(targets),))
        intervals = np.diff(times)
        if np.any(intervals <= 0) or np.any(intervals > 1):
            raise ValueError('FE sample times require increasing seconds with intervals at most one second; pause/seek state is unqualified')
    offsets=bool(fe.get('m_CtrlOffsets') or fe.get('m_CtrlSoftOffsets'))
    input_rows=None
    if offsets or fe.get('m_SphereRigids') or fe.get('m_TaperedCapsuleRigids') or fe.get('m_BoxRigids'):
        from .fe_control_offsets import control_transform_rows, control_transform_matrices, prepare_control_offsets
        input_rows=np.asarray([control_transform_rows(frame,unit_scale,discard_animation_scale=True) for frame in targets])
        targets=np.asarray([control_transform_matrices(prepare_control_offsets(frame,physics) if offsets else frame,unit_scale)
                            for frame in input_rows])
    dynamic = np.asarray(fe['m_NodeInvMasses']) > 0
    # The trailing position-driven interval contains outputs, not particles.
    # Earlier reverse offsets may also name simulated chain nodes; retain their
    # participation in the authored rods before the output transform is solved.
    driven = fe.get('m_nFirstPositionDrivenNode', count)
    if type(driven) is not int or not 0 <= driven <= count:
        raise ValueError('Invalid FE position-driven interval')
    if driven == count:
        outputs = {row['nBoneCtrl'] for row in fe.get('m_ReverseOffsets', [])}
        while driven and driven-1 in outputs:
            driven -= 1
    dynamic[driven:] = False
    planes=fe.get('m_CollisionPlanes',[])
    if any(not dynamic[row['nChildNode']] for row in planes):
        raise ValueError('FE collision planes cannot move a position-driven child')
    plane_parents=sorted({row['nCtrlParent'] for row in planes})
    plane_frames=_plane_frames(targets,plane_parents,unit_scale) if planes else None
    spheres=fe.get('m_SphereRigids',[]);capsules=fe.get('m_TaperedCapsuleRigids',[]);boxes=fe.get('m_BoxRigids',[])
    rigid_parents=sorted({row['nNode'] for row in spheres+capsules+boxes})
    rigid_frames=_plane_frames(targets,rigid_parents,unit_scale) if rigid_parents else None
    rigid_history=dict(valid=False,controls={})
    friction=fe.get('m_DynNodeFriction',[])
    if friction:
        coefficients=_finite(friction,'dynamic friction',(count-fe['m_nStaticNodes'],))
        if np.any(coefficients<0):raise ValueError('Negative FE dynamic friction')
    gravity = np.asarray([r['flGravity'] for r in fe['m_NodeIntegrator']]) * unit_scale * fe.get('m_flDefaultGravityScale', 1.)
    damping = np.asarray([r['flPointDamping'] for r in fe['m_NodeIntegrator']])
    attraction = np.asarray([r['flAnimationForceAttraction'] + r['flAnimationVertexAttraction']
                             for r in fe['m_NodeIntegrator']])
    static = fe['m_nStaticNodes']; dynamic_count = count-static
    radii = np.zeros(count)
    authored_radii = fe.get('m_NodeCollisionRadii', [])
    if len(authored_radii) == count:
        radii[:] = authored_radii
    elif len(authored_radii) == dynamic_count:
        radii[static:] = authored_radii
    elif authored_radii:
        raise ValueError('Invalid FE collision-radius count')
    radii *= unit_scale
    if not np.isfinite(radii).all() or np.any(radii < 0):
        raise ValueError('Invalid FE collision radii')
    points = targets[0, :, :3, 3].copy(); velocity = np.zeros_like(points)
    samples = [points.copy()]
    residuals = [];hard_residuals=[];soft_residuals=[]
    compiled=fe.get('m_SimdRods',[])
    animated=fe.get('m_SimdRodsAnim',[])
    quads=fe.get('m_SimdQuads',[])
    triangles=fe.get('m_SimdTris',[]);hinges=fe.get('m_HingeLimits',[])
    bends=fe.get('m_KelagerBends',[])
    maximum_hinge_residual=0.
    hard=[row for row in fe['m_Rods'] if row['flRelaxationFactor']==1.]
    soft=[row for row in fe['m_Rods'] if row['flRelaxationFactor']<1.]
    if compiled:
        from .fe_rod_constraints import project_compiled_rods
    if animated:
        from .fe_rod_constraints import project_animated_rods
    if triangles:
        from .fe_triangle_constraints import project_compiled_triangles
    if quads:
        from .fe_quad_constraints import project_compiled_quads
    if hinges:
        from .fe_hinge_limits import project_hinge_limits
    if bends:
        from .fe_kelager_bends import project_kelager_bends
    if planes or rigid_parents:
        from .vendor.pose_math import slerp
    if planes:
        from .fe_collision_planes import project_collision_planes
    default_substeps = max(1, math.ceil(256/fps))
    interval_substeps = ([default_substeps]*len(intervals) if sample_times is None else
                         [max(1, math.ceil(256*float(delta))) for delta in intervals])
    for frame in range(1, len(targets)):
        substeps = interval_substeps[frame-1]
        dt = float(intervals[frame-1])/substeps
        for step in range(1, substeps+1):
            blend = step/substeps
            if input_rows is not None:
                # Native goal preparation precedes the solver. Within this
                # explicitly approximate clock, blend raw animation roots then
                # prepare the hard forest/soft children again. The guarded
                # layout closes repeated native preparation passes exactly.
                rows=input_rows[frame-1]*(1-blend)+input_rows[frame]*blend
                qa=input_rows[frame-1,:,4:];qb=input_rows[frame,:,4:]
                qb=qb*np.where(np.sum(qa*qb,axis=1)<0,-1.,1.)[:,None]
                q=qa*(1-blend)+qb*blend;rows[:,4:]=q/np.linalg.norm(q,axis=1)[:,None]
                transforms=control_transform_matrices(prepare_control_offsets(rows,physics) if offsets else rows,unit_scale)
            else:
                transforms = targets[frame-1]*(1-blend) + targets[frame]*blend
            goals = transforms[:, :3, 3]
            if planes:
                plane_controls=plane_frames[frame-1].copy()
                plane_controls[:,:3]=goals/unit_scale
                plane_controls[plane_parents,4:]=slerp(plane_frames[frame-1,plane_parents,4:],
                    plane_frames[frame,plane_parents,4:],blend)
            rigid_controls=None
            if rigid_parents:
                rigid_controls=rigid_frames[frame-1].copy()
                rigid_controls[:,:3]=goals/unit_scale
                rigid_controls[rigid_parents,4:]=slerp(rigid_frames[frame-1,rigid_parents,4:],
                    rigid_frames[frame,rigid_parents,4:],blend)
            old = points.copy()
            velocity *= np.exp(-damping[:, None]*64*dt)
            velocity[dynamic, 2] -= gravity[dynamic]*dt
            points[dynamic] += velocity[dynamic]*dt
            alpha = 1-np.exp(-attraction*64*dt)
            points[dynamic] += alpha[dynamic, None]*(goals[dynamic]-points[dynamic])
            points[~dynamic] = goals[~dynamic]
            for _ in range(iterations):
                # Observed current native ordinary iteration: bends, hinges,
                # rods, animated rods, quads, triangles. Its separate last-rod
                # branch and full simulation clock are not reproduced here.
                if bends:
                    projected=project_kelager_bends(points/unit_scale,bends)*unit_scale
                    if np.any(abs(projected[~dynamic]-points[~dynamic])>1e-8):
                        raise ValueError('FE bends changed a position-driven/static control')
                    points=projected
                if hinges:
                    projected,hinge_report=project_hinge_limits(points/unit_scale,fe['m_NodeInvMasses'],hinges)
                    projected*=unit_scale
                    if np.any(abs(projected[~dynamic]-points[~dynamic])>1e-8):
                        raise ValueError('FE hinges changed a position-driven/static control')
                    points=projected
                    maximum_hinge_residual=max(maximum_hinge_residual,hinge_report['maximum_angle_residual_radians'])
                if compiled:
                    projected=project_compiled_rods(points/unit_scale,compiled)*unit_scale
                    if np.any(abs(projected[~dynamic]-points[~dynamic])>1e-8):
                        raise ValueError('Compiled FE rods changed a position-driven/static control')
                    points=projected
                else:
                    for rod in fe['m_Rods']:
                        a, b = rod['nNode']
                        delta = points[b]-points[a]; length = float(np.linalg.norm(delta))
                        if length < 1e-12:
                            continue
                        minimum, maximum = rod['flMinDist']*unit_scale, rod['flMaxDist']*unit_scale
                        target = min(max(length, minimum), maximum)
                        correction = delta*((length-target)/length*rod['flRelaxationFactor'])
                        if dynamic[a]:points[a] += correction*rod['flWeight0']
                        if dynamic[b]:points[b] -= correction*(1-rod['flWeight0'])
                if animated:
                    projected=project_animated_rods(points/unit_scale,animated,goals/unit_scale)*unit_scale
                    if np.any(abs(projected[~dynamic]-points[~dynamic])>1e-8):
                        raise ValueError('Animated FE rods changed a position-driven/static control')
                    points=projected
                if quads:
                    projected=project_compiled_quads(points/unit_scale,quads,
                        two_pin_count=fe['m_nSimdQuadCount2'],one_pin_count=fe['m_nSimdQuadCount1'])*unit_scale
                    if np.any(abs(projected[~dynamic]-points[~dynamic])>1e-8):
                        raise ValueError('Compiled FE quads changed a position-driven/static control')
                    points=projected
                if triangles:
                    projected=project_compiled_triangles(points/unit_scale,triangles,
                        two_pin_count=fe['m_nSimdTriCount2'],one_pin_count=fe['m_nSimdTriCount1'])*unit_scale
                    if np.any(abs(projected[~dynamic]-points[~dynamic])>1e-8):
                        raise ValueError('Compiled FE triangles changed a position-driven/static control')
                    points=projected
                for row in fe.get('m_AnimStrayRadii', []):
                    a, b = row['nNode']
                    if not dynamic[a]:continue
                    delta = points[a]-goals[b]; length = float(np.linalg.norm(delta))
                    maximum = row['flMaxDist']*unit_scale
                    if length > maximum and length > 1e-12:
                        points[a] -= delta*((length-maximum)/length*row['flRelaxationFactor'])
                _collide(points, transforms, fe, dynamic, radii, unit_scale,
                    previous_points=old,rigid_controls=rigid_controls,rigid_history=rigid_history)
                if planes:
                    projected=project_collision_planes(points/unit_scale,plane_controls,planes)*unit_scale
                    if np.any(abs(projected[~dynamic]-points[~dynamic])>1e-8):
                        raise ValueError('FE collision planes changed a position-driven/static control')
                    points=projected
                points[~dynamic] = goals[~dynamic]
            if not np.isfinite(points).all():
                raise ValueError('FE node simulation produced a nonfinite position')
            velocity = (points-old)/dt
        samples.append(points.copy())
        residual = rod_residuals(points, fe['m_Rods'], unit_scale)
        residuals.append(float(residual.max()) if len(residual) else 0.)
        for rods,values in ((hard,hard_residuals),(soft,soft_residuals)):
            errors=rod_residuals(points,rods,unit_scale);values.append(float(errors.max()) if len(errors) else 0.)
    return np.asarray(samples), dict(substeps=max(interval_substeps,default=default_substeps),iterations=iterations,
        sample_clock='output_fps' if sample_times is None else 'explicit_seconds',
        sample_time_span_seconds=float(intervals.sum()),
        minimum_sample_interval_seconds=float(intervals.min()) if len(intervals) else None,
        maximum_sample_interval_seconds=float(intervals.max()) if len(intervals) else None,
        maximum_rod_residual_m=max(residuals,default=0.),
        maximum_full_relaxation_rod_residual_m=max(hard_residuals,default=0.),
        maximum_soft_rod_residual_m=max(soft_residuals,default=0.),
        native_qualified_compiled_rod_blocks=len(compiled),
        native_qualified_animated_rod_blocks=len(animated),
        native_qualified_triangle_blocks=len(triangles),
        native_qualified_two_pin_triangle_blocks=fe.get('m_nSimdTriCount2',0) if triangles else 0,
        native_qualified_one_pin_triangle_blocks=(fe['m_nSimdTriCount1']-fe['m_nSimdTriCount2']) if triangles else 0,
        native_qualified_free_triangle_blocks=(len(triangles)-fe['m_nSimdTriCount1']) if triangles else 0,
        native_qualified_quad_blocks=len(quads),
        native_qualified_two_pin_quad_blocks=fe.get('m_nSimdQuadCount2',0) if quads else 0,
        native_qualified_free_quad_blocks=(len(quads)-fe['m_nSimdQuadCount1']) if quads else 0,
        native_qualified_static_parent_collision_planes=len(planes),
        native_qualified_outward_sphere_contact_count=len(spheres),
        native_qualified_outward_capsule_contact_count=len(capsules),
        native_qualified_outward_box_contact_count=len(boxes),
        sphere_contact_history='Prior substep points; collider cache advances each pass within the approximate clock',
        approximate_box_contact_count=0,
        body_collision_limits='Priority scheduling and integrator clock remain approximate; sphere/capsule/box geometry and friction operations qualified separately',
        native_qualified_hard_control_offsets=len(fe.get('m_CtrlOffsets',[])),
        native_qualified_soft_control_offsets=len(fe.get('m_CtrlSoftOffsets',[])),
        control_preparation_repeat_closure=offsets,
        control_preparation='Native animation gather forces control scale one; ordered hard/soft goals precede integration with normalized linear raw quaternion interpolation',
        native_qualified_hinge_count=len(hinges),maximum_hinge_pass_residual_radians=maximum_hinge_residual,
        native_qualified_kelager_bend_count=len(bends),
        ordinary_iteration_operation_order=['kelager_bends','hinges','rods','animated_rods','quads','triangles'],
        unmapped_parameter_arrays=[key for key in ('m_Ropes','m_DynNodeFriction','m_RigidColliderPriorities') if fe.get(key)],
        integration=('Fixed-step gravity with exponential animation attraction approximation' if sample_times is None else
                     'Explicit sample intervals subdivided to at most 1/256 second; gravity/exponential attraction approximation'),
        source_solver_parity=False)
