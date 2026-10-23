"""Keep scene preparation and Blender results visible in one coverage report."""
from __future__ import annotations


WARNING_STATUSES = frozenset({'failed', 'unavailable', 'partial'})


def recorded_birth_scope(instance, start_tick, end_tick, history_start_tick):
    """Birth timing describes recorded history, never whether a layer is active."""
    birth = instance.get('created_tick')
    if type(birth) is not int:
        return 'unknown'
    if birth > end_tick:
        return 'after_output'
    if birth >= start_tick:
        return 'in_output'
    return 'in_recovered_history' if birth >= history_start_tick else 'before_recovered_history'


def preparation_summary(report):
    """The render manifest needs diagnostics, not duplicate textures/geometry."""
    keys = ('status', 'reason', 'reason_code', 'error', 'error_type', 'stage',
            'network_instances', 'resolved_resource_instances', 'unresolved_resource_instances',
            'prepared_layer_count', 'prepared_particle_count', 'unsupported',
            'unsupported_occurrences', 'limitations', 'authored_expiry_pruning')
    return {key: report[key] for key in keys if key in report}


def effect_preparation_status(report):
    """Zero events are normal; zero results from failed resolution are not."""
    report['stage'] = 'preparation'
    if not report['network_instances']:
        report.update(status='no_events', reason_code='no_recorded_particle_instances',
                      reason='No recorded particle instances were available for this selection.')
    elif not report['resolved_resource_instances']:
        report.update(status='unavailable', reason_code='no_resolved_particle_resources',
                      reason='Recorded particle instances were found, but none of their resource IDs '
                             'matched an installed particle resource. No effects were prepared.')
    elif not report['prepared_layer_count'] and report['unsupported']:
        report.update(status='unavailable', reason_code='no_supported_particle_layers',
                      reason='Particle resources were matched, but no supported visual layers '
                             'could be prepared. See the unsupported entries for the causes.')
    elif not report['prepared_layer_count']:
        report.update(status='no_events', reason_code='no_active_supported_particles',
                      reason='No supported particles remained active within the selected tick range.')
    else:
        report['status'] = 'partial' if report['unsupported'] else 'prepared_supported_subset'
    return report


def merge_stage_coverage(preparation, rendering=None):
    """Rendering cannot erase an earlier failure or unsupported-layer summary.

    Blender effects already contain a merged report. Reusing their raw rendering
    child makes this merge idempotent when the CLI assembles its final report.
    """
    prep = dict(preparation or {'status': 'not_requested'})
    incoming = dict(rendering or {})
    if isinstance(incoming.get('rendering'), dict):
        if prep.get('status') == 'not_requested' and incoming.get('preparation'):
            prep = dict(incoming['preparation'])
        render = dict(incoming['rendering'])
    else:
        render = incoming
    if not render or render.get('status') == 'not_requested':
        return prep
    result = dict(prep)
    # Error/reason fields belong to the stage which determines the final status.
    for key, value in render.items():
        if key not in {'status', 'unsupported', 'unsupported_occurrences',
                       'reason', 'reason_code', 'error', 'error_type', 'stage', 'limitations'}:
            result[key] = value
    result['preparation'] = preparation_summary(prep)
    result['rendering'] = render
    result['unsupported'] = []
    for stage, source in (('preparation', prep), ('rendering', render)):
        for item in source.get('unsupported', []):
            result['unsupported'].append(dict(item, stage=stage))
    result['unsupported_occurrences'] = sum(
        source.get('unsupported_occurrences', sum(item.get('occurrences', 1)
                   for item in source.get('unsupported', []))) for source in (prep, render))
    result['limitations'] = list(dict.fromkeys(prep.get('limitations', []) + render.get('limitations', [])))
    severity = {'failed': 3, 'unavailable': 2, 'partial': 1}
    winner, stage = max(((prep, 'preparation'), (render, 'rendering')),
                        key=lambda item: (severity.get(item[0].get('status'), 0), item[1] == 'rendering'))
    for key in ('reason', 'reason_code', 'error', 'error_type', 'stage'):
        result.pop(key, None)
    result['status'] = winner.get('status', 'not_requested')
    result['stage'] = stage
    for key in ('reason', 'reason_code', 'error', 'error_type'):
        if key in winner:
            result[key] = winner[key]
    return result


