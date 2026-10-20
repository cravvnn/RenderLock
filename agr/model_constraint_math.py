"""Verified prepared-pose operations; model binding/constraint scheduling separate."""
import numpy as np
import math
from .vendor.pose_math import inv, mul, rotate


def aim_look_rotation(forward, up):
    """Source aim basis and float32 quaternion conversion for nondegenerate inputs.

    +Y points forward and +Z follows projected up. Extreme vector magnitudes
    and zero/parallel inputs require the separate engine fallback, not a guess.
    """
    f = np.float32
    def vector(value):
        value = np.asarray(value, dtype=f)
        if value.shape != (3,) or not np.isfinite(value).all():
            raise ValueError('Expected finite aim vector')
        return value
    def normalize3(value):
        squares = value*value
        length = f(np.sqrt(f(f(squares[2]+squares[1])+squares[0])))
        if not np.isfinite(length) or not f(1e-17) <= length <= f(1e17):
            raise ValueError('Degenerate or extreme aim basis')
        return value*f(f(1)/length)
    direction = normalize3(vector(forward))
    vertical = vector(up)
    products = vertical*direction
    dot = f(f(products[2]+products[1])+products[0])
    vertical = normalize3(vertical-direction*dot)
    side = normalize3(np.cross(direction, vertical))
    b = np.array([side,direction,vertical],dtype=f)
    q = np.array([f(b[1,2]-b[2,1]),f(b[2,0]-b[0,2]),f(b[0,1]-b[1,0]),
                  f(f(f(b[1,1]+b[0,0])+b[2,2])+f(1))],dtype=f)
    squares = q*q
    branch_length = f(f(f(squares[0]+squares[1])+squares[2])+squares[3])
    if branch_length > np.finfo(f).eps:
        length = f(np.sqrt(f(f(squares[0]+squares[1])+f(squares[2]+squares[3]))))
        return (q/length).astype(float)
    # Standard largest-diagonal conversion in the near-half-turn fallback.
    m = b.T
    index = 2 if m[2,2] > m[0,0] and m[2,2] > m[1,1] else 0 if m[0,0] > m[1,1] else 1
    j,k = (index+1)%3,(index+2)%3
    root = f(np.sqrt(f(f(m[index,index]-f(m[j,j]+m[k,k]))+f(1))))
    reciprocal = f(f(.5)/root)
    q = np.empty(4,dtype=f)
    q[index] = f(root*f(.5))
    q[j] = f(f(m[j,index]+m[index,j])*reciprocal)
    q[k] = f(f(m[k,index]+m[index,k])*reciprocal)
    q[3] = f(f(m[k,j]-m[j,k])*reciprocal)
    return q.astype(float)


def point_single(pose, bone, descendants, target_position, *, target_weight=1., slave_weight=1.):
    """Apply a resolved single-target point constraint in model space."""
    indices = list(descendants)
    values = orient_single(pose, bone, indices, (0.,0.,0.,1.), target_weight=0.)
    target = np.asarray(target_position, dtype=np.float32)
    if target.shape != (3,) or not np.isfinite(target).all():
        raise ValueError('Expected finite point target')
    if not np.isfinite([target_weight,slave_weight]).all():
        raise ValueError('Expected finite constraint weights')
    with np.errstate(over='ignore'):
        weight = np.float32(target_weight)*np.float32(slave_weight)
    if not np.isfinite(weight):
        raise ValueError('Constraint weight overflow')
    if abs(weight) <= np.float32(1e-4):
        return values
    old = values[bone,:3].astype(np.float32)
    new = target if abs(weight-np.float32(1)) <= np.float32(1e-4) else old+(target-old)*weight
    delta = new-old
    values[bone,:3] = new
    if indices and np.any(abs(delta)>np.float32(1e-4)):
        values[indices,:3] += delta
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite constrained pose')
    return values


def point_two_targets(pose, bone, descendants, positions, weights, *, slave_weight=1.):
    """Prepared two-bone point provider: float32 weighted mean and total weight.

    Target transforms/offsets are resolved by the caller. Attachment providers
    and their runtime state are separate; zero combined weight leaves the pose.
    """
    f=np.float32
    with np.errstate(over='ignore',invalid='ignore'):
        points=np.asarray(positions,dtype=f)
        factors=np.asarray(weights,dtype=f)
    if (points.shape!=(2,3) or factors.shape!=(2,) or
            not np.isfinite(points).all() or not np.isfinite(factors).all()):
        raise ValueError('Two finite point targets and weights required')
    with np.errstate(over='ignore',invalid='ignore',divide='ignore'):
        total=f(factors[0]+factors[1])
        target=points[0]*factors[0]+points[1]*factors[1]
        if total!=0:target=target*f(f(1)/total)
    if not np.isfinite(total) or not np.isfinite(target).all():
        raise ValueError('Point target average exceeds finite domain')
    return point_single(pose,bone,descendants,target,
                        target_weight=float(total),slave_weight=slave_weight)


