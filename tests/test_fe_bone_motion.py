import copy
import numpy as np
import pytest

from agr.fe_bone_motion import simulate_node_motion, validate_motion_physics, node_basis


def physics(gravity=0.):
    return {'m_pFeModel': {
        'm_nNodeCount': 2, 'm_nStaticNodes': 1, 'm_CtrlName': ['anchor', 'cloth'],
        'm_InitPose': [[0,0,0,1,0,0,0,1], [1,0,0,1,0,0,0,1]],
        'm_NodeInvMasses': [0,1], 'm_NodeIntegrator': [
            dict(flPointDamping=0., flAnimationForceAttraction=0.,
                 flAnimationVertexAttraction=0., flGravity=gravity) for _ in range(2)],
        'm_Rods': [],'m_TreeCollisionMasks':[65535]}}


def targets(frames):
    result = np.broadcast_to(np.eye(4), (frames,2,4,4)).copy()
    result[:,1,0,3] = 1
    return result


def test_gravity_clock_matches_across_power_of_two_output_rates():
    final=[]
    for fps in (32,64,128):
        values, report = simulate_node_motion(physics(10.), targets(fps+1), fps, 1.)
        np.testing.assert_array_equal(values[:,0], np.zeros((fps+1,3)))
        final.append(values[-1,1])
        assert abs(values[-1,1,2]+5.) < .025
        assert report['source_solver_parity'] is False
    np.testing.assert_allclose(final, np.broadcast_to(final[0],(3,3)), atol=1e-12)


def test_explicit_shot_times_use_replay_elapsed_time_instead_of_encoded_fps():
    # Every interval is an integer number of 1/256 steps. Sampling the same
    # free fall irregularly must agree with those exact uniform-time samples.
    times=np.array([0.,1/256,5/256,8/256])
    source=physics(10.)
    irregular,report=simulate_node_motion(source,targets(4),30,1.,sample_times=times+100)
    uniform,_=simulate_node_motion(source,targets(9),30,1.,sample_times=np.arange(9)/256)
    np.testing.assert_array_equal(irregular,uniform[[0,1,5,8]])
    duration=times[-1]
    assert irregular[-1,1,2]==pytest.approx(-10*(duration**2+duration/256)/2,abs=1e-15)
    encoded,_=simulate_node_motion(source,targets(4),30,1.)
    assert encoded[-1,1,2] < irregular[-1,1,2]*5
    assert report['sample_clock']=='explicit_seconds'
    assert report['sample_time_span_seconds']==duration
    assert report['source_solver_parity'] is False


def test_explicit_times_preserve_moving_pin_and_rod_without_changing_input():
    source=physics()
    source['m_pFeModel']['m_Rods']=[dict(nNode=[0,1],flMinDist=1.,flMaxDist=1.,
                                         flWeight0=0.,flRelaxationFactor=1.)]
    goals=targets(4);goals[:,0,0,3]=[0,.1,.3,.4]
    original=goals.copy()
    values,_=simulate_node_motion(source,goals,30,1.,sample_times=[0,1/128,1/32,1/16])
    np.testing.assert_array_equal(goals,original)
    np.testing.assert_array_equal(values[:,0],goals[:,0,:3,3])
    np.testing.assert_allclose(np.linalg.norm(values[:,1]-values[:,0],axis=1),1.,atol=1e-12)


@pytest.mark.parametrize('times',[[0,0,1/30],[0,.1,.05],[0,.1,float('nan')],
                                  [0,.1,float('inf')],[0,.1,1.2],[0,.1],[[0],[.1],[.2]]])
def test_invalid_or_discontinuous_shot_clock_refused(times):
    goals=targets(3);original=goals.copy()
    with pytest.raises(ValueError,match='sample times'):
        simulate_node_motion(physics(),goals,30,1.,sample_times=times)
    np.testing.assert_array_equal(goals,original)


