"""Ideal seamless cube sampling; hardware filter precision remains distinct."""
import numpy as np


def face_uv(direction, axis=None):
    d=np.asarray(direction,dtype=float)
    if d.shape!=(3,) or not np.isfinite(d).all() or not np.any(d):raise ValueError('Invalid cube direction')
    axis=int(np.argmax(abs(d))) if axis is None else axis
    x,y,z=d;major=abs(d[axis]);negative=d[axis]<0
    if axis==0:u,v=(z if negative else -z)/major,-y/major
    elif axis==1:u,v=x/major,(-z if negative else z)/major
    else:u,v=(-x if negative else x)/major,-y/major
    return axis*2+int(negative),np.array([u,v])*.5+.5


def face_direction(face, uv):
    u,v=np.asarray(uv)*2-1
    return np.array([(1,-v,-u),(-1,-v,u),(u,1,v),(u,-1,-v),(u,-v,1),(-u,-v,-1)][face],dtype=float)


def _tap(faces,face,x,y):
    n=faces.shape[1]
    if 0<=x<n and 0<=y<n:return faces[face,y,x].astype(float)
    if (x<0 or x>=n) and (y<0 or y>=n):
        # A corner footprint joins three face corners, rather than selecting an
        # arbitrary major axis for the doubly-outside tap.
        direction=face_direction(face,[0 if x<0 else 1,0 if y<0 else 1]);values=[]
        for axis in range(3):
            f,uv=face_uv(direction,axis);q=np.clip(np.floor(uv*n).astype(int),0,n-1);values.append(faces[f,q[1],q[0]].astype(float))
        return np.mean(values,axis=0)
    direction=face_direction(face,[(x+.5)/n,(y+.5)/n]);f,uv=face_uv(direction);q=np.clip(np.floor(uv*n).astype(int),0,n-1)
    return faces[f,q[1],q[0]].astype(float)


def sample_level(faces,direction):
    pixels=np.asarray(faces)
    if pixels.ndim!=4 or pixels.shape[0]!=6 or pixels.shape[1]!=pixels.shape[2] or pixels.shape[-1]!=4 or pixels.dtype!=np.float32:raise ValueError('Expected float32 square cube faces')
    face,uv=face_uv(direction);q=uv*pixels.shape[1]-.5;lo=np.floor(q).astype(int);fraction=q-lo
    rows=[]
    for y in range(2):
        a=_tap(pixels,face,int(lo[0]),int(lo[1]+y));b=_tap(pixels,face,int(lo[0]+1),int(lo[1]+y));rows.append(a+(b-a)*fraction[0])
    return rows[0]+(rows[1]-rows[0])*fraction[1]


def sample_cube(levels,directions,lods):
    d=np.asarray(directions,dtype=float);lod=np.asarray(lods,dtype=float)
    if d.ndim!=2 or d.shape[1]!=3 or lod.shape!=(len(d),) or not np.isfinite(lod).all() or not levels:raise ValueError('Invalid cube queries')
    sizes=[np.asarray(p).shape[1] for p in levels]
    if sizes!=[max(1,sizes[0]>>i) for i in range(len(sizes))]:raise ValueError('Invalid mip sequence')
    result=[]
    for direction,l in zip(d,lod):
        l=float(np.clip(l,0,len(levels)-1));low=int(np.floor(l));high=min(low+1,len(levels)-1)
        a=sample_level(levels[low],direction);b=sample_level(levels[high],direction);result.append(a+(b-a)*(l-low))
    return np.array(result).reshape(-1,4)