def compose_constraint_rotation(first,second):
    """Original transform-compose quaternion order and float32 normalization."""
    a,b=np.asarray(first,dtype=np.float32),np.asarray(second,dtype=np.float32)
    if (a.shape!=(4,) or b.shape!=(4,) or not np.isfinite(a).all() or not np.isfinite(b).all()
            or abs(np.linalg.norm(a.astype(float))-1)>1e-5 or abs(np.linalg.norm(b.astype(float))-1)>1e-5):
        raise ValueError('Expected unit constraint rotations')
    x=np.array([b[3],-b[2],b[1],-b[0]],dtype=np.float32)
    y=np.array([b[2],b[3],-b[0],-b[1]],dtype=np.float32)
    z=np.array([-b[1],b[0],b[3],-b[2]],dtype=np.float32)
    q=a[3]*b+a[0]*x
    q=q+a[1]*y
    q=a[2]*z+q
    squares=q*q
    length=np.float32(np.sqrt(np.float32(np.float32(squares[0]+squares[1])+np.float32(squares[2]+squares[3]))))
    return (q/length).astype(float)


def _constraint_slerp(first,second,weight):
    """Constraint Evaluate's float32 interpolation polynomial and unit fallback."""
    f=np.float32
    a,b=np.asarray(first,dtype=f),np.asarray(second,dtype=f)
    t=f(weight)
    product=a*b
    dot=f(f(product[0]+product[1])+f(product[2]+product[3]))
    x=abs(dot)
    aa=f(f(3.5564498901367188)-f(f(1.435189962387085)*x))
    aa=f(f(aa*x)-f(3.2451999187469482))
    aa=f(f(aa*x)+f(1.090399980545044))
    bb=f(f(f(.21563799679279327)*x)-f(1.0602099895477295))
    bb=f(f(bb*x)+f(.8480129837989807))
    half=f(t-f(.5))
    u=f(f(f(f(aa*f(half*half))+bb)*f(f(half*t)*f(t-f(1))))+t)
    result=a*f(f(1)-u)+b*(u if dot>0 else -u)
    squares=result*result
    length=f(np.sqrt(f(f(squares[0]+squares[1])+f(squares[2]+squares[3]))))
    return ((result/length).astype(float) if length else np.array([0.,0.,0.,1.]))


def quaternion_two_target_average(rotations,weights):
    """Current Source prepared two-target, hemisphere-aligned log/exp mean.

    The first target supplies the hemisphere. Positive total weights normalize
    the log mean; nonpositive totals keep the authored weights. Native small
    vector fallbacks are intentional, including +Z for negative identity.
    Only finite unit-quaternion inputs and unit output are admitted.
    """
    f=np.float32
    with np.errstate(over='ignore',invalid='ignore'):
        rotations=np.asarray(rotations,dtype=f)
        weights=np.asarray(weights,dtype=f)
    if (rotations.shape!=(2,4) or weights.shape!=(2,) or
            not np.isfinite(rotations).all() or not np.isfinite(weights).all() or
            np.any(abs(np.linalg.norm(rotations.astype(float),axis=1)-1)>1e-5)):
        raise ValueError('Two unit quaternion targets and finite weights required')
    with np.errstate(over='ignore',invalid='ignore',divide='ignore'):
        total=f(weights[0]+weights[1])
        reciprocal=f(f(1)/total) if total>0 else f(1)
        if not np.isfinite(total) or not np.isfinite(reciprocal):
            raise ValueError('Quaternion weight sum exceeds finite domain')
        result=np.zeros(4,dtype=f)
        for rotation,weight in zip(rotations,weights):
            q=rotation.copy()
            product=q*rotations[0]
            dot=f(f(product[0]+product[1])+f(product[2]+product[3]))
            if np.signbit(dot):q=-q
            squares=q*q
            length=f(np.sqrt(f(f(squares[0]+squares[1])+squares[2])))
            if length>=f(1e-5):
                q=q*f(f(math.atan2(float(length),float(q[3])))/length)
            elif q[3]>0:
                q=q/q[3]
            elif q[3]==0:
                q=np.zeros(4,dtype=f)
            else:
                axis=np.array([0,0,1,0],dtype=f)
                if length>0:axis[:3]=q[:3]/length
                q=axis*f(f(math.pi)-f(length/-q[3]))
            norm_squared=f(f(squares[0]+squares[1])+f(squares[2]+squares[3]))
            q[3]=f(math.log(float(norm_squared)))*f(.5)
            result=result+q*f(weight*reciprocal)
        if not np.isfinite(result).all():
            raise ValueError('Quaternion weighted logarithm exceeds finite domain')
        try:magnitude=f(math.exp(float(result[3])))
        except OverflowError as exc:
            raise ValueError('Quaternion target average exceeds finite domain') from exc
        squares=result*result
        length=f(np.sqrt(f(f(squares[0]+squares[1])+squares[2])))
        if not np.isfinite(length) or not np.isfinite(magnitude):
            raise ValueError('Quaternion target exponential exceeds finite domain')
        if length>=f(1e-4):
            sine=f(math.sin(float(length)));cosine=f(math.cos(float(length)))
            coefficient=f(sine/length)
        else:coefficient=cosine=f(1)
        result[:3]=result[:3]*f(coefficient*magnitude)
        result[3]=f(cosine*magnitude)
    if (not np.isfinite(total) or not np.isfinite(result).all() or
            abs(np.linalg.norm(result.astype(float))-1)>1e-5):
        raise ValueError('Quaternion target average exceeds finite domain')
    return result.astype(float),float(total)


