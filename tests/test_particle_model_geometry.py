import copy
import json
import struct
import numpy as np
import pytest
from agr.particle_model_geometry import read_model_geometry,morph_weights,morph_keyframes,transform_static_model_frame
from agr.particle_model_math import model_matrix
from agr.installed_effects import UnsupportedEffect


def fixture():
    binary=bytearray();views=[];accessors=[]
    def data(values,dtype,width,kind):
        array=np.asarray(values,dtype=dtype).reshape(-1,width);start=len(binary)
        binary.extend(array.tobytes());binary.extend(b'\0'*((-len(binary))%4))
        views.append(dict(buffer=0,byteOffset=start,byteLength=array.nbytes))
        accessors.append(dict(bufferView=len(views)-1,componentType=kind,count=len(array),type={1:'SCALAR',2:'VEC2',3:'VEC3',4:'VEC4',16:'MAT4'}[width]))
        return len(accessors)-1
    pos=data([[0,0,0],[.0254,0,0],[0,.0254,0]],'<f4',3,5126)
    uv=data([[0,0],[1,0],[0,1]],'<f4',2,5126)
    normal=data([[0,0,1]]*3,'<f4',3,5126)
    ids=data([0,1,2],'<u4',1,5125)
    morph=data([[0,0,0]]*3,'<f4',3,5126)
    sparse_index=data([1],'<u4',1,5125);sparse_value=data([[0,0,.0254]],'<f4',3,5126)
    accessors[morph]['sparse']=dict(count=1,indices=dict(bufferView=accessors[sparse_index]['bufferView'],componentType=5125),values=dict(bufferView=accessors[sparse_value]['bufferView']))
    times=data([0,1],'<f4',1,5126);weights=data([0,1],'<f4',1,5126)
    doc=dict(asset=dict(version='2.0'),buffers=[dict(byteLength=len(binary))],bufferViews=views,accessors=accessors,
        nodes=[dict(mesh=0)],scenes=[dict(nodes=[0])],meshes=[dict(primitives=[dict(attributes=dict(POSITION=pos,NORMAL=normal,TEXCOORD_0=uv),indices=ids,targets=[dict(POSITION=morph)])])],
        animations=[dict(name='flap',channels=[dict(sampler=0,target=dict(node=0,path='weights'))],samplers=[dict(input=times,output=weights)])])
    return doc,binary


def write(path,doc,binary):
    text=json.dumps(doc).encode();text+=b' '*((-len(text))%4)
    raw=struct.pack('<III',0x46546c67,2,28+len(text)+len(binary))+struct.pack('<II',len(text),0x4e4f534a)+text+struct.pack('<II',len(binary),0x004e4942)+binary
    path.write_bytes(raw);return path


def test_sparse_morph_source_axes_and_loop_interpolation(tmp_path):
    d,b=fixture();g=read_model_geometry(write(tmp_path/'model.glb',d,b))
    np.testing.assert_allclose(g['positions'][1],[0,1,0],atol=1e-6)
    np.testing.assert_allclose(g['morphs'][0][1],[1,0,0],atol=1e-6)
    np.testing.assert_allclose(g['morphs'][0][[0,2]],0)
    np.testing.assert_allclose(morph_weights(g,.25,60,30),[.5])
    np.testing.assert_allclose(morph_weights(g,.75,60,30),[.5])
    np.testing.assert_allclose(morph_weights(g,.75,60,30,loop=False),[1])


@pytest.mark.parametrize('mutate',[
    lambda d:d['buffers'][0].update(uri='outside.bin'),
    lambda d:d['bufferViews'][0].update(byteLength=1),
    lambda d:d['accessors'][0].update(count=True),
    lambda d:d['accessors'][0].update(normalized=True),
    lambda d:d['accessors'][4]['sparse']['indices'].update(componentType=5126),
    lambda d:d['nodes'][0].update(translation=[1,0,0]),
    lambda d:d['nodes'][0].update(skin=0),
    lambda d:d['meshes'][0]['primitives'].append(copy.deepcopy(d['meshes'][0]['primitives'][0])),
    lambda d:d['meshes'][0]['primitives'][0].update(mode=5),
    lambda d:d['animations'][0]['channels'][0]['target'].update(path='translation'),
    lambda d:d['animations'][0]['samplers'][0].update(interpolation='CUBICSPLINE'),
    lambda d:d.update(extensionsRequired=['KHR_draco_mesh_compression']),
])
def test_unrepresented_or_out_of_bounds_geometry_is_rejected(tmp_path,mutate):
    d,b=fixture();mutate(d)
    with pytest.raises(UnsupportedEffect):read_model_geometry(write(tmp_path/'model.glb',d,b))


