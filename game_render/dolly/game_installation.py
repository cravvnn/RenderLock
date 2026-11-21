"""Locate and validate one selected Deadlock installation without process access."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .launch_errors import LaunchError

GAME_EXECUTABLE_NAMES = ("deadlock.exe", "citadel.exe")


@dataclass(frozen=True)
class GamePaths:
    root: Path
    game_dir: Path
    citadel_dir: Path
    executable: Path
    gameinfo: Path


def _find_game_executable(directory: Path) -> Path | None:
    """Prefer the current executable name when a folder was selected."""
    # Windows filenames are case-insensitive. Retain their actual spelling when
    # inspecting an installation copied onto a case-sensitive filesystem too.
    if directory.is_dir():
        files = {entry.name.casefold(): entry for entry in directory.iterdir() if entry.is_file()}
        for name in GAME_EXECUTABLE_NAMES:
            if name in files:
                return files[name]
    return None


def validate_game(path: str | os.PathLike[str]) -> GamePaths:
    value = Path(path).expanduser().resolve()
    if any(c in str(value) for c in '\r\n\x00";+'):
        raise LaunchError("The game path contains console separators. Use a Steam library path without quotes, semicolons, or plus signs.")
    selected_executable = value if value.suffix.casefold() == ".exe" else None
    if selected_executable is not None:
        if selected_executable.name.casefold() not in GAME_EXECUTABLE_NAMES:
            raise LaunchError("Select Deadlock's deadlock.exe or legacy citadel.exe from game/bin/win64, or select the Deadlock installation folder.")
        if not selected_executable.is_file():
            raise LaunchError(f"The selected Deadlock executable is missing: {selected_executable}. Browse to the installed deadlock.exe or legacy citadel.exe in game/bin/win64.")
    candidates = [value, *list(value.parents)[:5]]
    missing: list[str] = []
    for root in candidates:
        game = root / "game"
        executable_dir = game / "bin" / "win64"
        if selected_executable is not None and selected_executable.parent != executable_dir:
            continue
        executable = selected_executable or _find_game_executable(executable_dir)
        gameinfo = game / "citadel" / "gameinfo.gi"
        if executable is not None and gameinfo.is_file():
            if not (game / "citadel" / "bin" / "win64" / "server.dll").is_file():
                raise LaunchError(f"Deadlock server.dll is missing: {game / 'citadel/bin/win64/server.dll'}. Verify the game's installed files in Steam.")
            return GamePaths(root, game, game / "citadel", executable, gameinfo)
        if executable is not None or executable_dir.is_dir() or (game / "citadel").is_dir():
            absent = []
            if executable is None:
                absent.append(f"game executable (deadlock.exe or legacy citadel.exe) in {executable_dir}")
            if not gameinfo.is_file():
                absent.append(f"gameinfo.gi at {gameinfo}")
            missing.append("Missing Deadlock " + " and ".join(absent) + ". Verify the game's installed files in Steam.")
    if missing:
        raise LaunchError(missing[0])
    raise LaunchError("Select the Deadlock installation folder containing game/bin/win64/deadlock.exe (or legacy citadel.exe) and game/citadel/gameinfo.gi, or browse directly to that executable.")
