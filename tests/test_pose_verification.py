import copy
import pytest
from agr.pose_verification import classify_unpacked_images, cloth_modified_bones, verification_status


def cloth():
    return dict(status='baked_partial',schema='agr_imported_fe_nodes_v1',body_action_edited=True,
                adjusted_cloth_bones=['hose'],affected_descendant_bones=['hose_end'])


PARENTS={'root':None,'body':'root','hose':'body','hose_end':'hose'}


def test_exact_cloth_subtree_is_reported_without_excluding_body():
    assert cloth_modified_bones(cloth(),PARENTS) == {'hose','hose_end'}
    assert cloth_modified_bones(None,PARENTS) == set()
    assert cloth_modified_bones({'body_action_edited':False},PARENTS) == set()


@pytest.mark.parametrize('mutation', [dict(schema='unknown'),dict(status='failed'),
    dict(adjusted_cloth_bones=[]),dict(adjusted_cloth_bones=['missing']),
    dict(affected_descendant_bones=[]),dict(affected_descendant_bones=['body']),
    dict(adjusted_cloth_bones=['hose','hose']),dict(affected_descendant_bones=['hose','hose_end'])])
def test_incomplete_or_widened_metadata_is_refused(mutation):
    report=cloth();report.update(mutation)
    with pytest.raises(ValueError):cloth_modified_bones(report,PARENTS)


def test_bundle_only_combines_validated_completed_parts():
    report=dict(status='baked_partial',schema='agr_decompiled_fe_proxy_bundle_v1',
                body_action_edited=True,parts=[cloth(),dict(body_action_edited=False)])
    assert cloth_modified_bones(report,PARENTS)=={'hose','hose_end'}
    report['body_action_edited']=False
    with pytest.raises(ValueError,match='Inconsistent'):cloth_modified_bones(report,PARENTS)


def test_modified_bundle_cannot_silently_omit_its_driven_bones():
    report=dict(status='baked_partial',schema='agr_decompiled_fe_proxy_bundle_v1',
                body_action_edited=True,parts=[dict(body_action_edited=False)])
    with pytest.raises(ValueError,match='lacks declared'):cloth_modified_bones(report,PARENTS)


def test_unpacked_images_keep_character_and_map_contracts_separate():
    images = ['char.png', 'map.png', 'shared.png', 'orphan.png']
    assert classify_unpacked_images(images, {'char.png', 'shared.png'},
                                    {'map.png', 'shared.png'}) == {
        'unpacked_images': ['char.png', 'orphan.png', 'shared.png'],
        'map_unpacked_images': ['map.png']}
    # Without a declared external map, unattributed images stay failures.
    assert classify_unpacked_images(images, {'char.png'}, {'map.png', 'shared.png'}) == {
        'unpacked_images': ['char.png', 'orphan.png'],
        'map_unpacked_images': ['map.png', 'shared.png']}
    # A declared external map claims unattributed post-cut debris as a limit.
    assert classify_unpacked_images(images, {'char.png', 'shared.png'}, {'map.png'},
                                    map_declared=True) == {
        'unpacked_images': ['char.png', 'shared.png'],
        'map_unpacked_images': ['map.png', 'orphan.png']}


def test_map_images_are_partial_until_explicitly_accepted_and_character_images_stay_strict():
    result = dict(max_root_error_metres=0., max_bone_matrix_error=0., unpacked_images=[],
                  map_unpacked_images=['map.png'], external_libraries=0,
                  cloth_bone_checks_not_performed=0)
    assert verification_status(result) == 'PARTIAL'
    result['map_images_external_accepted'] = True
    assert verification_status(result) == 'PASS'
    result['unpacked_images'] = ['char.png']
    assert verification_status(result) == 'FAIL'
    result['unpacked_images'] = []
    result['external_libraries'] = 1
    assert verification_status(result) == 'FAIL'
    result['external_libraries'] = 0
    result['cloth_bone_checks_not_performed'] = 1
    assert verification_status(result) == 'PARTIAL'


def test_skipped_cloth_never_becomes_full_pose_pass_and_other_failures_stay_strict():
    result=dict(max_root_error_metres=0.,max_bone_matrix_error=0.,unpacked_images=[],external_libraries=0,
                cloth_bone_checks_not_performed=0)
    assert verification_status(result)=='PASS'
    result['cloth_bone_checks_not_performed']=6
    assert verification_status(result)=='PARTIAL'
    for key,value in [('max_root_error_metres',.001),('max_bone_matrix_error',.001),
                      ('max_bone_matrix_error',float('nan')),('max_root_error_metres',float('inf')),
                      ('unpacked_images',['missing.png']),('external_libraries',1)]:
        failed=copy.deepcopy(result);failed[key]=value
        assert verification_status(failed)=='FAIL'
