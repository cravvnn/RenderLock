"""Installed FE inputs for native Seven waist cloth and an opt-in hose prototype.

Generated proxy and fit-matrix layouts remain unsupported; each accepted layout
is validated from its authored controls and graph relations.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess

from .asset_converter import ConversionError, compiled, sha
from .vendor.kv3_text import parse_kv3


class UnsupportedCloth(ValueError):
    def __init__(self, message, *, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


HOSE_NAMES = (
    'arm_upper_L', 'arm_lower_L', 'arm_upper_R', 'arm_lower_R',
    'hose_bottom_L_backpack', 'hose_bottom_L_hand',
    'hose_bottom_R_backpack', 'hose_bottom_R_hand',
    'hose_top_L_backpack', 'hose_top_L_hand',
    'hose_top_R_backpack', 'hose_top_R_hand',
    'hose_bottom_L_mid', 'hose_bottom_R_mid', 'hose_top_L_mid', 'hose_top_R_mid',
)
SCHEMA = 'agr_haze_hoses_v1'


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise UnsupportedCloth('FE parameters must be finite numbers')
    return float(value)


def haze_definition(physics, *, resource='', source_sha256=''):
    """Validate authored inputs; preserve source units, capsules and rest frames."""
    fe = physics.get('m_pFeModel') or {}
    if tuple(fe.get('m_CtrlName', ())) != HOSE_NAMES:
        raise UnsupportedCloth('Only the authored Haze hose control layout is supported; garments and other chains remain pending')
    if (fe.get('m_nNodeCount'), fe.get('m_nStaticNodes'), fe.get('m_nFirstPositionDrivenNode')) != (16, 12, 16):
        raise UnsupportedCloth('Haze FE control partition changed')
    for key in ('m_InitPose', 'm_NodeInvMasses', 'm_NodeIntegrator'):
        if len(fe.get(key, ())) != 16:
            raise UnsupportedCloth('Haze FE array size changed: ' + key)
    for key in ('m_Tris', 'm_Quads', 'm_CtrlOffsets', 'm_CtrlSoftOffsets', 'm_FitMatrices',
                'm_Twists', 'm_Ropes', 'm_FollowNodes', 'm_LockToParent', 'm_LockToGoal',
                'm_SphereRigids', 'm_BoxRigids', 'm_CollisionPlanes'):
        if fe.get(key):
            raise UnsupportedCloth('Haze FE now uses an unsupported feature: ' + key)
    initial = []
    for p in fe['m_InitPose']:
        if len(p) != 8:
            raise UnsupportedCloth('Invalid FE initial transform')
        row = [_number(v) for v in p]
        if abs(row[3] - 1) > .001 or abs(sum(v*v for v in row[4:]) - 1) > .001:
            raise UnsupportedCloth('Non-unit FE initial scale/quaternion')
        initial.append(row)
    masses = [_number(v) for v in fe['m_NodeInvMasses']]
    if any(masses[:12]) or any(v <= 0 for v in masses[12:]):
        raise UnsupportedCloth('Haze FE static/dynamic masses changed')
    rods = []
    pairs = set()
    for r in fe.get('m_Rods', ()):
        pair = tuple(r['nNode'])
        if len(pair) != 2 or not all(isinstance(n, int) for n in pair):
            raise UnsupportedCloth('Invalid rod node indices')
        a, b = pair
        if not (4 <= a < 12 and b == 12 + (a - 4)//2) or pair in pairs:
            raise UnsupportedCloth('Haze rod topology changed')
        lo, hi = _number(r['flMinDist']), _number(r['flMaxDist'])
        length = math.dist(initial[a][:3], initial[b][:3])
        if not 0 < lo <= hi or abs(lo - length) > .002 or abs(hi - length) > .002:
            raise UnsupportedCloth('Haze rod rest length disagrees with its initial control positions')
        pairs.add(pair)
        rods.append({'nodes': [a, b], 'min': lo, 'max': hi})
    if len(rods) != 8:
        raise UnsupportedCloth('Haze hose simulation requires exactly eight authored rods')
    bases = []
    for b in fe.get('m_NodeBases', ()):
        ids = [b[k] for k in ('nNode', 'nNodeX0', 'nNodeX1', 'nNodeY0', 'nNodeY1')]
        node = ids[0]
        if not isinstance(node, int) or not 12 <= node < 16:
            raise UnsupportedCloth('Haze output basis node changed')
        first = 4 + 2 * (node - 12)
        if ids != [node, first + 1, first, node, first + 1]:
            raise UnsupportedCloth('Haze output basis topology changed')
        bases.append(dict(zip(('node', 'x0', 'x1', 'y0', 'y1'), ids)))
    if sorted(b['node'] for b in bases) != [12, 13, 14, 15]:
        raise UnsupportedCloth('Haze output bases are incomplete or duplicated')
    stray = {}
    for s in fe.get('m_AnimStrayRadii', ()):
        pair = s['nNode']
        if len(pair) != 2 or pair[0] != pair[1] or pair[0] not in range(12, 16) or pair[0] in stray:
            raise UnsupportedCloth('Haze animation-stray mapping changed')
        radius = _number(s['flMaxDist'])
        if not 0 < radius <= 10:
            raise UnsupportedCloth('Unsupported Haze animation-stray radius')
        stray[pair[0]] = radius
    if set(stray) != set(range(12, 16)):
        raise UnsupportedCloth('Each Haze midpoint needs its authored animation-stray bound')
    gravity = []
    for row in fe['m_NodeIntegrator']:
        # The initial prototype deliberately does not invent attraction semantics.
        for key in ('flPointDamping', 'flAnimationForceAttraction', 'flAnimationVertexAttraction'):
            _number(row[key])
        gravity.append(_number(row['flGravity']))
    if any(any(fe['m_NodeIntegrator'][i][key] != 0 for key in
               ('flPointDamping', 'flAnimationForceAttraction', 'flAnimationVertexAttraction'))
           for i in range(12, 16)):
        raise UnsupportedCloth('Dynamic Haze controls now require unsupported attraction/damping semantics')
    capsules = []
    for c in fe.get('m_TaperedCapsuleRigids', ()):
        node = c['nNode']
        if node not in range(4) or c.get('nFlags') != 0 or c.get('nVertexMapIndex') != 65535 or c.get('nCollisionMask') != 15:
            raise UnsupportedCloth('Haze capsule binding or collision filter changed')
        ends = [[_number(x) for x in end] for end in c['vSphere']]
        if len(ends) != 2 or any(len(e) != 4 or e[3] <= 0 for e in ends):
            raise UnsupportedCloth('Invalid Haze tapered capsule')
        capsules.append({'node': node, 'ends': ends})
    if sorted(c['node'] for c in capsules) != [0, 1, 2, 3]:
        raise UnsupportedCloth('Haze needs its four source arm collision capsules')
    return {
        'schema': SCHEMA, 'resource': resource, 'source_sha256': source_sha256,
        'control_names': list(HOSE_NAMES), 'static_count': 12, 'initial_pose': initial,
        'inverse_masses': masses, 'rods': rods, 'bases': bases, 'capsules': capsules,
        'gravity': gravity, 'stray_radii': [stray[n] for n in range(12, 16)],
        'scope': 'Four Haze hose midpoint controls; garments, ropes on other heroes and engine-parity cloth are not implemented',
        'solver': 'Deterministic position-based reconstruction from authored lengths, gravity, stray limits and arm capsules',
    }


def decode_physics(resource, provider, decoder):
    """Keep FE conversion in a separate cache operation, preserving model keys."""
    resolved = provider.resolve(compiled(resource))
    def build(temp):
        proc = subprocess.run([str(decoder.executable), '-i', str(resolved.local_path), '-b', 'PHYS'],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        if proc.returncode:
            raise ConversionError(f'PHYS decode failed for {resource}: {proc.stderr[-1500:]}')
        try:
            value = parse_kv3(proc.stdout)
        except (ValueError, IndexError) as exc:
            raise ConversionError(f'Unsupported PHYS for {resource}: {exc}') from exc
        (temp / 'physics.json').write_text(json.dumps(value), encoding='utf-8')
    path = decoder._entry('cloth_PHYS', {compiled(resource): resolved.sha256},
                          {'adapter_sha256': sha(Path(__file__))}, build)
    return json.loads((path / 'physics.json').read_text(encoding='utf-8')), resolved.sha256


def prepare_cloth_assets(segment, bindings, provider, decoder, pack, log=print, *, include_hose_prototype=False):
    """Mutate supported entities with secondary_motion_file; return full coverage."""
    pack = Path(pack)
    dest = pack / 'secondary-motion'
    dest.mkdir(parents=True, exist_ok=True)
    entities = {str(e['id']): e for e in segment['entities']}
    for entity in entities.values():
        entity.pop('secondary_motion_file', None)
    report = {'status': 'prepared', 'supported_entities': [], 'unsupported_entities': [],
              'scope': 'Seven waist panels and version-matched prepared FE proxies; all other layouts explicitly reported'}
    decoded = {}
    for binding in bindings:
        resource = binding['resource']
        entity = entities[str(binding['id'])]
        row = {'id': entity['id'], 'hero_name': entity.get('hero_name'), 'resource': resource}
        try:
            if resource not in decoded:
                physics, digest = decode_physics(resource, provider, decoder)
                from .prepared_cloth import load
                cache = getattr(provider, 'cache_dir', None)
                prepared = load(cache, compiled(resource), digest, physics) if cache is not None else None
                if prepared is not None:
                    definition = prepared
                elif tuple((physics.get('m_pFeModel') or {}).get('m_CtrlName', ())) == HOSE_NAMES:
                    if not include_hose_prototype:
                        raise UnsupportedCloth('This PHYS layout contains Haze hose controls, not a supported garment. The development hose solver is not enabled by the cloth option.')
                    definition = haze_definition(physics, resource=resource, source_sha256=digest)
                else:
                    from .cloth_definition import seven_definition
                    definition = seven_definition(physics, resource=resource, source_sha256=digest)
                target = dest / (hashlib.sha256(resource.encode()).hexdigest()[:20] + '.json')
                target.write_text(json.dumps(definition, indent=2) + '\n', encoding='utf-8')
                decoded[resource] = (target, definition)
            target, definition = decoded[resource]
            entity['secondary_motion_file'] = str(target.resolve())
            row.update(status='ready_to_bake', dynamic_controls=4 if definition['schema'] == SCHEMA else 0,
                       garment_selection_controls=6 if definition['schema'] == 'agr_seven_garments_v1' else 0,
                       schema=definition['schema'], scope=definition['scope'],
                       limitations=definition.get('limitations', []))
            report['supported_entities'].append(row)
            if definition['schema'] == SCHEMA:
                log(f"Secondary motion: prepared four experimental hose controls for {entity['id']}")
            elif definition['schema'] == 'agr_seven_garments_v1':
                log(f"Cloth: prepared Seven waist surface inputs for {entity['id']}")
            else:
                log(f"Cloth: loaded version-matched reconstructed proxy for {entity['id']}")
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            row.update(status='unsupported', reason=str(exc))
            report['unsupported_entities'].append(row)
    if not report['supported_entities']:
        report['status'] = 'no_supported_entities'
    elif report['unsupported_entities']:
        report['status'] = 'prepared_partial'
    (dest / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report
