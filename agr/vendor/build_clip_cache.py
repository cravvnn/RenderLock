"""Decode the selected compiled Nm clips into lossless numeric working arrays.

The compression layout is documented by ValveResourceFormat AnimationClip.cs.
Only local files are read. Source 2 Viewer is used as a KV3 dumper.
"""
import argparse, concurrent.futures, hashlib, json, subprocess,os,threading
from pathlib import Path
import numpy as np
from .kv3_text import parse_kv3


def decode_frames(data):
    configs=data['m_trackCompressionSettings']; n=data['m_nNumFrames']; b=len(configs)
    words=np.frombuffer(bytes(data['m_compressedPoseData']),dtype='<u2')
    q=np.zeros((n,b,4),dtype=np.float64);t=np.zeros((n,b,3));s=np.zeros((n,b))
    offsets=data['m_compressedPoseOffsets'];assert len(offsets)==n
    for f in range(n):
        pos=offsets[f]
        for i,c in enumerate(configs):
            q[f,i]=c['m_constantRotation']
            t[f,i]=[c['m_translationRange'+axis]['m_flRangeStart'] for axis in 'XYZ']
            s[f,i]=c['m_scaleRange']['m_flRangeStart']
            if not c['m_bIsRotationStatic']:
                a,bb,cc=map(int,words[pos:pos+3]);pos+=3
                v=np.array([a&32767,bb&32767,cc])/32767*np.sqrt(2)-1/np.sqrt(2)
                largest=((a>>14)&2)|(bb>>15)
                q[f,i]=np.insert(v,largest,np.sqrt(max(0,1-v@v)))
            if not c['m_bIsTranslationStatic']:
                span=np.array([c['m_translationRange'+axis]['m_flRangeLength'] for axis in 'XYZ'])
                t[f,i]+=words[pos:pos+3]/65535*span;pos+=3
            if not c['m_bIsScaleStatic']:
                s[f,i]+=int(words[pos])/65535*c['m_scaleRange']['m_flRangeLength'];pos+=1
        end=offsets[f+1] if f+1<n else len(words)
        assert pos==end,(f,pos,end)
    assert np.all(np.isfinite(q)) and np.all(np.isfinite(t)) and np.all(np.isfinite(s))
    assert np.max(np.abs(np.linalg.norm(q,axis=-1)-1))<1e-4
    return q,t,s


def build(args):
    args.output.mkdir(parents=True,exist_ok=True)
    roots=[p.resolve() for p in args.assets]
    def resolve(resource):
        matches=[r/(resource+'_c') for r in roots if (r/(resource+'_c')).is_file()]
        if not matches:raise FileNotFoundError(resource)
        hashes={hashlib.sha256(p.read_bytes()).hexdigest() for p in matches}
        assert len(hashes)==1,('conflicting resource copies',resource)
        return matches[0]
    rows=json.loads(args.recipes.read_text())
    resources=sorted({t['clip_resource'] for r in rows for t in r['tasks'] if 'clip_resource' in t})
    resources += ['models/heroes_staging/haze/clips/'+x+'.vnmclip' for x in ['death_idle_01','death_idle_02','death_run_01','death_in_air']]
    def dump(resource):
        path=resolve(resource);out=args.output/(resource+'.json')
        if out.exists():return json.loads(out.read_text())
        proc=subprocess.run([str(args.cli),'-i',str(path),'-b','DATA'],capture_output=True,text=True,timeout=60)
        if proc.returncode:raise RuntimeError(proc.stderr)
        data=parse_kv3(proc.stdout);out.parent.mkdir(parents=True,exist_ok=True)
        tmp=out.with_suffix(out.suffix+'.'+str(threading.get_ident())+'.tmp')
        tmp.write_text(json.dumps(data,separators=(',',':')));os.replace(tmp,out)
        return data
    def one(resource):
        d=dump(resource);sk=dump(d['m_skeleton'])
        q,t,s=decode_frames(d)
        assert len(sk['m_boneIDs'])==q.shape[1],resource
        assert not d.get('m_secondaryAnimations'),('secondary animation',resource)
        npz=args.output/(resource+'.npz');npz.parent.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(npz,q=q,t=t,s=s)
        return {'resource':resource,'sha256':hashlib.sha256(resolve(resource).read_bytes()).hexdigest(),
                'skeleton':d['m_skeleton'],'frames':len(q),'duration':d['m_flDuration'],
                'additive':d['m_bIsAdditive'],'model_space_bones':d.get('m_modelSpaceBoneSamplingIndices',[]),
                'model_space_chain':d.get('m_modelSpaceSamplingChain',[]),
                'float_curves':d.get('m_floatCurveIDs',[]),'bones':len(sk['m_boneIDs']),
                'scale_range':[float(s.min()),float(s.max())]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(one,resources))
    (args.output/'manifest.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps({'clips':len(results),'total_frames':sum(r['frames'] for r in results),
          'additive':sum(r['additive'] for r in results),
          'model_space_clips':[r['resource'] for r in results if r['model_space_bones']],
          'skeletons':sorted({r['skeleton'] for r in results}),
          'non_unit_scale_clips':sum(r['scale_range']!=[1,1] for r in results)},indent=2),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--assets',type=Path,nargs='+',required=True)
    ap.add_argument('--recipes',type=Path,required=True)
    ap.add_argument('--cli',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    build(ap.parse_args())