def orient_two_targets(pose,bone,descendants,rotations,weights,*,slave_weight=1.):
    """Apply the prepared native two-target orientation provider and writeback."""
    rotation,total=quaternion_two_target_average(rotations,weights)
    return orient_single(pose,bone,descendants,rotation,
                         target_weight=total,slave_weight=slave_weight)


def compose_constraint_transform(first,second):
    """Positive-uniform Source transform composition, with float32 writeback."""
    a,b=np.asarray(first,dtype=np.float32),np.asarray(second,dtype=np.float32)
    if (a.shape!=(8,) or b.shape!=(8,) or not np.isfinite(a).all() or not np.isfinite(b).all()
            or a[3]<=0 or b[3]<=0):
        raise ValueError('Expected finite positive-uniform constraint transforms')
    result=np.empty(8,dtype=np.float32)
    result[:3]=rotate(a[4:],b[:3])*a[3]+a[:3]
    result[3]=a[3]*b[3]
    result[4:]=compose_constraint_rotation(a[4:],b[4:])
    if not np.isfinite(result).all() or result[3]<=0:
        raise ValueError('Constraint transform composition exceeds finite domain')
    return result.astype(float)


def single_bone_attachment_transform(bone, offset):
    """Qualified one-influence attachment kernel; rotation stays unnormalized."""
    a,b=np.asarray(bone,dtype=np.float32),np.asarray(offset,dtype=np.float32)
    result=compose_constraint_transform(a,b)
    product=a[4:]*b[4:]
    dot=np.float32(np.float32(product[0]+product[1])+np.float32(product[2]+product[3]))
    q=-b[4:] if np.signbit(dot) else b[4:]
    x=np.array([q[3],-q[2],q[1],-q[0]],dtype=np.float32)
    y=np.array([q[2],q[3],-q[0],-q[1]],dtype=np.float32)
    z=np.array([-q[1],q[0],q[3],-q[2]],dtype=np.float32)
    rotation=a[7]*q+a[4]*x
    rotation=rotation+a[5]*y
    result[4:]=a[6]*z+rotation
    return result


def _inverse_constraint_transform(value):
    f=np.float32
    value=np.asarray(value,dtype=f)
    raw=inv(value[4:])
    squares=raw*raw
    rotation=raw/f(np.sqrt(f(f(squares[0]+squares[1])+f(squares[2]+squares[3]))))
    result=np.empty(8,dtype=f)
    result[3]=f(1)/value[3]
    result[:3]=(-rotate(raw,value[:3]) if value[3]==f(1) else
                rotate(rotation,value[:3])*-result[3])
    result[4:]=rotation
    return result


