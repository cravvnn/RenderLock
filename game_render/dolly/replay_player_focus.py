"""Resolve replay participants and sample verified player camera views."""
import struct
import math
import logging

from ._runtime_generated import FollowTarget as profile
from .attach_camera import decode_view_offset
from .follow_target import FollowTargetMonitor


LOG = logging.getLogger(__name__)


class ReplayPlayerUnavailable(RuntimeError):
    """An identified participant has no usable player pawn at this tick."""


class ReplayPlayerDeferred(RuntimeError):
    """A verified participant's mutable view did not settle within the budget."""


class ReplayPlayerPoseChanged(RuntimeError):
    """Only reviewed mutable pose bytes changed; identities remained coherent."""

    def __init__(self, changes, identity):
        super().__init__('The selected player view moved during camera sampling.')
        self.changes = changes
        self.identity = identity


POSE_SAMPLE_ATTEMPTS = 3


def resolve_participant(monitor, identifier, roster, *, evidence=None):
    """Resolve the current hero, with a verified backlink fallback.

    A spectator controller can point at its observer pawn. Fresh roster entries
    are only candidate handles: every accepted hero must independently resolve
    by serial/backpointer/type and point back to this exact controller handle.
    No model name, roster position or old pawn handle identifies a participant.
    """
    evidence = evidence if evidence is not None else {}
    try:
        return resolve_player(monitor, identifier, evidence=evidence)
    except ReplayPlayerUnavailable as absent:
        evidence['primary_unavailable'] = str(absent)
    monitor._owned()
    reads = []
    def read(address, size):
        value = monitor.memory.read(address, size)
        reads.append((address, value))
        return value
    def pointer(address):
        return struct.unpack('<Q', read(address, 8))[0]
    def uint(address):
        return struct.unpack('<I', read(address, 4))[0]
    system = pointer(monitor.base + profile.ENTITY_LIST)
    chunk = pointer(system + 8*(identifier >> 9)) if system else 0
    identity = chunk + profile.IDENTITY_STRIDE*(identifier & 0x1ff) if chunk else 0
    controller = pointer(identity) if identity else 0
    controller_handle = uint(identity + profile.IDENTITY_HANDLE) if identity else 0
    if (not controller or controller_handle & 0x7fff != identifier
            or pointer(controller + profile.ENTITY_IDENTITY) != identity
            or pointer(controller) != monitor.base + profile.CONTROLLER_VTABLE):
        raise RuntimeError('The selected player controller changed. Select the player again.')
    matches = []
    seen = set()
    players = roster.get('players', []) if isinstance(roster, dict) else []
    from .editor_wire import ROSTER_PLAYERS
    if not isinstance(players, list) or len(players) > ROSTER_PLAYERS:
        raise RuntimeError('The current player roster is invalid. Select the player again.')
    for row in players:
        handle = row.get('handle') if isinstance(row, dict) else None
        index = row.get('entity_index') if isinstance(row, dict) else None
        if (type(handle) is not int or type(index) is not int or handle in seen
                or not 0 < handle < 0xfffffffe or not 0 < index < 0x7fff
                or handle & 0x7fff != index):
            continue
        seen.add(handle)
        pawn_chunk = pointer(system + 8*(index >> 9))
        if not pawn_chunk:
            continue
        pawn_identity = pawn_chunk + profile.IDENTITY_STRIDE*(index & 0x1ff)
        pawn = pointer(pawn_identity)
        serial = uint(pawn_identity + profile.IDENTITY_HANDLE)
        if not pawn or serial != handle:
            continue  # A stale roster candidate is never permission to use it.
        if (pointer(pawn + profile.ENTITY_IDENTITY) != pawn_identity
                or pointer(pawn) not in (monitor.base + profile.PLAYER_PAWN_VTABLE,
                                        monitor.base + profile.FAMILIAR_CLONE_PAWN_VTABLE)):
            continue
        if uint(pawn + profile.PAWN_CONTROLLER) == controller_handle:
            matches.append(dict(handle=handle, entity_index=index, controller_handle=controller_handle))
    if any(monitor.memory.read(address, len(value)) != value for address, value in reads):
        raise RuntimeError('The selected player changed during camera sampling. Select them again.')
    monitor._owned()
    evidence.update(stage='participant_backlink', controller_handle=controller_handle,
                    candidates=len(matches))
    if len(matches) > 1:
        raise ReplayPlayerUnavailable('This player has more than one possible hero at this tick. Camera unchanged.')
    if not matches:
        raise ReplayPlayerUnavailable('This player has no hero at this tick. Camera unchanged. Scrub forward to the match start, then select again.')
    evidence.update(lookup='pawn_controller_backlink', pawn_handle=matches[0]['handle'])
    return matches[0]


