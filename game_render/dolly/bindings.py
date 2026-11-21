"""Portable capture-binding values shared by settings, the UI and input polling."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# These are Windows virtual-key codes, independent of Tk key names or scan codes.
# Primary clicks and modifier-only bindings are deliberately unavailable.
_KEY_CODES = {
    "Mouse4": 0x05, "Mouse5": 0x06, "MiddleMouse": 0x04,
    **{chr(code): code for code in range(ord("A"), ord("Z") + 1)},
    **{str(number): ord(str(number)) for number in range(10)},
    **{f"F{number}": 0x6F + number for number in range(1, 25)},
    "Space": 0x20, "Tab": 0x09, "Enter": 0x0D, "Escape": 0x1B,
    "Backspace": 0x08, "Insert": 0x2D, "Delete": 0x2E,
    "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
    "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
}
KEY_CHOICES = tuple(_KEY_CODES)


@dataclass(frozen=True)
class CaptureBinding:
    """One supported keyboard/mouse button with optional required modifiers."""

    key: str = "K"
    ctrl: bool = True
    alt: bool = True
    shift: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or self.key not in _KEY_CODES:
            raise ValueError("Choose a supported capture key or Mouse4, Mouse5 or MiddleMouse.")
        for name in ("ctrl", "alt", "shift"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"Capture modifier {name} must be true or false.")

    @property
    def label(self) -> str:
        parts = [name for name, enabled in (
            ("Ctrl", self.ctrl), ("Alt", self.alt), ("Shift", self.shift),
        ) if enabled]
        return "+".join(parts + [self.key])

    @property
    def vk(self) -> int:
        return _KEY_CODES[self.key]

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "ctrl": self.ctrl, "alt": self.alt, "shift": self.shift}

    @classmethod
    def from_dict(cls, value: Any) -> CaptureBinding:
        if not isinstance(value, dict) or set(value) != {"key", "ctrl", "alt", "shift"}:
            raise ValueError("Capture binding must contain only key, ctrl, alt and shift.")
        return cls(**value)


DEFAULT_BINDING = CaptureBinding()
