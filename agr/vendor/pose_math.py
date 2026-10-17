"""Nm pose mathematics in Source units, xyzw quaternions, column-vector order.

The blend polynomial constants are read from the supplied client's blend kernel.
The model-space blend and mask feathering follow the inspected routines and
the MIT-licensed Esoterica reference implementation (see THIRD_PARTY.md).
"""
from dataclasses import dataclass
from functools import lru_cache
import numpy as np


def normalize(q):
    q=np.asarray(q,dtype=float)
    norm=np.linalg.norm(q,axis=-1,keepdims=True)
    if np.any(norm<1e-10):raise ValueError('Zero quaternion')
    return q/norm


def mul(a,b):
    a=np.asarray(a);b=np.asarray(b)
    av,aw=a[...,:3],a[...,3:];bv,bw=b[...,:3],b[...,3:]
    return np.concatenate((aw*bv+bw*av+np.cross(av,bv),aw*bw-np.sum(av*bv,axis=-1,keepdims=True)),axis=-1)


def inv(q):
    out=np.array(q,copy=True);out[...,:3]*=-1
    return out


def rotate(q,v):
    q=np.asarray(q);v=np.asarray(v)
    u=2*np.cross(q[...,:3],v)
    return v+q[...,3:]*u+np.cross(q[...,:3],u)


def fast_slerp(a,b,t):
    a=np.asarray(a);b=np.asarray(b);t=np.asarray(t)
    d=np.sum(a*b,axis=-1);x=np.abs(d)
    aa=((3.5564498901367188-1.435189962387085*x)*x-3.2451999187469482)*x+1.090399980545044
    bb=(0.21563799679279327*x-1.0602099895477295)*x+0.8480129837989807
    u=t+(aa*(t-.5)**2+bb)*(t-.5)*t*(t-1)
    return normalize(a*(1-u)[...,None]+b*(np.where(d>0,1.,-1.)*u)[...,None])


def slerp(a,b,t):
    a=np.asarray(a);b=np.asarray(b);d=np.sum(a*b,axis=-1)
    b=b*np.where(d<0,-1.,1.)[...,None];d=np.clip(np.abs(d),0,1)
    angle=np.arccos(d);sin=np.sin(angle);safe=np.where(sin<1e-8,1,sin)
    aa=np.where(sin<1e-8,1-np.asarray(t),np.sin((1-np.asarray(t))*angle)/safe)
    bb=np.where(sin<1e-8,np.asarray(t),np.sin(np.asarray(t)*angle)/safe)
    return normalize(a*aa[...,None]+b*bb[...,None])


def axis_angle(axis,angle):
    norm=np.linalg.norm(axis)
    if norm<1e-10:return np.array([0.,0.,0.,1.])
    return np.r_[np.asarray(axis)/norm*np.sin(angle/2),np.cos(angle/2)]


def between(a,b):
    an=np.linalg.norm(a);bn=np.linalg.norm(b)
    if an<1e-8 or bn<1e-8:return np.array([0.,0.,0.,1.])
    a=a/an;b=b/bn;d=np.clip(a@b,-1,1)
    if d<-1+1e-8:
        axis=np.cross(a,np.eye(3)[np.argmin(np.abs(a))])
        return axis_angle(axis,np.pi)
    return normalize(np.r_[np.cross(a,b),1+d])


@dataclass
class Pose:
    q: np.ndarray
    t: np.ndarray
    s: np.ndarray
    additive: bool=False

    def copy(self):return Pose(self.q.copy(),self.t.copy(),self.s.copy(),self.additive)


@lru_cache(maxsize=16)
def hierarchy_levels(parents):
    levels={};depths=[]
    for i,j in enumerate(parents):
        depth=0 if j<0 else depths[j]+1
        depths.append(depth);levels.setdefault(depth,[]).append(i)
    return tuple(np.asarray(v,dtype=int) for k,v in sorted(levels.items()) if k)


def globals_for(p,parents):
    q=p.q.copy();t=p.t.copy();s=p.s.copy()
    for i in hierarchy_levels(tuple(parents)):
        j=np.asarray(parents)[i]
        q[i]=normalize(mul(q[j],p.q[i]))
        t[i]=t[j]+rotate(q[j],p.t[i]*s[j,None]);s[i]=s[j]*p.s[i]
    return Pose(q,t,s,p.additive)


def local_from_global(g,index,parent):
    if parent<0:return g.q[index],g.t[index],g.s[index]
    return normalize(mul(inv(g.q[parent]),g.q[index])),rotate(inv(g.q[parent]),g.t[index]-g.t[parent])/g.s[parent],g.s[index]/g.s[parent]