def test_uniform_explicit_clock_matches_default_and_single_sample_is_valid():
    source=physics(10.)
    original,_=simulate_node_motion(source,targets(5),64,1.)
    explicit,_=simulate_node_motion(source,targets(5),64,1.,sample_times=np.arange(5)/64)
    np.testing.assert_array_equal(original,explicit)
    still,report=simulate_node_motion(source,targets(1),30,1.,sample_times=[123.])
    np.testing.assert_array_equal(still,targets(1)[:,:,:3,3])
    assert report['sample_time_span_seconds']==0


def test_authored_rod_tracks_moving_static_anchor_without_moving_it():
    source=physics()
    source['m_pFeModel']['m_Rods']=[dict(nNode=[0,1],flMinDist=1.,flMaxDist=1.,
                                         flWeight0=0.,flRelaxationFactor=1.)]
    goals=targets(33);goals[:,0,0,3]=np.linspace(0,2,33)
    values, report=simulate_node_motion(source,goals,64,1.)
    np.testing.assert_array_equal(values[:,0],goals[:,0,:3,3])
    np.testing.assert_allclose(np.linalg.norm(values[:,1]-values[:,0],axis=1),1.,atol=1e-12)
    assert report['maximum_rod_residual_m'] < 1e-12


def test_compiled_animated_rods_use_changing_goal_length_and_preserve_static_anchor():
    source=physics();fe=source['m_pFeModel']
    fe['m_SimdRodsAnim']=[dict(nNode=[[0]*4,[1]*4],f4Weight0=[0.]*4,f4RelaxationFactor=[1.]*4)]
    goals=targets(3);goals[:,1,0,3]=[1,2,4]
    values,report=simulate_node_motion(source,goals,64,1.)
    np.testing.assert_allclose(values[:,1,0],[1,2,4],atol=1e-12)
    np.testing.assert_array_equal(values[:,0],goals[:,0,:3,3])
    assert report['native_qualified_animated_rod_blocks']==1


def triangle_physics():
    source=physics(10.);fe=source['m_pFeModel'];fe.update(m_nNodeCount=3,m_nStaticNodes=2,
        m_CtrlName=['a','b','cloth'],m_InitPose=[[0,0,0,1,0,0,0,1],[2,0,0,1,0,0,0,1],[1,1,0,1,0,0,0,1]],
        m_NodeInvMasses=[0,0,1],m_NodeIntegrator=[dict(fe['m_NodeIntegrator'][0]) for _ in range(3)],
        m_Tris=[dict(nNode=[0,1,2],w1=0.,w2=1.,v1x=2.,v2=[1.,1.])],
        m_SimdTris=[dict(nNode=[[0]*4,[1]*4,[2]*4],w1=[0.]*4,w2=[1.]*4,v1x=[2.]*4,
                         v2=dict(x=[1.]*4,y=[1.]*4))],
        m_nTriCount1=1,m_nTriCount2=1,m_nSimdTriCount1=1,m_nSimdTriCount2=1)
    return source


def test_two_pin_triangles_preserve_both_anchors_and_authored_cross_section_during_motion():
    source=triangle_physics();goals=np.broadcast_to(np.eye(4),(4,3,4,4)).copy()
    goals[:,:,0,3]=[0,2,1];goals[:,2,1,3]=1
    values,report=simulate_node_motion(source,goals,64,1.)
    np.testing.assert_array_equal(values[:,:2],goals[:,:2,:3,3])
    np.testing.assert_allclose(values[:,2,0],1.,atol=1e-12)
    np.testing.assert_allclose(np.linalg.norm(values[:,2,1:],axis=1),1.,atol=1e-12)
    assert values[-1,2,2]<0 and report['native_qualified_two_pin_triangle_blocks']==1
    source['m_pFeModel']['m_nTriCount2']=0
    with pytest.raises(ValueError,match='triangle'):
        validate_motion_physics(source)


