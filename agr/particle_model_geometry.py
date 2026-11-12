"""Bounded embedded glTF geometry/morph reader for particle models.

The geometry-only VRF exporter bakes Source inches into glTF metres with
(X,Y,Z)->(Y,Z,X). Return Source-local vertices; placement is evaluated separately.
No external buffers, skinning, node animation or silently discarded primitives.
"""
import json
import math
import struct
from pathlib import Path
import numpy as np
from .installed_effects import UnsupportedEffect


def read_model_geometry(path):
    path=Path(path)
    if not 20<=path.stat().st_size<=64*1024*1024:
        raise UnsupportedEffect('Particle model exceeds bounded GLB size')
    raw=path.read_bytes()
    if struct.unpack_from('<III',raw)!=(0x46546c67,2,len(raw)):
        raise UnsupportedEffect('Invalid particle GLB header')
    chunks=[];offset=12
    while offset<len(raw):
        if offset+8>len(raw):raise UnsupportedEffect('Truncated particle GLB chunk')
        size,kind=struct.unpack_from('<II',raw,offset);offset+=8
        if size%4 or offset+size>len(raw):raise UnsupportedEffect('Invalid particle GLB chunk bounds')
        chunks.append((kind,raw[offset:offset+size]));offset+=size
    if [c[0] for c in chunks]!=[0x4e4f534a,0x004e4942]:
        raise UnsupportedEffect('Particle GLB requires one JSON and embedded binary chunk')
    doc=json.loads(chunks[0][1]);binary=chunks[1][1]
    buffers=doc.get('buffers',[])
    if (len(buffers)!=1 or 'uri' in buffers[0] or type(buffers[0].get('byteLength')) is not int
            or not len(binary)-3<=buffers[0]['byteLength']<=len(binary)
            or doc.get('extensionsRequired')):
        raise UnsupportedEffect('External, skinned or extended particle geometry is unsupported')
    binary=binary[:buffers[0]['byteLength']]

    def integer(value,low,high):
        if type(value) is not int or not low<=value<=high:raise UnsupportedEffect('Particle GLB index/range invalid')
        return value

    def view(index,count,width,dtype,byte_offset=0):
        views=doc.get('bufferViews',[]);v=views[integer(index,0,len(views)-1)]
        if v.get('buffer',0)!=0 or v.get('extensions'):raise UnsupportedEffect('Unsupported particle GLB buffer')
        item=np.dtype(dtype).itemsize;stride=integer(v.get('byteStride',width*item),width*item,252)
        start=integer(v.get('byteOffset',0),0,len(binary));size=integer(v['byteLength'],0,len(binary)-start)
        local=integer(byte_offset,0,size)
        if stride%item or local%item or local+(count-1)*stride+width*item>size:
            raise UnsupportedEffect('Particle GLB accessor exceeds its buffer view')
        return np.ndarray((count,width),dtype=dtype,buffer=binary,offset=start+local,strides=(stride,item)).copy()

    def accessor(index,kind,types=(5126,)):
        accessors=doc.get('accessors',[]);a=accessors[integer(index,0,len(accessors)-1)]
        if a.get('type')!=kind or a.get('componentType') not in types or a.get('normalized',False) or a.get('extensions'):
            raise UnsupportedEffect('Unsupported particle GLB accessor type')
        count=integer(a['count'],1,1000000);width={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[kind]
        dtype={5121:'u1',5123:'<u2',5125:'<u4',5126:'<f4'}[a['componentType']]
        result=view(a['bufferView'],count,width,dtype,a.get('byteOffset',0)) if 'bufferView' in a else np.zeros((count,width),dtype=dtype)
        if 'sparse' in a:
            s=a['sparse'];n=integer(s['count'],1,count);indices=s['indices'];values=s['values']
            if indices.get('componentType') not in (5121,5123,5125):raise UnsupportedEffect('Invalid sparse index type')
            ids=view(indices['bufferView'],n,1,{5121:'u1',5123:'<u2',5125:'<u4'}[indices['componentType']],indices.get('byteOffset',0)).ravel()
            if ids[-1]>=count or np.any(ids[1:]<=ids[:-1]):raise UnsupportedEffect('Invalid sparse particle GLB indices')
            result[ids]=view(values['bufferView'],n,width,dtype,values.get('byteOffset',0))
        if not np.isfinite(result).all():raise UnsupportedEffect('Non-finite particle model data')
        return result

    nodes=doc.get('nodes',[]);meshes=doc.get('meshes',[])
    static_skin=bool(doc.get('skins'))
    if static_skin:
        # The installed cylinder has one inert root joint. Accept it only when
        # its complete rest skin is identity; no skeletal animation is dropped.
        skins=doc['skins']
        if (doc.get('animations') or len(skins)!=1 or skins[0].get('joints')!=[1]
                or len(nodes)!=3 or nodes[0].get('children')!=[1]
                or nodes[2].get('mesh')!=0 or nodes[2].get('skin')!=0
                or any(k in n for n in nodes[:2] for k in ('mesh','skin'))
                or nodes[1].get('children') or nodes[2].get('children')
                or len(doc.get('scenes',[]))!=1 or doc['scenes'][0].get('nodes')!=[0,2]):
            raise UnsupportedEffect('Static particle skin requires one inert root joint')
        for node in nodes:
            matrix=np.asarray(node.get('matrix',np.eye(4).ravel()),dtype=float)
            if (matrix.shape!=(16,) or not np.allclose(matrix,np.eye(4).ravel(),atol=2e-6,rtol=0)
                    or not np.allclose(node.get('translation',[0,0,0]),[0,0,0],atol=2e-6,rtol=0)
                    or not np.allclose(node.get('scale',[1,1,1]),[1,1,1],atol=2e-6,rtol=0)
                    or not np.allclose(node.get('rotation',[0,0,0,1]),[0,0,0,1],atol=2e-6,rtol=0)
                    or node.get('weights')):
                raise UnsupportedEffect('Static particle joint/node transform is not identity')
        bind=accessor(skins[0]['inverseBindMatrices'],'MAT4')
        if bind.shape!=(1,16) or not np.allclose(bind[0],np.eye(4).ravel(),atol=2e-6,rtol=0):
            raise UnsupportedEffect('Static particle inverse bind is not identity')
        nodes=[{k:v for k,v in nodes[2].items() if k not in ('skin','translation','rotation','scale')}]
        doc=dict(doc,scenes=[dict(nodes=[0])])
    if len(nodes)!=1 or len(meshes)!=1 or nodes[0].get('mesh')!=0 or nodes[0].get('children') or 'skin' in nodes[0]:
        raise UnsupportedEffect('Particle model requires a single unskinned mesh node')
    node=nodes[0]
    if any(k in node for k in ('translation','rotation','scale','weights')) or not np.allclose(node.get('matrix',np.eye(4).ravel()),np.eye(4).ravel(),atol=2e-6,rtol=0):
        raise UnsupportedEffect('Particle GLB node transform is not baked')
    scenes=doc.get('scenes',[])
    if len(scenes)!=1 or scenes[0].get('nodes')!=[0]:raise UnsupportedEffect('Unexpected particle GLB scene graph')
    primitives=meshes[0].get('primitives',[])
    if len(primitives)!=1 or meshes[0].get('weights'):
        raise UnsupportedEffect('Particle model requires one primitive with zero base morph weights')
    primitive=primitives[0];attributes=primitive['attributes']
    # The exporter can bake a one-root static skin and retain its inert joint
    # streams while omitting the skin. Admit only that exact pair on static
    # geometry, and validate every value below; never discard a morph/skin.
    orphan_root_attributes=(not static_skin and not doc.get('animations')
        and not primitive.get('targets') and {'JOINTS_0','WEIGHTS_0'}<=set(attributes))
    allowed={'POSITION','NORMAL','TANGENT','TEXCOORD_0'}|({'JOINTS_0','WEIGHTS_0'} if static_skin or orphan_root_attributes else set())
    if primitive.get('mode',4)!=4 or primitive.get('extensions') or set(attributes)-allowed:
        raise UnsupportedEffect('Unsupported particle mesh topology or attributes')
    positions=accessor(attributes['POSITION'],'VEC3');uv=accessor(attributes['TEXCOORD_0'],'VEC2')
    normals=accessor(attributes['NORMAL'],'VEC3')
    indices=accessor(primitive['indices'],'SCALAR',(5121,5123,5125)).ravel()
    if len(positions)!=len(uv) or len(normals)!=len(positions) or len(indices)%3 or indices.max()>=len(positions):
        raise UnsupportedEffect('Invalid particle geometry vertex/index cardinality')
    if static_skin or orphan_root_attributes:
        joints=accessor(attributes['JOINTS_0'],'VEC4',(5121,5123));weights=accessor(attributes['WEIGHTS_0'],'VEC4')
        if (joints.shape!=weights.shape or joints.shape!=(len(positions),4) or np.any(joints)
                or not np.allclose(weights,np.tile([1,0,0,0],(len(positions),1)),atol=2e-6,rtol=0)):
            raise UnsupportedEffect('Static particle geometry is not wholly bound to its identity root')
    targets=primitive.get('targets',[])
    if not targets and not doc.get('animations'):
        return dict(positions=positions[:,[2,0,1]]/.0254,uv=uv,normals=normals[:,[2,0,1]],
                    indices=indices.reshape(-1,3),morphs=[],times=np.empty(0),weights=np.empty((0,0)),
                    animation_name=None,static=True)
    if not 1<=len(targets)<=64:raise UnsupportedEffect('Particle model requires bounded authored morph animation')
    morphs=[]
    for target in targets:
        if set(target)-{'POSITION','NORMAL'} or 'POSITION' not in target:raise UnsupportedEffect('Unsupported particle morph target')
        delta=accessor(target['POSITION'],'VEC3')
        if delta.shape!=positions.shape:raise UnsupportedEffect('Particle morph vertex cardinality mismatch')
        if 'NORMAL' in target and accessor(target['NORMAL'],'VEC3').shape!=normals.shape:
            raise UnsupportedEffect('Particle morph normal cardinality mismatch')
        morphs.append(delta[:,[2,0,1]]/.0254)
    animations=doc.get('animations',[])
    if len(animations)!=1:raise UnsupportedEffect('Particle model requires one authored animation')
    animation=animations[0];channels=animation.get('channels',[]);samplers=animation.get('samplers',[])
    if len(channels)!=1 or len(samplers)!=1 or channels[0].get('target')!={'node':0,'path':'weights'} or channels[0].get('sampler')!=0:
        raise UnsupportedEffect('Particle model animation is not a single morph channel')
    sampler=samplers[0]
    if sampler.get('interpolation','LINEAR')!='LINEAR':raise UnsupportedEffect('Unsupported particle morph interpolation')
    times=accessor(sampler['input'],'SCALAR').ravel();weights=accessor(sampler['output'],'SCALAR').ravel()
    if (not 2<=len(times)<=4096 or times[0]!=0 or np.any(np.diff(times)<=0)
            or len(weights)!=len(times)*len(morphs)):
        raise UnsupportedEffect('Invalid particle morph animation times/weights')
    return dict(positions=positions[:,[2,0,1]]/.0254,uv=uv,normals=normals[:,[2,0,1]],
                indices=indices.reshape(-1,3),morphs=morphs,times=times,weights=weights.reshape(len(times),len(morphs)),
                animation_name=animation.get('name',''))


def morph_weights(geometry,age,rate,source_fps,loop=True):
    """Age-driven authored frame rate; native playback scheduling remains approximate."""
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (age,rate,source_fps)) or age<0 or rate<0 or source_fps<=0:
        raise UnsupportedEffect('Invalid particle model animation timing')
    times=geometry['times'];time=age*rate/source_fps
    if not math.isfinite(time):raise UnsupportedEffect('Particle morph phase is non-finite')
    time=time%float(times[-1]) if loop else min(time,float(times[-1]))
    return np.array([np.interp(time,times,geometry['weights'][:,i]) for i in range(geometry['weights'].shape[1])])


