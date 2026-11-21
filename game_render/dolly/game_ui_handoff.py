"""Replay UI, spectator-hero and own-health-panel handoff shared by the controller.

Methods moved verbatim out of the controller into a mixin so this stateful
handoff owns its own module while remaining on the Controller instance. The
controller re-exports the extracted constant for existing callers and tests.
"""
from __future__ import annotations

import logging
import time

from .camera_commands import numeric
from .convar_response import read_cvar_value

LOG = logging.getLogger("dolly")
OWN_HEALTH_HUD = "citadel_hud_hide_own_health"


class GameUiHandoffMixin:
    def toggle_game_ui(self, enabled=None, *, _preserve_participant=False):
        """Hand camera/mouse ownership to the game's replay UI, or return."""
        with self._op_lock:
            enabled = not self._game_ui_visible if enabled is None else enabled
            if not isinstance(enabled, bool):
                raise ValueError("Game UI visibility must be a boolean.")
            if not enabled:
                # Refuse unverified return before changing camera/input owner,
                # hiding the console or taking new HUD restoration snapshots.
                self._require_probe()
            self._require_demo(require_tick=False)
            # Deadlock has no registered `demoui` command in the reviewed build.
            # These readable cvars control the replay HUD and its real cursor;
            # explicit values also survive manual UI changes and delayed events.
            self._remember_game_ui_settings()
            bridge = self._native_bridge()
            configure = getattr(bridge, "configure_editor", None)
            if callable(configure):
                # Keep ordinary game input available until native flight is
                # actually ready on return; never optimistically enable flight.
                configure(owner="game_ui")
            if self._console_open:
                self.toggle_console(enabled=False)
                if callable(configure):
                    configure(owner="game_ui")
            if enabled:
                if _preserve_participant:
                    self._halt(native_action="release", _preserve_participant=True)
                else:
                    self._halt(native_action="release")
                self._invalidate_paused_camera()
                restoration_error = self._restore_playback_settings()
                if restoration_error:
                    raise RuntimeError(restoration_error)
                self._restore_spectator_view()
                hero_pending = False
                try:
                    self._restore_spectator_hero()
                except (RuntimeError, ValueError, OSError) as exc:
                    # A stock HUD context that cannot be verified must not block
                    # the replay-UI return; keep the saved hero pending instead.
                    LOG.warning("Saved spectator hero handoff remains pending: %s", exc)
                    hero_pending = True
                values = {"citadel_hud_visible": 1, "citadel_hide_replay_hud": 0, "hud_free_cursor": 1}
                if OWN_HEALTH_HUD in self._game_ui_restore:
                    values[OWN_HEALTH_HUD] = self._game_ui_restore[OWN_HEALTH_HUD]
                if hero_pending:
                    values = self._defer_health_reveal(values, keep_hud=True)
                    health_pending = True
                else:
                    values, health_pending = self._safe_health_hud_values(values, keep_hud=True)
                    values, deferred = self._require_own_health_hud(values, keep_hud=True)
                    health_pending = health_pending or deferred
                self._request("; ".join(name + " " + numeric(value) for name, value in values.items()))
                self._verify_game_ui_values(values)
                self._game_ui_visible = True
                self._message("Deadlock owns the camera and replay UI. Select a hero, then return to Dolly editing."
                              + (" Health panel restoration awaits a valid hero view." if health_pending else ""))
                return self.status()
            camera_live = (bridge is not None and bridge.status().get("state") == "armed")
            if self._game_ui_visible or not (self._native_active and self._native_manual and camera_live):
                self.enter_native_flight()
            else:
                self._hide_game_ui()
                if callable(configure):
                    configure(owner="flight")
            self._game_ui_visible = False
            return self.status()

    def _detach_spectator_view(self):
        """Detach game hero effects only after native has captured the view.

        Hiding the HUD and overriding the camera pose do not leave PlayerView:
        the selected hero can still drive death effects behind Dolly's camera.
        Switching first would lose the hero view used to seed a new camera.
        POV recording deliberately never calls this helper.
        """
        self._remember_spectator_hero()
        # Seeking or applying a pose can bypass playback HUD preparation.
        # Suppress the ability container before invalidating its stock player.
        self._suppress_own_health_hud()
        name = "citadel_spectator_mode"
        current = read_cvar_value(name, self._request(name))
        if current not in (0, 1, 2, 3):
            raise RuntimeError("The game's spectator mode is unrecognized; camera handoff stopped.")
        self._game_ui_restore.setdefault(name, current)
        if current != 1:
            self._request(name + " 1")
            self._verify_game_ui_values({name: 1})

    def _restore_spectator_view(self):
        """Return F9 to the user's selected viewing mode, retaining its target."""
        name = "citadel_spectator_mode"
        if name in self._game_ui_restore:
            self._suppress_own_health_hud()
            value = self._game_ui_restore[name]
            self._request(name + " " + numeric(value))
            self._verify_game_ui_values({name: value})
            del self._game_ui_restore[name]

    def _remember_spectator_hero(self):
        """Retain hero intent, never an entity handle from before reload."""
        command = getattr(self._session, 'command', ())
        bridge = self._native_bridge()
        if (self._game_hero_restore is not None or not isinstance(command, (tuple, list)) or not command
                or not callable(getattr(bridge, 'editor_roster', None))):
            return
        from .follow_target import FollowTargetMonitor
        monitor = None
        try:
            monitor = FollowTargetMonitor(self._session)
            selected = monitor.sample_target(require_chase=False)
            roster = bridge.editor_roster()
            matches = [p for p in (roster or {}).get('players', [])
                       if p.get('handle') == selected['handle'] and p.get('model_path')]
            if len(matches) == 1:
                self._game_hero_restore = (self._session, self._demo, matches[0]['model_path'])
        except (RuntimeError, ValueError, OSError) as exc:
            LOG.info('No verified spectator hero to preserve: %s', exc)
        finally:
            if monitor is not None:
                monitor.close()

    def _restore_spectator_hero(self):
        """Explicit F9/Stop return; automatic shot completion never calls it."""
        if self._game_hero_restore is None:
            return
        session, demo, model_path = self._game_hero_restore
        if self._session is not session or self._demo != demo:
            raise RuntimeError('Saved spectator hero belongs to a different replay session.')
        if self._native_active or self._native_manual:
            raise RuntimeError('Release Dolly camera before restoring the game hero.')
        self._require_demo(require_tick=False)
        from .follow_target import FollowTargetMonitor
        bridge = self._native_bridge()
        def fresh_player():
            roster = bridge.editor_roster()
            matches = [p for p in (roster or {}).get('players', []) if p.get('model_path') == model_path]
            if not (roster or {}).get('available') or len(matches) != 1:
                raise RuntimeError('Saved spectator hero is absent or ambiguous in the current replay.')
            row = matches[0]
            handle, index = row.get('handle'), row.get('entity_index')
            if (type(handle) is not int or type(index) is not int
                    or handle in (0xffffffff, 0xfffffffe) or not 0 < index < 0x7fff
                    or handle & 0x7fff != index or not row.get('model')):
                raise RuntimeError('Saved spectator hero has no verified current identity.')
            return row
        monitor = FollowTargetMonitor(session)
        try:
            monitor.sample_selection_context()
            expected = fresh_player()
            # Keep all HUD children asleep while the stock target setter runs.
            self._suppress_own_health_hud()
            self._request('spec_target ' + str(expected['entity_index']))
            deadline = time.monotonic() + 5
            last_error = None
            while True:
                self._require_demo(require_tick=False)
                current = fresh_player()
                if any(current.get(key) != expected.get(key) for key in ('handle', 'entity_index', 'model', 'model_path')):
                    raise RuntimeError('Saved spectator hero identity changed during handoff.')
                try:
                    selected = monitor.sample_health_hud_context()
                    if selected['handle'] != expected['handle']:
                        raise RuntimeError('The game did not select the saved spectator hero.')
                    self._game_hero_restore = None
                    return
                except (RuntimeError, ValueError, OSError) as exc:
                    last_error = exc
                    text = str(exc).lower()
                    # A reviewed-build/predicate/identity mismatch is deterministic
                    # and cannot settle; do not stall the F9 return for the whole
                    # deadline when the profile simply does not match this build.
                    if ("reviewed build" in text or "predicates differ" in text
                            or "object type differs" in text):
                        raise RuntimeError('Saved spectator hero handoff remains pending: ' + str(exc)) from exc
                if time.monotonic() >= deadline:
                    raise RuntimeError('Saved spectator hero handoff remains pending: ' + str(last_error))
                time.sleep(.05)
        finally:
            monitor.close()

    def _remember_game_ui_settings(self):
        names = ("citadel_hud_visible", "citadel_hide_replay_hud", "hud_free_cursor")
        missing = [name for name in names if name not in self._game_ui_restore]
        # Validate all controls before releasing a camera or writing the HUD.
        # Only missing originals are saved: repeated handoffs must never
        # replace the baseline with temporary editing/playback values.
        output = self._request("; ".join(names))
        current = {name: read_cvar_value(name, output) for name in names}
        own_health = self._own_health_hud_value()
        if own_health is not None:
            current[OWN_HEALTH_HUD] = own_health
            if OWN_HEALTH_HUD not in self._game_ui_restore:
                missing.append(OWN_HEALTH_HUD)
        # Take one complete snapshot, before any UI write or camera release.
        # A playing shot's temporary hidden values are not the restore target.
        self._game_ui_restore.update({name: self._playback_restore.get(name, value)
                                      for name, value in current.items() if name in missing})

    def _hide_game_ui(self):
        # Hiding only the replay controls leaves the spectated hero HUD on
        # screen. Hide the full game HUD as well; Dolly's DX11 panel is separate.
        values = {"citadel_hud_visible": 0, "citadel_hide_replay_hud": 1, "hud_free_cursor": 0}
        if OWN_HEALTH_HUD in self._game_ui_restore:
            values[OWN_HEALTH_HUD] = 1
        values, _ = self._require_own_health_hud(values)
        self._request("; ".join(name + " " + numeric(value) for name, value in values.items()))
        self._verify_game_ui_values(values)

    def _verify_game_ui_values(self, values):
        output = self._request("; ".join(values))
        for name, expected in values.items():
            if read_cvar_value(name, output) != expected:
                raise RuntimeError("The game did not apply " + name + ". Retry the replay UI control.")

    def _own_health_hud_value(self):
        """Optional exact-build panel control, independent of health renderers."""
        command = getattr(self._session, 'command', ())
        if not isinstance(command, (tuple, list)) or not command:
            return None
        from .follow_capabilities import FollowCapabilityMonitor
        monitor = None
        try:
            monitor = FollowCapabilityMonitor(self._session)
            return monitor.sample_own_health()['value']
        except (RuntimeError, ValueError, OSError) as exc:
            LOG.info("Optional health HUD panel control unavailable: %s", exc)
            return None
        finally:
            if monitor is not None:
                monitor.close()

    def _suppress_own_health_hud(self):
        # This collapses abilities as well as health. Its lifetime is camera
        # ownership, not one shot; keep the first outer original for handoff.
        value = self._own_health_hud_value()
        if value is not None:
            self._game_ui_restore.setdefault(OWN_HEALTH_HUD, value)
            # Collapse alone does not stop stock ability child updates. Hide
            # the main/replay HUD before changing its player/camera context.
            for name, hidden in (('citadel_hud_visible', 0), ('citadel_hide_replay_hud', 1)):
                current = read_cvar_value(name, self._request(name))
                self._game_ui_restore.setdefault(name, self._playback_restore.get(name, current))
                if current != hidden:
                    self._request(name + ' ' + numeric(hidden))
                    self._verify_game_ui_values({name: hidden})
            if value != 1:
                self._request(OWN_HEALTH_HUD + ' 1')
                self._verify_game_ui_values({OWN_HEALTH_HUD: 1})

    def _require_own_health_hud(self, values, *, keep_hud=False):
        if OWN_HEALTH_HUD in values and self._own_health_hud_value() is None:
            raise RuntimeError('Health HUD panel verification is unavailable; saved settings remain pending.')
        if (values.get(OWN_HEALTH_HUD) == 0 or
                ((values.get('citadel_hud_visible') == 1 or values.get('citadel_hide_replay_hud') == 0) and
                 (OWN_HEALTH_HUD in self._game_ui_restore or self._own_health_hud_value() is not None))):
            try:
                self._verify_health_hud_handoff()
            except (RuntimeError, ValueError, OSError) as exc:
                # Dolly's own mode handoff can transiently expose an invalid
                # stock player context. Keep the panel hidden and retry later.
                LOG.info('Health panel reveal deferred: %s', exc)
                return self._defer_health_reveal(values, keep_hud=keep_hud), True
        return dict(values), False

    def _verify_health_hud_handoff(self):
        """Expose ability UI only after a coherent stock player handoff."""
        if self._native_active or self._native_manual:
            raise RuntimeError('Dolly still owns the camera; health panel restoration remains pending.')
        self._require_demo(require_tick=False)
        from .follow_target import FollowTargetMonitor
        monitor = None
        try:
            monitor = FollowTargetMonitor(self._session)
            monitor.sample_health_hud_context()
        finally:
            if monitor is not None:
                monitor.close()

    def _safe_health_hud_values(self, values, *, keep_hud=False):
        """Keep an unsafe reveal hidden, preserving the outer original."""
        values = dict(values)
        reveal_health = values.get(OWN_HEALTH_HUD) == 0
        reveal_hud = (values.get('citadel_hud_visible') == 1 and
                      (OWN_HEALTH_HUD in self._game_ui_restore or self._own_health_hud_value() is not None))
        reveal_replay = (values.get('citadel_hide_replay_hud') == 0 and
                         (OWN_HEALTH_HUD in self._game_ui_restore or self._own_health_hud_value() is not None))
        if not reveal_health and not reveal_hud and not reveal_replay:
            return values, False
        try:
            self._verify_health_hud_handoff()
        except (RuntimeError, ValueError, OSError) as exc:
            LOG.info('Health panel reveal deferred: %s', exc)
            return self._defer_health_reveal(values, keep_hud=keep_hud), True
        return values, False

    def _defer_health_reveal(self, values, *, keep_hud=False):
        """Hide an unverifiable health reveal and retain its outer original.

        ``keep_hud`` is used by the explicit F9/Stop handoff: the replay UI and
        cursor must still return so the user can select a hero, which is what
        makes a later verified handoff possible. Automatic playback completion
        and preference application keep the conservative behavior and hold the
        main HUD too, deferring its reveal to that explicit handoff.
        """
        values = dict(values)
        if OWN_HEALTH_HUD in values and values[OWN_HEALTH_HUD] == 0:
            self._game_ui_restore.setdefault(OWN_HEALTH_HUD, 0)
            values[OWN_HEALTH_HUD] = 1
        if not keep_hud:
            if values.get('citadel_hud_visible') == 1:
                self._game_ui_restore.setdefault('citadel_hud_visible', 1)
                values['citadel_hud_visible'] = 0
            if values.get('citadel_hide_replay_hud') == 0:
                self._game_ui_restore.setdefault('citadel_hide_replay_hud', 0)
                values['citadel_hide_replay_hud'] = 1
        return values

    def _health_panel_held(self, *, require_full_hud=True):
        """An intentional own-health hide that can span shots or a guarded reload.

        ``require_full_hud`` keeps the stricter reload rule: the main HUD hide
        must still be in place so an in-process replay reload cannot expose the
        panel. Playback only needs the own-health panel to stay safely hidden,
        so it passes ``require_full_hud=False`` and is not dead-ended once the
        main HUD has been restored and a verified health handoff is unavailable.
        """
        originals = self._game_ui_restore
        if (self._native_active or self._native_manual or not originals
                or set(originals) - {OWN_HEALTH_HUD, 'citadel_hud_visible', 'citadel_hide_replay_hud'}
                or (OWN_HEALTH_HUD in originals and originals[OWN_HEALTH_HUD] != 0)
                or self._own_health_hud_value() != 1):
            return False
        for name, original, hidden in (('citadel_hud_visible', 1, 0), ('citadel_hide_replay_hud', 0, 1)):
            current = read_cvar_value(name, self._request(name))
            if name not in originals:
                # Already restored; only a full-HUD reload needs it still hidden.
                if require_full_hud and current != hidden:
                    return False
                continue
            if originals[name] != original or current != hidden:
                return False
        return True

    def _restore_game_ui_settings(self):
        """Restore the original automatic cursor/HUD on Stop or disconnect."""
        if not self._game_ui_restore and self._game_hero_restore is None:
            return None
        process = getattr(self._session, "process", None)
        if process is None or process.poll() is not None:
            # The process that held these temporary settings is gone; a new
            # launch starts from the saved configuration, so drop the stale
            # journal silently instead of warning on every future launch.
            self._game_ui_restore.clear()
            self._game_hero_restore = None
            self._replay_hud_restore = {}
            self._replay_hud_session = None
            return None
        try:
            self._require_connection()
            # Mode restoration must settle before checking the player context;
            # batching it with the reveal recreates the stock null-panel fault.
            self._restore_spectator_view()
            try:
                self._restore_spectator_hero()
            except (RuntimeError, ValueError, OSError) as exc:
                # A hero handoff that cannot verify must not hold the whole HUD/
                # cursor restore hostage; keep it pending and restore the rest so
                # playback and export are not dead-ended by a cosmetic panel.
                LOG.warning("Saved spectator hero handoff remains pending: %s", exc)
            requested = dict(self._game_ui_restore)
            values, health_pending = self._safe_health_hud_values(requested, keep_hud=True)
            values, deferred = self._require_own_health_hud(values, keep_hud=True)
            health_pending = health_pending or deferred
            self._request("; ".join(name + " " + numeric(value) for name, value in values.items()))
            self._verify_game_ui_values(values)
            for name in values:
                if values[name] == requested.get(name):
                    self._game_ui_restore.pop(name, None)
            self._game_ui_visible = False
            if health_pending:
                return ('Health panel restoration is pending. Open replay controls, select a hero '
                        'and wait for its camera, then return to Dolly and use Stop / restore.')
        except (RuntimeError, ValueError, OSError) as exc:
            LOG.warning("Replay UI restoration remains pending: %s", exc)
            return "Replay UI settings could not be restored; reconnect and use Stop / restore. " + str(exc)
        return None