def parent_targets(pose,bone,descendants,targets,weights,*,slave_weight=1.):
    """Prepared one/two-target parent provider, combined blend and writeback.

    Native Parent uses total*slave for skip/full gates, but raw slave weight
    for the intermediate transform's linear position/scale and quaternion nlerp.
    Target offsets are resolved before entry; attachment providers are separate.
    """
    f=np.float32
    children=list(descendants)
    values=orient_single(pose,bone,children,(0.,0.,0.,1.),target_weight=0.)
    with np.errstate(over='ignore',invalid='ignore'):
        targets=np.asarray(targets,dtype=f);weights=np.asarray(weights,dtype=f)
    if (targets.shape not in ((1,8),(2,8)) or weights.shape!=(len(targets),) or
            not np.isfinite(targets).all() or not np.isfinite(weights).all() or
            not np.isfinite(slave_weight) or np.any(targets[:,3]<=0) or
            np.any(abs(np.linalg.norm(targets[:,4:].astype(float),axis=1)-1)>1e-5)):
        raise ValueError('One or two finite positive-uniform parent targets required')
    with np.errstate(over='ignore',invalid='ignore',divide='ignore'):
        total=f(0);position_scale=np.zeros(4,dtype=f)
        for target,weight in zip(targets,weights):
            total=f(total+weight);position_scale=position_scale+target[:4]*weight
        if total>0:position_scale=position_scale*f(f(1)/total)
        strength=f(total*f(slave_weight))
    if not np.isfinite(total) or not np.isfinite(strength) or not np.isfinite(position_scale).all():
        raise ValueError('Parent target weights exceed finite domain')
    if abs(strength)<=f(1e-4):return values
    rotation=(targets[0,4:].astype(float) if len(targets)==1 else
              quaternion_two_target_average(targets[:,4:],weights)[0])
    target=np.concatenate([position_scale,rotation]).astype(f)
    old=values[bone].astype(f)
    if abs(f(strength-f(1)))<=f(1e-4):
        new=target
    else:
        amount=f(slave_weight)
        product=old[4:]*target[4:]
        dot=f(f(product[0]+product[1])+f(product[2]+product[3]))
        aligned=-target[4:] if np.signbit(dot) else target[4:]
        blended=aligned*amount+old[4:]*f(f(1)-amount)
        squares=blended*blended
        length=f(np.sqrt(f(f(squares[0]+squares[1])+f(squares[2]+squares[3]))))
        new=np.empty(8,dtype=f)
        new[:4]=old[:4]+amount*(target[:4]-old[:4])
        new[4:]=blended/length if length else np.array([0,0,0,1],dtype=f)
    if not np.isfinite(new).all() or new[3]<=0:
        raise ValueError('Parent result exceeds positive-uniform transform domain')
    delta=compose_constraint_transform(new,_inverse_constraint_transform(old))
    near=(np.all(abs(delta[:3])<=f(1e-4)) and abs(delta[3]-1)<=f(1e-4) and
          np.all(abs(delta[4:7])<=f(.1)) and min(abs(delta[7]-1),abs(delta[7]+1))<=f(.1))
    result=values.copy()
    if not near:
        for child in children:result[child]=compose_constraint_transform(delta,values[child])
    result[bone]=new
    if not np.isfinite(result).all():raise ValueError('Nonfinite parent-constrained pose')
    return result