def _cloth_entity_label(row):
    name = row.get('hero_name') or 'Entity'
    return f"{name} [{row['id']}]" if row.get('id') is not None else str(name)


def merge_secondary_motion_coverage(preparation, blender_entities=None):
    """Count completed cloth bakes, retaining per-hero failures and unsupported scope.

    A prepared definition is not a successful simulation. If Blender supplies no
    bake for a prepared entity, report that omission rather than leaving it ready.
    """
    prep = dict(preparation or {'status': 'not_requested'})
    if isinstance(prep.get('preparation'), dict):
        prep = dict(prep['preparation'])

    def rows(source):
        return [dict(row) if isinstance(row, dict) else {'id': row}
                for row in source]

    supported = rows(prep.get('supported_entities', []))
    unsupported = rows(prep.get('unsupported_entities', []))
    incoming = {str(row['id']): row for row in (blender_entities or [])
                if isinstance(row, dict) and row.get('id') is not None
                and isinstance(row.get('secondary_motion'), dict)}
    selected = {str(row['id']): row for row in supported if row.get('id') is not None}
    preparation_failures=rows(prep.get('failed_entities', []))
    failed_ids={str(row['id']) for row in preparation_failures if row.get('id') is not None}
    for key, row in incoming.items():
        if key in failed_ids:
            continue
        if row['secondary_motion'].get('status') not in ('not_requested', 'not_prepared'):
            selected.setdefault(key, {'id': row['id'], 'hero_name': row.get('hero_name')})
    baked, failed = [], list(preparation_failures)
    for key, source in selected.items():
        if key in failed_ids:
            continue
        entity = incoming.get(key, {})
        render = dict(entity.get('secondary_motion', {}))
        row = dict(source)
        row.update(render)
        if entity.get('hero_name'):
            row['hero_name'] = entity['hero_name']
        if render.get('status') in ('baked', 'baked_partial'):
            controls = render.get('dynamic_controls', source.get('dynamic_controls', []))
            count = render.get('dynamic_control_count')
            if not isinstance(count, int) or count < 0:
                count = len(controls) if isinstance(controls, (list, tuple)) else controls
            row['dynamic_control_count'] = count if isinstance(count, int) and count >= 0 else 0
            withheld = render.get('withheld_controls', [])
            row['withheld_control_count'] = len(withheld) if isinstance(withheld, (list, tuple)) else 0
            for field in ('simulation_vertex_count', 'affected_render_vertex_count', 'pinned_vertex_count'):
                value = render.get(field, 0)
                row[field] = value if isinstance(value, int) and value >= 0 else 0
            baked.append(row)
        elif render.get('status') == 'unsupported':
            unsupported.append(row)
        else:
            row['status'] = 'failed'
            row.setdefault('error', render.get('reason') or
                           'Prepared cloth has no completed Blender bake; no simulated controls were counted.')
            failed.append(row)
    partial = [row for row in baked if row.get('status') == 'baked_partial']
    result = {key: value for key, value in prep.items()
              if key not in ('status', 'supported_entities', 'unsupported_entities')}
    result.update(preparation=prep, baked_entities=baked, failed_entities=failed,
                  partially_baked_entities=partial, partial_entity_count=len(partial),
                  withheld_control_count=sum(row['withheld_control_count'] for row in baked),
                  unsupported_entities=unsupported, baked_entity_count=len(baked),
                  failed_entity_count=len(failed), unsupported_entity_count=len(unsupported),
                  dynamic_control_count=sum(row['dynamic_control_count'] for row in baked),
                  simulation_vertex_count=sum(row['simulation_vertex_count'] for row in baked),
                  affected_render_vertex_count=sum(row['affected_render_vertex_count'] for row in baked),
                  pinned_vertex_count=sum(row['pinned_vertex_count'] for row in baked),
                  stage='baking' if selected else 'preparation')
    if prep.get('status') in ('failed', 'unavailable'):
        result['status'] = 'failed'
    elif failed:
        result['status'] = 'partial' if baked else 'failed'
    elif baked:
        result['status'] = 'partial' if unsupported or partial else 'baked'
    elif unsupported or prep.get('status') == 'no_supported_entities':
        result['status'] = 'no_supported_entities'
    elif prep.get('status') == 'not_requested':
        result['status'] = 'not_requested'
    elif prep.get('status') == 'not_applicable':
        result['status'] = 'not_applicable'
    else:
        result['status'] = 'no_supported_entities'
    details = []
    if preparation_failures:
        details.append('Cloth preparation failed: ' + '; '.join(
            _cloth_entity_label(row) + ': ' + str(row.get('error', 'preparation did not complete'))
            for row in preparation_failures))
    bake_failures=[row for row in failed if row not in preparation_failures]
    if bake_failures:
        details.append('Withheld cloth bakes: ' + '; '.join(
            _cloth_entity_label(row) + ': ' + str(row.get('error', 'bake did not complete'))
            for row in bake_failures))
    if baked:
        noun = "entity" if len(baked) == 1 else "entities"
        content = [f"{len(baked)} {noun} baked"]
        if result['dynamic_control_count']:
            content.append(f"{result['dynamic_control_count']} dynamic controls")
        if result['simulation_vertex_count']:
            content.append(f"{result['simulation_vertex_count']} simulation vertices; "
                           f"{result['affected_render_vertex_count']} affected render vertices")
        details.append('; '.join(content))
    retained = [row for row in partial if row['withheld_control_count'] > 0]
    if retained:
        details.append('Cloth controls retained their recorded motion: ' + ', '.join(
            _cloth_entity_label(row) + f" ({row['withheld_control_count']} controls)" for row in retained))
    surface_partial = [row for row in partial if not row['withheld_control_count'] and not row.get('approximation_only')]
    if surface_partial:
        details.append('Additional cloth parts remain unsupported: ' + '; '.join(
            _cloth_entity_label(row) + (': ' + ', '.join(str(part) for part in row['unsupported_parts'])
                                       if row.get('unsupported_parts') else '') for row in surface_partial))
    approximate = [row for row in partial if row.get('approximation_only')]
    if approximate:
        details.append('Approximate cloth simulation: ' + ', '.join(_cloth_entity_label(row) for row in approximate)
                       + '; authored solver differences are listed in the coverage limits')
    if unsupported:
        details.append('Unsupported cloth: ' + ', '.join(_cloth_entity_label(row) for row in unsupported))
    if details:
        result['reason'] = '. '.join(details)
    elif result['status'] == 'no_supported_entities':
        result.setdefault('reason', 'No supported cloth definitions were available for the selected heroes.')
    return result


