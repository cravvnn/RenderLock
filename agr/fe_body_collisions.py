"""Individually qualified FE body-contact operations; scheduling is separate."""
import numpy as np


def collider_node_selection(fe, row):
    """Select the dynamic prefix by compiled masks or a positive-byte named map."""
    count=fe['m_nNodeCount'];static=fe['m_nStaticNodes']
    if type(count) is not int or not 1<=count<=4096 or type(static) is not int or not 0<=static<count:
        raise ValueError('Bounded FE collider node/static intervals required')
    selection=row.get('nVertexMapIndex');mask=row.get('nCollisionMask')
    if any(type(v) is not int or not 0<=v<=65535 for v in (selection,mask)):
        raise ValueError('Unsigned FE collider selection and mask required')
    if selection==65535:
        masks=fe.get('m_TreeCollisionMasks',[]);dynamic=count-static
        if (not isinstance(masks,list) or len(masks)!=2*dynamic-1
                or any(type(v) is not int or not 0<=v<=65535 for v in masks)):
            raise ValueError('Complete compiled FE collision masks required')
        return np.flatnonzero((np.asarray(masks[:dynamic],dtype=np.uint16)&mask)!=0)+static
    maps=fe.get('m_VertexMaps',[]);values=fe.get('m_VertexMapValues',[])
    if (not isinstance(maps,list) or not selection<len(maps)<=256
            or not isinstance(values,list) or len(values)>2097152
            or any(type(v) is not int or not 0<=v<=255 for v in values)):
        raise ValueError('Bounded authored FE collider vertex map required')
    compiled_offset=0;selected=[]
    for number,mapping in enumerate(maps):
        base,length,offset=(mapping.get(k) for k in ('nVertexBase','nVertexCount','nMapOffset'))
        if (any(type(v) is not int for v in (base,length,offset))
                or not 0<=base<=base+length<=count or not 0<=offset<=offset+length<=len(values)):
            raise ValueError('FE collider map outside node/byte arrays')
        chosen=[base+j for j in range(length) if values[offset+j]>0]
        if mapping.get('nNodeListOffset')!=compiled_offset or mapping.get('nNodeListCount')!=len(chosen):
            raise ValueError('FE collider named-map compiled inventory differs')
        compiled_offset+=len(chosen)
        if number==selection:selected=[n for n in chosen if n>=static]
    # Explicit named maps replace mask selection; byte values are membership,
    # including 1 and 128, rather than fractional contact strengths.
    return np.asarray(selected,dtype=np.int64)


def world_sphere(local, control):
    """Transform an authored sphere by one xyz/scale/xyzw Source control."""
    from .fe_node_rotations import _rotate
    local=np.asarray(local,dtype='f4');control=np.asarray(control,dtype='f4')
    if (local.shape!=(4,) or control.shape!=(8,) or not np.isfinite(local).all()
            or not np.isfinite(control).all() or local[3]<0 or control[3]<=0
            or abs(np.linalg.norm(control[4:])-1)>2e-5):
        raise ValueError('Finite FE local sphere and positive-scale unit-quaternion control required')
    with np.errstate(over='ignore',invalid='ignore'):
        result=np.r_[control[:3]+control[3]*_rotate(control[4:],local[:3]),abs(control[3]*local[3])]
    if not np.isfinite(result).all():raise ValueError('FE world sphere exceeds finite Source coordinates')
    return result


def relative_collision_frame(current, previous, *, history_valid):
    """Return Source's rigid collider motion, in Source units.

    Rows are xyz/scale/xyzw. The native friction frame deliberately ignores
    both scale fields. A caller must advance its cached row after each collider
    pass, even when no point contacts the collider; this is not a frame clock.
    """
    from .fe_node_rotations import _multiply, _inverse, _rotate
    current=np.asarray(current,dtype=float);previous=np.asarray(previous,dtype=float)
    if (current.shape!=(8,) or previous.shape!=(8,)
            or not np.isfinite(current).all() or not np.isfinite(previous).all()
            or current[3]<=0 or previous[3]<=0
            or abs(np.linalg.norm(current[4:])-1)>2e-5
            or abs(np.linalg.norm(previous[4:])-1)>2e-5
            or type(history_valid) is not bool):
        raise ValueError('Finite FE collider transforms with unit quaternions and explicit history required')
    if not history_valid:return np.c_[np.eye(3),np.zeros(3)]
    q=_multiply(current[4:],_inverse(previous[4:]))
    rotation=np.column_stack([_rotate(q,axis) for axis in np.eye(3)])
    return np.c_[rotation,current[:3]-_rotate(q,previous[:3])]


