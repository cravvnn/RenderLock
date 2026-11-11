"""Separate bounded simulation/package preparation for unowned world effects."""
import hashlib
import json
from pathlib import Path
import subprocess
from .particle_world_origin import (CLIENT_SHA256,NATIVE_PROFILES,BACKEND,DEFINITIONS,request_header,
    control_points,world_identity,validate_response,validate_definitions,validate_package)
PARTICLES_SHA256=NATIVE_PROFILES[CLIENT_SHA256]

def qualify_installation(provider):
 """Admit only a measured Windows client/renderer pair."""
 path=Path(provider.install_path)/'game/citadel/bin/win64/client.dll'
 if not path.is_file():raise ValueError('World-origin creation is not yet qualified for this platform/client')
 with path.open('rb')as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
 if actual not in NATIVE_PROFILES:raise ValueError('World-origin creation client changed; native qualification is required')
 # The conditional projected layer also relies on the measured renderer defaults
 # in particles.dll (world/water enabled, characters disabled).
 particles=Path(provider.install_path)/'game/bin/win64/particles.dll'
 if not particles.is_file():raise ValueError('World-origin projected renderer platform is not yet qualified')
 with particles.open('rb')as stream:particle_sha=hashlib.file_digest(stream,'sha256').hexdigest()
 if particle_sha!=NATIVE_PROFILES[actual]:raise ValueError('World-origin projected renderer changed; native qualification is required')
 return actual

def prepare_world_package(data,instance,runtime,provider,decoder,adapter,directory):
 from .particle_runtime import particle_command,particle_environment
 from .recorded_effect_simulation import (substitute_seed,validate_request_response,
     validate_seed_response,validate_event_response,validate_control_response)
 from .asset_converter import compiled
 client_sha256=qualify_installation(provider)
 header=request_header(data,instance,client_sha256=client_sha256)
 definitions={name:decoder.dump(name,provider)for name in DEFINITIONS}
 validate_definitions(definitions)
 out=Path(directory);out.mkdir(parents=True,exist_ok=False)
 plan={key:data[key]for key in ('start_tick','end_tick','tick_rate')}
 plan.update(gameinfo=str(Path(runtime['install_path'])/'game/citadel/gameinfo.gi'),
             seed=substitute_seed(data,instance),instances=[dict(instance,
             world_origin_provider=header,control_points=control_points(data,instance))])
 plan_path=out/'request.json';result_path=out/'simulation.json'
 with plan_path.open('x',encoding='utf-8')as f:json.dump(plan,f)
 request_sha=hashlib.sha256(plan_path.read_bytes()).hexdigest()
 proc=subprocess.run(particle_command(runtime['probe'],runtime.get('dotnet'))+[str(plan_path),str(result_path)],
     env=particle_environment(runtime['probe'],runtime.get('dotnet')),capture_output=True,text=True,timeout=120)
 (out/'simulation.log').write_text(proc.stdout+proc.stderr,encoding='utf-8')
 if proc.returncode not in (0,2):raise ValueError(f'World particle simulator exit {proc.returncode}; see {out}/simulation.log')
 if not result_path.is_file()or result_path.stat().st_size>32*1024*1024:
  raise ValueError('World particle response is missing or exceeds the bounded32MiB output')
 sim=json.loads(result_path.read_text(encoding='utf-8'))
 validate_request_response(sim,instance,data['start_tick'],data['end_tick'],data['tick_rate'],request_sha)
 validate_seed_response(sim,plan['seed']);validate_response(sim,data,instance,client_sha256=client_sha256)
 validate_event_response(sim,instance,instance['created_tick'],data['end_tick'])
 validate_control_response(sim,instance['id'],[dict(control_point=0)])
 if sim.get('warnings'):raise ValueError('World particle simulator diagnostics: '+'; '.join(sim['warnings']))
 names={system['name'].removesuffix('_c')for frame in sim['instances'][0]['frames']for system in frame['systems']}
 if names!=set(DEFINITIONS):raise ValueError('World simulation system tree differs from the qualified explosion tree')
 sim['instances'][0]['entity_id']=None
 texture_names={layer['m_hTexture'] for name in names for renderer in definitions[name].get('m_Renderers',[])
                for layer in renderer.get('m_vecTexturesInput',[]) if layer.get('m_hTexture')}
 textures={}
 for name in sorted(texture_names):
  directory,descriptor=adapter.texture(name,provider)
  textures[name]={**descriptor,'directory':str(directory)}
 assets=dict(models={},definitions=definitions,textures=textures,
  provenance=dict(converter=decoder.identity,input_sha256={n:provider.resolve(compiled(n)).sha256 for n in names|texture_names}))
 package=dict(schema_version=1,backend=BACKEND,entity_ids=[],scene_identity=world_identity(data,instance,client_sha256=client_sha256),
  world_origin_provider=header,simulation_request_sha256=request_sha,simulation=sim,assets=assets,
  coverage_scope='One recorded unowned literal-birth explosion. Authored simulation and Blender shading use approximate Source behavior and AGR substitute seeds; projected rendering is verified separately.')
 validate_package(package,data)
 package_path=out/'package.json'
 with package_path.open('x',encoding='utf-8')as f:json.dump(package,f)
 return package,package_path
