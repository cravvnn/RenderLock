"""Preserve recorded model identities; never animate one form as another."""
from __future__ import annotations


def model_states(entity: dict) -> list[dict]:
    """Return observed contiguous model runs, or [] for a legacy absent timeline.

    A gap is not an observed state, even if its surrounding handles match.
    Numeric resource IDs are decimal strings to preserve all 64 bits in JSON.
    """
    samples = entity.get('samples', [])
    if not any('model_handle' in sample for sample in samples):
        return []
    states = []
    previous_tick = -1
    for sample in samples:
        tick, handle = sample.get('tick'), sample.get('model_handle')
        if type(tick) is not int or tick < 0 or tick <= previous_tick:
            raise ValueError('Recorded model states require increasing nonnegative sample ticks')
        if (not isinstance(handle, str) or not handle.isascii() or not handle.isdecimal()
                or len(handle) > 20 or not 0 <= int(handle) < 1 << 64
                or str(int(handle)) != handle):
            raise ValueError(f'Invalid or missing recorded model resource ID at replay tick {tick}')
        if (states and states[-1]['model_handle'] == handle
                and states[-1]['end_tick_exclusive'] == tick):
            states[-1]['end_tick_exclusive'] = tick + 1
        else:
            states.append({'start_tick': tick, 'end_tick_exclusive': tick + 1,
                           'model_handle': handle})
        previous_tick = tick
    return states


def model_state_issue(entity: dict) -> dict | None:
    """Guard the current single-model importer, including movement-only exports."""
    states = model_states(entity)
    if not states:
        return None
    first = states[0]['model_handle']
    changed = next((state for state in states if state['model_handle'] != first), None)
    if changed:
        tick = changed['start_tick']
        return {'code': 'recorded_model_change', 'demo_tick': tick,
                'previous_model_handle': first, 'model_handle': changed['model_handle'],
                'model_states': states,
                'error': f'The recorded character changes model at replay tick {tick}. '
                         'Exporting multiple forms in one clip is not yet supported. '
                         f'Try a clip ending at tick {tick - 1}, or a clip beginning after '
                         'the transformation has a fresh recorded pose. '
                         'The first model will not be substituted for the other form.'}
    if str(entity.get('model_handle', '')) != first:
        return {'code': 'recorded_model_identity_mismatch',
                'demo_tick': states[0]['start_tick'], 'model_states': states,
                'model_handle': entity.get('model_handle'),
                'error': 'The entity model resource ID differs from its recorded sample timeline.'}
    return None