def test_triangle_inventory_cannot_drop_declarations_or_reclassify_dynamic_nodes_as_pins():
    source=triangle_physics();source['m_pFeModel']['m_Tris'][0]['v2'][0]=1.5
    with pytest.raises(ValueError,match='inventory'):
        validate_motion_physics(source)
    source=triangle_physics();source['m_pFeModel']['m_NodeInvMasses'][1]=1
    source['m_pFeModel']['m_nStaticNodes']=1
    with pytest.raises(ValueError,match='two pinned'):
        validate_motion_physics(source)


@pytest.mark.parametrize('pins,w1,w2,masses',[(1,.25,.75,[0,1,2]),(0,.2,.3,[1,2,3])])
def test_mass_weighted_triangles_run_in_motion_clock_and_keep_rigid_shape(pins,w1,w2,masses):
    source=triangle_physics();fe=source['m_pFeModel'];fe['m_nStaticNodes']=pins
    fe['m_NodeInvMasses']=masses;fe['m_nTriCount1']=int(pins==1);fe['m_nTriCount2']=0
    fe['m_nSimdTriCount1']=int(pins==1);fe['m_nSimdTriCount2']=0
    fe['m_Tris'][0].update(w1=w1,w2=w2);fe['m_SimdTris'][0].update(w1=[w1]*4,w2=[w2]*4)
    goals=np.broadcast_to(np.eye(4),(9,3,4,4)).copy()
    goals[:,:,0,3]=[0,2,1];goals[:,2,1,3]=1
    values,report=simulate_node_motion(source,goals,64,1.)
    np.testing.assert_allclose(np.linalg.norm(values[:,1]-values[:,0],axis=1),2.,atol=1e-10)
    np.testing.assert_allclose(np.linalg.norm(values[:,2]-values[:,0],axis=1),np.sqrt(2),atol=1e-10)
    np.testing.assert_allclose(np.linalg.norm(values[:,2]-values[:,1],axis=1),np.sqrt(2),atol=1e-10)
    if pins:np.testing.assert_array_equal(values[:,0],goals[:,0,:3,3])
    else:assert values[-1,0,2]<0
    assert values[-1,2,2]<0
    assert report['native_qualified_one_pin_triangle_blocks']==int(pins==1)
    assert report['native_qualified_free_triangle_blocks']==int(pins==0)
    assert report['native_qualified_two_pin_triangle_blocks']==0


def test_kelager_bend_limits_centroid_separation_without_moving_anchor():
    source=triangle_physics();fe=source['m_pFeModel']
    for key in ['m_Tris','m_SimdTris']:fe[key]=[]
    fe['m_NodeInvMasses']=[0,1,1];fe['m_nStaticNodes']=1
    fe['m_KelagerBends']=[dict(nNode=[0,1,2],flWeight=[0,1.5,1.5],flHeight0=.5,nReserved=0)]
    goals=np.broadcast_to(np.eye(4),(33,3,4,4)).copy()
    goals[:,1,1,3]=1;goals[:,2,1,3]=-1
    values,report=simulate_node_motion(source,goals,64,1.)
    np.testing.assert_array_equal(values[:,0],np.zeros((33,3)))
    height=np.linalg.norm(values[:,0]-values.mean(axis=1),axis=1)
    assert height[-1]>.49 and height.max()<.500001
    assert report['native_qualified_kelager_bend_count']==1
    fe['m_KelagerBends'][0]['flWeight'][0]=-.5
    with pytest.raises(ValueError,match='zero-mass'):validate_motion_physics(source)


