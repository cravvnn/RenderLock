"""Pure, opt-in suggestions for a failed export; nothing here runs a command.

``recovery_choices(command, failure, log_text='')`` returns RecoveryChoice rows.
Pass the exact argv of the failed run, never a shell command. Each ``command``
is a new tuple preserving all arguments except the explicitly described change.
``recovery_summary`` accepts the same inputs and explains the problem even when
there is no safe suggestion. Unknown failures deliberately have no choices.

The caller must show the losses and wait for the user to choose Continue before
executing a row. ``dict(choice.values_overrides)`` updates existing GUI string
settings; ``partial_override`` updates its separate partial-export BooleanVar
only when non-None. These are display hints; the returned argv is authoritative.
No files, shell parsing, GUI imports, retries, or animation fallbacks are used.
"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class RecoveryChoice:
    id: str
    title: str
    description: str
    command: tuple[str, ...]
    values_overrides: tuple[tuple[str, str], ...] = ()
    partial_override: bool | None = None


# Consume value options as units, including --option=value. A value that happens
# to look like a switch must never be removed as if it were an option itself.
_VALUE_OPTIONS = frozenset({
    '--demo', '--segment', '--assets', '--game-install', '--asset-cache',
    '--source2viewer', '--map-name', '--map-margin', '--world-region-probe',
    '--lighting-dotnet', '--character-shader', '--probe-atlas-decoder',
    '--cubemap-decoder', '--particle-probe', '--dotnet', '--output',
    '--start-tick', '--end-tick', '--particle-history-ticks', '--fps',
    '--shot-hero', '--heroes', '--blender', '--blender-python', '--external-map',
})
_LIGHTING_SWITCHES = frozenset({
    '--source-lighting', '--source-hero-lighting', '--source-npr-lighting',
    '--partial-lighting',
})
_LIGHTING_OPTIONS = frozenset({
    '--world-region-probe', '--lighting-dotnet', '--probe-atlas-decoder',
    '--cubemap-decoder', '--character-shader',
})
_OPTIONAL_STAGES = frozenset({
    '', 'prepare_installed_assets', 'build_blender_scene',
    'validate_blender_completion',
})
_EXCEPTION_LINE = re.compile(r'^\s*(?:[\w.]*Error|[\w.]*Exception|ERROR):\s*(.+)$')


def _arguments(command):
    if not isinstance(command, (list, tuple)) or not all(isinstance(x, str) for x in command):
        raise TypeError('Pass the exact export argv as a list or tuple of strings.')
    rows = []
    index = 0
    while index < len(command):
        token = command[index]
        if token == '--':
            return []  # Appending switches after an option terminator is unsafe.
        name, separator, _ = token.partition('=')
        if name in _VALUE_OPTIONS and not separator:
            if index + 1 == len(command):
                return []  # Incomplete argv is not safe to rewrite.
            rows.append((name, (token, command[index + 1])))
            index += 2
        else:
            rows.append((name if name in _VALUE_OPTIONS or not separator else '', (token,)))
            index += 1
    return rows


def _rewrite(rows, remove=(), add=()):
    removed = frozenset(remove)
    result = tuple(token for name, tokens in rows if name not in removed for token in tokens)
    present = {name for name, _ in rows if name not in removed}
    return result + tuple(flag for flag in add if flag not in present)


def _last_error(log_text):
    errors = [match.group(1) for line in log_text.splitlines()
              if (match := _EXCEPTION_LINE.match(line))]
    return errors[-1] if errors else ''


def _evidence(failure, log_text):
    if not isinstance(failure, dict):
        raise TypeError('failure must be the failure.json dictionary (or {}).')
    stage = failure.get('stage', '')
    stage = stage if isinstance(stage, str) else 'unknown'
    error = failure.get('error', '')
    error = error.strip() if isinstance(error, str) else ''
    # A specific structured error wins over old warnings/progress in the log.
    # Only Blender's generic outer failure needs its nested final exception.
    blender_process_failure = (stage == 'build_blender_scene'
        and failure.get('type') == 'CalledProcessError'
        and error.startswith('Command ') and ' returned non-zero exit status ' in error)
    if not error or error.startswith(('Blender failed', 'Blender exited')) or blender_process_failure:
        tail = failure.get('blender_log_tail', '')
        nested = _last_error(tail) if isinstance(tail, str) else ''
        error = nested or _last_error(log_text) or error
    match = _EXCEPTION_LINE.match(error)
    if match:
        error = match.group(1)
    details = failure.get('details')
    code = details.get('code', '') if isinstance(details, dict) else ''
    return stage, error, code


def _problem(failure, log_text):
    stage, error, code = _evidence(failure, log_text)
    lowered = error.lower()
    if stage in _OPTIONAL_STAGES | {'preflight', 'validate_options'} and error.startswith('Viewport proxy: '):
        return 'map_proxy', error
    if stage in _OPTIONAL_STAGES | {'preflight', 'validate_options'} and error.startswith('External map: '):
        return 'external_map', error
    # The known report contains per-hero optional cloth errors, including an
    # unbound generated proxy's vertex-map identity. Omitting that cloth never
    # relaxes the separate core model/rig/entity identity checks.
    # Legacy AGR error lines lack a stage. Only this exact preparation prefix
    # can identify optional cloth there; a conflicting explicit stage wins.
    if stage in ('', 'validate_cloth_preparation') and error.startswith('Requested cloth preparation did not complete: '):
        return 'cloth', error
    if stage == 'validate_blender_completion' and re.fullmatch(r'.+: requested cloth failed: .+', error, re.S):
        return 'cloth', error
    if (code == 'animation_reset_only_range' or 'only an unavailable reset pose' in lowered
            or 'only an unrecorded reset pose' in lowered):
        return 'reset_only', error
    # These are data/validation failures, never consent to skip validation.
    if (code == 'animation_skeleton_mismatch'
            or any(word in lowered for word in (
                'identity', 'identities', 'checksum', 'corrupt', 'truncated',
                'out of bounds', 'out of range', 'outside skeleton',
                'outside recorded skeleton', 'array bound', 'task ', 'task/',
                'task_', 'dependency', 'dependencies', 'different replay',
                'does not match the replay', 'skeleton mismatch',
            ))):
        return 'strict', error
    if stage in _OPTIONAL_STAGES:
        if error.startswith(('Installed game compatibility check failed for NPR lighting before asset preparation: ',
                             'Material expression evaluator module requires requalification',
                             'NPR shader requires requalification',
                             'NPR texture decoder requires requalification',
                             'Extended NPR source requires exact bounded BC7 mip payload',
                             'Extended NPR source requires exact bounded BC7 or BC5 mip payload',
                             'Extended NPR BC5 source requires aligned, uncompressed mip data after DATA',
                             'Unsupported bounded NPR texture resource header',
                             'Combined NPR/unlit permutation requires qualification',
                             'Material family requires a qualified Source route')):
            return 'npr', error
        if error.startswith(('Character shader library changed or is not the supported Elagrimm library; ',
                             'Elagrimm character shader requires the qualified local .blend library')):
            return 'shader', error
        if error.startswith(('Installed game compatibility check failed for hero lighting before asset preparation: ',
                             'Installed game compatibility check failed for static map lighting before asset preparation: ',
                             'World probe layout requires requalification',
                             'Probe atlas format/mip layout requires requalification',
                             'Probe cubemap format/layout requires requalification',
                             'Unsupported probe cubemap array depth',
                             'Unsupported probe layout',
                             'Unqualified probe decoder companion: ',
                             'Installed Source lighting shader changed; requalification required',
                             'Regional exporter VRF assembly is not qualified',
                             'Source lightmap texture decoder is not qualified',
                             'Source lightmap native image decoder is not qualified')):
            return 'lighting', error
        if (error.startswith(('Particle texture conversion failed for ',
                              'Particle model/attachment providers across recorded model changes are not yet qualified',
                              'Simulator diagnostics: ', 'Simulator exit ',
                              'Particle simulation has unresolved diagnostics',
                              'Unsupported effects manifest version',
                              'Unknown prepared particle backend: ',
                              'Choose an existing AGR ParticleProbe.dll or portable ParticleProbe.exe.',
                              'Choose the .NET executable for the particle simulator DLL.',
                              'Particle simulator DLL requires .NET.'))
                or error.startswith('Blender did not complete requested effects: ')):
            return 'effects', error
        for name, kind in (('hero_lighting', 'lighting'), ('map_lighting', 'lighting'),
                           ('npr_lighting', 'npr'), ('character_shader', 'shader')):
            if error.startswith(f'Blender did not complete requested {name}: '):
                return kind, error
    if stage in ('', 'model_coverage', 'evaluate_animation'):
        if error == ('Saved model tracks contain unavailable reset pose samples; enable explicit partial export '
                     'or select a range after the reset.'):
            return 'reset', error
        if re.search(r'(?:^|: )No pose recipe is recorded for the new model at replay tick \d+\. '
                     r'Strict export cannot invent this reset pose\. Explicit partial export can '
                     r'keep both forms and hide this unavailable sample, with a coverage warning\.$', error):
            return 'reset', error
        if (re.fullmatch(r'Missing model files for \d+ of \d+ entity tracks: .+\. Add prepared hero assets, '
                         r'enter explicit Hero IDs to export a subset, or enable partial export to retain '
                         r'all selected tracks with movement placeholders\.', error)
                or (error.startswith('Saved package has no evaluated bone tracks for: ')
                    and error.endswith('. Select supported entities, enable partial export, or convert the original replay.'))):
            return 'missing', error
        # These failures occur inside evaluate_segment's per-entity try/except.
        # Explicit partial mode reports an unsupported track with movement only.
        # Missing global replay sidecars/catalogues are outside that block and
        # deliberately do not qualify.
        if stage in ('', 'evaluate_animation') and re.search(
                r'(?:^|: )(?:No raw animation state for this entity|'
                r'No animation record for requested tick \d+|'
                r'Missing decoded animation clips \(\d+\): .+|'
                r'Missing decoded skeleton .+)\Z', error):
            return 'missing', error
    return 'unknown', error


def recovery_summary(command: list[str], failure: dict, log_text: str = '') -> str:
    """Explain the failure without promising the next export will succeed."""
    _arguments(command)
    kind, _ = _problem(failure, log_text)
    return {
        'map_proxy': 'The simplified viewport map could not be created. You can explicitly retry with the full downloaded map in the viewport and render.',
        'external_map': 'The downloaded map could not be used. Review its file or explicitly continue without the map; character animation and lighting settings remain requested.',
        'cloth': 'Requested experimental cloth could not be prepared for all selected characters. '
                 'You can explicitly keep supported approximate cloth and report omitted/failed cloth, '
                 'or continue without cloth. Character models and animation remain strict.',
        'effects': 'Requested particle effects could not be prepared or rendered. '
                   'For simulation failures you can explicitly try recorded effects, accept the documented incomplete simulation, or continue without effects. Character animation stays requested.',
        'lighting': 'The requested Source lighting is incompatible with the installed data or supported layout. '
                    'You can explicitly continue without Source lighting; character animation stays requested.',
        'npr': 'The requested NPR lighting or material route is unsupported. '
               'You can explicitly continue without NPR and its character shader, retaining Source probe lighting and animation.',
        'shader': 'The optional character shader could not be used. '
                  'You can explicitly continue without that shader, retaining the other lighting and animation settings.',
        'reset': 'The model change has an unavailable recorded pose. Full character animation remains the default. '
                 'An explicit partial export can hide unavailable samples and report the missing coverage.',
        'missing': 'Selected characters are missing model files or usable recorded bone tracks. Full character animation remains '
                   'the default. Restore the matching assets or explicitly accept partial movement placeholders.',
        'reset_only': 'The selected model has no recorded pose anywhere in this range. Choose a range before the reset '
                      'or containing a later recorded pose; partial export cannot supply this pose.',
        'strict': 'The export stopped at a data or validation limit. These checks must remain enabled; '
                  'review the diagnostics and correct the input or choose a supported range.',
        'unknown': 'The export stopped without a recognized safe retry suggestion. '
                   'Review the diagnostics and settings before trying again.',
    }[kind]


def recovery_choices(command: list[str], failure: dict, log_text: str = '') -> list[RecoveryChoice]:
    """Return deterministic, explicit user choices; never execute or auto-select one."""
    rows = _arguments(command)
    if not rows:
        return []
    present = {name for name, _ in rows}
    kind, _ = _problem(failure, log_text)
    if kind == 'map_proxy' and '--map-viewport-proxy' in present and '--external-map' in present:
        return [RecoveryChoice('without_map_proxy', 'Continue with full map',
            'Retry without the simplified viewport map. The full map, character animation, effects and lighting stay requested. '
            'Setting: Map > Simplified map in viewport.',
            _rewrite(rows, {'--map-viewport-proxy'}), (('map_viewport_proxy', '0'),))]
    if kind == 'external_map' and '--external-map' in present:
        return [RecoveryChoice('without_external_map', 'Continue without downloaded map',
            'Retry without appending the downloaded map. Character models, animation, effects and lighting stay requested. '
            'Setting: Map > Include downloaded map.',
            _rewrite(rows, {'--external-map', '--map-viewport-proxy'}, ('--character-only',)),
            (('external_map_enabled', '0'), ('map_viewport_proxy', '0'), ('character_only', '1')))]
    if kind == 'cloth' and present & {'--secondary-motion', '--cloth'}:
        choices = []
        if '--partial-cloth' not in present:
            choices.append(RecoveryChoice(
                'supported_cloth', 'Retry with supported cloth only',
                'Keep supported experimental cloth and report unavailable or failed cloth as omitted. '
                'Supported cloth remains an approximation; unsupported cloth motion will be absent. '
                'This accepts cloth limitations only: character models and recorded animation keep their '
                'existing strict requirements. Review the cloth coverage report. '
                'Setting: Advanced > Output policy > Keep supported cloth if some cloth is unavailable.',
                _rewrite(rows, add=('--partial-cloth',))))
        choices.append(RecoveryChoice(
            'without_cloth', 'Continue without cloth',
            'Retry with experimental cloth / secondary motion disabled. Cloth will not be baked. '
            'Recorded character animation and all other export settings stay requested. '
            'Setting: Advanced > Character options > Cloth / secondary motion (experimental).',
            _rewrite(rows, {'--secondary-motion', '--cloth', '--partial-cloth'}), (('cloth_enabled', '0'),)))
        return choices
    if kind == 'effects' and '--no-effects' not in present:
        choices = []
        if '--particle-probe' in present:
            choices.append(RecoveryChoice('recorded_effects', 'Retry with recorded effects',
                'Use supported recorded visual layers instead of particle simulation. Simulation and its earlier history are disabled; character animation, output range and other requested features stay strict. Unsupported layers remain in coverage. Setting: Advanced > Particle simulation off; Export > Include supported particle effects on.',
                _rewrite(rows, {'--particle-probe','--dotnet','--particle-history-ticks','--partial-effects'}),
                (('effects_enabled','1'),('simulate_effects','0'),('particle_history_ticks','0'))))
            if '--partial-effects' not in present:
                choices.append(RecoveryChoice('partial_effects', 'Retry with incomplete effects',
                    'Accept the omitted control-point scalar-expression operator and unavailable simulated instances. Counts, placement or appearance may differ. If simulation produces no objects, keep supported recorded layers instead; if neither backend works, effects remain unavailable. Invalid states, identity and character animation stay strict. Review coverage.',
                    _rewrite(rows,add=('--partial-effects',))))
        choices.append(RecoveryChoice(
            'without_effects', 'Continue without effects',
            'Retry without particle effects or particle simulation, including its earlier history. '
            'Character animation and the selected output tick range stay requested. '
            'Settings: Export > Output > Include supported particle effects; Advanced > Particle simulation.',
            _rewrite(rows, {'--particle-probe', '--dotnet', '--particle-history-ticks','--partial-effects'}, ('--no-effects',)),
            (('effects_enabled', '0'), ('simulate_effects', '0'), ('particle_history_ticks', '0'))))
        return choices
    if kind == 'lighting' and present & (_LIGHTING_SWITCHES - {'--partial-lighting'}):
        return [RecoveryChoice(
            'without_source_lighting', 'Continue without Source lighting',
            'Retry without Source diffuse, hero probes/reflections, NPR indirect or the optional character shader. '
            'Their Source appearance will be absent. Character animation, effects and map selection stay requested. '
            'Settings: Lighting lab > Source lighting, hero lighting, NPR indirect and Elagrimm shader.',
            _rewrite(rows, _LIGHTING_SWITCHES | _LIGHTING_OPTIONS),
            (('source_lighting', '0'), ('source_hero_lighting', '0'), ('source_npr_lighting', '0'),
             ('character_shader', ''), ('world_region_probe', ''), ('lighting_dotnet', ''),
             ('probe_atlas_decoder', ''), ('cubemap_decoder', '')))]
    if kind == 'npr' and '--source-npr-lighting' in present:
        return [RecoveryChoice(
            'without_npr', 'Continue without NPR lighting',
            'Retry without NPR indirect and its optional character shader. Their material look will be absent. '
            'Source diffuse/probe lighting and character animation stay requested. '
            'Settings: Lighting lab > Partial NPR indirect and Elagrimm shader .blend.',
            _rewrite(rows, {'--source-npr-lighting', '--character-shader'}),
            (('source_npr_lighting', '0'), ('character_shader', '')))]
    if kind == 'shader' and '--character-shader' in present:
        return [RecoveryChoice(
            'without_character_shader', 'Continue without character shader',
            'Retry without the optional Elagrimm character shader and its artistic material look. '
            'Source/NPR lighting and character animation stay requested. '
            'Setting: Lighting lab > Elagrimm shader .blend.',
            _rewrite(rows, {'--character-shader'}), (('character_shader', ''),))]
    if kind in ('reset', 'missing') and not present & {'--partial', '--motion-only'}:
        loss = ('Unavailable reset samples will be hidden; recorded poses elsewhere are retained.' if kind == 'reset'
                else 'Characters without usable models or bone tracks may have movement placeholders without skeletal animation.')
        return [RecoveryChoice(
            'partial_animation', 'Continue with partial animation (explicit consent)',
            loss + ' This enables broad partial export, which also permits unsupported models or animation. '
            'Full character animation is no longer required; review the coverage report. '
            'Setting: Advanced > Output policy > Allow partial export / movement placeholders.',
            _rewrite(rows, add=('--partial',)), partial_override=True)]
    return []
