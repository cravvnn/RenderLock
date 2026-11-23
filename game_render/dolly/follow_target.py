"""Read-only verification of the stock spectator's exact player target."""
import struct

from .follow_capabilities import FollowCapabilityMonitor
from .preload import PreloadError
from .preload import _image_bytes, game_file
from .replay_camera import ReplayCameraMonitor

from ._runtime_generated import FollowTarget as _profile

# Keep this pin independent of the shared replay-camera identity. A camera-only
# review must not enable stale player-target data. The FollowTarget group and
# observer schema, typed objects and target/health HUD consumers were reviewed
# together for build 6812; the 6813/6814 relocations, the 6817 span moves and
# the subsequent reviewed updates carry that review forward. Update only with an independent
# target review.
REVIEWED_CLIENT_SHA256 = '822c9422fde4d6b5becd0b94e21a45038d84405da659f82120c4b2835ae93c31'

TARGET_SPANS = _profile.TARGET_SPANS
# Exact stock HUD getter chain and its entity predicates. Camera mode alone
# cannot prove that health/ability children have a non-null player receiver.
HEALTH_CONTEXT_SPANS = _profile.HEALTH_CONTEXT_SPANS
# Reviewed exact-build vtables. Some heroes use the familiar/clone pawn class
# for their selected target, and the local pawn can be either the observer
# pawn or a player pawn; both derive from CBasePlayerPawn and expose the same
# observer-services member used below.
CONTROLLER_VTABLE = _profile.CONTROLLER_VTABLE
OBSERVER_PAWN_VTABLE = _profile.OBSERVER_PAWN_VTABLE
PLAYER_PAWN_VTABLE = _profile.PLAYER_PAWN_VTABLE
FAMILIAR_CLONE_PAWN_VTABLE = _profile.FAMILIAR_CLONE_PAWN_VTABLE
SERVICES_VTABLES = _profile.SERVICES_VTABLES
CONTROLLER_FINAL_VTABLE = _profile.CONTROLLER_FINAL_VTABLE
# Build 6745: C_BasePlayerPawn::m_pObserverServices schema field and both
# stock pawn getters (0x815ee0/0x815f00) agree on +0xe98. The old +0xe40
# field no longer holds observer services. Audit those getters for Follow too.
OBSERVER_SERVICES_OFFSET = _profile.OBSERVER_SERVICES_OFFSET


