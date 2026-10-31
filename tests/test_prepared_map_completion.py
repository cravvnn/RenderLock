import copy

import pytest

from agr import external_map as maps, map_package as packages
from test_prepared_map_package import prepared


def completed(folder):
    library=maps.validate_map_library(folder/packages.LIBRARY)
    definition=maps.map_definition(library)
    matrix=maps.map_coordinate_matrix()
    result=dict(status='appended',workflow='prepared_map_first',package_id=definition['package_id'],
        library_sha256=library['sha256'],prepared_manifest_sha256=library['manifest_sha256'],
        collection='RenderLockMap',character_collection='RenderLockChar',imported_objects=20,
        mesh_objects=10,vertices=100,map_cutting='manual',linked_libraries=0,coordinate_matrix=matrix,
        texture_relink='automatic',lighting_basis=dict(algorithm='map-lighting-basis-v1',matrix=matrix,
            vector_transports=6,fog_distance_scale=1.),
        textures=dict(status='connected',manifest_sha256=library['manifest_sha256'],referenced_images=1,
            connected_images=1,external_files=1,project_relative=True))
    return library,result


@pytest.mark.parametrize('change',['paths','images','lighting','basis','proxy'])
def test_prepared_map_cannot_publish_missing_resources_or_wrong_lighting_frame(prepared,change):
    folder,_=prepared;library,result=completed(folder)
    maps.validate_map_completion(result,library)
    if change=='paths':result['textures']['project_relative']=False
    elif change=='images':result['textures']['connected_images']=0
    elif change=='lighting':result['lighting_basis']['vector_transports']=0
    elif change=='basis':
        result['lighting_basis']['matrix']=copy.deepcopy(result['coordinate_matrix'])
        result['lighting_basis']['matrix'][0][3]=10
    else:
        proxy=dict(packages.load_package(folder)['document']['proxy'],source_map_sha256='wrong')
        with pytest.raises(RuntimeError,match='Viewport proxy:'):
            maps.validate_map_proxy_completion(proxy,result,library)
        return
    with pytest.raises(RuntimeError,match='External map:'):
        maps.validate_map_completion(result,library)


def test_cached_manifest_cannot_be_mutated_to_override_later_inventory(prepared):
    folder,_=prepared
    package=packages.load_package(folder)
    package['document']['inventory']['objects']=0
    assert packages.load_package(folder)['document']['inventory']['objects']==20


def test_project_assets_are_portable_and_an_artists_modified_complete_copy_is_not_overwritten(prepared):
    folder,_=prepared;package=packages.load_package(folder)
    output=folder.parent/'project/shot.blend'
    assets=packages.install_project_assets(package,output)
    assert assets.is_relative_to(output.parent)
    target=packages.project_asset_path(assets,package['document']['files'][1])
    assert target.read_bytes()==b'fixture pixels'
    target.write_bytes(b'artist replacement')
    with pytest.raises(ValueError,match='were changed'):
        packages.install_project_assets(package,output)
    assert target.read_bytes()==b'artist replacement'
