"""User-consented retries preserve the failed export and keep validation strict."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from agr.export_recovery import recovery_choices, recovery_summary


BASE = [
    'D:/RenderLock/python.exe', '-m', 'agr', 'convert',
    '--demo', 'D:/private/replay & recording.dem',
    '--game-install', 'B:/Steam/Deadlock', '--source2viewer', 'D:/tools/viewer.exe',
    '--asset-cache', 'D:/cache', '--blender', 'D:/Blender/blender.exe',
    '--start-tick', '80300', '--end-tick', '81100', '--fps', '64',
    '--heroes', '84,65', '--shot-hero', '84',
    '--output', 'D:/shots/$literal; output.blend', '--overwrite', '--keep-work',
    '--model-constraints', '--material-repairs',
]
CLOTH = {
    'stage': 'validate_cloth_preparation', 'type': 'RuntimeError',
    'error': 'Requested cloth preparation did not complete: Hero84: unsupported m_WorldCollisionNodes layout',
}
EFFECTS = {
    'stage': 'build_blender_scene',
    'error': 'Particle model/attachment providers across recorded model changes are not yet qualified',
}
LIGHTING = [
    '--source-lighting', '--partial-lighting', '--source-hero-lighting', '--source-npr-lighting',
    '--world-region-probe', 'D:/tools/WorldRegionProbe.dll',
    '--lighting-dotnet', 'D:/tools/dotnet.exe', '--probe-atlas-decoder', 'D:/tools/ProbeAtlasDecode.dll',
    '--cubemap-decoder', 'D:/tools/CubemapDecode.dll', '--character-shader', 'D:/look/shader.blend',
]
MAP = ['--include-map', '--map-name', 'dl_midtown', '--map-margin', '26', '--character-only']
RESET_MESSAGE = (
    'Silver (7:2@model:12): No pose recipe is recorded for the new model at replay tick 80366. '
    'Strict export cannot invent this reset pose. Explicit partial export can '
    'keep both forms and hide this unavailable sample, with a coverage warning.'
)
MISSING_MODELS = (
    'Missing model files for 1 of 2 entity tracks: Hero84 (ID 84). Add prepared hero assets, '
    'enter explicit Hero IDs to export a subset, or enable partial export to retain '
    'all selected tracks with movement placeholders.'
)
MISSING_POSES = (
    'Saved package has no evaluated bone tracks for: Hero84. Select supported entities, '
    'enable partial export, or convert the original replay.'
)


def single(command, failure, log='', *, choice_id=None):
    choices = recovery_choices(command, failure, log)
    if choice_id:
        choices = [choice for choice in choices if choice.id == choice_id]
    assert len(choices) == 1
    return choices[0]


@pytest.mark.parametrize('switches', [['--secondary-motion'], ['--cloth'], ['--cloth', '--secondary-motion']])
def test_exact_hero84_cloth_failure_removes_only_cloth_switches(switches):
    original = BASE + LIGHTING + MAP + ['--particle-probe', 'D:/p.dll', '--dotnet', 'D:/net.exe']
    command = original + switches
    before = deepcopy((command, CLOTH))
    assert [choice.id for choice in recovery_choices(command, CLOTH)] == ['supported_cloth', 'without_cloth']
    choice = single(command, CLOTH, choice_id='without_cloth')
    assert choice.id == 'without_cloth'
    assert choice.command == tuple(original)
    assert '--partial' not in choice.command
    assert '--motion-only' not in choice.command
    assert dict(choice.values_overrides) == {'cloth_enabled': '0'}
    assert choice.partial_override is None
    assert 'cloth will not be baked' in choice.description.lower()
    assert 'Advanced > Character options' in choice.description
    assert (command, CLOTH) == before
    assert single(command, CLOTH, choice_id='without_cloth') == choice
    assert 'cloth' in recovery_summary(command, CLOTH)


def test_existing_partial_consent_is_preserved_without_enabling_another_policy():
    choice = single(BASE + ['--partial', '--secondary-motion'], CLOTH, choice_id='without_cloth')
    assert choice.command == tuple(BASE + ['--partial'])
    assert choice.partial_override is None


@pytest.mark.parametrize('extra', [
    ['--particle-probe', 'D:/probe.dll', '--dotnet', 'D:/dotnet.exe', '--particle-history-ticks', '36'],
    ['--particle-probe=D:/probe.dll', '--dotnet=D:/dotnet.exe', '--particle-history-ticks=36'],
    ['--particle-probe', 'D:/probe.exe', '--particle-history-ticks=36', '--particle-history-ticks', '40'],
    [],  # Effects from a saved package do not require a new simulator.
])
def test_effects_choice_removes_simulator_arguments_with_values(extra):
    retained = BASE + LIGHTING + MAP + ['--secondary-motion']
    choice = single(retained + extra, EFFECTS, choice_id='without_effects')
    assert choice.command == tuple(retained + ['--no-effects'])
    assert dict(choice.values_overrides) == {
        'effects_enabled': '0', 'simulate_effects': '0', 'particle_history_ticks': '0'}
    assert choice.partial_override is None
    assert 'without particle effects' in choice.description
    assert 'Export > Output' in choice.description


@pytest.mark.parametrize('error', [
    'Installed game compatibility check failed for hero lighting before asset preparation: probe atlas changed',
    'Installed game compatibility check failed for static map lighting before asset preparation: shader changed',
    'World probe layout requires requalification',
    'Probe atlas format/mip layout requires requalification',
    'Probe cubemap format/layout requires requalification',
    'Unsupported probe cubemap array depth',
    'Unqualified probe decoder companion: ValveResourceFormat.dll',
])
def test_source_compatibility_choices_remove_only_lighting_and_its_dependencies(error):
    retained = BASE + MAP + ['--secondary-motion', '--particle-probe', 'D:/p.exe']
    failure = {'stage': 'prepare_installed_assets', 'error': error}
    choice = single(retained + LIGHTING, failure)
    assert choice.id == 'without_source_lighting'
    assert choice.command == tuple(retained)
    assert dict(choice.values_overrides)['source_lighting'] == '0'
    assert dict(choice.values_overrides)['character_shader'] == ''
    assert dict(choice.values_overrides)['probe_atlas_decoder'] == ''
    assert choice.partial_override is None
    assert 'Source' in recovery_summary(retained + LIGHTING, failure)


def test_equals_lighting_values_are_removed_without_losing_map_or_other_helpers():
    flags = ['--source-lighting', '--partial-lighting', '--source-hero-lighting',
             '--source-npr-lighting', '--lighting-dotnet=D:/net.exe',
             '--world-region-probe=D:/world.dll', '--probe-atlas-decoder=D:/atlas.dll',
             '--cubemap-decoder=D:/cube.dll', '--character-shader=D:/shader.blend']
    retained = BASE + MAP + ['--dotnet', 'D:/particle-net.exe', '--particle-probe', 'D:/p.dll']
    choice = single(retained + flags, {'stage': 'prepare_installed_assets',
                                     'error': 'World probe layout requires requalification'})
    assert choice.command == tuple(retained)


@pytest.mark.parametrize('error', [
    'Installed game compatibility check failed for NPR lighting before asset preparation: npr_pbr_shader changed',
    'Material expression evaluator module requires requalification',
    'NPR shader requires requalification',
    'NPR texture decoder requires requalification',
    'Extended NPR source requires exact bounded BC7 mip payload',
    'Extended NPR source requires exact bounded BC7 or BC5 mip payload',
    'Extended NPR BC5 source requires aligned, uncompressed mip data after DATA',
    'Combined NPR/unlit permutation requires qualification',
])
def test_npr_failure_keeps_source_probe_lighting_and_removes_dependent_shader(error):
    retained = BASE + MAP + LIGHTING[:-2]
    choice = single(BASE + MAP + LIGHTING, {'stage': 'prepare_installed_assets', 'error': error})
    assert choice.id == 'without_npr'
    assert choice.command == tuple(x for x in retained if x != '--source-npr-lighting')
    assert dict(choice.values_overrides) == {'source_npr_lighting': '0', 'character_shader': ''}
    assert 'Source diffuse/probe lighting' in choice.description


def test_optional_shader_failure_can_keep_npr_lighting():
    failure = {'error': 'Character shader library changed or is not the supported Elagrimm library; '
                        'choose BLENDER-Deadlock-Shader.blend from the documented revision'}
    choice = single(BASE + LIGHTING, failure)
    assert choice.id == 'without_character_shader'
    assert choice.command == tuple(BASE + LIGHTING[:-2])
    assert choice.values_overrides == (('character_shader', ''),)


@pytest.mark.parametrize(('stage', 'error', 'details'), [
    ('evaluate_animation', RESET_MESSAGE, {'code': 'animation_reset_pose_unavailable'}),
    ('model_coverage', 'Saved model tracks contain unavailable reset pose samples; '
     'enable explicit partial export or select a range after the reset.', {}),
    ('model_coverage', MISSING_MODELS, {}),
    ('model_coverage', MISSING_POSES, {}),
    ('evaluate_animation', 'Hero84 (7:2): No raw animation state for this entity', {}),
    ('evaluate_animation', 'Hero84 (7:2): No animation record for requested tick 80366', {}),
    ('evaluate_animation', 'Hero84 (7:2): Missing decoded animation clips (2): idle.vanim, run.vanim', {}),
    ('evaluate_animation', 'Hero84 (7:2): Missing decoded skeleton models/hero.vskel', {}),
])
def test_only_known_missing_models_or_reset_poses_offer_labeled_partial_consent(stage, error, details):
    command = BASE + ['--secondary-motion']
    failure = {'stage': stage, 'error': error, 'details': details}
    choice = single(command, failure)
    assert choice.id == 'partial_animation'
    assert choice.command == tuple(command + ['--partial'])
    assert choice.partial_override is True
    assert 'partial' in choice.title and 'explicit consent' in choice.title
    assert 'broad partial export' in choice.description
    assert 'Full character animation is no longer required' in choice.description
    assert 'Advanced > Output policy' in choice.description
    assert 'Full character animation remains the default' in recovery_summary(command, failure)
    if details:
        assert 'hidden' in choice.description
    else:
        assert 'placeholders' in choice.description


@pytest.mark.parametrize('flag', ['--partial', '--motion-only'])
def test_no_partial_retry_when_animation_was_already_opted_out(flag):
    assert recovery_choices(BASE + [flag], {'stage': 'model_coverage', 'error': MISSING_MODELS}) == []


@pytest.mark.parametrize('error', [
    'Replay SHA256 does not match the segment provenance',
    'Fresh animation sidecars belong to a different replay',
    'Hero84: Bone index outside skeleton: 256',
    'Mask index outside recorded skeleton',
    'Animation type ID 45 exceeds the recorded registry',
    'Unsupported recorded animation operation: NewTask (ID 72)',
    'Task dependency references an unavailable slot',
    'Animation extraction checksum mismatch: raw_animation_state.json',
    'Truncated replay data', 'Corrupt replay', 'Array index out of bounds',
    'Recorded primary graph does not match models/hero.vnmgraph',
    'Cached mapping does not match the asset skeleton',
    'Duplicate animation records for one entity and tick',
    'First recipe at tick 80366 is stale; extract earlier state through the last fresh pose',
    'No animation record for requested tick 80366, because the task dependencies are corrupt',
    'No raw animation state for this entity: input mismatch',
    'Fresh animation needs raw_animation_state.json and network_ticks.json',
    'Blender completion report character tracks differ from this job.',
    'Something failed while loading Source lighting, cloth and particle data',
])
def test_validation_and_unknown_failures_never_get_blanket_partial(error):
    command = BASE + LIGHTING + ['--secondary-motion', '--particle-probe', 'D:/p.exe']
    failure = {'stage': 'evaluate_animation', 'error': error}
    assert recovery_choices(command, failure) == []
    assert isinstance(recovery_summary(command, failure), str)


@pytest.mark.parametrize('error', [
    'Cloth preparation character identities differ from this job.',
    'Invalid cloth preparation entity coverage.',
    'Installed game compatibility check failed for hero lighting before asset preparation: truncated resource',
])
def test_known_prefix_does_not_bypass_corruption_or_validation(error):
    stage = 'validate_cloth_preparation' if 'cloth' in error.lower() else 'prepare_installed_assets'
    assert recovery_choices(BASE + LIGHTING + ['--cloth'], {'stage': stage, 'error': error}) == []


@pytest.mark.parametrize('failure', [
    {'stage': 'evaluate_animation', 'error': 'The selected model contains only an unrecorded reset pose; '
     'choose a range ending before the reset or containing a later recorded pose.',
     'details': {'code': 'animation_reset_only_range'}},
    {'stage': 'model_coverage', 'error': 'A selected model track contains only an unavailable reset pose; '
     'choose a range before the reset or containing a later recorded pose.'},
])
def test_reset_only_range_has_guidance_but_no_partial_choice(failure):
    assert recovery_choices(BASE, failure) == []
    assert 'partial export cannot supply this pose' in recovery_summary(BASE, failure)


def test_conflicting_reset_code_never_turns_a_skeleton_mismatch_into_partial():
    assert recovery_choices(BASE, {'stage': 'evaluate_animation', 'error': RESET_MESSAGE,
                                   'details': {'code': 'animation_skeleton_mismatch'}}) == []


def test_optional_option_must_have_been_requested_and_failure_must_be_at_known_stage():
    assert recovery_choices(BASE, CLOTH) == []
    assert recovery_choices(BASE + ['--cloth'], dict(CLOTH, stage='read_segment')) == []
    assert recovery_choices(BASE + ['--no-effects'], EFFECTS) == []
    assert recovery_choices(BASE, {'stage': 'prepare_installed_assets',
                                   'error': 'World probe layout requires requalification'}) == []
    assert recovery_choices(BASE + LIGHTING, {'stage': 'publish_scene',
                                             'error': 'World probe layout requires requalification'}) == []


def test_specific_failure_beats_stale_errors_or_progress_in_log():
    log = ('RuntimeError: Requested cloth preparation did not complete: Hero84: unsupported layout\n'
           'Source lighting failed; particle simulation skipped\n')
    assert recovery_choices(BASE + ['--cloth'], {'stage': 'publish_scene', 'error': 'Disk full'}, log) == []
    assert recovery_choices(BASE + ['--cloth'], {}, 'cloth failed\nparticle failed\n') == []


def test_empty_failure_uses_only_final_exception_and_not_traceback_source_lines():
    log = ('RuntimeError: Requested cloth preparation did not complete: Hero84: unsupported layout\n'
           '  raise ValueError("NPR shader requires requalification")\n'
           'ValueError: Disk full\n')
    assert recovery_choices(BASE + LIGHTING + ['--cloth'], {}, log) == []
    choice = single(BASE + LIGHTING, {}, 'ValueError: NPR shader requires requalification\n')
    assert choice.id == 'without_npr'


def test_generic_blender_failure_uses_last_nested_exception():
    failure = {'stage': 'build_blender_scene', 'error': 'Blender failed with exit code 1',
               'blender_log_tail': 'ValueError: Particle simulation has unresolved diagnostics\n'}
    choice = single(BASE + ['--particle-probe', 'D:/p.exe'], failure, choice_id='without_effects')
    assert choice.id == 'without_effects'
    assert choice.command == tuple(BASE + ['--no-effects'])


def test_actual_blender_subprocess_error_uses_its_nested_failure_only_at_blender_stage():
    import subprocess
    error = subprocess.CalledProcessError(1, ['blender.exe', '--background', '--python', 'blender_scene.py'])
    failure = dict(stage='build_blender_scene', error=str(error), type=type(error).__name__,
                   blender_log_tail='ValueError: Particle simulation has unresolved diagnostics\n')
    assert single(BASE + ['--particle-probe', 'D:/p.exe'], failure, choice_id='without_effects').id == 'without_effects'
    assert recovery_choices(BASE + ['--particle-probe', 'D:/p.exe'], dict(failure, stage='publish_scene')) == []


def test_runtime_cloth_failure_offers_cloth_only_consent_and_omission():
    failure = dict(stage='validate_blender_completion',
                   error='82:382: requested cloth failed: Cloth solver did not complete its bake')
    command = BASE + ['--secondary-motion']
    choices = recovery_choices(command, failure)
    assert [choice.id for choice in choices] == ['supported_cloth', 'without_cloth']
    assert choices[0].command == tuple(command + ['--partial-cloth'])
    assert '--partial' not in choices[0].command
    assert choices[1].command == tuple(BASE)
    assert recovery_choices(command, dict(failure, stage='evaluate_animation')) == []


def test_flag_like_option_values_are_not_removed_as_cloth_switches():
    command = BASE + ['--character-shader=--cloth', '--map-name', '--secondary-motion', '--cloth']
    assert single(command, CLOTH, choice_id='without_cloth').command == tuple(command[:-1])


@pytest.mark.parametrize('command', [BASE + ['--character-shader'], BASE + ['--', '--cloth'], []])
def test_incomplete_or_terminated_argv_has_no_retry(command):
    assert recovery_choices(command, CLOTH) == []


def test_shell_text_is_rejected_and_rows_are_immutable():
    with pytest.raises(TypeError, match='exact export argv'):
        recovery_choices('python -m agr convert --cloth', CLOTH)
    with pytest.raises(TypeError, match='exact export argv'):
        recovery_choices(BASE + [None], CLOTH)
    with pytest.raises(FrozenInstanceError):
        single(BASE + ['--cloth'], CLOTH, choice_id='without_cloth').command = ()


def test_unrecognized_or_malformed_metadata_has_no_retry():
    assert recovery_choices(BASE + LIGHTING, {'stage': [], 'error': 'World probe layout requires requalification'}) == []
    assert recovery_choices(BASE + LIGHTING, {'stage': 'prepare_installed_assets', 'error': None}) == []
    with pytest.raises(TypeError, match='failure.json dictionary'):
        recovery_choices(BASE, None)


@pytest.mark.parametrize(('extra', 'failure'), [
    (['--cloth'], CLOTH),
    (['--particle-probe=D:/p.dll', '--dotnet=D:/net.exe', '--particle-history-ticks=36'], EFFECTS),
    (LIGHTING + MAP, {'stage': 'prepare_installed_assets',
                      'error': 'World probe layout requires requalification'}),
    (LIGHTING + MAP, {'stage': 'prepare_installed_assets',
                      'error': 'NPR shader requires requalification'}),
    ([], {'stage': 'model_coverage', 'error': MISSING_MODELS}),
])
def test_retry_argv_still_parses_as_the_same_conversion(extra, failure):
    from agr.cli import parser

    before = parser().parse_args((BASE + extra)[3:])
    after = parser().parse_args(recovery_choices(BASE + extra, failure)[0].command[3:])
    for name in ('demo', 'segment', 'start_tick', 'end_tick', 'heroes', 'shot_hero',
                 'output', 'overwrite', 'fps', 'assets', 'game_install', 'source2viewer',
                 'asset_cache', 'keep_work', 'model_constraints', 'material_repairs',
                 'motion_only', 'include_map', 'map_name', 'map_margin', 'character_only'):
        assert getattr(after, name) == getattr(before, name), name


@pytest.mark.parametrize('error', [
    'Requested cloth preparation did not complete: Hero 84: Imported-bone solver requires additional authored operations: m_WorldCollisionNodes',
    'Requested cloth preparation did not complete: Hero 2: Imported-bone solver requires additional authored operations: m_WorldCollisionNodes, m_Effects; Hero 80: Imported-bone solver requires additional authored operations: m_WorldCollisionNodes, m_Effects; Hero 81: Generated cloth proxy lacks an explicit vertex-map identity; Hero 84: Imported-bone solver requires additional authored operations: m_WorldCollisionNodes; Hero 85: Imported-bone solver requires additional authored operations: m_Effects',
])
def test_supplied_cloth_failures_offer_supported_cloth_first_without_broad_partial(error):
    from agr.cli import parser

    command = BASE + LIGHTING + ['--secondary-motion']
    failure = dict(CLOTH, error=error)
    choices = recovery_choices(command, failure)
    assert [choice.id for choice in choices] == ['supported_cloth', 'without_cloth']
    supported, omitted = choices
    assert supported.title == 'Retry with supported cloth only'
    assert supported.command == tuple(command + ['--partial-cloth'])
    assert '--partial' not in supported.command
    assert '--motion-only' not in supported.command
    assert supported.partial_override is None
    assert 'omitted' in supported.description and 'approximation' in supported.description
    args = parser().parse_args(supported.command[3:])
    assert args.partial_cloth and args.secondary_motion and not args.partial and not args.motion_only
    assert omitted.command == tuple(BASE + LIGHTING)


def test_existing_partial_cloth_has_only_the_without_cloth_choice():
    command = BASE + ['--secondary-motion', '--partial-cloth']
    choice = single(command, CLOTH)
    assert choice.id == 'without_cloth'
    assert choice.command == tuple(BASE)


def test_cloth_report_identity_requires_the_exact_stage_and_prefix():
    error = 'Requested cloth preparation did not complete: Hero 81: Generated cloth proxy lacks an explicit vertex-map identity'
    assert recovery_choices(BASE + ['--cloth'], dict(CLOTH, stage='model_coverage', error=error)) == []
    assert recovery_choices(BASE + ['--cloth'], dict(CLOTH, error='Generated cloth proxy lacks an explicit vertex-map identity')) == []
