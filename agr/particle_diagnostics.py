"""Narrow, explicit consent for incomplete simulator capabilities, never data errors."""

PARTIAL_OPERATOR = 'C_OP_SetControlPointFieldToScalarExpression'
CAPABILITY_WARNING = f"Unsupported pre-emission operator class '{PARTIAL_OPERATOR}'"


def simulation_diagnostics(simulation, *, allow_partial_effects=False):
    if type(allow_partial_effects) is not bool:
        raise ValueError('Partial-effects consent must be explicit')
    warnings = simulation.get('warnings', [])
    if not isinstance(warnings, list) or any(not isinstance(row, str) or not row for row in warnings):
        raise ValueError('Particle simulation has malformed diagnostics')
    if not warnings:
        return dict(status='clean', warnings=[], omitted_operator_classes=[])
    instances = simulation.get('instances', [])
    if not isinstance(instances, list):
        raise ValueError('Particle simulation has malformed instance diagnostics')
    allowed = {CAPABILITY_WARNING}
    for instance in instances:
        if not isinstance(instance, dict) or not isinstance(instance.get('id'), str):
            continue
        prefix = instance['id'] + ': ' + CAPABILITY_WARNING
        allowed.add(prefix)
        resources = {instance.get('resource')}
        for frame in instance.get('frames', []):
            if isinstance(frame, dict):
                resources.update(system.get('name') for system in frame.get('systems', [])
                                 if isinstance(system, dict))
        for resource in resources:
            if isinstance(resource, str) and resource.endswith(('.vpcf', '.vpcf_c')):
                allowed.add(prefix + ' ' + resource)
                allowed.add(prefix + ' ' + resource.removesuffix('_c') + '_c')
    if not allow_partial_effects or any(warning not in allowed for warning in warnings):
        raise ValueError('Particle simulation has unresolved diagnostics: ' + '; '.join(warnings))
    return dict(status='partial', policy='explicit_partial_effects_v1', warnings=list(warnings),
                omitted_operator_classes=[PARTIAL_OPERATOR],
                limitation='The control-point scalar-expression operator was not simulated. Particle counts, positions or appearance may differ from the game.')