def test_native_model_frame_uses_projected_perpendicular():
    p=dict(normal=[1,0,0],rotation=[0,0,0],radius=2,position=[5,7,9])
    np.testing.assert_allclose(model_matrix(p,{}),[[2,0,0,5],[0,0,2,7],[0,-2,0,9],[0,0,0,1]])
    np.testing.assert_allclose(model_matrix(p,dict(m_bOrientZ=True))[:3,:3],[[0,0,2],[2,0,0],[0,2,0]])
    np.testing.assert_allclose(model_matrix(p,dict(m_bIgnoreNormal=True,m_bIgnoreRadius=True))[:3,:3],np.eye(3))


@pytest.mark.parametrize('field,value',[('normal',[0,0,1e30]),('radius',-1),('radius',True),('position',[float('nan'),0,0]),('rotation',[0,0])])
def test_invalid_model_transform_rejected(field,value):
    p=dict(normal=[1,0,0],rotation=[0,0,0],radius=1,position=[0,0,0]);p[field]=value
    with pytest.raises(UnsupportedEffect):model_matrix(p,{})


def test_model_normal_magnitude_does_not_scale_the_mesh():
    p=dict(normal=[.5,-2,3],rotation=[.3,.2,-.1],radius=.7,position=[3,4,5])
    normalized=dict(p,normal=(np.array(p['normal'])/np.linalg.norm(p['normal'])).tolist())
    np.testing.assert_allclose(model_matrix(p,dict(m_bOrientZ=True)),model_matrix(normalized,dict(m_bOrientZ=True)),atol=1e-12)


def static_fixture(skinned=True):
    d,b=fixture();d.pop('animations');d['meshes'][0]['primitives'][0].pop('targets')
    if not skinned:return d,b
    def append(values,dtype,kind,type_name):
        array=np.asarray(values,dtype=dtype);start=len(b);b.extend(array.tobytes());b.extend(b'\0'*((-len(b))%4))
        d['bufferViews'].append(dict(buffer=0,byteOffset=start,byteLength=array.nbytes))
        d['accessors'].append(dict(bufferView=len(d['bufferViews'])-1,componentType=kind,count=len(array),type=type_name))
        return len(d['accessors'])-1
    bind=append([np.eye(4).ravel()],'<f4',5126,'MAT4')
    attrs=d['meshes'][0]['primitives'][0]['attributes']
    attrs['JOINTS_0']=append([[0,0,0,0]]*3,'<u2',5123,'VEC4')
    attrs['WEIGHTS_0']=append([[1,0,0,0]]*3,'<f4',5126,'VEC4')
    d['buffers'][0]['byteLength']=len(b)
    d['skins']=[dict(joints=[1],inverseBindMatrices=bind)]
    d['nodes']=[dict(children=[1]),{},dict(mesh=0,skin=0,translation=[0,0,0],rotation=[0,0,0,1],scale=[1,1,1])]
    d['scenes']=[dict(nodes=[0,2])]
    return d,b


@pytest.mark.parametrize('skinned',[False,True])
def test_static_geometry_keeps_original_vertices_uvs_and_normals(tmp_path,skinned):
    d,b=static_fixture(skinned);g=read_model_geometry(write(tmp_path/'static.glb',d,b))
    assert g['static'] and g['morphs']==[] and g['weights'].shape==(0,0)
    np.testing.assert_allclose(g['positions'],[[0,0,0],[0,1,0],[0,0,1]],atol=1e-6)
    np.testing.assert_allclose(g['normals'],[[1,0,0]]*3)
    np.testing.assert_allclose(g['uv'],[[0,0],[1,0],[0,1]])


@pytest.mark.parametrize('change',[
    lambda d:d['nodes'][1].update(translation=[1,0,0]),
    lambda d:d['nodes'][0].update(scale=[1,2,1]),
    lambda d:d['nodes'][2].update(rotation=[0,0,1,0]),
    lambda d:d['nodes'][1].update(children=[2]),
    lambda d:d['skins'][0].update(joints=[0,1]),
    lambda d:d.update(animations=[{}]),
    lambda d:d['accessors'][d['skins'][0]['inverseBindMatrices']].update(count=2),
])
def test_static_skin_never_discards_motion_or_nonidentity_rest(tmp_path,change):
    d,b=static_fixture();change(d)
    with pytest.raises(UnsupportedEffect):read_model_geometry(write(tmp_path/'bad.glb',d,b))