def morph_keyframes(geometry, samples, rate, source_fps, *, manual=False):
    """Preserve authored morph breakpoints between captured replay ticks.

    Captured phase is interpolated linearly, without inferring missed loops or
    engine scheduling. Keeping every crossed authored breakpoint avoids losing
    wing poses when the output FPS differs from the replay or animation rate.
    Result times are replay ticks, independent of Blender/output FPS.
    """
    if type(manual) is not bool or not samples or len(samples) > 100000:
        raise UnsupportedEffect('Missing model morph samples or invalid manual mode')
    times = geometry['times']
    period = float(times[-1])
    if (period <= 0 or not np.allclose(geometry['weights'][0], geometry['weights'][-1], atol=1e-7, rtol=0)):
        raise UnsupportedEffect('Looping morph animation requires matching endpoint weights')
    phases = []
    for tick, _, particle in samples:
        value = particle.get('manual_frame' if manual else 'age')
        # Reuse the public timing validation; phase is measured in authored seconds.
        morph_weights(geometry, value, 1 if manual else rate, source_fps)
        if type(tick) is not int or (phases and tick != phases[-1][0] + 1):
            raise UnsupportedEffect('Model morph samples require consecutive replay ticks')
        phases.append((tick, value * (1 if manual else rate) / source_fps))
    result = []
    def append(tick, phase):
        if len(result) >= 100000:
            raise UnsupportedEffect('Model morph keyframe bound exceeded')
        phase %= period
        weights = np.array([np.interp(phase, times, geometry['weights'][:, i])
                            for i in range(geometry['weights'].shape[1])])
        result.append((float(tick), weights))
    append(*phases[0])
    for (tick0, phase0), (tick1, phase1) in zip(phases, phases[1:]):
        if phase0 != phase1:
            low, high = sorted((phase0, phase1))
            first, last = math.floor(low / period), math.floor(high / period)
            if (last - first + 1) * (len(times) - 1) > 100000:
                raise UnsupportedEffect('Model morph phase span exceeds keyframe bound')
            crossings = [cycle * period + float(time)
                         for cycle in range(first, last + 1) for time in times[:-1]
                         if low < cycle * period + float(time) < high]
            for phase in sorted(crossings, reverse=phase1 < phase0):
                append(tick0 + (phase - phase0) / (phase1 - phase0), phase)
        append(tick1, phase1)
    return result