def test_triangle_prefix_validation_cannot_omit_or_reclassify_free_lanes():
    source=triangle_physics();fe=source['m_pFeModel'];fe['m_nStaticNodes']=0
    fe['m_NodeInvMasses']=[1,1,1];fe['m_nTriCount1']=fe['m_nTriCount2']=0
    fe['m_nSimdTriCount1']=fe['m_nSimdTriCount2']=0
    fe['m_Tris'][0].update(w1=.2,w2=.3);fe['m_SimdTris'][0].update(w1=[.2]*4,w2=[.3]*4)
    validate_motion_physics(source)
    fe['m_nTriCount1']=1
    with pytest.raises(ValueError,match='One pinned'):validate_motion_physics(source)
    fe['m_nTriCount1']=0;fe['m_SimdTris'][0]['v2']['x'][3]=1.01
    with pytest.raises(ValueError,match='inventory'):validate_motion_physics(source)


def test_authored_collision_layers_are_respected():
    source=physics();fe=source['m_pFeModel']
    fe['m_SphereRigids']=[dict(nNode=0,vSphere=[0,0,0,1.],nFlags=0,
                              nVertexMapIndex=65535,nCollisionMask=1)]
    goals=targets(2);goals[:,1,0,3]=.1
    fe['m_TreeCollisionMasks']=[2]
    ignored,_=simulate_node_motion(source,goals,64,1.)
    assert ignored[-1,1,0]==pytest.approx(.1)
    fe['m_TreeCollisionMasks']=[1]
    collided,_=simulate_node_motion(source,goals,64,1.)
    assert collided[-1,1,0]>=1.


def test_authored_node_radius_inflates_body_sphere_and_leaves_static_anchor_fixed():
    source=physics();fe=source['m_pFeModel']
    fe['m_SphereRigids']=[dict(nNode=0,vSphere=[0,0,0,1.],nFlags=0,
                              nVertexMapIndex=65535,nCollisionMask=1)]
    fe['m_NodeCollisionRadii']=[10.]
    goals=targets(2);goals[:,1,0,3]=1.5
    values,_=simulate_node_motion(source,goals,64,1.)
    assert values[-1,1,0]>=11.
    np.testing.assert_array_equal(values[:,0],goals[:,0,:3,3])
    # A point already outside the inflated shape must not be pulled inward.
    goals[:,1,0,3]=12.
    values,report=simulate_node_motion(source,goals,64,1.)
    assert values[-1,1,0]==pytest.approx(12.)
    assert report['native_qualified_outward_sphere_contact_count']==1
    assert report['source_solver_parity'] is False


def test_sphere_friction_uses_collider_motion_and_requires_complete_dynamic_coefficients():
    source=physics();fe=source['m_pFeModel']
    fe['m_SphereRigids']=[dict(nNode=0,vSphere=[0,0,0,1.],nFlags=0,
                              nVertexMapIndex=65535,nCollisionMask=1)]
    fe['m_DynNodeFriction']=[0.]
    fe['m_NodeIntegrator'][1]['flAnimationVertexAttraction']=1.
    goals=targets(3);goals[:,0,0,3]=[0,.2,.4];goals[:,0,1,3]=[0,.2,.4];goals[:,1,0,3]=.8
    baseline,_=simulate_node_motion(source,goals,64,1.)
    fe['m_DynNodeFriction']=[1.]
    frictional,_=simulate_node_motion(source,goals,64,1.)
    assert frictional[-1,1,1]>baseline[-1,1,1]+.01
    np.testing.assert_array_equal(frictional[:,0],goals[:,0,:3,3])
    fe['m_DynNodeFriction']=[1.,1.]
    with pytest.raises(ValueError,match='dynamic friction'):
        simulate_node_motion(source,goals,64,1.)


def test_sphere_goals_use_native_scale_one_gather_without_requiring_control_offsets():
    source=physics();fe=source['m_pFeModel']
    fe['m_SphereRigids']=[dict(nNode=0,vSphere=[0,0,0,1.],nFlags=0,
                              nVertexMapIndex=65535,nCollisionMask=1)]
    goals=targets(3);goals[:,1,0,3]=.2
    baseline,_=simulate_node_motion(source,goals,64,1.)
    for raw_scale in (.01,2):
        scaled=goals.copy();scaled[:,:,:3,:3]*=raw_scale
        actual,_=simulate_node_motion(source,scaled,64,1.)
        np.testing.assert_array_equal(actual,baseline)
    goals[:,0,0,0]=2
    with pytest.raises(ValueError,match='uniform control scale'):
        simulate_node_motion(source,goals,64,1.)


