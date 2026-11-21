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


def single(command, failure, log='', *, choice_id=None):
    choices = recovery_choices(command, failure, log)
    if choice_id:
        choices = [choice for choice in choices if choice.id == choice_id]
    assert len(choices) == 1
    return choices[0]


def test_exact_hero84_cloth_failure_removes_only_cloth_switches():
    original = BASE + LIGHTING + MAP + ['--particle-probe', 'D:/p.dll', '--dotnet', 'D:/net.exe']
    command = original + ['--cloth', '--secondary-motion']
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
    # Existing partial consent is preserved without enabling another policy.
    choice = single(BASE + ['--partial', '--secondary-motion'], CLOTH, choice_id='without_cloth')
    assert choice.command == tuple(BASE + ['--partial'])
    assert choice.partial_override is None


def test_effects_choice_removes_simulator_arguments_with_values():
    retained = BASE + LIGHTING + MAP + ['--secondary-motion']
    extra = ['--particle-probe', 'D:/probe.exe', '--particle-history-ticks=36', '--particle-history-ticks', '40']
    choice = single(retained + extra, EFFECTS)
    assert choice.command == tuple(retained + ['--no-effects'])
    assert dict(choice.values_overrides) == {
        'effects_enabled': '0', 'simulate_effects': '0', 'particle_history_ticks': '0'}
    assert choice.partial_override is None
    assert 'without particle effects' in choice.description
    assert 'Export > Output' in choice.description
    # Supplied particle inputs are dropped as whole option/value units.
    extra = ['--particle-observations', '--source-lighting', '--captured-slide-inputs', '--model-constraints']
    choice = single(BASE + extra, EFFECTS)
    assert choice.command==tuple(BASE+['--no-effects'])
    assert dict(choice.values_overrides)==dict(effects_enabled='0',simulate_effects='0',
        particle_history_ticks='0',particle_observations='',captured_slide_inputs='')


def test_model_rule_omission_cannot_keep_an_input_requiring_those_rules():
    failure=dict(stage='prepare_installed_assets',error='Requested model bone rules could not be prepared: missing bone')
    command=BASE+['--model-constraints','--captured-ambient-inputs','capture.json']
    assert recovery_choices(command,failure)==[]
    assert 'supplied particle inputs require model bone rules' in recovery_summary(command,failure).lower()


def test_source_compatibility_choices_remove_only_lighting_and_its_dependencies():
    error = 'Unqualified probe decoder companion: ValveResourceFormat.dll'
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


def test_npr_failure_keeps_source_probe_lighting_and_removes_dependent_shader():
    error = 'Extended NPR BC5 source requires aligned, uncompressed mip data after DATA'
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
    ('model_coverage', MISSING_MODELS, {}),
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


def test_no_partial_retry_when_animation_was_already_opted_out():
    for flag in ('--partial', '--motion-only'):
        assert recovery_choices(BASE + [flag], {'stage': 'model_coverage', 'error': MISSING_MODELS}) == []


def test_validation_unknown_and_corrupt_failures_never_get_a_retry():
    command = BASE + LIGHTING + ['--secondary-motion', '--particle-probe', 'D:/p.exe']
    for error in ('No animation record for requested tick 80366, because the task dependencies are corrupt',
                  'Something failed while loading Source lighting, cloth and particle data'):
        failure = {'stage': 'evaluate_animation', 'error': error}
        assert recovery_choices(command, failure) == []
        assert isinstance(recovery_summary(command, failure), str)
    # A known prefix does not bypass corruption or validation.
    error = 'Installed game compatibility check failed for hero lighting before asset preparation: truncated resource'
    assert recovery_choices(BASE + LIGHTING + ['--cloth'], {'stage': 'prepare_installed_assets', 'error': error}) == []


def test_reset_only_range_has_guidance_but_no_partial_choice():
    failure = {'stage': 'evaluate_animation', 'error': 'The selected model contains only an unrecorded reset pose; '
               'choose a range ending before the reset or containing a later recorded pose.',
               'details': {'code': 'animation_reset_only_range'}}
    assert recovery_choices(BASE, failure) == []
    assert 'partial export cannot supply this pose' in recovery_summary(BASE, failure)
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
    # An empty failure uses only the final exception, not traceback source lines.
    log = ('RuntimeError: Requested cloth preparation did not complete: Hero84: unsupported layout\n'
           '  raise ValueError("NPR shader requires requalification")\n'
           'ValueError: Disk full\n')
    assert recovery_choices(BASE + LIGHTING + ['--cloth'], {}, log) == []
    choice = single(BASE + LIGHTING, {}, 'ValueError: NPR shader requires requalification\n')
    assert choice.id == 'without_npr'