def world_box(local, control, size):
    """Compose a unit local box with a positive-scale Source control.

    Returns world rotation/center rows and the scaled authored half extents.
    Signed extents are retained: the native clamp order also handles these
    compiled declarations; replacing them with absolute values changes contact.
    """
    from .fe_node_rotations import _multiply, _rotate
    local=np.asarray(local,dtype='f4');control=np.asarray(control,dtype='f4')
    size=np.asarray(size,dtype='f4')
    if (local.shape!=(8,) or control.shape!=(8,) or size.shape!=(3,)
            or not all(np.isfinite(v).all() for v in (local,control,size))
            or local[3]!=1 or control[3]<=0
            or abs(np.linalg.norm(local[4:])-1)>2e-5
            or abs(np.linalg.norm(control[4:])-1)>2e-5):
        raise ValueError('Finite FE box, unit local frame and positive-scale unit-quaternion control required')
    q=_multiply(control[4:],local[4:])
    rotation=np.column_stack([_rotate(q,axis) for axis in np.eye(3)])
    with np.errstate(over='ignore',invalid='ignore'):
        center=control[:3]+control[3]*_rotate(control[4:],local[:3])
        frame=np.c_[rotation,center].astype('f4');half=size*control[3]
    if not np.isfinite(frame).all() or not np.isfinite(half).all():
        raise ValueError('FE world box exceeds finite Source coordinates')
    return frame,half


def project_box(points, frame, half, node_radii, selected, *, previous=None,
                relative_transform=None, friction=None):
    """Project the qualified outward self-box contact with explicit history.

    Distances use Source units. Without a friction array the native operation
    expands the box and requires a strictly nearest face (ties stay unchanged).
    An existing array, including zero coefficients, uses rounded corner distance
    and a distinct nearest-face tie rule. Collision order/clock are separate.
    """
    kwargs={} if friction is None else dict(previous=previous,relative_transform=relative_transform,friction=friction)
    # Validate every input point/radius/history value, even when unselected.
    result=project_sphere(points,[0,0,0,0],node_radii,[],**kwargs)
    if friction is None and (previous is not None or relative_transform is not None):
        raise ValueError('FE box history requires an explicit friction array')
    if (not isinstance(selected,(list,tuple,np.ndarray)) or len(selected)>len(result)
            or any(not isinstance(n,(int,np.integer)) or isinstance(n,(bool,np.bool_))
                   or not 0<=n<len(result) for n in selected) or len(set(selected))!=len(selected)):
        raise ValueError('Distinct bounded FE box selected nodes required')
    frame=np.asarray(frame,dtype=float);half=np.asarray(half,dtype=float)
    if (frame.shape!=(3,4) or half.shape!=(3,)
            or not np.isfinite(frame).all() or not np.isfinite(half).all()
            or np.max(abs(frame[:,:3].T@frame[:,:3]-np.eye(3)))>2e-5
            or abs(np.linalg.det(frame[:,:3])-1)>2e-5):
        raise ValueError('Finite FE box frame with a proper unit rotation and signed half extents required')
    with np.errstate(over='ignore',invalid='ignore'):
        frame=frame.astype('f4');half=half.astype('f4');radii=np.asarray(node_radii,dtype='f4')
    if not np.isfinite(frame).all() or not np.isfinite(half).all():
        raise ValueError('FE box frame exceeds finite Source coordinates')
    if friction is not None:
        previous=np.asarray(previous,dtype=float);relative_transform=np.asarray(relative_transform,dtype=float)
        friction=np.asarray(friction,dtype=float)
    for node in selected:
        node=int(node)
        with np.errstate(over='ignore',invalid='ignore'):
            offset=result[node].astype('f4')-frame[:,3]
            products=offset[:,None]*frame[:,:3]
            local=(products[1]+products[0])+products[2]
            distances=half+radii[node]-abs(local)
        if not np.isfinite(local).all() or not np.isfinite(distances).all():
            raise ValueError('FE box distances exceed finite Source coordinates')
        if friction is None:
            if np.any(distances<0):continue
            minimum=np.minimum(distances[[1,2,0]],distances[[2,0,1]])
            correction=np.where(distances<minimum,distances,np.float32(0))*np.where(np.signbit(local),-1.,1.)
            products=frame[:,:3]*correction.astype('f4')[None,:]
            result[node]+=(products[:,0]+products[:,1])+products[:,2]
        else:
            # Keep MIN then MAX order, including authored signed extents.
            nearest=np.maximum(np.minimum(local,half),-half)
            with np.errstate(over='ignore',invalid='ignore'):
                delta=local-nearest;squares=delta*delta
                squared=(squares[0]+squares[1])+squares[2]
            if not np.isfinite(squared):raise ValueError('FE box corner distance exceeds finite Source coordinates')
            if squared>np.float32(1e-5):
                distance=np.sqrt(squared);depth=float(radii[node]-distance)
                if depth<=0:continue
                normal=frame[:,:3]@(delta/distance)
            else:
                distances=half-abs(local)
                axis=0 if distances[0]<distances[1] and distances[0]<distances[2] else 1 if distances[1]<distances[2] else 2
                normal=frame[:,axis]*(-1 if local[axis]<0 else 1)
                depth=float(distances[axis]+radii[node])
                if depth<=0:continue
            result[node]+=_contact_correction(result[node],previous[node],relative_transform,
                                               normal,depth,float(friction[node]))
        result[node]=result[node].astype('f4')
    if not np.isfinite(result).all():raise ValueError('FE box projection became nonfinite')
    return result


