from types import SimpleNamespace

import pytest

from agr.particle_transparency import configure_particle_transparency


@pytest.mark.parametrize('previous,expected', [(8, 128), (128, 128), (256, 256)])
def test_particle_scenes_have_sufficient_transparency_without_reducing_user_settings(previous, expected):
    scene = SimpleNamespace(render=SimpleNamespace(engine='CYCLES'),
                            cycles=SimpleNamespace(transparent_max_bounces=previous))
    report = configure_particle_transparency(scene, {'objects': 727})
    assert scene.cycles.transparent_max_bounces == expected
    assert report['previous_bounces'] == previous and report['transparent_max_bounces'] == expected


@pytest.mark.parametrize('engine,effects', [('BLENDER_EEVEE', {'objects': 727}),
                                         ('CYCLES', None), ('CYCLES', {'objects': 0}),
                                         ('CYCLES', {'objects': True})])
def test_other_scenes_preserve_existing_settings(engine, effects):
    scene = SimpleNamespace(render=SimpleNamespace(engine=engine),
                            cycles=SimpleNamespace(transparent_max_bounces=8))
    assert configure_particle_transparency(scene, effects)['status'] == 'not_required'
    assert scene.cycles.transparent_max_bounces == 8
