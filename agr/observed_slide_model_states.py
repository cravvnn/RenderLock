"""Immutable dense model package. No capture, storage or allocation owner."""
import copy,hashlib,math,re
from pathlib import Path
from .replay_capture import canonical_sha256
from .captured_slide_binding import _source_scene_identity as scene_identity
from .observed_slide_profile import NATIVE_DRIVER_DEFAULTS
from .observed_slide_profile import keys,digest
from .particle_slide_mesh import prepare_slide_model_frame_from_cmdl
from .particle_model_geometry import read_model_geometry
from .particle_slide_material import VERTEX_SHADER_SHA256,PIXEL_SHADER_SHA256

BACKEND='observed_slide_model_frames_v1'
POLICY='agr_dense_source_bound_slide_model_frames_v2'
OWNER='56:895'
MODEL='models/particle/dust_ground_streak_fx_model.vmdl'
GEOMETRY_SHA='80571a6e1d2ad375756cc60c62b5a056fbea44333d017b63d7d14b97fb0c1d38'
BUNDLE_SHA='86e4af946c96de7283d4c84beaa99026814003a93fc53f7da9c70c34a59daee6'
RESOURCES={'particles/generic/slide_spray_model.vpcf','particles/generic/slide_spray_model_hot.vpcf'}
SOURCE_KEYS={'report_sha256','observer_sha256','field_reader_sha256','freeze_sha256','cleanup_sha256',
             'projector_sha256','native_projection_sha256','native_material_sha256'}
SCOPE=('Two current-property slide model emitters for Venator56:895, complete frame-local meshes and '
       'source-bound material inputs, with observed evaluated CMDL color. Arrays hold between native ticks; '
       'no root/particle lifetime joins. Host RGB conversion, unquantized CPU alpha, VRF PBR/filtering and '
       'Source lighting/GPU parity remain approximate. Mina, history/orphans/death/endcaps and common '
       'hero/render clock remain separate unfinished scene requirements.')

def require(ok,why):
    if not ok:raise ValueError('Observed slide models: '+why)

def integer(v,lo,hi):return type(v)is int and lo<=v<=hi

def validate_reference(reference,data):
    keys(reference,('capture','capture_sha256'),'model envelope');c=reference['capture']
    require(digest(reference['capture_sha256']) and canonical_sha256(c)==reference['capture_sha256'],
            'reference digest differs')
    keys(c,('schema_version','policy','entity_id','scene_identity','native','source','between_tick_policy','frames',
            'model_row_count','material_positive_frames','scope'),'model capture')
    require(type(c['schema_version'])is int and c['schema_version']==2 and c['policy']==POLICY
            and c['entity_id']==OWNER and c['scene_identity']==scene_identity(data,[OWNER])
            and c['native']==NATIVE_DRIVER_DEFAULTS and c['between_tick_policy']=='hold_complete_arrays'
            and c['scope']==SCOPE,'capture schema/scene/scope differs')
    keys(c['source'],SOURCE_KEYS,'model provenance');require(all(digest(v)for v in c['source'].values()),'source digests unavailable')
    start,end=data['start_tick'],data['end_tick']
    require(integer(start,0,2**31-2) and integer(end,start,start+9999)
            and (start,end)==(17920,18240),'qualified dense model range differs')
    frames=c['frames'];require(type(frames)is list and len(frames)==end-start+1,'dense frames unavailable')
    require(integer(c['model_row_count'],1,64) and integer(c['material_positive_frames'],1,64),
            'native frame/candidate bounds differ')
    total=0;positive_frames=0;observed=set();scene_bases=set()
    for tick,frame in zip(range(start,end+1),frames):
        keys(frame,('tick','root_count','systems'),'model frame')
        require(type(frame['tick'])is int and frame['tick']==tick and integer(frame['root_count'],0,8)
                and type(frame['systems'])is list and len(frame['systems'])==2*frame['root_count'],
                'current root/model coverage differs')
        seen=set();paths=set();wrappers=set();cohorts=set();frame_rows=0
        for system in frame['systems']:
            keys(system,('root_ordinal','resource','child_path','state','particle_count','models'),'model system')
            root,name=system['root_ordinal'],system['resource']
            require(integer(root,0,frame['root_count']-1) and name in RESOURCES and (root,name)not in seen
                    and type(system['child_path'])is str and re.fullmatch(r'root/[0-7]',system['child_path'])
                    and type(system['models'])is list and len(system['models'])<=1
                    and integer(system['particle_count'],0,1) and system['particle_count']==len(system['models'])
                    and system['state']==('captured'if system['models']else'known_empty'),
                    'system identity/count/state differs')
            require((root,system['child_path'])not in paths,'aliased current model child')
            seen.add((root,name));paths.add((root,system['child_path']))
            for row in system['models']:
                keys(row,('resource','root_ordinal','child_path','slot','wrapper','scene_base','matrix','frame','colors','cohort_sha256'),'model instance')
                require(row['resource']==name and row['root_ordinal']==root and row['child_path']==system['child_path']
                        and type(row['slot'])is int and row['slot']==0 and digest(row['cohort_sha256']),
                        'frame-local instance association differs')
                require(all(type(row[k])is int and 0<row[k]<2**47 and row[k]%8==0 for k in('wrapper','scene_base')),
                        'typed model/module addresses unavailable')
                require(row['wrapper']not in wrappers,'aliased current model wrapper')
                wrappers.add(row['wrapper']);cohorts.add(row['cohort_sha256']);scene_bases.add(row['scene_base'])
                require(len(cohorts)<=1 and len(scene_bases)<=1,'mixed CPU cohorts/module instances')
                matrix=row['matrix']
                require(type(matrix)is list and len(matrix)==3 and all(type(v)is list and len(v)==4 for v in matrix)
                        and all(type(v)in(int,float) and math.isfinite(v) and abs(v)<=1e7 for axis in matrix for v in axis),
                        'affine matrix bounds differ')
                total+=1;frame_rows+=1;observed.add(name);require(total<=64,'native model slot bound exceeded')
        positive_frames+=bool(frame_rows)
    require(observed==RESOURCES,'one model emitter was never captured')
    require(total==c['model_row_count'] and positive_frames==c['material_positive_frames'],
            'native model row/frame coverage differs')
    return c