def resolve_player(monitor, identifier, players=None, *, evidence=None):
    # DemoFile PlayerIndex is the controller entity index, not the pawn's
    # roster position. Reuse the exact-build identity fields already reviewed
    # by FollowTargetMonitor; never guess a roster index from the kill list.
    # The optional legacy picker snapshot is deliberately unused: it can be
    # two seconds old and is capped at 16 entities, including extra player pawns.
    evidence = evidence if evidence is not None else {}
    evidence.update(stage='controller_identity', player_id=identifier)
    if type(identifier) is not int or not 0 < identifier < 0x7fff:
        raise ValueError('Choose a valid replay player.')
    monitor._owned()
    reads = []

    def read(address, size):
        value = monitor.memory.read(address, size)
        reads.append((address, value))
        return value

    def pointer(address):
        return struct.unpack('<Q', read(address, 8))[0]

    def unavailable(message):
        if any(monitor.memory.read(address, len(value)) != value for address, value in reads):
            raise RuntimeError('The selected player changed during camera sampling. Select them again.')
        monitor._owned()
        raise ReplayPlayerUnavailable(message)

    system = pointer(monitor.base + profile.ENTITY_LIST)
    if not system:
        raise RuntimeError('The player entity system is unavailable.')
    chunk = pointer(system + 8 * (identifier >> 9)) if system else 0
    if not chunk:
        unavailable('This player is unavailable at this tick. Camera unchanged.')
    identity = chunk + profile.IDENTITY_STRIDE * (identifier & 0x1ff)
    controller = pointer(identity)
    handle = struct.unpack('<I', read(identity + profile.IDENTITY_HANDLE, 4))[0]
    evidence['controller_handle'] = handle
    if not controller:
        unavailable('This player is unavailable at this tick. Camera unchanged.')
    if (handle & 0x7fff != identifier
            or pointer(controller + profile.ENTITY_IDENTITY) != identity
            or pointer(controller) != monitor.base + profile.CONTROLLER_VTABLE):
        raise RuntimeError('The selected player controller changed. Select the player again.')
    pawn = struct.unpack('<I', read(controller + profile.CONTROLLER_PAWN, 4))[0]
    evidence.update(stage='pawn_identity', pawn_handle=pawn)
    index = pawn & 0x7fff
    if pawn in (0, 0xffffffff, 0xfffffffe) or not 0 < index < 0x7fff:
        unavailable('This player has no hero at this tick. Camera unchanged. Scrub forward to the match start, then select again.')
    pawn_chunk = pointer(system + 8 * (index >> 9))
    if not pawn_chunk:
        unavailable('This player has no hero at this tick. Camera unchanged. Scrub forward to the match start, then select again.')
    pawn_identity = pawn_chunk + profile.IDENTITY_STRIDE * (index & 0x1ff)
    instance = pointer(pawn_identity)
    observed_handle = struct.unpack('<I', read(pawn_identity + profile.IDENTITY_HANDLE, 4))[0]
    evidence['observed_pawn_handle'] = observed_handle
    if observed_handle != pawn:
        raise RuntimeError('The selected player pawn changed. Select the player again.')
    if not instance:
        unavailable('This player has no hero at this tick. Camera unchanged. Scrub forward to the match start, then select again.')
    if pointer(instance + profile.ENTITY_IDENTITY) != pawn_identity:
        raise RuntimeError('The selected player pawn changed. Select the player again.')
    vtable = pointer(instance)
    evidence['pawn_vtable_rva'] = vtable - monitor.base
    if vtable == monitor.base + profile.OBSERVER_PAWN_VTABLE:
        unavailable('This player has no hero at this tick. Camera unchanged. Scrub forward to the match start, then select again.')
    if vtable not in (monitor.base + profile.PLAYER_PAWN_VTABLE,
                      monitor.base + profile.FAMILIAR_CLONE_PAWN_VTABLE):
        raise RuntimeError('The selected player pawn identity differs. Select the player again.')
    evidence['stage'] = 'identity_consistency'
    if any(monitor.memory.read(address, len(value)) != value for address, value in reads):
        raise RuntimeError('The selected player changed during camera sampling. Select them again.')
    monitor._owned()
    return dict(handle=pawn, entity_index=index, controller_handle=handle)


def _verify_pose_structure(monitor, snapshot, evidence):
    if not snapshot:
        raise RuntimeError('The selected player camera identity is unavailable.')
    changes = {}
    for name,address,data in snapshot:
        current = monitor.memory.read(address,len(data))
        if current != data:
            changes[name] = dict(before_hex=data.hex(),after_hex=current.hex())
    if changes:
        evidence['structural_changes'] = changes
        raise RuntimeError('The selected player identity changed during camera sampling. Select them again.')
    monitor._owned()


