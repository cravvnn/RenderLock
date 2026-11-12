"""Optional measured slide sprites through the normal package transaction."""
import json
from pathlib import Path
import subprocess
from .observed_slide_sprite_states import BACKEND,OWNER,ROOT,PROFILES,SCOPE,validate_reference,prepare_package


def build_observed_slide_sprite_effects(data,scene,camera,entity_objects,runtime,provider,decoder,adapter):
    from .particle_render_assets import prepare_render_assets
    from .particle_sheet_uv import collect_shader_inputs
    from .simulated_effect_package import build_package_effects
    report=dict(backend=BACKEND,status='partial',objects=0,particle_tracks=0,point_lights=0,
        rendered_collection_layers=0,collection_frame_meshes=0,unsupported=[],coverage_scope=SCOPE)
    if OWNER not in entity_objects:
        report.update(status='not_selected',reason='Native reference owner is not selected');return report
    try:
        from .observed_slide_cohort import validate_slide_cohort
        validate_slide_cohort(runtime)
        reference=runtime['observed_slide_sprite_reference'];validate_reference(reference,data)
        # A separate sheet-only helper can be used without replacing the
        # general model/simulation helper in the caller's runtime settings.
        sheet_probe=runtime.get('texture_sheet_probe',runtime['probe']);asset_runtime=dict(runtime,probe=sheet_probe)
        assets=prepare_render_assets(set(PROFILES),decoder,adapter,provider,asset_runtime)
        for name,texture in assets['textures'].items():
            if texture.get('is_sprite_sheet') and not texture.get('source_sheet'):
                texture['source_sheet']=adapter.texture_sheet(name,provider,sheet_probe,runtime.get('dotnet'))
        assets['provenance']['spritecard_sheet_uv']=collect_shader_inputs(provider)
        package=prepare_package(data,reference,assets)
        path=Path(runtime['output_dir'])/'native-slide-sprites.package.json'
        with path.open('x',encoding='utf-8') as stream:json.dump(package,stream)
        rendered=build_package_effects(package,data,path,scene,camera)
        for key in ('objects','particle_tracks','point_lights','rendered_collection_layers','collection_frame_meshes'):report[key]=rendered.get(key,0)
        report.update(package=str(path),rendering=rendered)
        for name in ('slide_streaks','slide_spray_model','slide_spray_model_hot','slide_start_light'):
            report['unsupported'].append(dict(resource='particles/generic/'+name+'.vpcf',occurrences=1,
                reason='Native inputs captured; this sprite component does not qualify this renderer',
                instance_examples=['native-owner:'+OWNER],coverage_scope='Unresolved native renderer component; not a recorded birth count'))
        report['unsupported'].append(dict(resource=ROOT,occurrences=1,
            reason='Mina unobserved; root/endcap/orphan lifetime, common clock and Source lighting remain unqualified',
            instance_examples=['native-owner:'+OWNER],coverage_scope='Unresolved parent scope; not a recorded birth count'))
    except (ValueError,KeyError,OSError,RuntimeError,subprocess.SubprocessError) as exc:
        report.update(status='unavailable',reason=str(exc))
        report['unsupported'].append(dict(resource=ROOT,occurrences=1,reason=str(exc),coverage_scope='Native slide sprite stage unavailable; no recorded birth inferred'))
    return report


def merge_observed_slide_sprite_report(report,observed):
    report['observed_slide_sprite_effects']=observed
    for key in ('objects','point_lights'):report[key]=report.get(key,0)+observed.get(key,0)
    report.setdefault('unsupported',[]).extend(observed.get('unsupported',[]))
    report.setdefault('limitations',[]).append(observed['coverage_scope'])
