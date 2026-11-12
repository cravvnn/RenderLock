"""Complete current-property slide sprite arrays; no cross-frame identities.

Native clocks/creation/lifetime are used only by the measured gather equations.
They never become inferred births or lifetime joins. Missing graphs/fields are
refused; a frame with no current property roots is not proof of no orphan FX.
"""
import copy
import math
import re
import struct
import numpy as np
from .client_modifier_writes import integer
from .replay_capture import canonical_sha256
from .captured_slide_binding import _source_scene_identity as scene_identity
from .observed_slide_profile import NATIVE_DRIVER_DEFAULTS
from .observed_slide_profile import native_color_bytes, finite, digest, keys
from .observed_slide_sprite_geometry import captured_renderer

BACKEND = 'observed_slide_sprite_frames_v1'
POLICY = 'agr_complete_native_slide_sprite_arrays_v1'
OWNER = '56:895'
ROOT = 'particles/generic/slide.vpcf'
PROFILES = {
    'particles/generic/slide_dust.vpcf': ('77e463f70b6b382f8394575f92230aac063e3b5d02fb378e7f0c6bf8b7457133', '33a8ee6a0c7d1f22b1b394d64da519a2aa64c39fecc5f4a4e23ccf949ea8f148'),
    'particles/generic/slide_dust_start.vpcf': ('d54c11229c5559594038196f617e332cf55c6be00cb8773b4928645d27b1101a', '1cae855b4dc813bd4d8260a6c9a1e601c78d2e0bc9c7e1f92cf337a0b3f35378'),
    'particles/generic/slide_debris.vpcf': ('8ddd82eb09c4b117024696d332d715ee8c2e7c2bc10ba2cd0e3e3a15b3dde296', 'c316b954e337a0714022587b33572f253aabded5bd5596796575a331179cc961'),
}
RENDERERS = {name: 1 if name.endswith('slide_debris.vpcf') else 2 for name in PROFILES}
CHILDREN = set(PROFILES) | {'particles/generic/'+n+'.vpcf' for n in ('slide_streaks','slide_spray_model','slide_spray_model_hot','slide_start_light')}
FIELDS = {0:3,1:1,3:1,4:1,6:3,7:1,8:1,9:1,11:1,12:1,13:1,16:1,20:1,38:1,39:1,40:1}
SOURCE_KEYS = {'report_sha256','observer_sha256','field_reader_sha256','freeze_sha256','cleanup_sha256','native_gathers_sha256','projection_sha256'}
POINT_KEYS = {'position','radius','rotation','color_bytes','sequence','native_animation_input','shader_extra_data1','native_random'}
SCOPE = ('Five native sprite render layers for Venator56:895 current slide properties, with complete frame-local arrays. '
         'No root/particle identities are joined across frames; complete arrays hold between native ticks. '
         'No current property root is not proof of no orphan effects. Trail, two model emitters, start light, Mina, '
         'root/endcap lifetime, history, common hero/render clock and Source shader/lighting/pixel/subframe parity remain separate.')


def require(value, message):
    if not value: raise ValueError('Observed slide sprites: '+message)


def profile_pins():
    return {name:dict(source_sha256=a,definition_sha256=b) for name,(a,b) in PROFILES.items()}


def validate_profiles(assets):
    require(isinstance(assets,dict) and assets.get('models') == {} and set(assets.get('definitions',{})) == set(PROFILES), 'asset/profile coverage differs')
    from .particle_sheet_uv import validate_shader_inputs
    from .particle_texture_animation import validate_sheet
    validate_shader_inputs(assets.get('provenance',{}).get('spritecard_sheet_uv'))
    pins = assets.get('provenance',{}).get('input_sha256',{})
    for name,(source,definition) in PROFILES.items():
        require(pins.get(name)==source and canonical_sha256(assets['definitions'][name])==definition, 'authored renderer definition changed')
    for name,texture in assets.get('textures',{}).items():
        require(digest(pins.get(name)), 'texture source pin unavailable')
        if texture.get('is_sprite_sheet'):
            sheet=validate_sheet(texture.get('source_sheet'))
            require(sheet.get('input_sha256')==pins[name], 'original sheet identity differs')
    return assets['definitions']


