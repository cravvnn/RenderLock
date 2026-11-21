"""User-wide launcher, editor and capture preferences.

The capture-enabled switch intentionally never persists. Invalid preferences
raise on load so the UI can explain its fallback without changing the file.
An explicit save preserves invalid existing content in a sibling ``.invalid``
backup before replacing it atomically.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import shutil
import tempfile

from .bindings import CaptureBinding, DEFAULT_BINDING
from .editor_actions import (
    ACTION_LABELS, EditorBinding, bindings_from_dict, bindings_to_dict,
    default_action_bindings, validate_action_bindings,
)
from .replays import parse_launch_options

SETTINGS_VERSION = 11
DEFAULT_RESHADE_BINDING = EditorBinding("F11")
MAX_SETTINGS_BYTES = 64 * 1024


@dataclass(frozen=True)
class AppSettings:
    capture_binding: CaptureBinding = field(default_factory=lambda: DEFAULT_BINDING)
    game_path: str = ""
    replay_folder: str = ""
    demo_path: str = ""
    launch_options: str = ""
    movement_speed: float = 320.0
    mouse_sensitivity: float = 0.12
    action_bindings: dict[str, EditorBinding | None] = field(default_factory=dict)
    reshade_binding: EditorBinding | None = field(default_factory=lambda: DEFAULT_RESHADE_BINDING)
    reshade_runtime_path: str = ""
    full_editor: bool = False
    ffmpeg_path: str = ""
    export_folder: str = ""
    # Legacy saved-field name: enables automatic release metadata checks only.
    # Downloading and installing still require an explicit Update now action.
    auto_updates: bool = True
    framing_grid_enabled: bool = False
    pause_after_capture: bool = False
    allow_mod_overrides: bool = False
    nvidia_dlss: bool = False
    discord_presence: bool = True
    migration_warnings: tuple[str, ...] = field(default=(), compare=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.auto_updates) is not bool:
            raise ValueError("auto_updates must be a boolean.")
        if type(self.full_editor) is not bool:
            raise ValueError("full_editor must be a boolean.")
        if type(self.framing_grid_enabled) is not bool:
            raise ValueError("framing_grid_enabled must be a boolean.")
        if type(self.pause_after_capture) is not bool:
            raise ValueError("pause_after_capture must be a boolean.")
        if type(self.allow_mod_overrides) is not bool:
            raise ValueError("allow_mod_overrides must be a boolean.")
        if type(self.nvidia_dlss) is not bool:
            raise ValueError("nvidia_dlss must be a boolean.")
        if type(self.discord_presence) is not bool:
            raise ValueError("discord_presence must be a boolean.")
        if not isinstance(self.capture_binding, CaptureBinding):
            raise ValueError("capture_binding must be a CaptureBinding.")
        # Validate here as well as at the JSON boundary before saving anything.
        CaptureBinding.from_dict(self.capture_binding.to_dict())
        for name in ("game_path", "replay_folder", "demo_path", "reshade_runtime_path", "ffmpeg_path", "export_folder"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > 32768 or any(ord(char) < 32 for char in value):
                raise ValueError(f"{name} must be a path without control characters.")
        parse_launch_options(self.launch_options)
        for name, low, high in (("movement_speed", 1.0, 10000.0), ("mouse_sensitivity", 0.001, 10.0)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError(f"{name} must be a finite number between {low:g} and {high:g}.")
            object.__setattr__(self, name, float(value))
        if not isinstance(self.action_bindings, dict):
            raise ValueError("action_bindings must be a mapping of editor actions.")
        incoming = dict(self.action_bindings)
        if "capture" not in incoming:
            incoming["capture"] = self.capture_binding
        bindings = validate_action_bindings(incoming)
        capture = bindings["capture"]
        if capture is not None:
            legacy = CaptureBinding.from_dict(capture.to_dict())
            if self.capture_binding != DEFAULT_BINDING and self.capture_binding != legacy:
                raise ValueError("capture_binding and the capture editor action disagree. Use with_action_bindings to update bindings together.")
            object.__setattr__(self, "capture_binding", legacy)
        object.__setattr__(self, "action_bindings", bindings)
        object.__setattr__(self, "reshade_binding", validate_reshade_binding(self.reshade_binding, bindings))
        if (not isinstance(self.migration_warnings, tuple)
                or any(not isinstance(item, str) for item in self.migration_warnings)):
            raise ValueError("migration_warnings must be a tuple of messages.")

    def with_capture_binding(self, binding: CaptureBinding) -> AppSettings:
        if not isinstance(binding, CaptureBinding):
            raise ValueError("capture_binding must be a CaptureBinding.")
        bindings = dict(self.action_bindings)
        bindings["capture"] = EditorBinding.from_dict(binding.to_dict())
        return replace(self, capture_binding=binding, action_bindings=bindings, migration_warnings=())

    def with_action_bindings(self, bindings: dict[str, EditorBinding | None]) -> AppSettings:
        """Replace bindings and keep the external capture compatibility alias in sync."""
        normalized = validate_action_bindings(bindings)
        capture = normalized["capture"]
        legacy = CaptureBinding.from_dict(capture.to_dict()) if capture is not None else self.capture_binding
        return replace(self, capture_binding=legacy, action_bindings=normalized, migration_warnings=())

    def with_reshade_binding(self, binding: EditorBinding | None) -> AppSettings:
        return replace(self, reshade_binding=binding, migration_warnings=())


def validate_reshade_binding(binding, bindings):
    """Keep the optional menu shortcut separate from existing action IDs."""
    if binding is None:
        return None
    if not isinstance(binding, EditorBinding):
        raise ValueError("ReShade menu binding must be an EditorBinding or None.")
    binding = EditorBinding.from_dict(binding.to_dict())
    if binding.key in ("Ctrl", "Alt", "Shift"):
        raise ValueError("ReShade menu needs a keyboard key or mouse button, not a modifier alone.")
    for name, other in bindings.items():
        if other is not None and (binding.vk, binding.modifiers) == (other.vk, other.modifiers):
            raise ValueError(f"{binding.label} is assigned to both ReShade menu and {ACTION_LABELS[name]}. Choose a different binding or clear one.")
    return binding


def _migrate_reshade_binding(bindings, warnings):
    binding = DEFAULT_RESHADE_BINDING
    if binding in bindings.values():
        warnings.append("Your existing F11 binding was kept. ReShade menu is unbound; choose a key in Controls.")
        return None
    return binding


def reshade_config_path() -> Path:
    return settings_path().parent / "ReShade" / "ReShade.ini"


def settings_path() -> Path:
    """Return the per-user settings path, shared by extracted Dolly versions."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata and appdata.strip() else Path.home() / ".config"
    return base / "DeadlockDolly" / "settings.json"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate settings field: {key}.")
        result[key] = value
    return result


