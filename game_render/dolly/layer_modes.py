"""Scene-layer isolation and matte post-processing shared by the controller.

The scene-class keep-lists, the matte cvar list and their methods moved verbatim
out of the controller into a mixin so this layer/matte domain owns its own module
while remaining on the Controller instance. No behavior change.
"""
from __future__ import annotations

import math

from .convar_response import read_cvar_value
from .path import format_cvar_value


class LayerModesMixin:
    # Scene-system classes used to isolate recording layers. Flags byte 8
    # hides a class for the current frames; 0 restores it. The live registry
    # is read with sc_showclasses so a game update is detected instead of
    # silently producing a wrong layer.
    #
    # Every mode is expressed as a KEEP-list: the layer shows exactly the listed
    # classes and hides the rest of the live registry. This is deliberate — the
    # registry is dynamic (a build can add or drop classes such as
    # ``projectedDecal``), so a hide-list would silently leak an unlisted class
    # into the layer. A keep-list excludes unknown classes by default and fails
    # closed when a required class disappears (`apply_layer_mode`). Reviewed
    # legacy classes absent from current builds are optional; if registered,
    # they are still kept. Unknown classes remain hidden.
    #
    # Owners (verified live 2026-09-27 by hiding each class and comparing the
    # rendered frame): characters -> SkinnedObject, effects -> ParticleSystem,
    # static world/lighting -> AggregateDesc/BarnLight/Default and the remaining
    # scene classes.
    LAYER_MODES = {
        "world": {"keep": ("AggregateDesc", "BarnLight", "OmniLight",
                           "LightProbeVolume", "Default", "MeshBuilderObject",
                           "ZipLineRopeSegment", "InstancedMesh", "EnvMap", "Skybox"),
                  "optional_keep": ("DirectionalLight", "projectedDecal")},
        "players": {"keep": ("SkinnedObject",)},
        "effects": {"keep": ("ParticleSystem",)},
    }

    def scene_classes(self):
        output = self._request("sc_showclasses")
        classes = []
        for line in str(output).splitlines():
            name = line.strip().split()[0] if line.strip() else ""
            if name:
                classes.append(name)
        if not classes:
            raise RuntimeError("The game did not list its scene classes; layer export is unavailable.")
        return classes

    def apply_layer_mode(self, mode):
        """Show exactly the selected layer's keep-list; hide every other class.

        Owners verified live: characters -> SkinnedObject, effects ->
        ParticleSystem, static world/lighting -> AggregateDesc/BarnLight/Default
        and the remaining scene classes. Unknown/new registry classes are hidden
        by default (keep-list), so the layer fails closed instead of leaking.
        """
        if mode not in self.LAYER_MODES:
            raise ValueError("Unknown layer mode: " + str(mode))
        classes = self.scene_classes()
        spec = self.LAYER_MODES[mode]
        keep = set(spec.get("keep", ()))
        hide = set(spec.get("hide", ()))
        required = keep | hide
        missing = sorted(required - set(classes))
        if missing:
            raise RuntimeError("The game no longer reports the scene classes needed for the "
                               + mode + " layer: " + ", ".join(missing))
        keep.update(spec.get("optional_keep", ()))
        targets = [name for name in classes if name not in keep] if keep else [
            name for name in classes if name in hide]
        # A console batch can fail after earlier classes were hidden. Retain
        # every possible owned change before the first command, for retry.
        self._layer_hidden = set(targets) | set(self._layer_hidden or ())
        # Start from a clean slate: flags left by a previous layer must never
        # hide the class this layer keeps. Each class is its own short command
        # because the engine silently drops over-long multi-command lines.
        failures = []
        for name in classes:
            try:
                self._request("sc_setclassflags " + name + " 0")
            except (RuntimeError, ValueError, OSError) as exc:
                failures.append(name + " reset (" + str(exc) + ")")
        for name in targets:
            try:
                self._request("sc_setclassflags " + name + " 8")
            except (RuntimeError, ValueError, OSError) as exc:
                failures.append(name + " (" + str(exc) + ")")
        if failures:
            raise RuntimeError("Could not hide scene classes for the " + mode + " layer: "
                               + ", ".join(failures))
        # Remember exactly which classes this layer hid so reset restores only
        # what needs restoring (the registry is dynamic; a keep-list can hide
        # many classes).
        self._layer_hidden = set(targets)
        return {"mode": mode, "hidden": targets, "classes": classes}

    def reset_layer_modes(self):
        """Restore every scene class the layer export can hide.

        Raises when any class could not be shown again: a silently failed
        restore would leave the game hiding players or effects.
        """
        pending = getattr(self, "_layer_hidden", None)
        if pending is None:
            # No layer applied this session; try the live registry so a stray
            # flag cannot persist, but send nothing if it is unreadable.
            try:
                classes = self.scene_classes()
            except (RuntimeError, ValueError, OSError):
                classes = []
        else:
            classes = sorted(pending)
        failures = []
        for name in classes:
            try:
                self._request("sc_setclassflags " + name + " 0")
            except (RuntimeError, ValueError, OSError) as exc:
                failures.append(name + " (" + str(exc) + ")")
        if failures:
            raise RuntimeError("Could not restore scene classes: " + ", ".join(failures))
        self._layer_hidden = None

    # Screen post-processing that contaminates black/white matte passes.
    # Bloom spills over the layer and the forced white clear; eye-adaptation
    # then washes the layer out of the white pass and makes the derived alpha
    # opaque. Tonemapping itself stays on so the white clear stays white.
    MATTE_CVARS = ("r_effects_bloom", "r_post_bloom", "r_post_bloom_strength")

    def begin_matte_layer(self):
        """Disable screen post-processing for clean matte passes.

        Values are saved and restored by :meth:`end_matte_layer`; a cvar that
        cannot be read or written is skipped instead of failing the take.
        """
        if getattr(self, "_matte_cvar_restore", None) is not None:
            return
        saved = {}
        for name in self.MATTE_CVARS:
            try:
                output = self._request(name)
                read_cvar_value(name, output)
                saved[name] = output
            except (RuntimeError, ValueError, OSError):
                continue
        commands = [name + " 0" for name in saved]
        # A failed batch may have applied a prefix. Keep originals until a
        # successful restore rather than losing them on either failure path.
        self._matte_cvar_restore = saved
        if commands:
            self._request("; ".join(commands))

    def end_matte_layer(self):
        saved = getattr(self, "_matte_cvar_restore", None)
        if not saved:
            self._matte_cvar_restore = None
            return
        commands = []
        for name, output in saved.items():
            try:
                value = read_cvar_value(name, output)
                commands.append(name + " " + format_cvar_value(value))
            except (ValueError, TypeError) as exc:
                raise RuntimeError("Could not read the saved matte setting for " + name) from exc
        if commands:
            self._request("; ".join(commands))
        for name, output in saved.items():
            expected = float(read_cvar_value(name, output))
            actual = float(read_cvar_value(name, self._request(name)))
            if not math.isfinite(actual) or not math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6):
                raise RuntimeError("Could not restore the matte setting for " + name + "; use Stop / restore to retry")
        self._matte_cvar_restore = None