class FollowTargetMonitor(FollowCapabilityMonitor):
    """Uses the same exact-build/session gates as the rig capability sampler."""

    def __init__(self, session):
        # Include the separately reviewed observer getters in the code audit.
        from pathlib import Path
        from .preload import _image_bytes
        super().__init__(session)
        try:
            path = Path(self.command[0]).resolve().parents[2] / 'citadel/bin/win64/client.dll'
            data, digest = game_file(path)
            if digest != REVIEWED_CLIENT_SHA256:
                raise PreloadError('Game Follow is not reviewed for this game update. '
                                   'Its camera profile still belongs to the previous review.')
            for rva, size in TARGET_SPANS:
                if self.memory.read(self.base + rva, size) != _image_bytes(data, rva, size):
                    raise PreloadError('Game Follow observer code differs from the reviewed build.')
        except BaseException:
            self.close()
            raise

    def sample_health_hud_context(self):
        """Allow only the reviewed spectator-to-selected-player HUD branch."""
        self._owned()
        if self.memory is None:
            raise PreloadError('Game Follow target monitor is closed.')
        for rva, size in HEALTH_CONTEXT_SPANS:
            if self.memory.read(self.base + rva, size) != _image_bytes(self._client_data, rva, size):
                raise PreloadError('Health HUD player getter code differs from the reviewed build.')
        before = ReplayCameraMonitor.sample(self)
        if not before.get('coherent') or not before.get('ready'):
            raise PreloadError('Health HUD requires a settled stock gameplay camera.')
        # The stock HUD getter rejects observer mode4; unlike Follow, it does
        # not require chase2/3. Still require a valid target/controller below.
        result = self.sample_target(require_chase=False, _require_health_context=True)
        after = ReplayCameraMonitor.sample(self)
        if not after.get('coherent') or not after.get('ready') or after != before:
            raise PreloadError('Health HUD stock camera changed during verification.')
        return result

    def sample_selection_context(self):
        """Prove the local observer can select; never authorize HUD reveal."""
        return self.sample_target(require_chase=False, _selection_only=True)

    def sample_target(self, *, require_chase=True, _require_health_context=False, _selection_only=False):
        self._owned()
        if self.memory is None:
            raise PreloadError('Game Follow target monitor is closed.')
        reads = []

        def read(address, size):
            value = self.memory.read(address, size)
            reads.append((address, value))
            return value

        def pointer(address):
            return struct.unpack('<Q', read(address, 8))[0]

        def uint(address):
            return struct.unpack('<I', read(address, 4))[0]

        def require_type(address, rvas, label):
            observed = pointer(address) if address else 0
            if not address or observed not in tuple(self.base + rva for rva in rvas):
                got = hex(observed - self.base) if observed else 'null'
                raise PreloadError(
                    f'Game Follow {label} object type differs from the reviewed build (got {got}).')

        entity_system = pointer(self.base + _profile.ENTITY_LIST)
        if not entity_system:
            raise PreloadError('Game Follow entity system is unavailable.')

        def resolve(handle):
            index = handle & 0x7fff
            if handle in (0xffffffff, 0xfffffffe) or index >= 0x7fff:
                raise PreloadError('Game Follow observer handle is unavailable.')
            chunk = pointer(entity_system + 8 * (index >> 9))
            if not chunk:
                raise PreloadError('Game Follow observer identity chunk is unavailable.')
            identity = chunk + _profile.IDENTITY_STRIDE * (index & 0x1ff)
            if uint(identity + _profile.IDENTITY_HANDLE) != handle:
                raise PreloadError('Game Follow observer handle serial differs.')
            instance = pointer(identity)
            if not instance or pointer(instance + _profile.ENTITY_IDENTITY) != identity:
                raise PreloadError('Game Follow observer identity backpointer differs.')
            return instance

        controller = pointer(self.base + _profile.CONTROLLER)
        require_type(controller, (CONTROLLER_VTABLE,), 'observer controller')
        pawn = resolve(uint(controller + _profile.CONTROLLER_PAWN))
        require_type(pawn, (OBSERVER_PAWN_VTABLE, PLAYER_PAWN_VTABLE), 'observer pawn')
        observer_pawn = pointer(pawn) == self.base + OBSERVER_PAWN_VTABLE
        if _require_health_context and not observer_pawn:
            raise PreloadError(
                'Health HUD requires the reviewed spectator pawn; reveal deferred.')
        if _require_health_context:
            if (read(controller + _profile.NETWORK_STATE, 1)[0] in (2, 3)
                    or read(pawn + _profile.NETWORK_STATE, 1)[0] in (2, 3)
                    or pointer(self.base + OBSERVER_PAWN_VTABLE + _profile.OBSERVER_PREDICATE_SLOT) != self.base + _profile.OBSERVER_PREDICATE):
                raise PreloadError('Health HUD requires the reviewed spectator player branch.')
        services = pointer(pawn + OBSERVER_SERVICES_OFFSET)
        require_type(services, SERVICES_VTABLES, 'observer services')
        vtable = pointer(services)
        if (pointer(vtable + _profile.OBSERVER_MODE_SLOT) != self.base + _profile.GET_OBSERVER_MODE
                or pointer(vtable + _profile.OBSERVER_TARGET_SLOT) != self.base + _profile.GET_OBSERVER_TARGET):
            raise PreloadError('Game Follow observer getters differ from the reviewed build.')
        mode = read(services + _profile.OBSERVER_MODE, 1)[0]
        if not _selection_only and (mode not in (0, 1, 2, 3) or (require_chase and mode not in (2, 3))):
            error = PreloadError(f'Game Follow requires a selected player chase view (observer mode {mode}).')
            error.observer_mode = mode
            raise error
        handle = uint(services + _profile.OBSERVER_TARGET)
        if _selection_only:
            pass  # Previous target may be absent/recycled; it will not be used.
        elif handle not in (0xffffffff, 0xfffffffe):
            target = resolve(handle)
            require_type(target, (PLAYER_PAWN_VTABLE, FAMILIAR_CLONE_PAWN_VTABLE),
                         'selected player')
            player_pawn = pointer(target) == self.base + PLAYER_PAWN_VTABLE
            if _require_health_context and not player_pawn:
                raise PreloadError(
                    'Health HUD requires the reviewed player pawn; reveal deferred.')
            if _require_health_context:
                if (pointer(self.base + PLAYER_PAWN_VTABLE + _profile.PLAYER_PREDICATE_SLOT) != self.base + _profile.PLAYER_PREDICATE
                        or pointer(self.base + PLAYER_PAWN_VTABLE + _profile.PLAYER_DATA_PREDICATE_SLOT) != self.base + _profile.PLAYER_DATA_PREDICATE):
                    raise PreloadError('Health HUD player predicates differ from the reviewed build.')
                target_controller = resolve(uint(target + _profile.PAWN_CONTROLLER))
                require_type(target_controller, (CONTROLLER_VTABLE,), 'selected player controller')
                if (pointer(self.base + CONTROLLER_VTABLE + _profile.CONTROLLER_PREDICATE_SLOT) != self.base + _profile.CONTROLLER_PREDICATE
                        # Final network-bound type, not the temporary base
                        # vtable installed earlier inside the constructor.
                        or pointer(target_controller + _profile.CONTROLLER_FINAL_DATA) != self.base + CONTROLLER_FINAL_VTABLE):
                    raise PreloadError('Health HUD player data receiver differs from the reviewed build.')
        elif require_chase or _require_health_context:
            raise PreloadError('Game Follow observer target is unavailable.')
        if any(self.memory.read(address, len(value)) != value for address, value in reads):
            raise PreloadError('Game Follow observer target changed during verification.')
        self._owned()
        if _selection_only:
            return {'observer_mode': mode, 'previous_target_handle': handle}
        return {'handle': handle, 'entity_index': handle & 0x7fff, 'mode': mode}