def test_named_capsule_map_overrides_masks_while_excluding_static_and_zero_map_bytes():
    source=physics();fe=source['m_pFeModel']
    fe['m_TaperedCapsuleRigids']=[dict(nNode=0,vSphere=[[0,0,0,1.],[0,0,4,3]],nFlags=0,
        nVertexMapIndex=0,nCollisionMask=0)]
    fe['m_TreeCollisionMasks']=[0];fe['m_VertexMapValues']=[255,128]
    fe['m_VertexMaps']=[dict(nVertexBase=0,nVertexCount=2,nMapOffset=0,nNodeListOffset=0,nNodeListCount=2)]
    goals=targets(2);goals[:,1,0,3]=.2
    collided,report=simulate_node_motion(source,goals,64,1.)
    assert collided[-1,1,0]>=1
    np.testing.assert_array_equal(collided[:,0],goals[:,0,:3,3])
    assert report['native_qualified_outward_capsule_contact_count']==1
    fe['m_VertexMapValues'][1]=0;fe['m_VertexMaps'][0]['nNodeListCount']=1
    excluded,_=simulate_node_motion(source,goals,64,1.)
    assert excluded[-1,1,0]==pytest.approx(.2)
    fe['m_VertexMaps'][0]['nNodeListCount']=2
    with pytest.raises(ValueError,match='compiled inventory'):validate_motion_physics(source)


def test_missing_compiled_collision_masks_cannot_be_replaced_with_all_nodes():
    source=physics();fe=source['m_pFeModel']
    fe['m_SphereRigids']=[dict(nNode=0,vSphere=[0,0,0,1.],nFlags=0,
        nVertexMapIndex=65535,nCollisionMask=1)]
    fe.pop('m_TreeCollisionMasks')
    with pytest.raises(ValueError,match='Complete compiled'):validate_motion_physics(source)


def test_static_parent_plane_follows_recorded_goal_translation_and_rotation():
    source=physics(10.);fe=source['m_pFeModel']
    fe['m_CollisionPlanes']=[dict(nCtrlParent=0,nChildNode=1,
        m_Plane=dict(m_vNormal=[1.,0,0],m_flOffset=.5),flStrength=1.)]
    goals=targets(5);goals[:,0,:3,:3]=[[0,-1,0],[1,0,0],[0,0,1]]
    goals[:,0,1,3]=np.linspace(0,1,5);goals[:,1,1,3]=.5
    values,report=simulate_node_motion(source,goals,64,1.)
    np.testing.assert_array_equal(values[:,0],goals[:,0,:3,3])
    np.testing.assert_allclose(values[1:,1,1],goals[1:,0,1,3]+.5,atol=1e-12)
    assert report['native_qualified_static_parent_collision_planes']==1


@pytest.mark.parametrize('kind',['dynamic_parent','driven_child','scaled_parent'])
def test_plane_phase_and_scale_are_not_inferred_for_unqualified_layouts(kind):
    source=physics();fe=source['m_pFeModel'];goals=targets(2)
    fe['m_CollisionPlanes']=[dict(nCtrlParent=0,nChildNode=1,
        m_Plane=dict(m_vNormal=[0.,0,1.],m_flOffset=0.),flStrength=1.)]
    if kind=='dynamic_parent':fe['m_CollisionPlanes'][0]['nCtrlParent']=1
    elif kind=='driven_child':fe['m_nFirstPositionDrivenNode']=1
    else:goals[:,0,:3,:3]*=2
    with pytest.raises(ValueError,match='static parents|position-driven child|unit instance'):
        simulate_node_motion(source,goals,64,1.)


