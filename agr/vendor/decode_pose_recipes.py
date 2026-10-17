"""Experimental bounded AG2 task payload parser. Does not evaluate bone poses.

Numeric task IDs are retained. Sample/blend layouts are research hypotheses.
Unknown task IDs stop parsing; no fabricated defaults or skipped bits.
"""
import json, collections, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class Unsupported(ValueError): pass

class Bits:
    def __init__(self, payload, p):
        self.v = int.from_bytes(payload, 'little'); self.size = len(payload)*8; self.p=p
    def read(self,n):
        if n < 0 or self.p+n>self.size: raise ValueError('payload overrun')
        v=(self.v>>self.p)&((1<<n)-1);self.p+=n;return v

def masks(b,mask_bits):
    count=b.read(5)
    if not 0<count<32:raise ValueError('invalid mask task count')
    w=count.bit_length();out=[]
    for i in range(count):
        t=b.read(3);m={'type_id':t}
        if t==0:m['mask_index']=b.read(mask_bits)
        elif t==1:m['weight_u8']=b.read(8)
        elif t in (2,3,4):
            m['source']=b.read(w)
            if m['source']>=i:raise ValueError('mask source dependency')
            if t in (2,4):
                m['target']=b.read(w)
                if m['target']>=i:raise ValueError('mask target dependency')
            if t in (2,3):m['weight_u8']=b.read(8)
        else:raise Unsupported(f'mask task type {t}')
        out.append(m)
    return out

def parse(row,resource_bits=8,mask_bits=5,bone_bits=None):
    payload=bytes.fromhex(row['payload_hex'])
    b=Bits(payload,row['candidate_task_data_start_bit']);w=row['candidate_dependency_bits']
    tasks=[];error=None;unsupported=None
    if bone_bits is None:
        raise ValueError('bone_bits must be derived from the recorded skeleton')
    topology = row.get('topology_tasks')
    for i,t in enumerate(row['candidate_task_type_ids_4bit']):
        task={'type_id':t,'start_bit':b.p}
        try:
            if t==0:
                dependency=topology[i]['dependencies'][0] if topology is not None else b.read(w)
                if dependency>=i:raise ValueError('invalid cache dependency')
                task.update(dependencies=[dependency],cache_id=b.read(6))
            elif t==5:
                task['cache_id']=b.read(6)
            elif t==11:
                dependency=topology[i]['dependencies'][0] if topology is not None else b.read(w)
                if dependency>=i:raise ValueError('invalid follow dependency')
                task.update(dependencies=[dependency],bone_index=b.read(bone_bits),
                            follow_bone_index=b.read(bone_bits),mode=b.read(2))
            elif t==1:
                task.update(resource_index=b.read(resource_bits),phase_u16=b.read(16))
            elif t==12:
                # Named CNmZeroPoseTask has no dependencies or dynamic values.
                if topology is not None and topology[i]['dependencies']:
                    raise ValueError('Zero pose task cannot have dependencies')
                task['dependencies']=[]
            elif t==4:
                dependency=topology[i]['dependencies'][0] if topology is not None else b.read(w)
                if dependency>=i:raise ValueError('invalid IK dependency')
                task.update(dependencies=[dependency],effector_bone_index=b.read(bone_bits))
                if not b.read(1):raise Unsupported('IK explicit transform target')
                task['target_bone_index']=b.read(bone_bits)
                # The observed extension fits both flag/weight orderings. Keep
                # raw bits; do not assert solver semantics from byte-fit alone.
                task['extension_flag_a']=b.read(1)
                if task['extension_flag_a']:task['extension_value_9bit']=b.read(9)
                task['extension_flag_b']=b.read(1)
                if task['extension_flag_b']:raise Unsupported('IK second extension flag')
            elif t in (6,7,8,9):
                if topology is not None:
                    a,c=topology[i]['dependencies']
                else:
                    a=b.read(w);c=b.read(w)
                if a>=i or c>=i:raise ValueError('invalid task dependency')
                task.update(dependencies=[a,c],weight_u8=b.read(8))
                task['masks']=masks(b,mask_bits) if b.read(1) else []
            else:raise Unsupported(f'task type {t}')
            task['end_bit']=b.p;tasks.append(task)
        except Unsupported as e:unsupported=str(e);break
        except ValueError as e:error=str(e);break
    complete=len(tasks)==len(row['candidate_task_type_ids_4bit'])
    tail=b.size-b.p
    # V3's replicated dynamic vector retains capacity and old tail bytes.
    # Its active topology bounds the task stream; unlike V2 it has no
    # m_nSerializePoseRecipeSizeAG2 field declaring a used-byte count.
    if complete and topology is None and (not 0<=tail<8 or b.read(tail)!=0):
        error='non-padding remainder';complete=False
    return {'hero_id':row['hero_id'],'demo_tick':row['demo_tick'],
            'recipe_age_ticks':row['recipe_age_ticks'],
            'complete_layout':complete,'error':error,'unsupported':unsupported,
            'tasks':tasks,'consumed_bits':b.p,'valid_bytes':(b.p+7)//8 if topology is not None else len(payload),
            'dynamic_capacity_bytes':len(payload) if topology is not None else None}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,default=ROOT.parent/'haze_graph_work/results/recipe_headers.json')
    ap.add_argument('--output',type=Path,default=ROOT/'task_payload_probe.json')
    args=ap.parse_args()
    rows=json.loads(args.input.read_text())
    parsed=[parse(r) for r in rows]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(parsed,indent=2)+'\n')
    for h in sorted({r['hero_id'] for r in rows}):
        rr=[r for r in parsed if r['hero_id']==h]
        print(h,'complete',sum(r['complete_layout'] for r in rr),
              'errors',dict(collections.Counter(r['error'] for r in rr)),
              'unsupported',dict(collections.Counter(r['unsupported'] for r in rr)))

if __name__=='__main__':main()