def player_freecam_pose(monitor, player, fields, current_pose, *, evidence=None, identity_baseline=None,
                        structural_snapshot=None):
    """Read a fresh pawn's eye position; compose a nearby independent view."""
    evidence = evidence if evidence is not None else {}
    evidence['stage'] = 'camera_fields'
    required = ('scene_node', 'owner', 'player_origin', 'player_angles', 'eye_offset', 'eye_angles')
    if not isinstance(fields, dict) or any(type(fields.get(key)) is not int or
            not 8 <= fields[key] <= 0x8000 for key in required):
        raise RuntimeError('Player camera fields are not ready. Wait for camera setup to complete.')
    if (not isinstance(current_pose,(tuple,list)) or len(current_pose) != 7
            or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1e8 for v in current_pose)
            or not .25 <= current_pose[6] <= 8):
        raise RuntimeError('The current free-camera view is unavailable.')
    monitor._owned()
    reads = []
    def read(address, size, name):
        data = monitor.memory.read(address, size)
        reads.append((name,address,data))
        return data
    def pointer(address, name):
        return struct.unpack('<Q',read(address,8,name))[0]
    def verify_structure():
        _verify_pose_structure(monitor,reads,evidence)
    handle = player['handle']
    evidence['stage'] = 'pose_pawn_identity'
    system = pointer(monitor.base + profile.ENTITY_LIST,'entity_system')
    chunk = pointer(system + 8*((handle & 0x7fff) >> 9),'pawn_chunk') if system else 0
    if not chunk:
        raise RuntimeError('The selected player is unavailable at this tick.')
    identity = chunk + profile.IDENTITY_STRIDE*(handle & 0x1ff)
    pawn = pointer(identity,'pawn_instance')
    if (not pawn or struct.unpack('<I',read(identity+profile.IDENTITY_HANDLE,4,'pawn_handle'))[0] != handle
            or pointer(pawn+profile.ENTITY_IDENTITY,'pawn_backpointer') != identity
            or pointer(pawn,'pawn_vtable') not in (monitor.base+profile.PLAYER_PAWN_VTABLE,
                                    monitor.base+profile.FAMILIAR_CLONE_PAWN_VTABLE)):
        raise RuntimeError('The selected player changed. Select the player again.')
    node = pointer(pawn+fields['scene_node'],'scene_node')
    evidence['stage'] = 'scene_identity'
    if not node:
        verify_structure()
        monitor._owned()
        if identity_baseline is not None:
            raise RuntimeError('The selected player scene changed between camera samples. Select them again.')
        raise ReplayPlayerUnavailable('This player has no scene at this tick. Camera unchanged.')
    if pointer(node+fields['owner'],'scene_owner') != pawn:
        raise RuntimeError('The selected player scene changed. Select the player again.')
    structural = {name:data.hex() for name,_address,data in reads}
    if identity_baseline is not None and structural != identity_baseline:
        evidence['structural_changes'] = {name:dict(before_hex=identity_baseline.get(name),after_hex=value)
            for name,value in structural.items() if identity_baseline.get(name) != value}
        raise RuntimeError('The selected player scene changed between camera samples. Select them again.')
    if structural_snapshot is not None:
        structural_snapshot[:] = reads
    evidence['stage'] = 'pose_sample'
    mutable = {}
    def decoded(name, raw):
        values = decode_view_offset(raw) if name == 'eye_offset' else struct.unpack('<3f',raw)
        maximum = 1e8 if name == 'player_origin' else 36000
        if values is None or any(not math.isfinite(v) or abs(v)>maximum for v in values):
            raise RuntimeError('The selected player position is unavailable.')
        return values
    for name in ('player_origin','player_angles','eye_angles','eye_offset'):
        address = (node if name.startswith('player_') else pawn)+fields[name]
        raw = monitor.memory.read(address,36 if name == 'eye_offset' else 12)
        mutable[name] = (address,raw,decoded(name,raw))
    # Structural changes always win, even when a mutable field changed too.
    verify_structure()
    changes = {}
    for name,(address,raw,values) in mutable.items():
        current = monitor.memory.read(address,len(raw))
        current_values = decoded(name,current)  # Invalid/read failures are never retryable.
        if current != raw:
            changes[name] = dict(before=list(values),after=list(current_values),
                                 before_hex=raw.hex(),after_hex=current.hex())
    verify_structure()
    monitor._owned()
    if changes:
        raise ReplayPlayerPoseChanged(changes,structural)
    origin,angles,aim,local = (mutable[name][2] for name in
                              ('player_origin','player_angles','eye_angles','eye_offset'))
    # Source's same forward/right/up basis used by the native attach sampler.
    pitch,yaw,roll = map(math.radians,angles)
    sp,cp,sy,cy,sr,cr = math.sin(pitch),math.cos(pitch),math.sin(yaw),math.cos(yaw),math.sin(roll),math.cos(roll)
    forward=(cp*cy,cp*sy,-sp)
    right=(-sr*sp*cy+cr*sy,-sr*sp*sy-cr*cy,-sr*cp)
    up=(cr*sp*cy+sr*sy,cr*sp*sy-sr*cy,cr*cp)
    eye=tuple(origin[i]+forward[i]*local[0]+right[i]*local[1]+up[i]*local[2] for i in range(3))
    heading=math.radians(aim[1])
    x,y,z=eye[0]-160*math.cos(heading),eye[1]-160*math.sin(heading),eye[2]+24
    return dict(x=x,y=y,z=z,pitch=math.degrees(math.atan2(24,160)),yaw=aim[1],
                roll=current_pose[5],aspect_ratio=current_pose[6])