def _scalar(value, base, slots):
    require(isinstance(value,dict) and value.get('input_type')=='0x462d28', 'scalar native class differs')
    require(finite(value.get('value'),1e6), 'scalar value invalid')
    if value.get('state')=='literal':
        keys(value,('state','input_type','value','raw_hex'),'literal input')
        require(isinstance(value['raw_hex'],str) and re.fullmatch('[0-9a-f]{8}',value['raw_hex']) is not None
                and struct.unpack('<f',bytes.fromhex(value['raw_hex']))[0]==value['value'], 'literal raw value differs')
    else:
        keys(value,('state','input_type','value','provider_raw','slot_offset','slot_seed_raw','native_unit_random'),'random input')
        require(value['state']=='constant_random' and isinstance(value['provider_raw'],str)
                and re.fullmatch('[0-9a-f]{48}',value['provider_raw']) is not None, 'random provider missing')
        raw=bytes.fromhex(value['provider_raw']);lo,hi=struct.unpack_from('<ff',raw)
        mode,slot=struct.unpack_from('<ii',raw,16);unit=value['native_unit_random']
        require(raw[8]==0 and mode==0 and finite(lo,1e6) and finite(hi,1e6)
                and integer(slot,base,base+4*slots-4) and slot%4==0 and value['slot_offset']==slot
                and isinstance(value['slot_seed_raw'],str) and re.fullmatch('[0-9a-f]{8}',value['slot_seed_raw']) is not None
                and finite(unit) and 0<=unit<=1, 'random context/provider bounds differ')
        f=np.float32;expected=float(f(f(f(unit)*f(f(hi)-f(lo)))+f(lo)))
        expected=min(max(expected,min(lo,hi)),max(lo,hi))
        require(value['value']==expected, 'random evaluated value differs')


def _inputs(value):
    keys(value,('index','kind','context_record_raw','animation_raw','radius_scale','alpha_scale','depth_bias','gamma_raw','texture_layers'),'renderer input')
    require(integer(value['index'],0,1) and value['kind']=='C_OP_RenderSprites'
            and isinstance(value['context_record_raw'],str) and re.fullmatch('[0-9a-f]{16}',value['context_record_raw']) is not None, 'renderer context invalid')
    displacement,base,slots=struct.unpack('<IHH',bytes.fromhex(value['context_record_raw']))
    require(displacement<=1024**2 and 0<base<=4096 and slots<=512, 'renderer allocation bounds differ')
    require(isinstance(value['animation_raw'],str) and re.fullmatch('[0-9a-f]{16}',value['animation_raw']) is not None, 'animation metadata missing')
    rate,mode=struct.unpack('<fi',bytes.fromhex(value['animation_raw']))
    require(finite(rate,1e6) and mode in (0,1,2) and value['gamma_raw'] in ('00','01'), 'animation/gamma input invalid')
    for key in ('radius_scale','alpha_scale','depth_bias'): _scalar(value[key],base,slots)
    layers=value['texture_layers'];require(isinstance(layers,list) and 1<=len(layers)<=5, 'texture layer bounds differ')
    seen=set()
    for i,layer in enumerate(layers):
        keys(layer,('accepted_index','source_index','scale_u','scale_v'),'texture input')
        require(integer(layer['accepted_index'],0,4) and layer['accepted_index']==i
                and integer(layer['source_index'],0,4) and layer['source_index'] not in seen, 'original texture association invalid')
        seen.add(layer['source_index'])
        for axis in ('scale_u','scale_v'): _scalar(layer[axis],base,slots)
    return mode