def scene_coverage_warnings(report):
    """Return concrete stage warnings while leaving a valid hero export intact."""
    warnings = list(report.get('blender', {}).get('warnings', []))
    extras = report.get('scene_extras', {})
    for name in ('particles', 'map', 'secondary_motion'):
        entry = report.get(name, extras.get(name, {}))
        if not isinstance(entry, dict):
            continue
        if entry.get('status') not in WARNING_STATUSES and not (
                name == 'secondary_motion' and entry.get('status') == 'no_supported_entities'):
            continue
        stage = entry.get('stage', 'preparation')
        detail = entry.get('error') or entry.get('reason')
        if not detail:
            if name == 'particles':
                detail = (f"{entry.get('objects', entry.get('prepared_particle_count', 0))} particle objects; "
                          f"{entry.get('unsupported_occurrences', len(entry.get('unsupported', [])))} "
                          'unsupported or unavailable layer occurrences, including retained earlier replay history. '
                          'This count is not the number of effects missing from the selected clip. See the particle coverage details.')
                timing = entry.get('unsupported_by_birth_scope')
                if isinstance(timing, dict):
                    detail += (f" Of those, {timing.get('in_output', 0)} instances were born inside this clip; "
                               f"{timing.get('in_recovered_history', 0) + timing.get('before_recovered_history', 0)} were born earlier. "
                               'Earlier birth does not establish that an effect should still be visible.')
            else:
                detail = 'See the stage coverage details.'
        # Full converter output stays in the structured stage error. Console
        # warnings remain readable even when an exporter lists thousands of files.
        detail = str(detail)
        if len(detail) > 1200:
            detail = detail[:1200] + '… Full error is retained in the coverage report.'
        label = "Cloth / secondary motion" if name == "secondary_motion" else name.capitalize()
        warnings.append(f"{label} {stage} {entry['status']}: {detail}")
    return list(dict.fromkeys(warnings))


