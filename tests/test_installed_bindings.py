from agr.installed_assets import bind_models


def test_original_replay_ids_resolve_without_display_names():
    resources = [
        'models/heroes_staging/haze/haze.vmdl_c',
        'models/heroes_staging/gigawatt_prisoner/gigawatt_prisoner.vmdl_c',
        'models/heroes_staging/bebop/bebop.vmdl_c',
        'models/heroes_wip/lash/lash.vmdl_c',
    ]
    ids = ['8650427710956971903', '13630547586661891608',
           '4751397631629285555', '17806992914299500025']
    segment = {'entities': [{'id': str(i), 'hero_id': i + 1,
                            'hero_name': 'An unrelated display name', 'model_handle': handle}
                           for i, handle in enumerate(ids)]}
    result, errors = bind_models(segment, resources)
    assert not errors
    assert [row['resource'] for row in result] == resources


def test_repeated_hero_tracks_keep_separate_model_identity():
    segment = {'entities': [
        {'id': 'one', 'hero_id': 13, 'model_handle': '8650427710956971903'},
        {'id': 'two', 'hero_id': 13, 'model_handle': '4751397631629285555'},
        {'id': 'three', 'hero_id': 13, 'model_handle': '1'},
    ]}
    result, errors = bind_models(segment, [
        'models/heroes_staging/haze/haze.vmdl_c',
        'models/heroes_staging/bebop/bebop.vmdl_c',
    ])
    assert [x['id'] for x in result] == ['one', 'two']
    assert result[0]['resource'] != result[1]['resource']
    assert len(errors) == 1 and errors[0]['id'] == 'three'
