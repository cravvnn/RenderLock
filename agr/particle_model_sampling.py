"""Prepared model-shape sampler; no entity acquisition or initializer registration.

Caller supplies a random source. No game random table or recovered engine seed
is included. The callback receives whether this draw uses the extra seed offset.
"""
import numpy as np


def sample_model_shapes(shapes, origin, *, scale, bias, shell, count, weighted, random_value):
    """Sample prepared boxes with the checked Source float32 operation order.

    This subset excludes forced/excluded shapes and even distribution. Bounds,
    transform, mapping and weight must already have been resolved by the caller.
    """
    f=np.float32
    if type(count) is not int or not 0 <= count <= 100000 or type(weighted) is not bool:
        raise ValueError('Invalid model sample count or weighted selection flag')
    if len(shapes)>32767:
        raise ValueError('Too many model shapes')
    def vector(value, length):
        value=np.asarray(value,dtype=f)
        if value.shape!=(length,) or not np.isfinite(value).all():
            raise ValueError('Expected finite model sampling vector')
        return value
    origin=vector(origin,3); scale=vector(scale,3); bias=vector(bias,3)
    if np.any(scale<0) or not np.isfinite(shell) or not 0<=shell<=1:
        raise ValueError('Unsupported model sampling scale or shell fraction')
    for shape in shapes:
        low,high=vector(shape['low'],3),vector(shape['high'],3)
        vector(shape['translation'],3); vector(shape['rotation'],4)
        if np.any(low>high) or not np.isfinite([shape['scale'],shape['weight']]).all():
            raise ValueError('Invalid model shape bounds or transform')
        if shape['scale']<=0 or shape['weight']<0:
            raise ValueError('Unsupported model shape scale or weight')
        if type(shape['mapping']) is not int or not -1<=shape['mapping']<32767:
            raise ValueError('Unsupported model shape mapping/exclusion')
    total=sum(shape['weight'] for shape in shapes)
    if not np.isfinite(total) or total>np.finfo(f).max or (weighted and shapes and total<=0):
        raise ValueError('Invalid total model shape weight')
    def rand(extra_seed=False):
        value=f(random_value(extra_seed))
        if not np.isfinite(value) or not 0<=value<1:
            raise ValueError('Model sampling random source must return [0,1)')
        return value
    scale=np.array(scale,dtype='f');bias=np.array(bias,dtype='f');shell=f(shell)
    high=scale*f(.5)+f(.5);low=f(1)-high
    attempts=0 if np.all((bias>f(-.0001))&(bias<f(.0001))) else 5
    points=[];uvs=[];ids=[]
    for _ in range(count):
        if not shapes:
            points.append(origin.copy());uvs.append([0,0,0]);ids.append(0);continue
        best=f(-1e20)
        for __ in range(attempts+1):
            index=int(f(len(shapes))*rand())
            if weighted:
                target=f(sum(s['weight'] for s in shapes))*rand()
                for index,s in enumerate(shapes):
                    target=f(target-f(s['weight']))
                    if target<=0:break
            s=shapes[index]
            # Source uses counter+2*seed for the contiguous XYZ triplet.
            uv=np.array([f(f(high[j]-low[j])*rand(True)+low[j]) for j in range(3)],dtype='f')
            if shell!=1:
                axis=int(rand()*f(3));sign=f(1 if int(rand()*f(2))==1 else -1)
                edge=high[axis] if sign<0 else low[axis]
                uv[axis]=f(f(f(f(scale[axis]*shell)*f(.5))*rand())*sign+edge)
            local=(np.array(s['high'],dtype='f')-np.array(s['low'],dtype='f'))*uv+np.array(s['low'],dtype='f')
            q=np.array(s['rotation'],dtype='f');t=f(2)*np.cross(q[:3],local)
            point=(local+q[3]*t+np.cross(q[:3],t))*f(s['scale'])+np.array(s['translation'],dtype='f')
            score=f(np.finfo(np.float32).max)
            if attempts>1:
                delta=point-origin
                score=f(f(f(delta[2]*bias[2])+f(delta[1]*bias[1]))+f(delta[0]*bias[0]))
                score=f(score+f(rand()*f(72)))
            if score>best:
                best=score;selected=(point.copy(),uv.copy(),s['mapping'] if s['mapping']>=0 else index)
        point,uv,index=selected;points.append(point);uvs.append(uv);ids.append(index)
    result=(np.asarray(points,dtype=f).reshape(-1,3), np.asarray(uvs,dtype=f).reshape(-1,3),
            np.asarray(ids,dtype=np.int32))
    if not np.isfinite(result[0]).all() or not np.isfinite(result[1]).all():
        raise ValueError('Nonfinite model sampling result')
    return result