def validate_reference(envelope,data):
    keys(envelope,('capture','capture_sha256'),'envelope');c=envelope['capture']
    require(digest(envelope['capture_sha256']) and canonical_sha256(c)==envelope['capture_sha256'], 'reference digest differs')
    keys(c,('schema_version','policy','entity_id','demo_sha256','scene_identity','start_tick','end_tick','inclusive','native','profiles','source','between_tick_policy','samples','scope'),'capture')
    require(type(c['schema_version']) is int and c['schema_version']==1 and c['policy']==POLICY
            and c['entity_id']==OWNER and c['native']==NATIVE_DRIVER_DEFAULTS and c['profiles']==profile_pins(), 'schema/native/profile identity differs')
    require(digest(c['demo_sha256']) and c['demo_sha256']==data.get('metadata',{}).get('demo_sha256')
            and c['scene_identity']==scene_identity(data,[OWNER]), 'replay/scene identity differs')
    require(integer(c['start_tick'],0,2**31-2) and integer(c['end_tick'],c['start_tick'],2**31-2)
            and (c['start_tick'],c['end_tick'])==(data.get('start_tick'),data.get('end_tick'))
            and c['end_tick']-c['start_tick']<10000 and c['inclusive'] is True, 'complete inclusive range differs')
    keys(c['source'],SOURCE_KEYS,'provenance');require(all(digest(v) for v in c['source'].values()), 'source proof missing')
    require(c['scope']==SCOPE and c['between_tick_policy']=='hold_complete_arrays', 'coverage/timing policy differs')
    samples=c['samples'];require(isinstance(samples,list) and len(samples)==c['end_tick']-c['start_tick']+1, 'dense frames unavailable')
    total=0
    for tick,sample in zip(range(c['start_tick'],c['end_tick']+1),samples):
        keys(sample,('tick','root_count','owner_life_state','collections'),'frame')
        require(type(sample['tick']) is int and sample['tick']==tick and integer(sample['root_count'],0,8)
                and integer(sample['owner_life_state'],0,2), 'frame/root/life metadata invalid')
        rows=sample['collections'];expected=[(i,n) for i in range(sample['root_count']) for n in sorted(PROFILES)]
        require(isinstance(rows,list) and len(rows)==len(expected), 'current-root child coverage differs')
        require([(r.get('root_ordinal'),r.get('resource')) for r in rows if isinstance(r,dict)]==expected, 'root/child array order differs')
        paths=set()
        for row in rows:
            keys(row,('root_ordinal','resource','child_path','state','count','collection_time','renderers'),'collection')
            require(integer(row['root_ordinal'],0,7) and isinstance(row['child_path'],str)
                    and re.fullmatch('root(?:/[0-7]){1,4}',row['child_path']) is not None
                    and integer(row['count'],0,64) and finite(row['collection_time'],36000), 'native collection metadata invalid')
            require((row['root_ordinal'],row['child_path']) not in paths, 'duplicate current child path')
            paths.add((row['root_ordinal'],row['child_path']))
            require(row['state']==('captured' if row['count'] else 'known_empty') and isinstance(row['renderers'],list)
                    and len(row['renderers'])==(RENDERERS[row['resource']] if row['count'] else 0), 'unavailable is not empty')
            for index,renderer in enumerate(row['renderers']):
                keys(renderer,('index','inputs','points'),'renderer')
                require(type(renderer['index']) is int and renderer['index']==index and isinstance(renderer['inputs'],dict)
                        and renderer['inputs'].get('index')==index, 'renderer order differs')
                _inputs(renderer['inputs']);points=renderer['points']
                require(isinstance(points,list) and len(points)==row['count'], 'native renderer array truncated')
                total+=len(points);require(total<=200000, 'point budget exceeded')
                for p in points:
                    keys(p,POINT_KEYS,'point')
                    require(all(isinstance(p[k],list) and len(p[k])==3 and all(finite(v) for v in p[k]) for k in ('position','rotation')), 'point vectors invalid')
                    require(finite(p['radius']) and p['radius']>=0 and finite(p['native_animation_input']) and finite(p['shader_extra_data1'])
                            and finite(p['native_random']) and 0<=p['native_random']<=1, 'point inputs invalid')
                    require(isinstance(p['sequence'],list) and len(p['sequence'])==2 and all(integer(v,0,32767) for v in p['sequence']), 'native sequence invalid')
                    require(isinstance(p['color_bytes'],list) and len(p['color_bytes'])==4 and all(integer(v,0,255) for v in p['color_bytes']), 'native packed color invalid')
    return c