def encode_morph_curves(schedule,start_tick,frame_step):
    """Encode a linear curve in Blender's float32 frame/weight representation.

    Coincident encoded times can be consolidated only when the resulting
    piecewise-linear curve stays within 1e-5 absolute weight error everywhere.
    Checking the union of original and encoded knots bounds every intervening
    linear segment, including near-coincident authored pose/capture knots.
    Discontinuous or materially different collisions still refuse the instance.
    """
    if (not schedule or len(schedule)>100000 or type(start_tick)is not int
            or type(frame_step)not in (int,float)or not math.isfinite(frame_step)or frame_step<=0):
        raise UnsupportedEffect('Invalid model morph curve encoding inputs')
    original=np.array([1+(tick-start_tick)*frame_step for tick,_ in schedule],dtype=np.float64)
    values=np.asarray([weights for _,weights in schedule],dtype=np.float64)
    if (not np.isfinite(original).all()or np.any(np.diff(original)<=0)
            or values.ndim!=2 or not 1<=values.shape[1]<=64 or not np.isfinite(values).all()
            or np.any(np.abs(values)>10)):
        raise UnsupportedEffect('Unqualified model morph curve shape, order or weights')
    with np.errstate(over='ignore',invalid='ignore'):
        encoded=original.astype(np.float32)
    if not np.isfinite(encoded).all():
        raise UnsupportedEffect('Model morph times exceed Blender frame precision')
    # Retain the final knot in each equal-time group. This is only a candidate;
    # the complete curve error check below decides whether it is representable.
    keep=np.r_[np.diff(encoded)>0,True]
    frames=encoded[keep];weights=values[keep].astype(np.float32)
    probes=np.unique(np.concatenate((original,frames.astype(np.float64))))
    worst=0.
    for column in range(values.shape[1]):
        expected=np.interp(probes,original,values[:,column])
        actual=np.interp(probes,frames,weights[:,column])
        worst=max(worst,float(np.max(np.abs(actual-expected))))
    if worst>1e-5:
        raise UnsupportedEffect('Model morph curve exceeds bounded Blender frame precision')
    return frames,weights,dict(policy='agr_float32_linear_morph_curves_v1',
        collapsed_knots=int(len(original)-len(frames)),max_weight_error=worst,weight_error_limit=1e-5)