def feather_mask(skeleton,definition,missing_bones=None):
    names=skeleton['m_boneIDs'];parents=skeleton['m_parentIndices']
    weights=np.full(len(names),-1.);index={n:i for i,n in enumerate(names)}
    mask_id=definition.get('m_ID')
    definition=definition['m_primaryWeightList']
    if len(definition['m_boneIDs'])!=len(definition['m_weights']):
        raise ValueError('Mask bone and weight counts differ')
    for n,w in zip(definition['m_boneIDs'],definition['m_weights']):
        if not isinstance(n,str) or not n or not np.isfinite(w) or not -1<=w<=1:
            raise ValueError('Invalid mask bone name or weight')
        if n in index:weights[index[n]]=w
        elif missing_bones is not None:
            missing_bones.append({'mask':mask_id,'bone':n,'weight':w,
                                  'reason':'not_in_primary_skeleton'})
        # Esoterica BoneMask(weightList) skips unresolved authoring names before
        # ResetWeights. Keep the mask slot and the remaining feathering intact.
    original=weights.copy()
    for i in range(len(names)-1,0,-1):
        if weights[i]==-1:
            chain=[i];j=parents[i];weight=0.
            while j!=-1:
                if original[j]!=-1:weight=original[j];break
                chain.append(j);j=parents[j]
            if j==-1:chain.pop()
            weights[chain]=weight
        elif weights[parents[i]]==-1:
            end=weights[i];start=-1.;chain=[i];j=parents[i]
            while j!=-1:
                chain.append(j)
                if original[j]!=-1:start=original[j];break
                j=parents[j]
            for k in range(len(chain)-2,0,-1):
                weights[chain[k]]=end+(start-end)*k/(len(chain)-1) if start!=-1 else 0.
    if weights[0]==-1:weights[0]=0
    assert np.all((weights>=0)&(weights<=1))
    return weights


def mask_tasks(tasks,masks,bones):
    if not tasks:return None
    values=[]
    for task in tasks:
        t=task['type_id'];w=task.get('weight_u8',0)/255
        if t==0:out=masks[task['mask_index']].copy()
        elif t==1:out=np.full(bones,w)
        elif t==2:out=values[task['source']]*(1-w)+values[task['target']]*w
        elif t==3:out=values[task['source']]*w
        elif t==4:out=values[task['source']]*values[task['target']]
        else:raise ValueError('Unsupported mask task '+str(t))
        values.append(out)
    return values[-1]


def blend(a,b,w,mask=None,kind='normal'):
    if kind=='additive':
        if not b.additive:raise ValueError('Additive blend target is not an additive pose')
    elif a.additive!=b.additive:raise ValueError('Incompatible pose types')
    if w==0:return a.copy()
    # The supplied normal blend kernel returns the entire target at weight 1,
    # before checking its mask; overlay/additive kernels do not take this path.
    if kind=='normal' and w==1:return b.copy()
    weights=np.full(len(a.s),w) if mask is None else w*mask
    target=mul(a.q,b.q) if kind=='additive' else b.q
    q=fast_slerp(a.q,target,weights)
    if kind=='additive':t=a.t+b.t*weights[:,None];s=a.s+b.s*weights
    else:t=a.t+(b.t-a.t)*weights[:,None];s=a.s+(b.s-a.s)*weights
    return Pose(q,t,s,a.additive and b.additive)


def model_blend(a,b,w,mask,parents):
    if a.additive or b.additive:raise ValueError('Model-space blend on additive pose')
    if mask is None:raise ValueError('Model-space blend needs a mask')
    if w==0 or not np.any(mask):return a.copy()
    ag=globals_for(a,parents);bg=globals_for(b,parents)
    rotations=ag.q.copy();result=b.copy()
    result.t=a.t+(b.t-a.t)*mask[:,None];result.s=a.s+(b.s-a.s)*mask
    rotations[0]=fast_slerp(a.q[0],b.q[0],mask[0]) if mask[0] else a.q[0]
    # The binary starts with the layer buffer; the reference algorithm does not
    # replace its root rotation before the final local-space blend.
    rotations[1:]=fast_slerp(ag.q[1:],bg.q[1:],mask[1:])
    result.q[1:]=normalize(mul(inv(rotations[np.asarray(parents)[1:]]),rotations[1:]))
    result.q[mask==0]=a.q[mask==0]
    return blend(a,result,w)