def focus_replay_player(controller, identifier, fields=None):
    evidence = dict(player_id=identifier, stage='session', tick=None)
    try:
        with controller._op_lock:
            controller._require_quiet_session('moving free camera to a player')
            controller._require_demo(require_tick=False)
            controller._check_paused_cancelled()
            bridge = controller._native_bridge()
            bridge.require_capability('camera')
            before = bridge.status()
            evidence.update(tick=before.get('tick'), paused=before.get('paused'), stage='paused_session')
            controller._require_native_demo(before)
            if not before.get('paused') or controller._recorder_active():
                raise RuntimeError('Pause the replay and finish recording before selecting a player.')
            monitor = FollowTargetMonitor(controller._session)
            try:
                def verify_context(player=None):
                    controller._check_paused_cancelled()
                    if player is not None:
                        try:
                            fresh = resolve_player(monitor,identifier,evidence=evidence)
                        except ReplayPlayerUnavailable as exc:
                            raise RuntimeError('The selected player changed during camera sampling. Select them again.') from exc
                        if fresh != player:
                            raise RuntimeError('The player or replay changed during camera sampling. Select them again.')
                    current = bridge.status()
                    controller._require_native_demo(current)
                    if (not current.get('paused') or current.get('tick') != before.get('tick')
                            or controller._recorder_active()):
                        raise RuntimeError('The player or replay changed during camera sampling. Select them again.')
                    monitor._owned()
                    controller._check_paused_cancelled()
                try:
                    player = resolve_player(monitor, identifier, evidence=evidence)
                    evidence.update(initial_controller_handle=player['controller_handle'], initial_pawn_handle=player['handle'])
                except ReplayPlayerUnavailable:
                    verify_context()
                    raise
                identity_baseline = None
                for attempt in range(1,POSE_SAMPLE_ATTEMPTS+1):
                    evidence['attempt'] = attempt
                    evidence.pop('changed_fields',None)
                    verify_context(player)
                    structural_snapshot = []
                    try:
                        pose = player_freecam_pose(monitor,player,fields,before.get('applied_pose',()),
                            evidence=evidence,identity_baseline=identity_baseline,
                            structural_snapshot=structural_snapshot)
                    except ReplayPlayerUnavailable:
                        verify_context(player)
                        raise
                    except ReplayPlayerPoseChanged as exc:
                        verify_context(player)
                        _verify_pose_structure(monitor,structural_snapshot,evidence)
                        controller._check_paused_cancelled()
                        identity_baseline = exc.identity
                        evidence.update(stage='pose_settling',changed_fields=exc.changes)
                        LOG.info('Replay player focus mutable sample changed: %s',dict(evidence))
                        if attempt == POSE_SAMPLE_ATTEMPTS:
                            raise ReplayPlayerDeferred("This player's view is still settling. Camera unchanged; try again.")
                        continue
                    verify_context(player)
                    _verify_pose_structure(monitor,structural_snapshot,evidence)
                    controller._check_paused_cancelled()
                    break
            finally:
                monitor.close()
            controller._check_paused_cancelled()
            evidence['stage'] = 'camera_handoff'
            controller.enter_native_flight(pose=pose, owner='panel')
            controller._freecam_player_evidence = dict(player_id=identifier, handle=player['handle'],
                                                     tick=before.get('tick'), pose=pose)
            LOG.info('Replay player focus completed: %s', dict(evidence, stage='complete', pose=pose))
    except ReplayPlayerDeferred as exc:
        LOG.info('Replay player focus deferred: %s',dict(evidence,reason=str(exc)))
        raise
    except ReplayPlayerUnavailable as exc:
        LOG.info('Replay player focus unavailable: %s', dict(evidence, reason=str(exc)))
        raise
    except Exception as exc:
        LOG.warning('Replay player focus rejected: %s', dict(evidence, reason=str(exc)))
        raise