def transform_static_model_frame(geometry, matrix, unit_scale):
    """Bake one complete captured affine transform without losing its shear.

    Input geometry is already in Source-local inches, as returned above. The
    caller owns replay/model identity, frame completeness and material admission;
    this pure operation neither infers tracks nor allocates Blender data blocks.
    Returned positions use scene units; UVs flip from Source to Blender once.
    Normals use the inverse transpose, not the position transform. This is a
    geometry component, not a qualification of native shading or visibility.
    """
    def array(value, width, name, *, indices=False):
        try:
            result = np.asarray(value)
        except (TypeError, ValueError) as exc:
            raise UnsupportedEffect('Invalid captured model '+name) from exc
        if (result.ndim != 2 or result.shape[1] != width
                or not 1 <= len(result) <= 1000000
                or result.dtype.kind not in ('iu' if indices else 'fiu')
                or not np.isfinite(result).all()):
            raise UnsupportedEffect('Invalid captured model '+name)
        return result if indices else result.astype(np.float64)

    if (not isinstance(geometry, dict) or geometry.get('static') is not True
            or len(geometry.get('morphs', [])) or geometry.get('animation_name') is not None):
        raise UnsupportedEffect('Captured model frame requires static geometry')
    if type(unit_scale) not in (int, float) or not math.isfinite(unit_scale) or unit_scale <= 0:
        raise UnsupportedEffect('Captured model frame requires positive scene units')
    positions = array(geometry.get('positions'), 3, 'positions')
    normals = array(geometry.get('normals'), 3, 'normals')
    uv = array(geometry.get('uv'), 2, 'UVs')
    indices = array(geometry.get('indices'), 3, 'indices', indices=True)
    affine = array(matrix, 4, 'affine transform')
    if (affine.shape != (3, 4) or normals.shape != positions.shape or len(uv) != len(positions)
            or np.any(indices < 0) or int(indices.max()) >= len(positions)):
        raise UnsupportedEffect('Captured model frame cardinality differs')
    linear = affine[:, :3]
    try:
        transformed_normals = np.linalg.solve(linear.T, normals.T).T
    except np.linalg.LinAlgError as exc:
        raise UnsupportedEffect('Captured model frame has singular normal transform') from exc
    lengths = np.linalg.norm(transformed_normals, axis=1)
    vertices = (positions @ linear.T + affine[:, 3]) * unit_scale
    if (not np.isfinite(vertices).all() or not np.isfinite(transformed_normals).all()
            or not np.isfinite(lengths).all() or np.any(lengths == 0)):
        raise UnsupportedEffect('Captured model frame has invalid transformed geometry')
    return dict(vertices=vertices, normals=transformed_normals / lengths[:, None],
                uv=np.column_stack((uv[:, 0], 1 - uv[:, 1])), indices=indices.copy())