def scene_completion_message(report):
    """Describe actual failed stages separately from supported partial content."""
    extras = report.get('scene_extras', {})
    stages = {name: report.get(name, extras.get(name, {}))
              for name in ('map', 'particles', 'secondary_motion')}
    problems = []
    missing_animation = report.get('coverage', {}).get('skeletal_animation', {}).get('unsupported_entities', 0)
    if isinstance(missing_animation, int) and missing_animation > 0:
        rows = [row for row in report.get('blender', {}).get('entities', [])
                if isinstance(row, dict) and isinstance(row.get('pose'), dict)
                and row['pose'].get('status') != 'baked']
        partial_rows = [row for row in rows if row['pose']['status']=='baked_partial']
        for row in partial_rows:
            count=len(row['pose'].get('unavailable_pose_ticks', []))
            problems.append(f"partial skeletal animation for {_cloth_entity_label(row)} ({count} unavailable recorded pose samples hidden)")
        names = [_cloth_entity_label(row) for row in rows if row not in partial_rows]
        detail = ', '.join(names) if names else f'{missing_animation} character tracks'
        if names or not partial_rows:
            problems.append('skeletal animation unavailable for ' + detail)
    for name, entry in stages.items():
        if not isinstance(entry, dict):
            continue
        if name == 'secondary_motion' and entry.get('failed_entities'):
            problems.append('cloth failed for ' + ', '.join(
                _cloth_entity_label(row) for row in entry['failed_entities']))
        elif entry.get('status') in ('failed', 'unavailable'):
            label = 'cloth' if name == 'secondary_motion' else name
            problems.append(label + ' ' + entry['status'])
    if problems:
        return 'Scene saved; ' + ', '.join(problems) + '. See the coverage report.'
    content = []
    if isinstance(stages['map'], dict) and stages['map'].get('status') in ('imported', 'appended'):
        content.append('map')
    elif report.get('external_map', {}).get('status') == 'appended':
        content.append('map')
    effects = stages['particles']
    if isinstance(effects, dict) and effects.get('objects', 0) > 0:
        content.append('supported effects')
    message = 'Scene saved' + (': ' + ' and '.join(content) + ' imported' if content else '') + '.'
    cloth = stages['secondary_motion']
    if isinstance(cloth, dict) and cloth.get('baked_entity_count', 0):
        noun = "entity" if cloth["baked_entity_count"] == 1 else "entities"
        message += f" Cloth baked for {cloth['baked_entity_count']} {noun}."
    if isinstance(cloth, dict) and cloth.get('withheld_control_count', 0):
        message += ' Some cloth controls retained their recorded motion; see coverage.'
    elif isinstance(cloth, dict) and any(not row.get('approximation_only') for row in cloth.get('partially_baked_entities', [])):
        message += ' Additional cloth parts remain unsupported; see coverage.'
    elif isinstance(cloth, dict) and cloth.get('partial_entity_count', 0):
        message += ' Cloth simulation is approximate; see coverage limits.'
    if isinstance(cloth, dict) and cloth.get('unsupported_entity_count', 0):
        message += ' Some heroes have unsupported cloth; see coverage.'
    if isinstance(effects, dict) and effects.get('status') == 'partial':
        message += ' Additional effect layers remain unsupported; see coverage.'
    elif report.get('warnings'):
        message += ' Review the coverage warnings.'
    lighting=report.get('hero_lighting',{})
    if isinstance(lighting,dict):
        routes=lighting.get('material_routes')
        count=(sum('diffuse' in row.get('applied_components',[]) for row in routes)
               if isinstance(routes,list) else lighting.get('applied_material_count',lighting.get('material_count',0)))
        if 'diffuse' in lighting.get('applied_components',[]) and count>0:
            message += f" Source character diffuse lighting applied to {count} {'material' if count==1 else 'materials'}."
    return message