def aim_single(pose, bone, descendants, target_position, *, bone_parent,
               up_type=3, up_vector=(0., 0., 1.), up_target=None,
               aim_offset=(0., 0., 0., 1.), target_weight=1., slave_weight=1.):
    """Apply a resolved aim target to a parented bone in model space.

    Up modes: 0 target rotation, 1 world vector, 2 target position, 3 parent
    rotation. Up target is an Nx8-style single transform, not an attachment.
    Root-bone and degenerate-direction engine fallbacks remain unsupported.
    """
    indices = list(descendants)
    values = orient_single(pose, bone, indices, (0., 0., 0., 1.), target_weight=0.)
    if type(bone_parent) is not int or not 0 <= bone_parent < len(values) or bone_parent == bone:
        raise ValueError('Aim requires a valid distinct parent binding')
    if type(up_type) is not int or up_type not in (0, 1, 2, 3):
        raise ValueError('Invalid aim up type')
    position = np.asarray(target_position, dtype=float)
    up = np.asarray(up_vector, dtype=float)
    offset = np.asarray(aim_offset, dtype=float)
    if (position.shape != (3,) or up.shape != (3,) or offset.shape != (4,) or
            not all(np.isfinite(v).all() for v in (position, up, offset)) or
            abs(np.linalg.norm(offset)-1) > 1e-5):
        raise ValueError('Invalid aim target, up vector or unit offset')
    if up_type in (0, 2):
        up_target = np.asarray(up_target, dtype=float)
        if (up_target.shape != (8,) or not np.isfinite(up_target).all() or
                up_target[3] <= 0 or abs(np.linalg.norm(up_target[4:])-1) > 1e-5):
            raise ValueError('Aim up mode requires a valid target transform')
    if not np.isfinite([target_weight, slave_weight]).all():
        raise ValueError('Expected finite constraint weights')
    with np.errstate(over='ignore'):
        weight = np.float32(target_weight)*np.float32(slave_weight)
    if not np.isfinite(weight):
        raise ValueError('Constraint weight overflow')
    if abs(weight) <= np.float32(1e-4):
        return values
    # Preserve the game transform order: inverse translation is added after
    # rotating/scaling each point, before their difference is normalized.
    f = np.float32
    def normalize3(v):
        v = np.asarray(v, dtype=f)
        square = v*v
        length = f(np.sqrt(f(f(square[1]+square[2])+square[0])))
        if not f(1e-17) <= length <= f(1e17):
            raise ValueError('Degenerate or extreme aim direction')
        return v*f(f(1)/length)
    parent = values[bone_parent].astype(f)
    raw_inverse = inv(parent[4:])
    parent_inverse = raw_inverse.copy()
    square = parent_inverse*parent_inverse
    parent_inverse /= f(np.sqrt(f(f(square[0]+square[1])+f(square[2]+square[3]))))
    inverse_scale = f(f(1)/parent[3])
    inverse_position = (-rotate(raw_inverse, parent[:3]) if parent[3] == f(1)
                        else rotate(parent_inverse, parent[:3])*(-inverse_scale))
    local_position = rotate(parent_inverse, values[bone, :3].astype(f))*inverse_scale+inverse_position
    local_target = rotate(parent_inverse, position.astype(f))*inverse_scale+inverse_position
    up = up.astype(f)
    if up_type == 0:
        up = rotate(up_target[4:].astype(f), up)
    elif up_type == 2:
        world_position = rotate(parent[4:], local_position)*parent[3]+parent[:3]
        up = normalize3(up_target[:3].astype(f)-world_position)
    elif up_type == 3:
        up = rotate(parent[4:], up)
    look = aim_look_rotation(normalize3(local_target-local_position), rotate(parent_inverse, up))
    local = mul(look, -offset if np.dot(look, offset) < 0 else offset)
    rotation = mul(parent[4:], -local if np.dot(parent[4:], local) < 0 else local)
    return orient_single(values, bone, indices, rotation,
                         target_weight=target_weight, slave_weight=slave_weight)


def twist_angle(rotation, axis):
    """Installed tilt/twist angle extraction, in radians."""
    q = np.asarray(rotation, dtype=float)
    if type(axis) is not int or axis not in (0, 1, 2) or q.shape != (4,) or not np.isfinite(q).all():
        raise ValueError('Invalid twist quaternion or axis')
    length = np.linalg.norm(q)
    q = q / length if length else np.array([0., 0., 0., 1.])
    basis = rotate(q, np.eye(3))
    one, two = (axis+1) % 3, (axis+2) % 3
    diagonal = np.clip(np.floor(basis[axis, axis]*1e7+.5)/1e7, -1, 1)
    tilt = math.acos(diagonal+1 if diagonal < 0 else 1-diagonal)
    if abs(tilt-math.pi/2) > 1e-4:
        a, b = basis[axis, one], basis[axis, two]
        length = max(math.hypot(a, b), 1.17549435e-38)
        a, b = a/length, b/length
        m1, m2 = basis[one, axis], basis[two, axis]
        return math.atan2(m2*a-m1*b, -m1*a-m2*b)
    return 0. if abs(basis[one, one]-1) <= 1e-4 else math.atan2(basis[one, two], basis[one, one])


def twist_history(angle, weight, previous=None):
    """Return weighted continuous angle using the installed float32 order."""
    if not np.isfinite([angle, weight] + ([] if previous is None else [previous])).all():
        raise ValueError('Nonfinite twist history')
    f = np.float32
    with np.errstate(over='ignore'):
        current = f(f(angle)*f(weight))
    if not np.isfinite(current):
        raise ValueError('Twist weight overflow')
    if previous is None or f(previous) == current:
        return float(current)
    tau, pi = f(2*math.pi), f(math.pi)
    def wrap(value):
        shifted = f(value+tau)
        width = f(tau+tau)
        return f(f(shifted-f(width*f(math.floor(f(shifted/width)))))-tau)
    old, new = wrap(f(previous)), wrap(current)
    delta = f(new-old)
    if abs(delta) >= pi:
        delta = f(delta+(-tau if new > old else tau))
    return float(f(f(previous)+delta))


