"""Bounded regional preparation for explicitly partial Source static lighting."""
import json
from pathlib import Path
import shutil

from .asset_converter import ConversionError, sha
from .gltf_region import crop
from .installed_map import selected_world, replay_bounds, blender_map_matrix, _run_map_export, _keep_failure_diagnostics
from .source_lightmap_assets import prepare_lightmap_assets
from .source_brdf_assets import prepare_brdf_assets
from .source_lighting_profiles import VRF_6779, VRF_20_1
from .source_lighting_package import build_lighting_package
from .helper_files import helper_file_inventory


VRF_SHA256 = '5475c86ef49bab2163e5f8e8e83f0e01514c4a2c07ec734abad5e42a28c18951'


def verify_region_inputs(report, provider, world, world_sha256):
    inputs = report.get('inputs')
    if not isinstance(inputs,dict) or not 1 <= len(inputs) <= 100000 or world not in inputs:
        raise ConversionError('Regional export lacks its actual source payload inventory')
    if set(report.get('loaded',[])) != set(inputs):
        raise ConversionError('Regional export source inventory is inconsistent')
    for name, identity in inputs.items():
        current = provider.resource_identity(name)
        if (current['resource_path'] != name or current['sha256'] != identity.get('sha256')
                or Path(current['archive']).resolve() != Path(identity.get('archive','')).resolve()):
            raise ConversionError(f'Regional export source identity/precedence mismatch: {name}')
    if inputs[world]['sha256'] != world_sha256:
        raise ConversionError('Regional export world source changed')


def regional_command(probe, dotnet=None):
    probe = Path(probe).expanduser().resolve()
    if not probe.is_file() or probe.name.lower() != 'worldregionprobe.dll':
        raise ConversionError('Choose a built WorldRegionProbe.dll for partial Source lighting')
    if not dotnet or not Path(dotnet).is_file():
        raise ConversionError('WorldRegionProbe requires an explicit .NET executable')
    vrf = probe.with_name('ValveResourceFormat.dll')
    if not vrf.is_file() or sha(vrf) not in (VRF_SHA256,VRF_6779,VRF_20_1):
        raise ConversionError('Regional exporter VRF assembly is not qualified')
    # Include runtime/native companions, not just the small adapter DLL.
    files = helper_file_inventory(probe.parent)
    return [str(Path(dotnet).resolve()),str(probe)], files


def prepare_source_map(scene, provider, decoder, *, probe, dotnet, margin_metres=26.0,
                       allow_partial=False, log=print):
    if allow_partial is not True:
        raise ConversionError('Source static diffuse requires explicit partial-lighting selection')
    command, helper_identity = regional_command(probe,dotnet)
    world = selected_world(provider)
    world_identity = provider.resource_identity(world)
    bounds = replay_bounds(scene,margin_metres).tolist()
    assets = prepare_lightmap_assets(provider,decoder,allow_partial=True,log=log)
    assets['brdf'] = prepare_brdf_assets(provider,decoder)
    if assets['world_sha256'] != world_identity['sha256']:
        raise ConversionError('World changed while preparing Source lightmaps')
    settings = {'bounds':bounds,'helper':helper_identity,'adapter_sha256':sha(Path(__file__)),
                'crop_adapter_sha256':sha(Path(__file__).with_name('gltf_region.py'))}

    def build(temp):
        try:
            raw = temp/'raw'
            config = {'operation':'export_region','output':str(raw),'bounds':bounds,
                      'archives':[str(a.path) for a in provider.archives],
                      'world':world[:-2] if world.endswith('_c') else world,
                      'vrf_sha256':helper_identity['ValveResourceFormat.dll']}
            config_path=temp/'helper-config.json';config_path.write_text(json.dumps(config,indent=2))
            _run_map_export([*command,str(config_path)],temp/'converter.log',log)
            report=json.loads((raw/'region-report.json').read_text())
            verify_region_inputs(report,provider,world,world_identity['sha256'])
            cropped=crop(raw/'region.gltf',temp/'region.gltf',bounds)
            if not cropped['selected_mesh_nodes'] or cropped['selected_mesh_nodes'] != report['selected']:
                raise ConversionError('Regional selection changed during compaction')
            (temp/'region-source-report.json').write_text(json.dumps(report,indent=2))
            # Only freshly created intermediates in this unique cache transaction.
            # Crop validates the final dependency payloads before they are removed.
            if raw.resolve().parent != temp.resolve() or raw.is_symlink():
                raise ConversionError('Unexpected temporary regional output path')
            shutil.rmtree(raw)
        except Exception as error:
            diagnostic=_keep_failure_diagnostics(temp,decoder,'source-region',error)
            if diagnostic:
                raise ConversionError(f'{error}\nRegional diagnostics retained at: {diagnostic}') from error
            raise

    region=decoder._entry('source_static_region_v1',
        {'snapshot':provider.snapshot['snapshot_id'],'world':world_identity['sha256']},settings,build)
    report=json.loads((region/'region-source-report.json').read_text())
    # Snapshot includes mount trees; independently rehash actual payloads on cache
    # reuse too, detecting changes in numbered VPK chunks without copying them.
    verify_region_inputs(report,provider,world,world_identity['sha256'])
    descriptor={'schema_version':1,'status':'ready','source':'installed_game','name':str(provider.map_name),
        'world_resource':world,'source_world_sha256':world_identity['sha256'],
        'model_file':str(region/'region.gltf'),'report_file':str(region/'region-source-report.json'),
        'source_bounds_xyz':bounds,'margin_metres':float(margin_metres),
        'blender_matrix':blender_map_matrix(scene.get('metadata',{}).get('coordinate_system')),
        'snapshot_id':provider.snapshot['snapshot_id'],'selected_mesh_nodes':report['selected'],
        'source_actions':0,'collision':False,'physics_imported':False,
        'limits':['World-node static geometry only; entity-lump geometry/lights are omitted.',
                  'Installed map may differ from the replay; destruction/movement are not reconstructed.',
                  'Partial SH diffuse only; direct/specular/probes/fog and full Source material response are not reproduced.']}
    descriptor['lighting']=build_lighting_package(descriptor,assets,region,allow_partial=True)
    return descriptor