def test_earlier_reverse_offset_does_not_pin_a_simulated_chain_node():
    source=physics(10.);fe=source['m_pFeModel']
    fe['m_nNodeCount']=3;fe['m_CtrlName'].append('tail')
    fe['m_InitPose'].append([2,0,0,1,0,0,0,1]);fe['m_NodeInvMasses'].append(1)
    fe['m_NodeIntegrator'].append(dict(fe['m_NodeIntegrator'][1]))
    fe['m_nFirstPositionDrivenNode']=3
    fe['m_ReverseOffsets']=[dict(nBoneCtrl=1,nTargetNode=2,vOffset=[1,0,0])]
    goals=np.broadcast_to(np.eye(4),(3,3,4,4)).copy()
    values,_=simulate_node_motion(source,goals,64,1.)
    assert values[-1,1,2] < 0 and values[-1,2,2] < 0
    fe['m_nFirstPositionDrivenNode']=2
    values,_=simulate_node_motion(source,goals,64,1.)
    assert values[-1,1,2] < 0 and values[-1,2,2] == 0


@pytest.mark.parametrize('key', ['m_Prisms','m_WorldCollisionNodes'])
def test_required_unknown_constraints_are_never_silently_discarded(key):
    source=physics();source['m_pFeModel'][key]=[{}]
    with pytest.raises(ValueError,match='additional authored operations'):
        validate_motion_physics(source)


def quad_physics():
    source=physics(10.);fe=source['m_pFeModel']
    shape=[[-1.,0,0,0],[1.,0,0,0],[0,1.,0,.4],[0,2.,0,.6]]
    fe.update(m_nNodeCount=4,m_nStaticNodes=2,m_CtrlName=['a','b','c','d'],
        m_InitPose=[[x,y,z,1,0,0,0,1] for x,y,z,w in shape],m_NodeInvMasses=[0,0,1,1],
        m_NodeIntegrator=[dict(fe['m_NodeIntegrator'][0]) for _ in range(4)],
        m_Quads=[dict(nNode=[0,1,2,3],flSlack=0.,vShape=shape)],
        m_SimdQuads=[dict(nNode=[[i]*4 for i in range(4)],f4Slack=[0.]*4,
            vShape=[[v for coordinate in row[:3] for v in [coordinate]*4] for row in shape],
            f4Weights=[[row[3]]*4 for row in shape])],
        m_nQuadCount1=1,m_nQuadCount2=1,m_nSimdQuadCount1=1,m_nSimdQuadCount2=1)
    return source


def test_two_pin_quads_preserve_anchors_and_authored_shape_during_motion():
    source=quad_physics();goals=np.broadcast_to(np.eye(4),(4,4,4,4)).copy()
    goals[:,:,:3,3]=np.asarray(source['m_pFeModel']['m_InitPose'])[:,:3]
    values,report=simulate_node_motion(source,goals,64,1.)
    np.testing.assert_array_equal(values[:,:2],goals[:,:2,:3,3])
    np.testing.assert_allclose(np.linalg.norm(values[:,2,1:],axis=1),1.,atol=1e-12)
    np.testing.assert_allclose(np.linalg.norm(values[:,3,1:],axis=1),2.,atol=1e-12)
    assert values[-1,2,2]<0 and report['native_qualified_two_pin_quad_blocks']==1


def test_quad_inventory_and_pinned_mass_layout_cannot_be_reclassified():
    source=quad_physics();source['m_pFeModel']['m_Quads'][0]['vShape'][2][1]=1.5
    with pytest.raises(ValueError,match='inventory'):
        validate_motion_physics(source)
    source=quad_physics();source['m_pFeModel']['m_NodeInvMasses'][1]=1
    source['m_pFeModel']['m_nStaticNodes']=1
    with pytest.raises(ValueError,match='two pinned'):
        validate_motion_physics(source)
    source=quad_physics();source['m_pFeModel']['m_nQuadCount2']=0
    with pytest.raises(ValueError,match='one-pinned'):
        validate_motion_physics(source)


