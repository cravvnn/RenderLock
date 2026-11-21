"""Replay browsing and the deliberately additive launcher option surface."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex


@dataclass(frozen=True)
class ReplayEntry:
    path: Path
    name: str
    size_bytes: int
    modified_ns: int


# Displayed ordering applies only to the library; source entries and their
# stable indices remain unchanged so selection still identifies the same file.
REPLAY_SORTS = {
    "Name ↑": ("name", False),
    "Name ↓": ("name", True),
    "Size ↑": ("size_bytes", False),
    "Size ↓": ("size_bytes", True),
    "Modified ↑": ("modified_ns", False),
    "Modified ↓": ("modified_ns", True),
}
DEFAULT_REPLAY_SORT = "Name ↑"


def sorted_replay_indices(entries, sort=DEFAULT_REPLAY_SORT):
    """Return source indices in the requested order, using raw sizes and timestamps."""
    field, descending = REPLAY_SORTS.get(sort, REPLAY_SORTS[DEFAULT_REPLAY_SORT])
    def key(index):
        entry = entries[index]
        value = getattr(entry, field)
        if field == "name":
            value = value.casefold()
        return value, entry.name.casefold(), str(entry.path).casefold()
    return sorted(range(len(entries)), key=key, reverse=descending)


def same_replay_name(selected: str | os.PathLike[str], reported: str | None) -> bool:
    """Match an engine basename with or without the final .dem extension.

    A custom recording may contain dots in its name. Only the known .dem
    extension is optional; stripping arbitrary suffixes can reject that name
    or mistake a different file type for the selected replay. Replay process
    ownership and live playback status remain the controller's responsibility.
    """
    if not isinstance(reported, str) or any(char in reported for char in "\0\r\n"):
        return False
    expected = str(selected).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if not expected.endswith(".dem") or len(expected) <= 4:
        return False
    actual = reported.strip()
    if len(actual) >= 2 and actual[0] in "\"'" and actual[-1] == actual[0]:
        actual = actual[1:-1]
    actual = actual.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return actual in {expected, expected[:-4]}


def discover_replays(folder: str | os.PathLike[str]) -> list[ReplayEntry]:
    """List local replay files without opening or parsing their binary content.

    Files removed during a Steam download/cleanup are skipped. Folder access
    errors propagate so the UI can explain them. No recursive disk scan occurs.
    """
    directory = Path(folder).expanduser()
    entries = []
    for candidate in directory.iterdir():
        if candidate.suffix.casefold() != ".dem":
            continue
        try:
            if not candidate.is_file():
                continue
            info = candidate.stat()
        except FileNotFoundError:
            continue
        entries.append(ReplayEntry(candidate.resolve(), candidate.name, info.st_size, info.st_mtime_ns))
    return sorted(entries, key=lambda item: (-item.modified_ns, item.name.casefold(), str(item.path)))


def find_replay_folder(game_path: str | os.PathLike[str]) -> Path:
    """Resolve Dolly's supported installation selections to citadel/replays.

    This only computes a path; launch-time game validation remains in launcher.
    """
    path = Path(game_path).expanduser()
    if path.name.casefold() in {"deadlock.exe", "citadel.exe"}:
        path = path.parent
    if path.name.casefold() == "win64" and path.parent.name.casefold() == "bin":
        path = path.parent.parent
    if path.name.casefold() == "citadel":
        return path / "replays"
    if path.name.casefold() == "game":
        return path / "citadel" / "replays"
    return path / "game" / "citadel" / "replays"


_FIXED_FLAGS = {"-dev", "-insecure", "-console"}
_SWITCHES = {"-windowed": "-windowed", "-window": "-windowed", "-sw": "-windowed",
             "-fullscreen": "-fullscreen", "-noborder": "-noborder"}
_VALUES = {"-w": ("-w", 320, 16384), "-width": ("-w", 320, 16384),
           "-h": ("-h", 200, 16384), "-height": ("-h", 200, 16384),
           "-refresh": ("-refresh", 24, 1000)}
LAUNCH_OPTIONS_HELP = "Additional options: -windowed, -fullscreen, -noborder, -w WIDTH, -h HEIGHT, -refresh HZ. Dolly always adds -dev -insecure -console."


_SWITCHES["-dolly-lite"] = "-dolly-lite"

def parse_launch_options(value: str) -> list[str]:
    """Return validated argv additions; never evaluate a shell or game command.

    Dolly owns game/addon selection, renderer, console ports and replay startup.
    The editable portion therefore accepts only window, size and refresh flags.
    """
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Additional launch options must be text of at most 2048 characters.")
    if any(ord(char) < 32 for char in value):
        raise ValueError("Additional launch options must be on one line without control characters.")
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValueError("Additional launch options contain an unmatched quote.") from exc
    result: list[str] = []
    seen: set[str] = set()
    index = 0
    while index < len(tokens):
        flag = tokens[index].casefold()
        index += 1
        if flag in _FIXED_FLAGS:
            continue
        if flag in _SWITCHES:
            normalized = _SWITCHES[flag]
            if normalized in seen:
                raise ValueError(f"Launch option {normalized} is repeated.")
            if normalized in {"-windowed", "-fullscreen"} and {"-windowed", "-fullscreen"} & seen:
                raise ValueError("Choose either -windowed or -fullscreen, not both.")
            seen.add(normalized)
            result.append(normalized)
            continue
        if flag in _VALUES:
            normalized, minimum, maximum = _VALUES[flag]
            if normalized in seen:
                raise ValueError(f"Launch option {normalized} is repeated.")
            if index >= len(tokens) or not tokens[index].isascii() or not tokens[index].isdigit():
                raise ValueError(f"Launch option {flag} needs an integer between {minimum} and {maximum}.")
            number = int(tokens[index])
            index += 1
            if not minimum <= number <= maximum:
                raise ValueError(f"Launch option {flag} must be between {minimum} and {maximum}.")
            seen.add(normalized)
            result.extend((normalized, str(number)))
            continue
        raise ValueError(f"Launch option {tokens[index - 1]!r} is not supported. {LAUNCH_OPTIONS_HELP}")
    if "-fullscreen" in seen and "-noborder" in seen:
        raise ValueError("Use -noborder with windowed mode; remove -fullscreen or -noborder.")
    return result