def project_sphere(points, sphere, node_radii, selected, *, previous=None,
                   relative_transform=None, friction=None):
    """Project an outward world sphere with an explicit, ordered node selection.

    All distances use Source units. ``friction=None`` selects the native
    no-friction-array operation. An existing array, including an all-zero array,
    selects a different near-center fallback and needs explicit history inputs.
    This operation does not choose collision masks, traverse a tree, record
    contacts, or advance the caller's collider/position history.
    """
    points=np.asarray(points,dtype=float);sphere=np.asarray(sphere,dtype=float)
    radii=np.asarray(node_radii,dtype=float)
    if (points.ndim!=2 or points.shape[1:]!=(3,) or not 1<=len(points)<=4096
            or sphere.shape!=(4,) or radii.shape!=(len(points),)
            or not all(np.isfinite(v).all() for v in (points,sphere,radii))
            or sphere[3]<0 or np.any(radii<0)
            or not isinstance(selected,(list,tuple,np.ndarray)) or len(selected)>len(points)
            or any(not isinstance(n,(int,np.integer)) or isinstance(n,(bool,np.bool_))
                   or not 0<=n<len(points) for n in selected)
            or len(set(selected))!=len(selected)):
        raise ValueError('Finite bounded FE sphere, radii and distinct selected nodes required')
    if friction is not None:
        friction=np.asarray(friction,dtype=float);previous=np.asarray(previous,dtype=float)
        relative_transform=np.asarray(relative_transform,dtype=float)
        if (friction.shape!=(len(points),) or previous.shape!=points.shape
                or relative_transform.shape!=(3,4)
                or not all(np.isfinite(v).all() for v in (friction,previous,relative_transform))
                or np.any(friction<0)):
            raise ValueError('FE sphere friction requires finite coefficients, previous points and affine motion')
    elif previous is not None or relative_transform is not None:
        raise ValueError('FE sphere history requires an explicit friction array')
    with np.errstate(over='ignore',invalid='ignore'):
        source_points=points.astype('f4');source_sphere=sphere.astype('f4');source_radii=radii.astype('f4')
    if not all(np.isfinite(v).all() for v in (source_points,source_sphere,source_radii)):
        raise ValueError('FE sphere inputs exceed finite Source coordinates')
    result=points.copy()
    for node in selected:
        node=int(node)
        _project_sphere_node(result[node],source_points[node],sphere,source_sphere,source_radii[node],
            previous=None if friction is None else previous[node],relative_transform=relative_transform,
            friction=None if friction is None else float(friction[node]))
    if not np.isfinite(result).all():raise ValueError('FE sphere projection became nonfinite')
    return result


def _project_sphere_node(point,source_point,sphere,source_sphere,source_radius,*,
                         previous,relative_transform,friction):
    """Apply one contact after the public operation validates all input buffers.

    Capsule nodes have distinct projected spheres. Reusing this kernel keeps
    their float32 branch/order semantics without repeating full-array validation.
    """
    # Preserve float32 subtraction and x+y+z addition order at the test;
    # a double dot product can choose the other normal at exactly 0.1.
    with np.errstate(over='ignore',invalid='ignore'):
        delta=source_point-source_sphere[:3]
        squares=delta*delta;squared=float((squares[0]+squares[1])+squares[2])
        radius=source_sphere[3]+source_radius;radius_squared=radius*radius
    if not np.isfinite([squared,radius,radius_squared]).all():
        raise ValueError('FE sphere distances exceed finite Source coordinates')
    radius=float(radius)
    if radius_squared<=squared:return
    if friction is None:
        if squared>2**-23:
            point+=delta*(radius/np.sqrt(squared)-1)
        else:point[:]=sphere[:3]+[0.,0.,radius]
    else:
        # Array presence, including zero coefficients, selects this native path.
        if squared<float(np.float32(.01)):
            normal=np.array([0.,0.,1.]);penetration=radius
        else:
            distance=float(np.sqrt(squared));normal=delta/distance
            penetration=radius-distance
        point+=_contact_correction(point,previous,relative_transform,
                                   normal,penetration,friction)
    point[:]=point.astype('f4')