def test_mixed_two_pin_and_free_quads_keep_pins_and_project_both_prefixes():
    from agr.fe_quad_constraints import _basis
    source=quad_physics();fe=source['m_pFeModel']
    square=np.array([[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0]],float)
    shape=np.c_[square@_basis(square[2]-square[0],square[3]-square[1]).T,[.25]*4]
    free=dict(nNode=[4,5,6,7],flSlack=0.,vShape=shape.tolist())
    fe['m_Quads'].append(free)
    fe['m_SimdQuads'].append(dict(nNode=[[i]*4 for i in range(4,8)],f4Slack=[0.]*4,
        vShape=[[value for coordinate in row[:3] for value in [coordinate]*4] for row in shape],
        f4Weights=[[row[3]]*4 for row in shape]))
    fe['m_nNodeCount']=8;fe['m_CtrlName']+=['e','f','g','h'];fe['m_NodeInvMasses']+=[1]*4
    fe['m_InitPose']+=[[*point,1,0,0,0,1] for point in square+[10,0,0]]
    fe['m_NodeIntegrator']+=[dict(fe['m_NodeIntegrator'][0]) for _ in range(4)]
    goals=np.broadcast_to(np.eye(4),(5,8,4,4)).copy();goals[:,:,:3,3]=np.asarray(fe['m_InitPose'])[:,:3]
    values,report=simulate_node_motion(source,goals,64,1.)
    np.testing.assert_array_equal(values[:,:2],goals[:,:2,:3,3])
    np.testing.assert_allclose(np.linalg.norm(values[:,5]-values[:,4],axis=1),2,atol=1e-10)
    np.testing.assert_allclose(np.linalg.norm(values[:,6]-values[:,4],axis=1),np.sqrt(8),atol=1e-10)
    assert values[-1,4:,2].mean()<0
    assert report['native_qualified_quad_blocks']==2
    assert report['native_qualified_two_pin_quad_blocks']==1
    assert report['native_qualified_free_quad_blocks']==1
    fe['m_SimdQuads'][1]['vShape'][0][0]+=.01
    with pytest.raises(ValueError,match='inventory'):validate_motion_physics(source)


def test_native_qualified_twists_require_valid_ordered_control_identities():
    source=physics();source['m_pFeModel']['m_Twists']=[dict(nNodeOrient=1,nNodeEnd=0,flTwistRelax=.618,flSwingRelax=.5)]
    assert validate_motion_physics(source) is source['m_pFeModel']
    source['m_pFeModel']['m_Twists'][0]['nNodeEnd']=9
    with pytest.raises(ValueError,match='index outside'):
        validate_motion_physics(source)


def test_goal_locks_validate_authored_control_domain_before_bake():
    source=physics();source['m_pFeModel']['m_LockToGoal']=[1,1]
    assert validate_motion_physics(source) is source['m_pFeModel']
    source['m_pFeModel']['m_LockToGoal']=[2]
    with pytest.raises(ValueError,match='goal-lock control outside'):
        validate_motion_physics(source)


def test_node_indices_and_degenerate_rotation_frames_remain_strict():
    source=physics()
    source['m_pFeModel']['m_Rods']=[dict(nNode=[0,2],flMinDist=1.,flMaxDist=1.,flWeight0=0.,flRelaxationFactor=1.)]
    with pytest.raises(ValueError,match='outside control nodes'):
        validate_motion_physics(source)
    with pytest.raises(ValueError,match='Degenerate'):
        node_basis(np.zeros((3,3)),dict(nNodeX0=0,nNodeX1=1,nNodeY0=1,nNodeY1=2))
