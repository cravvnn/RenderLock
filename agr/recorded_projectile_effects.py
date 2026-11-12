"""Render entity-driven effects using captured projectile fields, with separate provenance."""
import json
import subprocess
from pathlib import Path
from .asset_converter import sha
from .projectile_particles import QUALIFIED_CLIENT_SHA256S,POLICY,SCOPE,digest,request,selected_tracks
from .particle_runtime import particle_command,particle_environment


def build_projectile_effects(data,scene,camera,runtime,provider,decoder,adapter):
    from .recorded_effect_simulation import substitute_seed
    from .simulated_effect_package import build_package_effects,scene_identity
    from .particle_render_assets import prepare_render_assets
    capture=data.get('network_projectiles')
    if capture is not None:
        from .network_projectile_capture import validate_envelope
        validate_envelope(capture,data)
    if capture is None or capture['availability']=='unavailable':
        reason=capture['reason']if capture is not None else 'helper_not_advertised'
        message=('This saved extraction has no qualified network projectile capture; re-export the original replay with the updated native extractor.'
                 if reason=='helper_not_advertised'else
                 f'Network projectile capture is unavailable ({reason}); particle coverage cannot be established. See the retained extraction diagnostics.')
        return dict(status='unavailable',reason=message,capture_availability='unavailable',capture_reason=reason,
                    rendered_instances=0,objects=0,point_lights=0,particle_tracks=0,instances=[],unsupported=[])
    tracks=selected_tracks(capture,data)
    report=dict(status='partial',backend='network_projectile_simulation_v2',policy=POLICY,coverage_scope=SCOPE,
        selected_recorded_instances=0,attempted_instances=0,rendered_instances=0,objects=0,point_lights=0,particle_tracks=0,
        pre_shot_rendered_instances=0,instances=[],unsupported=[],unsupported_by_birth_scope={},candidate_by_birth_scope={})
    if not tracks:return dict(report,status='no_instances')
    try:current=sha(Path(runtime['install_path'])/'game/citadel/bin/win64/client.dll')
    except OSError:current=None
    out=Path(runtime['output_dir'])/'projectiles';out.mkdir(parents=True,exist_ok=False)
    definitions={};textures={}
    for number,track in enumerate(sorted(tracks,key=lambda t:(t['first_seen_tick'],t['id']))):
        samples=track.get('samples',[])
        if not samples or samples[-1].get('tick',-1)<data['start_tick'] or track['first_seen_tick']>data['end_tick']:continue
        report['selected_recorded_instances']+=1
        scope='in_recovered_history' if track['first_seen_tick']<data['start_tick'] else 'in_output'
        report['candidate_by_birth_scope'][scope]=report['candidate_by_birth_scope'].get(scope,0)+1
        try:
            if current not in QUALIFIED_CLIENT_SHA256S:
                raise ValueError('Projectile creation provider requires qualification for the installed client module')
            seed=substitute_seed(data,dict(id='projectile:'+track['id'],entity_id=track['id'],resource=track['class'],created_tick=track['first_seen_tick']))
            plan,owner=request(track,data,str(Path(runtime['install_path'])/'game/citadel/gameinfo.gi'),seed)
            stem=f'projectile-{number:06d}';plan_path=out/(stem+'.plan.json');result_path=out/(stem+'.simulation.json')
            plan_path.write_text(json.dumps(plan,sort_keys=True,separators=(',',':'),allow_nan=False),encoding='utf-8')
            print('AGR projectile simulation:',track['id'],owner,flush=True)
            report['attempted_instances']+=1
            proc=subprocess.run(particle_command(runtime['probe'],runtime.get('dotnet'))+[str(plan_path),str(result_path)],
                env=particle_environment(runtime['probe'],runtime.get('dotnet')),capture_output=True,text=True,timeout=120)
            (out/(stem+'.log')).write_text(proc.stdout+proc.stderr,encoding='utf-8')
            if proc.returncode:raise ValueError('Projectile simulator diagnostics: '+(proc.stdout+proc.stderr)[-1500:])
            simulation=json.loads(result_path.read_text(encoding='utf-8'))
            simulation['instances'][0]['entity_id']=owner
            names={s['name'].removesuffix('_c') for f in simulation['instances'][0]['frames'] for s in f['systems']}
            assets=prepare_render_assets(names,decoder,adapter,provider,runtime,definitions=definitions,textures=textures)
            package=dict(schema_version=1,backend='simulated_projectile_flight_v2',entity_ids=[owner],scene_identity=scene_identity(data,[owner]),
                projectile_provider=dict(client_sha256=current,entity_id=track['id'],track_sha256=digest(track),
                    capture_sha256=capture['provenance']['capture_sha256'],capture_provenance_sha256=digest(capture['provenance'])),
                projectile_request=plan,simulation_request_sha256=digest(plan),simulation=simulation,assets=assets,coverage_scope=SCOPE)
            package_path=out/(stem+'.package.json');package_path.write_text(json.dumps(package),encoding='utf-8')
            if sha(Path(runtime['install_path'])/'game/citadel/bin/win64/client.dll')!=current:
                raise ValueError('Projectile client module changed during preparation')
            result=build_package_effects(package,data,package_path,scene,camera)
            report['instances'].append(dict(id='projectile:'+track['id'],resource=plan['instances'][0]['resource'],
                projectile_entity_id=track['id'],owner_entity_id=owner,package=str(package_path),rendering=result,
                first_tick=track['first_seen_tick'],last_in_flight_tick=plan['end_tick'],coverage_scope=SCOPE))
            report['rendered_instances']+=1
            for key in ('objects','point_lights','particle_tracks'):report[key]+=result.get(key,0)
            if track['first_seen_tick']<data['start_tick']:report['pre_shot_rendered_instances']+=1
        except (ValueError,KeyError,OSError,RuntimeError,subprocess.SubprocessError) as exc:
            report['unsupported'].append(dict(resource=track['class'],reason=str(exc),occurrences=1,instance_examples=['projectile:'+track['id']],by_birth_scope={scope:1}))
            report['unsupported_by_birth_scope'][scope]=report['unsupported_by_birth_scope'].get(scope,0)+1
    (out/'coverage.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def merge_projectile_report(report,projectiles):
    """Add separate projectile coverage without replacing manager diagnostics."""
    report['projectile_effects']=projectiles
    if projectiles.get('status')=='unavailable':
        report.update(status='unavailable',reason=projectiles['reason'])
    report['candidate_instances']+=projectiles.get('selected_recorded_instances',0)
    for key in ('selected_recorded_instances','attempted_instances','rendered_instances',
                'objects','particle_tracks','point_lights','pre_shot_rendered_instances'):
        report[key]+=projectiles.get(key,0)
    report['instances'].extend(projectiles['instances'])
    report['unsupported'].extend(projectiles['unsupported'])
    for field in ('candidate_by_birth_scope','unsupported_by_birth_scope'):
        for scope,count in projectiles.get(field,{}).items():
            report[field][scope]=report[field].get(scope,0)+count
    report['limitations'].append(projectiles.get('coverage_scope')or projectiles.get('reason')or 'Projectile coverage is separate.')