def _contact_correction(point,previous,relative,normal,penetration,friction):
    correction=normal*penetration
    if friction>0:
        target=relative[:,:3]@previous+relative[:,3]
        tangent=target-point;tangent-=normal*(tangent@normal)
        length=float(np.linalg.norm(tangent));maximum=friction*penetration
        if length>maximum:tangent*=maximum/length
        correction+=tangent
    return correction


def project_capsule(points, spheres, node_radii, selected, *, previous=None,
                    relative_transform=None, friction=None):
    """Project Source's outward tapered capsule with explicit contact history.

    ``spheres`` contains its two world xyz/radius rows. The radial distance
    changes the chosen sphere's axial location; simply choosing the closest
    centerline point gives the wrong result for unequal authored radii.
    """
    spheres=np.asarray(spheres,dtype='f4')
    if spheres.shape!=(2,4) or not np.isfinite(spheres).all() or np.any(spheres[:,3]<0):
        raise ValueError('Two finite positive-radius FE capsule spheres required')
    kwargs={} if friction is None else dict(previous=previous,relative_transform=relative_transform,friction=friction)
    # Validate all buffers/selection even for a noncontacting/empty capsule.
    result=project_sphere(points,spheres[0],node_radii,[],**kwargs)
    # project_sphere cannot validate nodes omitted from its empty selection.
    if (not isinstance(selected,(list,tuple,np.ndarray)) or len(selected)>len(result)
            or any(not isinstance(n,(int,np.integer)) or isinstance(n,(bool,np.bool_))
                   or not 0<=n<len(result) for n in selected) or len(set(selected))!=len(selected)):
        raise ValueError('Distinct bounded FE capsule selected nodes required')
    if friction is None and (previous is not None or relative_transform is not None):
        raise ValueError('FE capsule history requires an explicit friction array')
    delta=spheres[1]-spheres[0];squared=delta[:3]*delta[:3]
    length=np.sqrt((squared[0]+squared[1])+squared[2])
    if not np.isfinite(length):raise ValueError('FE capsule length exceeds Source coordinates')
    if np.float32(length-delta[3])<=np.float32(.5):
        return project_sphere(points,spheres[1],node_radii,selected,**kwargs)
    if length==0:
        return project_sphere(points,spheres[0],node_radii,selected,**kwargs)
    direction=delta/length
    radii=np.asarray(node_radii,dtype='f4');prior=None if previous is None else np.asarray(previous,dtype=float)
    coefficients=None if friction is None else np.asarray(friction,dtype=float)
    relative=None if friction is None else np.asarray(relative_transform,dtype=float)
    for node in selected:
        node=int(node);point=np.asarray(result[node],dtype='f4');offset=point-spheres[0,:3]
        products=direction[:3]*offset;axial=(products[1]+products[0])+products[2]
        radial=offset-direction[:3]*axial;products=radial*radial
        radial_distance=np.sqrt((products[0]+products[1])+products[2])
        distance=direction[3]*radial_distance+axial
        if distance<=0:sphere=spheres[0]
        elif distance>length:sphere=spheres[1]
        else:
            blend=distance/length;sphere=(np.float32(1)-blend)*spheres[0]+blend*spheres[1]
        _project_sphere_node(result[node],point,sphere,sphere,radii[node],
            previous=None if coefficients is None else prior[node],relative_transform=relative,
            friction=None if coefficients is None else float(coefficients[node]))
    if not np.isfinite(result).all():raise ValueError('FE capsule projection became nonfinite')
    return result


def project_contact(points,previous,relative_transform,node,normal,penetration,friction):
    """Project one frictional contact using the collider-relative prior point.

    Positions and penetration use Source units. The caller supplies the actual
    previous-point buffer and affine collider motion; this function does not
    establish either binding or a time step. Contact recording is not included.
    """
    points=np.asarray(points,dtype=float);previous=np.asarray(previous,dtype=float)
    relative=np.asarray(relative_transform,dtype=float);normal=np.asarray(normal,dtype=float)
    if (points.ndim!=2 or points.shape[1:]!=(3,) or not 1<=len(points)<=4096
            or previous.shape!=points.shape or relative.shape!=(3,4) or normal.shape!=(3,)
            or not all(np.isfinite(v).all() for v in (points,previous,relative,normal))
            or abs(np.linalg.norm(normal)-1)>2e-5):
        raise ValueError('Finite bounded FE contact positions, affine motion and unit normal required')
    if type(node) is not int or not 0<=node<len(points):
        raise ValueError('FE contact node outside point buffer')
    if any(type(v) not in (int,float) or not np.isfinite(v) or v<0 for v in (penetration,friction)):
        raise ValueError('Nonnegative finite FE penetration and friction required')
    result=points.copy()
    result[node]+=_contact_correction(points[node],previous[node],relative,normal,penetration,friction)
    if not np.isfinite(result).all():raise ValueError('FE contact projection became nonfinite')
    return result