def _decode_settings(data: bytes) -> AppSettings:
    if len(data) > MAX_SETTINGS_BYTES:
        raise ValueError("Dolly settings exceed the 64 KiB size limit.")
    try:
        raw = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Dolly settings are not valid UTF-8 JSON.") from exc
    if not isinstance(raw, dict) or "version" not in raw:
        raise ValueError("Dolly settings must contain a version and preferences.")
    if type(raw["version"]) is not int or raw["version"] not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, SETTINGS_VERSION):
        raise ValueError(f"Unsupported Dolly settings version: {raw['version']!r}.")
    if raw["version"] == 1:
        if set(raw) != {"version", "capture_binding"}:
            raise ValueError("Version 1 Dolly settings must contain version and capture_binding only.")
        capture = CaptureBinding.from_dict(raw["capture_binding"])
        bindings = default_action_bindings(capture)
        warnings = []
        # Preserve a user's old binding, explicitly clearing a newly introduced
        # conflicting default instead of silently assigning the key two actions.
        for name, binding in bindings.items():
            if name != "capture" and binding == bindings["capture"]:
                bindings[name] = None
                warnings.append(f"Your existing capture key {capture.label} was kept. {ACTION_LABELS[name]} is unbound because its new default conflicts; choose a replacement in Controls.")
        reshade_binding = _migrate_reshade_binding(bindings, warnings)
        return AppSettings(capture_binding=capture, action_bindings=bindings,
                           reshade_binding=reshade_binding, migration_warnings=tuple(warnings))
    fields = {"capture_binding", "game_path", "replay_folder", "demo_path", "launch_options",
              "movement_speed", "mouse_sensitivity", "action_bindings"}
    if raw["version"] >= 3:
        fields |= {"reshade_binding", "reshade_runtime_path"}
    if raw["version"] >= 4:
        fields.add("full_editor")
    if raw["version"] >= 5:
        # Keep either saved value; False remains an automatic-check opt-out.
        fields |= {"ffmpeg_path", "auto_updates"}
    if raw["version"] >= 6:
        fields.add("framing_grid_enabled")
    if raw["version"] >= 7:
        fields.add("export_folder")
    if raw["version"] >= 8:
        fields.add("pause_after_capture")
    if raw["version"] >= 9:
        fields.add("allow_mod_overrides")
    if raw["version"] >= 10:
        fields.add("discord_presence")
    if raw["version"] >= 11:
        fields.add("nvidia_dlss")
    if set(raw) != fields | {"version"}:
        raise ValueError(f"Version {raw['version']} Dolly settings have missing or unknown preference fields.")
    values = {name: raw[name] for name in fields}
    values["capture_binding"] = CaptureBinding.from_dict(raw["capture_binding"])
    values["action_bindings"] = bindings_from_dict(raw["action_bindings"])
    if raw["version"] == 2:
        warnings = []
        values["reshade_binding"] = _migrate_reshade_binding(values["action_bindings"], warnings)
        values["migration_warnings"] = tuple(warnings)
    else:
        value = raw["reshade_binding"]
        values["reshade_binding"] = EditorBinding.from_dict(value) if value is not None else None
    return AppSettings(**values)