def load_geometry(assets):
    require(type(assets)is dict and set(assets)=={'models','textures','slide_material'},'asset fields differ')
    require(type(assets['models'])is dict and set(assets['models'])=={MODEL},'model asset identity differs')
    model=assets['models'][MODEL]
    require(model['scene']=='model.glb' and model['scene_sha256']==GEOMETRY_SHA
            and model['material_bundle']['sha256']==BUNDLE_SHA,'geometry/material pins differ')
    path=Path(model['directory'])/model['scene'];bundle=Path(model['material_bundle']['path'])
    require(all(p.is_file() and 20<=p.stat().st_size<=64*1024**2 for p in(path,bundle)),
            'geometry/material file bounds differ')
    require(hashlib.sha256(path.read_bytes()).hexdigest()==GEOMETRY_SHA
            and hashlib.sha256(bundle.read_bytes()).hexdigest()==BUNDLE_SHA,'geometry/material bytes differ')
    return read_model_geometry(path)

def prepare_frames(package,data,assets):
    c=validate_reference(package['reference'],data);geometry=load_geometry(assets)
    coordinates=data.get('metadata',{}).get('coordinate_system',{})
    scale=coordinates.get('unit_scale')
    require(type(scale)in(int,float) and math.isfinite(scale) and scale>0,'scene unit scale unavailable')
    require(coordinates.get('origin')==[0,0,0]
            and type(coordinates.get('world_yaw_degrees'))in(int,float)
            and coordinates['world_yaw_degrees']==0,
            'captured slide model profile requires the original Source world basis')
    result=[]
    for sample in c['frames']:
        for system in sample['systems']:
            for row in system['models']:
                prepared=prepare_slide_model_frame_from_cmdl(geometry,row['matrix'],row['frame'],assets['slide_material'],row['colors'],scale,
                    slot=row['slot'],expected_wrapper=row['wrapper'],scene_base=row['scene_base'],
                    vertex_shader_sha256=VERTEX_SHADER_SHA256,pixel_shader_sha256=PIXEL_SHADER_SHA256)
                result.append((sample['tick'],system,row,prepared))
    return result

def validate_package(package,data):
    keys(package,('schema_version','backend','entity_ids','scene_identity','reference','assets','assets_sha256','coverage_scope'),'model package')
    require(type(package['schema_version'])is int and package['schema_version']==1 and package['backend']==BACKEND
            and package['entity_ids']==[OWNER] and package['scene_identity']==scene_identity(data,[OWNER])
            and canonical_sha256(package['assets'])==package['assets_sha256'] and package['coverage_scope']==SCOPE,
            'package identity/assets differ')
    prepare_frames(package,data,package['assets'])

def prepare_package(data,reference,assets):
    package=dict(schema_version=1,backend=BACKEND,entity_ids=[OWNER],scene_identity=scene_identity(data,[OWNER]),
                 reference=copy.deepcopy(reference),assets=copy.deepcopy(assets),assets_sha256=canonical_sha256(assets),coverage_scope=SCOPE)
    validate_package(package,data);return package
