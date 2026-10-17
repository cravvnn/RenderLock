"""Evaluate the verified recipe stream; unknown operations raise an error.

Outputs primary animation-skeleton transforms. It does not simulate particles,
cloth, collision or a Source 2 ragdoll. Stale recipes are explicitly recorded.
"""
import argparse, collections, json, time
from pathlib import Path
import numpy as np
from .pose_math import Pose,normalize,fast_slerp,globals_for,feather_mask,mask_tasks,blend,model_blend,solve_ik,follow_bone


class Evaluator:
    def __init__(self,hero,skeleton,cache):
        self.hero=hero;self.skeleton=skeleton;self.parents=np.asarray(skeleton['m_parentIndices'])
        self.names=skeleton['m_boneIDs'];self.cache=Path(cache);self.clips={}
        reference=np.asarray(skeleton['m_parentSpaceReferencePose'])
        # Compiled skeleton transforms use [translation.xyz, scale, quat.xyzw].
        self.reference=Pose(normalize(reference[:,4:]),reference[:,:3],reference[:,3])
        self.mask_diagnostics=[]
        self.masks=[feather_mask(skeleton,m,self.mask_diagnostics) for m in skeleton['m_maskDefinitions']]
        self.histogram=collections.Counter();self.ik_records=[]
        self.pose_cache={};self.cache_context=None

    def sample(self,resource,phase):
        if resource not in self.clips:
            path=(self.cache/(resource+'.json')).resolve()
            if not path.is_relative_to(self.cache.resolve()):raise ValueError('Invalid clip resource path')
            data=json.loads(path.read_text())
            skeleton_path=(self.cache/(data['m_skeleton']+'.json')).resolve()
            if not skeleton_path.is_relative_to(self.cache.resolve()):raise ValueError('Invalid skeleton resource path')
            sk=json.loads(skeleton_path.read_text())
            assert sk['m_boneIDs']==self.names and sk['m_parentIndices']==list(self.parents),('Retarget required',resource)
            if data.get('m_secondaryAnimations'):raise ValueError('Secondary animation is not implemented')
            if data['m_modelSpaceBoneSamplingIndices']:raise ValueError('Model-space clip sampling not implemented')
            with np.load(self.cache/(resource+'.npz'),allow_pickle=False) as f:
                q,t,s=f['q'],f['t'],f['s']
            if q.shape != (len(q),len(self.names),4) or t.shape != q.shape[:-1]+(3,) or s.shape != q.shape[:-1]:
                raise ValueError('Invalid cached clip array shapes')
            if not len(q) or not all(np.isfinite(a).all() for a in (q,t,s)):
                raise ValueError('Invalid cached clip values')
            self.clips[resource]=(data,q,t,s)
        data,q,t,s=self.clips[resource]
        if not 0<=phase<=1:raise ValueError('Clip phase out of range')
        frame=phase*(len(q)-1);i=int(frame);j=min(i+1,len(q)-1);alpha=frame-i
        return Pose(fast_slerp(q[i],q[j],alpha),t[i]+(t[j]-t[i])*alpha,s[i]+(s[j]-s[i])*alpha,data['m_bIsAdditive'])

    def evaluate(self,row,observer=None):
        context=row.get('context')
        if context!=self.cache_context:
            self.pose_cache.clear();self.cache_context=context
        outputs=[]
        for index,task in enumerate(row['tasks']):
            typ=task['type_id'];deps=task.get('dependencies',[])
            assert all(i<index for i in deps)
            self.histogram[task['task_class']]+=1
            if typ==1:out=self.sample(task['clip_resource'],task['phase_u16']/65535)
            elif typ in [6,7,8,9]:
                a,b=[outputs[i] for i in deps]
                mask=mask_tasks(task.get('masks',[]),self.masks,len(self.names));w=task['weight_u8']/255
                if typ==9:out=model_blend(a,b,w,mask,self.parents)
                else:out=blend(a,b,w,mask,{6:'normal',7:'additive',8:'overlay'}[typ])
            elif typ==0:
                out=outputs[deps[0]].copy()
                self.pose_cache[task['cache_id']]=out.copy()
            elif typ==5:
                if task['cache_id'] not in self.pose_cache:
                    raise ValueError(f"Cached pose {task['cache_id']} has no recorded write in this animation context; extract an earlier range")
                out=self.pose_cache[task['cache_id']].copy()
            elif typ==4:
                if task.get('extension_flag_b'):raise ValueError('Unimplemented IK extension')
                weight=task.get('extension_weight_01',1.)
                mode='effector' if task.get('extension_mode_bit',1) else 'pose'
                out=solve_ik(outputs[deps[0]],self.parents,self.reference,task['effector_bone_index'],task['target_bone_index'],weight,mode)
            elif typ==11:out=follow_bone(outputs[deps[0]],self.parents,task['bone_index'],task['follow_bone_index'],task['mode'])
            elif typ==12:
                if deps:raise ValueError('Zero pose task cannot have dependencies')
                # Installed 6779 zero-pose reset: zero translation and additive
                # scale delta, identity rotation, additive pose state 3.
                q=np.zeros((len(self.names),4));q[:,3]=1
                out=Pose(q,np.zeros((len(self.names),3)),np.zeros(len(self.names)),True)
            else:raise ValueError(f'Unimplemented task {typ}')
            assert np.isfinite(out.q).all() and np.isfinite(out.t).all() and np.isfinite(out.s).all(),(self.hero,row['demo_tick'],index)
            if observer is not None:observer(self,row,index,task,outputs,out)
            outputs.append(out)
        if outputs[-1].additive:raise ValueError('Final pose is an additive layer')
        return outputs[-1]


def main(args):
    rows=json.loads(args.recipes.read_text());args.output.mkdir(parents=True,exist_ok=True)
    summary={'schema_version':1,'scope':'Offline evaluation of recorded skeletal tasks. Engine equivalence is separately tested.','heroes':{},'particles':False,'ragdoll':False}
    for hero in ['Haze','Seven','Bebop','Lash']:
        sk=json.loads((args.skeletons/(hero+'_skeleton.json')).read_text());ev=Evaluator(hero,sk,args.cache)
        rr=[r for r in rows if r['hero_name']==hero];qs=[];ts=[];ss=[];last=None;start=time.monotonic()
        for j,r in enumerate(rr):
            if r['fresh_recipe']:
                last=ev.evaluate(r)
            elif last is None:raise ValueError('Missing initial live pose')
            qs.append(last.q.copy());ts.append(last.t.copy());ss.append(last.s.copy())
            if j%200==0:print(hero,j,'/',len(rr),round(time.monotonic()-start,1),'s',flush=True)
        np.savez_compressed(args.output/(hero+'_poses.npz'),q=np.array(qs),t=np.array(ts),s=np.array(ss),ticks=[r['demo_tick'] for r in rr],fresh=[r['fresh_recipe'] for r in rr])
        report={'frames':len(rr),'fresh':sum(r['fresh_recipe'] for r in rr),'bones':len(ev.names),'clips':len(ev.clips),'operations':dict(ev.histogram),'seconds':time.monotonic()-start,'unimplemented_operations':[]}
        summary['heroes'][hero]=report
        print(hero,'complete',round(report['seconds'],1),'s',flush=True)
    (args.output/'evaluation_summary.json').write_text(json.dumps(summary,indent=2)+'\n')

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--recipes',type=Path,required=True);ap.add_argument('--cache',type=Path,required=True)
    ap.add_argument('--skeletons',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    main(ap.parse_args())