def load_settings(path: str | os.PathLike[str] | None = None) -> AppSettings:
    """Load preferences; missing files use defaults, invalid files raise.

    This function never writes or renames anything. The UI should catch
    ``ValueError`` and ``OSError``, explain the problem, and use ``AppSettings()``.
    """
    target = Path(path) if path is not None else settings_path()
    try:
        with target.open("rb") as handle:
            data = handle.read(MAX_SETTINGS_BYTES + 1)
    except FileNotFoundError:
        return AppSettings()
    except OSError:
        # The file exists but could not be read (locked, permissions). Whoever
        # falls back to defaults must not silently replace it on the next save.
        _UNREAD.add(os.path.normcase(os.path.abspath(target)))
        raise
    return _decode_settings(data)


# Settings files whose last load failed with an I/O error in this process.
_UNREAD: set[str] = set()


def _backup_invalid_settings(target: Path) -> None:
    """Preserve content an explicit save would otherwise lose.

    That is malformed content, and any content of a file this process failed
    to read earlier (the save would be built from defaults, not from it). If
    it still cannot be read, the save fails rather than replacing it unseen.
    """
    unread = os.path.normcase(os.path.abspath(target)) in _UNREAD
    try:
        with target.open("rb") as handle:
            data = handle.read(MAX_SETTINGS_BYTES + 1)
    except FileNotFoundError:
        return
    try:
        _decode_settings(data)
        if unread:
            raise ValueError("kept because an earlier load failed")
    except ValueError:
        staging: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=target.name + ".",
                suffix=".invalid.tmp", delete=False,
            ) as destination:
                staging = Path(destination.name)
                with target.open("rb") as source:
                    shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            published = staging.with_suffix("")
            if os.name == "nt":
                os.rename(staging, published)  # Windows refuses an existing name.
            else:
                os.link(staging, published)  # Non-overwriting publication on POSIX.
        finally:
            if staging is not None:
                try:
                    staging.unlink(missing_ok=True)
                except OSError:
                    pass  # A leftover stage must not mask the backup result.


def save_settings(
    settings: AppSettings, path: str | os.PathLike[str] | None = None,
) -> None:
    """Atomically save preferences, retaining a backup of invalid old content.

    Saving failures raise and leave the original file in place. Backups are
    named ``settings.json.<unique>.invalid`` in the same folder.
    """
    if not isinstance(settings, AppSettings):
        raise ValueError("settings must be an AppSettings instance.")
    payload = {
        "version": SETTINGS_VERSION,
        "full_editor": settings.full_editor,
        "ffmpeg_path": settings.ffmpeg_path,
        "export_folder": settings.export_folder,
        "auto_updates": settings.auto_updates,
        "framing_grid_enabled": settings.framing_grid_enabled,
        "pause_after_capture": settings.pause_after_capture,
        "allow_mod_overrides": settings.allow_mod_overrides,
        "discord_presence": settings.discord_presence,
        "nvidia_dlss": settings.nvidia_dlss,
        "capture_binding": settings.capture_binding.to_dict(),
        "game_path": settings.game_path,
        "replay_folder": settings.replay_folder,
        "demo_path": settings.demo_path,
        "launch_options": settings.launch_options,
        "movement_speed": settings.movement_speed,
        "mouse_sensitivity": settings.mouse_sensitivity,
        "action_bindings": bindings_to_dict(settings.action_bindings),
        "reshade_binding": settings.reshade_binding.to_dict() if settings.reshade_binding is not None else None,
        "reshade_runtime_path": settings.reshade_runtime_path,
    }
    data = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _decode_settings(data)
    target = Path(path) if path is not None else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=target.name + ".",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _backup_invalid_settings(target)
        os.replace(temporary, target)
        _UNREAD.discard(os.path.normcase(os.path.abspath(target)))
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Best-effort cleanup must preserve the save result.