@pytest.mark.parametrize('field,value',[('JOINTS_0',1),('WEIGHTS_0',.5),('bind',2)])
def test_nonidentity_bind_and_nonroot_weights_are_refused(tmp_path,field,value):
    d,b=static_fixture();attrs=d['meshes'][0]['primitives'][0]['attributes']
    index=d['skins'][0]['inverseBindMatrices'] if field=='bind' else attrs[field]
    accessor=d['accessors'][index];offset=d['bufferViews'][accessor['bufferView']]['byteOffset']
    struct.pack_into('<H' if field=='JOINTS_0' else '<f',b,offset,value)
    with pytest.raises(UnsupportedEffect):read_model_geometry(write(tmp_path/'bad.glb',d,b))


def test_captured_affine_frame_preserves_source_axes_shear_and_normals(tmp_path):
    d,b=static_fixture(False);geometry=read_model_geometry(write(tmp_path/'source.glb',d,b))
    matrix=np.array([[2.,.75,-.25,100.],[.1,3.,.5,-200.],[.2,.4,4.,300.]])
    frame=transform_static_model_frame(geometry,matrix,.0254)
    # GLB's X/Y axes became Source Y/Z. This also catches a second inch/metre
    # conversion or an unnecessary model-basis rotation after reading geometry.
    expected=np.array([matrix[:,3],matrix[:,3]+matrix[:,1],matrix[:,3]+matrix[:,2]])*.0254
    np.testing.assert_allclose(frame['vertices'],expected,atol=1e-7,rtol=0)
    for tangent in np.diff(expected,axis=0):
        np.testing.assert_allclose(frame['normals']@tangent,0.,atol=1e-14,rtol=0)
    np.testing.assert_allclose(np.linalg.norm(frame['normals'],axis=1),1.,atol=1e-14)
    np.testing.assert_array_equal(frame['indices'],[[0,1,2]])
    np.testing.assert_allclose(frame['uv'],[[0,1],[1,1],[0,0]])
    # Neither geometry nor its model-local normal/UV streams are changed.
    np.testing.assert_allclose(geometry['positions'],[[0,0,0],[0,1,0],[0,0,1]],atol=1e-6)
    np.testing.assert_array_equal(geometry['uv'],[[0,0],[1,0],[0,1]])


@pytest.mark.parametrize('mutation', ['singular','missing_axis','nonfinite','indices','animated','units'])
def test_captured_frame_refuses_incomplete_or_unrepresentable_inputs(tmp_path,mutation):
    d,b=static_fixture(False);geometry=read_model_geometry(write(tmp_path/'source.glb',d,b))
    matrix=np.eye(4)[:3];scale=.0254
    if mutation=='singular':matrix[1]=0
    elif mutation=='missing_axis':matrix=matrix[:2]
    elif mutation=='nonfinite':matrix[0,0]=float('nan')
    elif mutation=='indices':geometry['indices']=np.array([[0,1,3]])
    elif mutation=='animated':geometry['static']=False
    else:scale=True
    with pytest.raises(UnsupportedEffect):transform_static_model_frame(geometry,matrix,scale)


@pytest.mark.parametrize('fps', [24, 30, 60])
@pytest.mark.parametrize('manual', [False, True])
def test_authored_morph_breakpoints_survive_output_fps(fps, manual):
    # More authored pose changes than replay keys. A replay-tick-only bake
    # flattens these turns; the authored breakpoint schedule must retain them.
    geometry = dict(times=np.array([0., .01, .02]), weights=np.array([[0.], [1.], [0.]]))
    samples = [(tick, 1/64, dict(age=tick/64, manual_frame=tick/64*30)) for tick in range(65)]
    keys = morph_keyframes(geometry, samples, 30, 30, manual=manual)
    frames = np.array([1 + tick * fps / 64 for tick, _ in keys])
    values = np.array([w[0] for _, w in keys])
    for frame in np.linspace(1, 1 + fps, fps * 8 + 1):
        age = float((frame - 1) / fps)
        expected = morph_weights(geometry, age, 30, 30)[0]
        assert np.interp(frame, frames, values) == pytest.approx(expected, abs=1e-11)


def test_morph_schedule_rejects_noncontinuous_loop_and_unbounded_phase():
    geometry = dict(times=np.array([0., 1.]), weights=np.array([[0.], [1.]]))
    samples = [(0, 1/64, dict(age=0.)), (1, 1/64, dict(age=1.))]
    with pytest.raises(UnsupportedEffect, match='matching endpoint'):
        morph_keyframes(geometry, samples, 30, 30)
    geometry['weights'][-1] = 0
    samples[-1][2]['age'] = 1e8
    with pytest.raises(UnsupportedEffect, match='keyframe bound'):
        morph_keyframes(geometry, samples, 30, 30)