def tilt_twist(pose, bone, descendants, target, *, target_parent=-1, bone_parent=-1,
               inverse_offset=(0., 0., 0., 1.), target_axis=0, bone_axis=0,
               weight=1., previous=None):
    """Apply one resolved tilt/twist constraint; return pose and next history."""
    values = np.asarray(pose, dtype=float)
    if values.ndim != 2 or values.shape[1] != 8:
        raise ValueError('Expected Nx8 model transforms')
    for index in (target, target_parent, bone_parent):
        if type(index) is not int or not -1 <= index < len(values):
            raise ValueError('Invalid twist binding')
    if target < 0 or bone_axis not in (0, 1, 2) or type(bone_axis) is not int:
        raise ValueError('Invalid twist target or axis')
    offset = np.asarray(inverse_offset, dtype=float)
    if offset.shape != (4,) or not np.isfinite(offset).all() or abs(np.linalg.norm(offset)-1) > 1e-5:
        raise ValueError('Expected unit inverse offset')
    local = values[target, 4:]
    if target_parent >= 0:
        local = mul(inv(values[target_parent, 4:]), local)
    angle = twist_history(twist_angle(mul(offset, local), target_axis), weight, previous)
    rotation = np.zeros(4)
    rotation[bone_axis] = math.sin(angle*.5)
    rotation[3] = math.cos(angle*.5)
    if bone_parent >= 0:
        rotation = mul(values[bone_parent, 4:], rotation)
    return orient_single(values, bone, descendants, rotation), angle


def orient_single(pose, bone, descendants, target_rotation, *, target_weight=1., slave_weight=1.):
    """Apply a resolved single-target orientation constraint, returning a copy.

    Pose columns are Source-unit position XYZ, positive uniform scale, XYZW.
    Descendants must come from the authored model hierarchy, not pose order.
    This does not resolve attachments, average targets or register export support.
    """
    values = np.asarray(pose, dtype=float)
    target = np.asarray(target_rotation, dtype=float)
    if values.ndim != 2 or values.shape[1] != 8 or not np.isfinite(values).all():
        raise ValueError('Expected finite Nx8 model transforms')
    if np.any(values[:, 3] <= 0) or np.any(abs(np.linalg.norm(values[:, 4:], axis=1)-1) > 1e-5):
        raise ValueError('Expected positive uniform scales and unit quaternions')
    if target.shape != (4,) or not np.isfinite(target).all() or abs(np.linalg.norm(target)-1) > 1e-5:
        raise ValueError('Expected unit target quaternion')
    indices = list(descendants)
    if (type(bone) is not int or not 0 <= bone < len(values) or
            any(type(i) is not int or not 0 <= i < len(values) or i == bone for i in indices) or
            len(set(indices)) != len(indices)):
        raise ValueError('Invalid constrained bone or descendant indices')
    if not np.isfinite([target_weight, slave_weight]).all():
        raise ValueError('Expected finite constraint weights')
    with np.errstate(over='ignore'):
        weight = float(np.float32(target_weight) * np.float32(slave_weight))
    if not np.isfinite(weight):
        raise ValueError('Constraint weight overflow')
    result = values.copy()
    if abs(weight) <= np.float32(1e-4):
        return result
    old = values[bone]
    new_rotation = target if abs(np.float32(weight)-np.float32(1)) <= np.float32(1e-4) else _constraint_slerp(old[4:], target, weight)
    result[bone, 4:] = new_rotation
    delta_rotation = mul(new_rotation, inv(old[4:]))
    delta_position = old[:3] - rotate(delta_rotation, old[:3])
    near_identity = (np.all(abs(delta_position) <= 1e-4) and
                     np.all(abs(delta_rotation[:3]) <= .1) and
                     min(abs(delta_rotation[3]-1), abs(delta_rotation[3]+1)) <= .1)
    if indices and not near_identity:
        result[indices, :3] = rotate(delta_rotation, values[indices, :3]) + delta_position
        result[indices, 4:] = mul(delta_rotation, values[indices, 4:])
    if not np.isfinite(result).all():
        raise ValueError('Nonfinite constrained pose')
    return result