def test_actual_blender_subprocess_error_uses_its_nested_failure_only_at_blender_stage():
    import subprocess
    error = subprocess.CalledProcessError(1, ['blender.exe', '--background', '--python', 'blender_scene.py'])
    failure = dict(stage='build_blender_scene', error=str(error), type=type(error).__name__,
                   blender_log_tail='ValueError: Particle simulation has unresolved diagnostics\n')
    assert single(BASE + ['--particle-probe', 'D:/p.exe'], failure).id == 'without_effects'
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
    # Incomplete or terminated argv has no retry.
    for command in (BASE + ['--character-shader'], BASE + ['--', '--cloth'], []):
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


def test_retry_argv_still_parses_as_the_same_conversion():
    from agr.cli import parser

    extra, failure = ['--particle-probe=D:/p.dll', '--dotnet=D:/net.exe', '--particle-history-ticks=36'], EFFECTS
    before = parser().parse_args((BASE + extra)[3:])
    after = parser().parse_args(recovery_choices(BASE + extra, failure)[0].command[3:])
    for name in ('demo', 'segment', 'start_tick', 'end_tick', 'heroes', 'shot_hero',
                 'output', 'overwrite', 'fps', 'assets', 'game_install', 'source2viewer',
                 'asset_cache', 'keep_work', 'model_constraints', 'material_repairs',
                 'motion_only', 'include_map', 'map_name', 'map_margin', 'character_only'):
        assert getattr(after, name) == getattr(before, name), name


def test_supplied_cloth_failures_offer_supported_cloth_first_without_broad_partial():
    from agr.cli import parser

    error = ('Requested cloth preparation did not complete: Hero 2: Imported-bone solver requires additional authored operations: m_WorldCollisionNodes, m_Effects; Hero 80: Imported-bone solver requires additional authored operations: m_WorldCollisionNodes, m_Effects; Hero 81: Generated cloth proxy lacks an explicit vertex-map identity; Hero 84: Imported-bone solver requires additional authored operations: m_WorldCollisionNodes; Hero 85: Imported-bone solver requires additional authored operations: m_Effects')
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
    # Existing partial cloth consent leaves only the without-cloth choice.
    command = BASE + ['--secondary-motion', '--partial-cloth']
    choice = single(command, CLOTH)
    assert choice.id == 'without_cloth'
    assert choice.command == tuple(BASE)


def test_cloth_report_identity_requires_the_exact_stage_and_prefix():
    error = 'Requested cloth preparation did not complete: Hero 81: Generated cloth proxy lacks an explicit vertex-map identity'
    assert recovery_choices(BASE + ['--cloth'], dict(CLOTH, stage='model_coverage', error=error)) == []
    assert recovery_choices(BASE + ['--cloth'], dict(CLOTH, error='Generated cloth proxy lacks an explicit vertex-map identity')) == []


def test_optional_bone_rule_retry_removes_only_that_requested_feature():
    command = BASE + LIGHTING + MAP + ['--secondary-motion', '--particle-probe', 'D:/probe.dll', '--dotnet', 'D:/dotnet.exe']
    failure = dict(stage='prepare_installed_assets', error='Requested model bone rules could not be prepared: models/heroes_wip/inferno/inferno.vmdl_c: Constraint bone hash is absent from model')
    before = deepcopy((command, failure))
    choice = single(command, failure)
    assert choice.id == 'without_model_constraints'
    assert choice.command == tuple(a for a in command if a != '--model-constraints')
    assert dict(choice.values_overrides) == {'model_constraints': '0'}
    assert choice.partial_override is None
    assert '--partial' not in choice.command and '--motion-only' not in choice.command
    assert 'recorded' in recovery_summary(command, failure).lower()
    assert (command, failure) == before
    assert recovery_choices(list(choice.command), failure) == []
    # The bone-rule retry does not bypass other validation.
    assert recovery_choices(BASE, dict(stage='prepare_installed_assets',
        error='Requested model bone rules could not be prepared: checksum mismatch')) == []
