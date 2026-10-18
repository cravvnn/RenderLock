"""Orchestrate explicit partial hero lighting from installed assets."""
from pathlib import Path
from .asset_converter import ConversionError,sha
from .source_probe_assets import ATLAS_HELPER_HASHES,plan_probe_assets,prepare_probe_atlas
from .source_cube_assets import HELPER_SHA256,prepare_cube_assets
from .source_hero_lighting_package import build_hero_lighting_package,REFERENCE_ASSUMPTIONS
from .source_lighting_profiles import qualified_probe_helpers


def preflight_hero_lighting(scene, provider, decoder, *, map_name, margin_metres):
    """Fail bounded look selection before exporting thousands of map meshes."""
    from .game_assets import GameAssetProvider
    from .installed_map import replay_bounds
    mounted = GameAssetProvider(provider.install_path, provider.cache_dir,
                               map_name=map_name or getattr(provider, 'map_name', None))
    return plan_probe_assets(mounted, decoder, replay_bounds(scene, margin_metres))


def validate_hero_decoder_paths(atlas,cube,dotnet):
    if not atlas or not cube or not dotnet:raise ConversionError('Hero lighting requires atlas/cube decoders and .NET')
    atlas,cube,dotnet=map(lambda p:Path(p).expanduser().resolve(),(atlas,cube,dotnet))
    if atlas.name!='ProbeAtlasDecode.dll' or cube.name!='CubemapDecode.dll' or not dotnet.is_file():raise ConversionError('Choose qualified hero lighting decoder DLLs and .NET executable')
    for helper,required in [(atlas,ATLAS_HELPER_HASHES),(cube,{k:v for k,v in ATLAS_HELPER_HASHES.items() if k!='ProbeAtlasDecode.dll'}|{'CubemapDecode.dll':HELPER_SHA256})]:
        qualified_probe_helpers(helper,required)
    return atlas,cube,dotnet


def prepare_hero_lighting(descriptor,provider,decoder,*,atlas_probe,cube_probe,dotnet,allow_partial=False):
    if allow_partial is not True or descriptor.get('status')!='ready' or 'brdf' not in descriptor.get('lighting',{}):
        raise ConversionError('Hero lighting requires explicit partial selection and prepared Source map lighting')
    atlas_probe,cube_probe,dotnet=validate_hero_decoder_paths(atlas_probe,cube_probe,dotnet)
    plan=plan_probe_assets(provider,decoder,descriptor['source_bounds_xyz'])
    if plan['world_sha256']!=descriptor['source_world_sha256']:raise ConversionError('Hero/map world identity mismatch')
    atlas=prepare_probe_atlas(provider,decoder,plan,probe=atlas_probe,dotnet=dotnet)
    cubes=prepare_cube_assets(provider,decoder,plan,probe=cube_probe,dotnet=dotnet)
    return build_hero_lighting_package(atlas,cubes,descriptor['lighting']['brdf'],allow_partial=True,assumptions=REFERENCE_ASSUMPTIONS)
