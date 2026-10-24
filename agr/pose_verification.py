"""Separate recorded body-pose checks from intentionally modified cloth bones."""


def cloth_modified_bones(report, parents):
    """Return only a validated declared cloth subtree; never infer by bone name.

    The caller must report these bones as unverified, not as successful checks.
    Unknown or incomplete metadata is an error rather than a broad exclusion.
    """
    if report is None:
        return set()
    if not isinstance(report, dict):
        raise ValueError('Invalid cloth verification metadata')
    if report.get('body_action_edited', False) is not True:
        if any(p.get('body_action_edited') is True for p in report.get('parts', []) if isinstance(p, dict)):
            raise ValueError('Inconsistent cloth body-action metadata')
        return set()
    if report.get('status') not in ('baked', 'baked_partial'):
        raise ValueError('Modified cloth action lacks a completed bake report')
    if report.get('schema') == 'agr_decompiled_fe_proxy_bundle_v1':
        parts = report.get('parts')
        if not isinstance(parts, list) or not 1 <= len(parts) <= 16 or any(
                not isinstance(p, dict) or p.get('schema') == report['schema'] for p in parts):
            raise ValueError('Invalid cloth verification bundle')
        affected = set().union(*(cloth_modified_bones(p, parents) for p in parts))
        if not affected:
            raise ValueError('Modified cloth bundle lacks declared driven bones')
        return affected
    if report.get('schema') not in ('agr_imported_fe_nodes_v1', 'agr_decompiled_fe_bone_proxy_v1'):
        raise ValueError('Unknown body-modifying cloth schema')
    explicit = report.get('adjusted_cloth_bones')
    descendants = report.get('affected_descendant_bones')
    for names in (explicit, descendants):
        if (not isinstance(names, list) or any(not isinstance(n, str) or n not in parents for n in names)
                or len(names) != len(set(names))):
            raise ValueError('Invalid cloth-driven bone inventory')
    if not explicit or set(explicit) & set(descendants):
        raise ValueError('Invalid cloth-driven bone scope')
    affected = set(explicit)
    while True:
        expanded = affected | {n for n, parent in parents.items() if parent in affected}
        if expanded == affected:
            break
        affected = expanded
    if affected - set(explicit) != set(descendants):
        raise ValueError('Cloth descendant inventory differs from the rig')
    return affected


def verification_status(result):
    if (not 0 <= result['max_root_error_metres'] < .0001 or not 0 <= result['max_bone_matrix_error'] < .0001
            or result['unpacked_images'] or result['external_libraries']):
        return 'FAIL'
    return 'PARTIAL' if (result.get('cloth_bone_checks_not_performed', 0) or
        any(e.get('cloth_bones_not_verified') for e in result.get('entities', []))) else 'PASS'