def _points(node,mode):
    count=node['particles'];keys(node['fields'],(str(f) for f in FIELDS),'raw fields');fields={}
    for f,components in FIELDS.items():
        entry=node['fields'][str(f)];keys(entry,('components','stride','raw_hex'),'raw field')
        stride=entry['stride'];require(type(stride) is int and stride in (0,components*4) and type(entry['components']) is int and entry['components']==components, 'raw format differs')
        last=min(count-1,3) if stride==0 else (count-1)%4
        length=((count-1)//4*stride+(components-1)*4+last+1)*4
        require(isinstance(entry['raw_hex'],str) and re.fullmatch('[0-9a-f]{'+str(length*2)+'}',entry['raw_hex']) is not None, 'raw length differs')
        fields[f]=stride,bytes.fromhex(entry['raw_hex'])
    clocks=node['clock_40_78_raw'];require(isinstance(clocks,str) and re.fullmatch('[0-9a-f]{112}',clocks) is not None, 'native clock unavailable')
    lanes=struct.unpack_from('<4f',bytes.fromhex(clocks));require(all(finite(v,36000) for v in lanes), 'native clock invalid')
    random=node['native_random_values'];require(isinstance(random,list) and len(random)==count, 'native shader random unavailable')
    def value(f,i,axis=0):
        stride,raw=fields[f];return struct.unpack_from('<f',raw,((i//4)*stride+axis*4+i%4)*4)[0]
    points=[];f=np.float32
    for i in range(count):
        life=value(1,i);creation=value(8,i)
        require(finite(life) and finite(creation,36000), 'native lifetime/creation invalid')
        age=f(f(lanes[i%4])-f(creation))
        animation=float(age) if mode==0 else float(f(age/f(life))) if mode==1 and life>0 else 0. if mode==1 else value(38,i)
        seq=[value(n,i) for n in (9,13)];require(all(finite(v) for v in seq), 'raw sequence invalid')
        points.append(dict(position=[value(0,i,c) for c in range(3)],radius=value(3,i),rotation=[value(n,i) for n in (12,20,4)],
            color_bytes=native_color_bytes([value(6,i,c) for c in range(3)],value(7,i),value(16,i)),sequence=[int(np.rint(f(v))) for v in seq],
            native_animation_input=animation,shader_extra_data1=value(39,i),native_random=random[i]))
    return points


def build_reference(report,data,source,cleanup):
    from .observed_capture_read_profile import read_limit
    maximum_external_bytes = read_limit(report, source, cleanup)
    require(report.get('phase')=='closed' and type(report.get('launch_count')) is int and report['launch_count']==1
            and type(report.get('exit_code')) is int and report['exit_code']==0 and report.get('workflow_passed') is True
            and report.get('exact_target_coverage') is True and report.get('recordings')==[] and not report.get('error')
            and not report.get('cleanup_errors') and not report.get('monitor_error') and not report.get('watchdog_fired')
            and not report.get('crash_inventory_changed') and report.get('config_unchanged_after_cleanup') is True
            and report.get('source_unchanged_after_run') is True and report.get('retained_baseline_unchanged') is True, 'owned capture incomplete')
    pending=report.get('pending_runtime_restoration');require(isinstance(pending,dict) and set(pending)=={'camera','playback','game_ui','replay_hud','timing','resolution','layers','hero','speed','follow'} and not any(pending.values()), 'runtime restoration incomplete')
    require(cleanup.get('status')=='PASS_EXACT_OWNED_DEPLOYMENT_ARCHIVED' and cleanup.get('source_absent') is True
            and cleanup.get('runtime_restored') is True and cleanup.get('owned_native_removed_from_game') is True, 'owned archive unavailable')
    require(report.get('replay_sha256')==data.get('metadata',{}).get('demo_sha256'), 'raw replay differs')
    start,end=data['start_tick'],data['end_tick'];require(integer(start,0,2**31-2) and integer(end,start,start+9999), 'raw range exceeds bound')
    observations=report.get('seeks');require(isinstance(observations,list) and len(observations)==end-start+1, 'raw dense frames unavailable')
    mapping=report.get('initial_seek',{}).get('details',{})
    require(mapping.get('verified') is True and integer(mapping.get('game_tick_offset'),-2**31,2**31-1)
            and integer(mapping.get('actual_tick',mapping.get('target_tick')),0,start-1), 'fresh seek clock mapping unavailable')
    samples=[];previous_bytes=0
    for tick,obs in zip(range(start,end+1),observations):
        require(type(obs.get('target')) is int and type(obs.get('after')) is int and obs['target']==obs['after']==tick, 'raw tick differs')
        native=obs['native_capture'];actors=native.get('actors');require(native.get('tick')==tick and native.get('client_sha256')==NATIVE_DRIVER_DEFAULTS['client_sha256'] and native.get('particles_sha256')==NATIVE_DRIVER_DEFAULTS['particles_sha256'], 'raw native identity differs')
        goal=obs.get('pause_goal',{});final=goal.get('final',{})
        require(goal.get('verified') is True and integer(goal.get('fresh_frames'),2,1000)
                and goal.get('no_final_seek') is True and goal.get('cleanup')=='not_needed'
                and goal.get('target_tick')==goal.get('console_tick')==final.get('tick')==tick and final.get('paused') is True
                and goal.get('server_pause_tick')==tick+mapping['game_tick_offset'], 'exact paused endpoint unavailable')
        require(integer(native.get('external_bytes'),previous_bytes+1,maximum_external_bytes)
                and integer(native.get('read_bytes'),1,256*1024), 'raw aggregate/snapshot budget differs')
        previous_bytes=native['external_bytes']
        require(isinstance(actors,list) and len(actors)==1, 'raw actor scope differs');actor=actors[0]
        require(type(actor.get('entity_index')) is int and actor['entity_index']==56 and type(actor.get('handle')) is int and actor['handle']==56+(895<<15), 'raw owner differs')
        effects=actor.get('effects');require(isinstance(effects,list) and len(effects)<=8, 'raw root scope unavailable')
        roots=[];collections=[]
        for ordinal,effect in enumerate(effects):
            require(effect.get('resource',{}).get('name','').removesuffix('_c')==ROOT and type(effect.get('collection')) is int and effect['collection']>0 and effect['collection'] not in roots, 'raw current root duplicated/unknown')
            roots.append(effect['collection']);tree=effect['declared_tree'];nodes=tree.get('nodes')
            require(tree.get('coherent') is True and isinstance(nodes,list) and len(nodes)==8 and {n.get('resource') for n in nodes}==CHILDREN|{ROOT}, 'raw child graph incomplete')
            for name in sorted(PROFILES):
                node=next(n for n in nodes if n['resource']==name);count=node['particles'];require(integer(count,0,64) and node.get('field_state')==('captured' if count else 'known_empty'), 'raw array unavailable')
                inputs=node.get('renderer_inputs');require(isinstance(inputs,list) and len(inputs)==(RENDERERS[name] if count else 0), 'raw renderer coverage differs')
                if not count: require(node.get('fields')=={} and node.get('native_random_values')==[], 'empty raw arrays differ')
                renderers=[]
                for index,value in enumerate(inputs):
                    require(value.get('index')==index, 'raw renderer ordering differs');mode=_inputs(value)
                    renderers.append(dict(index=index,inputs=copy.deepcopy(value),points=_points(node,mode)))
                raw=node['clock_40_78_raw'];require(isinstance(raw,str) and re.fullmatch('[0-9a-f]{112}',raw) is not None, 'raw collection clock unavailable')
                collections.append(dict(root_ordinal=ordinal,resource=name,child_path=node['path'],state=node['field_state'],count=count,collection_time=struct.unpack_from('<f',bytes.fromhex(raw),0x10)[0],renderers=renderers))
        samples.append(dict(tick=tick,root_count=len(roots),owner_life_state=actor['life_state'],collections=collections))
    require(report.get('observer_read_bytes')==previous_bytes, 'aggregate read counter differs')
    capture=dict(schema_version=1,policy=POLICY,entity_id=OWNER,demo_sha256=report['replay_sha256'],scene_identity=scene_identity(data,[OWNER]),start_tick=start,end_tick=end,inclusive=True,native=dict(NATIVE_DRIVER_DEFAULTS),profiles=profile_pins(),source=copy.deepcopy(source),between_tick_policy='hold_complete_arrays',samples=samples,scope=SCOPE)
    result=dict(capture=capture,capture_sha256=canonical_sha256(capture));validate_reference(result,data);return result


def prepare_package(data,reference,assets):
    validate_reference(reference,data);validate_profiles(assets)
    result=dict(schema_version=1,backend=BACKEND,entity_ids=[OWNER],scene_identity=scene_identity(data,[OWNER]),reference=copy.deepcopy(reference),assets=copy.deepcopy(assets),assets_sha256=canonical_sha256(assets),coverage_scope=SCOPE)
    validate_package(result,data);return result


def validate_package(package,data):
    keys(package,('schema_version','backend','entity_ids','scene_identity','reference','assets','assets_sha256','coverage_scope'),'package')
    require(type(package.get('schema_version')) is int and package['schema_version']==1 and package.get('backend')==BACKEND
            and package.get('entity_ids')==[OWNER] and package.get('scene_identity')==scene_identity(data,[OWNER]), 'package scene/backend differs')
    capture=validate_reference(package.get('reference'),data);definitions=validate_profiles(package.get('assets'))
    require(package.get('assets_sha256')==canonical_sha256(package['assets']) and package.get('coverage_scope')==SCOPE, 'package asset/scope digest differs')
    for sample in capture['samples']:
        for row in sample['collections']:
            for renderer in row['renderers']:
                captured_renderer(definitions[row['resource']]['m_Renderers'][renderer['index']],renderer['inputs'])