def solve_ik(p,parents,reference,effector,target,weight=1.,mode='effector',chain_weight=0.):
    if weight==0:return p.copy()
    g=globals_for(p,parents);ref=globals_for(reference,parents)
    end=effector;mid=parents[end];root=parents[mid]
    if root<0:raise ValueError('IK chain has fewer than three bones')
    tq=g.q[target].copy();tt=g.t[target].copy();ts=g.s[target]
    if weight!=1 and mode=='effector':
        tq=fast_slerp(g.q[end],tq,weight);tt=g.t[end]+(tt-g.t[end])*weight;ts=g.s[end]+(ts-g.s[end])*weight
    v1=g.t[mid]-g.t[root];v2=g.t[end]-g.t[mid];desired=tt-g.t[root]
    l1=np.linalg.norm(v1);l2=np.linalg.norm(v2);ld=np.linalg.norm(desired)
    # Installed animationsystem solver 0x471730 returns without touching the
    # chain when either segment is <= 0.001 Source units. Confirmed by the
    # bounded oracle in research/verify_ik_degenerate.py, including zero.
    if min(l1,l2)<=float(np.float32(.001)):return p.copy()
    cosine=np.clip(-(v1/l1)@(v2/l2),-1,1)
    theta=np.arccos(cosine)
    wanted=np.arccos(np.clip((l1*l1+l2*l2-ld*ld)/(2*l1*l2),-1,1))
    hinge=np.cross(v2/l2,v1/l1)
    if abs(cosine+1)<1e-5:
        cross=np.cross(ref.t[end]-ref.t[mid],ref.t[mid]-ref.t[root])
        # This client build takes the fixed local-Y branch when the reference
        # cross length squared EXCEEDS 1e-6. That branch is reversed relative
        # to current Esoterica source; retain the observed binary behavior.
        local=np.array([0.,1.,0.]) if cross@cross>1e-6 else rotate(inv(ref.q[mid]),cross)
        if np.linalg.norm(local)<1e-12:raise ValueError('Degenerate native IK fallback axis')
        hinge=rotate(g.q[mid],local/np.linalg.norm(local))
    else:hinge/=np.linalg.norm(hinge)
    hinge_local=rotate(inv(g.q[mid]),hinge)
    g.q[mid]=normalize(mul(axis_angle(hinge,wanted-theta),g.q[mid]))
    g.t[end]=g.t[mid]+rotate(g.q[mid],p.t[end]*g.s[mid]);g.q[end]=mul(g.q[mid],p.q[end])
    midq=mul(inv(g.q[root]),g.q[mid]);midt=rotate(inv(g.q[root]),g.t[mid]-g.t[root])/g.s[root]
    align=between(g.t[end]-g.t[root],desired)
    g.q[root]=normalize(mul(align,g.q[root]))
    g.q[mid]=normalize(mul(g.q[root],midq));g.t[mid]=g.t[root]+rotate(g.q[root],midt*g.s[root])
    g.q[end]=tq;g.t[end]=g.t[mid]+rotate(g.q[mid],p.t[end]*g.s[mid])
    if chain_weight>0:
        current=rotate(g.q[mid],hinge_local)
        desired_hinge=rotate(g.q[end],rotate(inv(ref.q[end]),rotate(ref.q[mid],hinge_local)))
        axis=g.t[root]-g.t[end];axis/=np.linalg.norm(axis)
        a=desired_hinge-axis*(desired_hinge@axis);b=current-axis*(current@axis)
        angle=np.arctan2(axis@np.cross(a,b),a@b)
        twist=slerp(np.array([0.,0.,0.,1.]),axis_angle(axis,-angle),chain_weight)
        g.q[root]=normalize(mul(twist,g.q[root]))
        g.q[mid]=normalize(mul(g.q[root],midq));g.t[mid]=g.t[root]+rotate(g.q[root],midt*g.s[root])
        g.t[end]=g.t[mid]+rotate(g.q[mid],p.t[end]*g.s[mid]);g.q[end]=tq
    out=p.copy()
    for i in [root,mid,end]:
        q,t,s=local_from_global(g,i,parents[i])
        if weight!=1 and mode=='pose':
            out.q[i]=fast_slerp(p.q[i],q,weight);out.t[i]=p.t[i]+(t-p.t[i])*weight;out.s[i]=p.s[i]+(s-p.s[i])*weight
        else:out.q[i]=q;out.t[i]=t;out.s[i]=s
    return out


def follow_bone(p,parents,bone,target,mode):
    if mode not in (0,2):raise ValueError(f'Unsupported FollowBone mode {mode}; supported modes are 0 and 2')
    g=globals_for(p,parents)
    # Supplied client 0x181c5b4d8: mode 2 substitutes the driven bone's LOCAL
    # quaternion into the target model-space transform before parent inversion.
    # Keeping its old global rotation instead produces a different result.
    g.q[bone]=g.q[target] if mode==0 else p.q[bone]
    g.t[bone]=g.t[target];g.s[bone]=g.s[target]
    out=p.copy();out.q[bone],out.t[bone],out.s[bone]=local_from_global(g,bone,parents[bone])
    return out
