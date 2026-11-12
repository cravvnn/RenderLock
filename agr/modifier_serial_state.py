"""Fold raw modifier deltas by serial, without asserting client effect lifetime.

The adjacent model covers this synchronous fold. Slot indices are event storage,
not modifier identity. Snapshots may be ahead of entity time and do not mutate
this exact-tick view. Only present header fields merge; no defaults fill data.
"""
from .client_modifier_writes import validate_capture,validate_view,integer,require,canonical_sha256

POLICY='agr_modifier_serial_header_state_v1'

def fold_capture(capture,*,through_tick=None):
    requested=capture.get('requested_range',{})if isinstance(capture,dict)else {}
    start,end=requested.get('start_tick'),requested.get('end_tick')
    validate_capture(capture,start,end)
    require(capture['coverage']['availability']=='available','raw modifier history is unavailable')
    if through_tick is None:through_tick=end
    require(integer(through_tick,0,end),'serial-state query tick differs')
    states={};events=[]
    counts=dict(processed_rows=0,snapshots_ignored=0,omitted_payloads=0,empty_payloads=0,removals_without_prior_state=0)
    for index,row in enumerate(capture['rows']):
        if row['tick']>through_tick:break
        counts['processed_rows']+=1
        if row['kind']=='Snapshot':counts['snapshots_ignored']+=1;continue
        if not row['payload_present']:counts['omitted_payloads']+=1;continue
        if not row['wire_bytes']:counts['empty_payloads']+=1;continue
        header=row['header'];serial=header['serial_number']
        require(serial is not None,'nonempty modifier delta lacks serial; no slot identity fallback')
        kind=header['entry_type']if header['entry_type']is not None else 1
        require(kind in (1,2),'unknown modifier entry type; no active-state default')
        if kind==2:
            state=states.pop(serial,None)
            if state is None:counts['removals_without_prior_state']+=1
            action='raw_removed'if state is not None else 'raw_remove_without_prior_state'
        else:
            previous=states.get(serial)
            fields=dict(previous['header'])if previous is not None else dict.fromkeys(header)
            fields.update((key,value)for key,value in header.items()if value is not None)
            state=dict(header=fields,
                first_active_row_index=previous['first_active_row_index']if previous is not None else index,
                first_active_tick=previous['first_active_tick']if previous is not None else row['tick'],
                last_write_row_index=index)
            states[serial]=state
            action='raw_active_update'if previous is not None else 'raw_first_active'
        events.append(dict(tick=row['tick'],row_index=index,slot=row['slot'],serial_number=serial,
            action=action,state=state))
    return dict(schema_version=1,policy=POLICY,through_tick=through_tick,
        source_capture_sha256=canonical_sha256(capture),states=[states[s]for s in sorted(states)],
        events=events,counts=counts,
        scope='Raw serial-keyed header state from packet deltas. First active observation is not a particle birth; duration, aura, owner/view visibility, client activation and effects phase are not established. Absence does not prove no active modifiers.')

def fold_retained(envelope,scene,*,through_tick=None):
    validate_view(envelope,demo_sha256=scene.get('metadata',{}).get('demo_sha256'),
        start_tick=scene.get('start_tick'),end_tick=scene.get('end_tick'))
    require(envelope['capture']is not None,'raw modifier history is unavailable from this helper')
    if through_tick is None:through_tick=scene['end_tick']
    require(integer(through_tick,0,scene['end_tick']),'serial-state query exceeds selected shot end')
    return fold_capture(envelope['capture'],through_tick=through_tick)
