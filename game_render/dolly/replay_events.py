"""Read complete replay events in an isolated, bounded background process."""
from dataclasses import dataclass
from pathlib import Path
import json
import os
import subprocess
import threading

MAX_KILLS = 3500
MAX_PLAYERS = 32


@dataclass(frozen=True)
class KillIndex:
    status: int = 0  # unavailable, loading, complete, failed
    total_ticks: int = 0
    kills: tuple = ()
    players: tuple = ()
    message: str = 'Choose a completed replay to show kills.'


def decode_index(data):
    value = json.loads(data)
    if not isinstance(value, dict) or type(value.get('abi')) is not int or value.get('abi') != 1:
        raise ValueError('Unsupported kill index')
    total = value.get('total_ticks')
    if type(total) is not int or not 0 < total <= 2147483647:
        raise ValueError('Invalid replay length')
    kills, players = value.get('kills'), value.get('players')
    if (not isinstance(kills, list) or len(kills) > MAX_KILLS
            or not isinstance(players, list) or len(players) > MAX_PLAYERS):
        raise ValueError('Replay event index exceeds supported limits')
    names = []
    for player in players:
        if not isinstance(player, dict):
            raise ValueError('Invalid player record')
        identifier, name = player.get('id'), player.get('name')
        if type(identifier) is not int or not 0 <= identifier <= 65535 or not isinstance(name, str) or not name or '\0' in name:
            raise ValueError('Invalid player name')
        names.append((identifier, name[:128]))
    if len({p[0] for p in names}) != len(names):
        raise ValueError('Duplicate player identity')
    identifiers = {p[0] for p in names}
    events = []
    for kill in kills:
        if not isinstance(kill, dict):
            raise ValueError('Invalid kill record')
        tick, killer, victim = kill.get('tick'), kill.get('killer'), kill.get('victim')
        if (any(type(v) is not int for v in (tick, killer, victim)) or not 0 <= tick < total
                or killer not in identifiers or victim not in identifiers):
            raise ValueError('Invalid kill event')
        events.append((tick, killer, victim))
    if events != sorted(events, key=lambda e:e[0]) or len(set(events)) != len(events):
        raise ValueError('Kill events must be ordered and unique')
    credited = sum(killer != 0 and killer != victim for _, killer, victim in events)
    return KillIndex(2, total, tuple(events), tuple(names),
                     f'{credited} player kills, {len(events)} deaths in this replay')


class ReplayEventReader:
    def __init__(self):
        self._lock = threading.Lock()
        self._identity = None
        self._result = KillIndex()

    def snapshot(self, path):
        try:
            selected = Path(path).resolve(strict=True)
            info = selected.stat()
            identity = (str(selected), info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        except (OSError, TypeError, ValueError):
            return KillIndex()
        with self._lock:
            if identity != self._identity:
                self._identity = identity
                self._result = KillIndex(1, message='Reading replay kills...')
                try:
                    threading.Thread(target=self._read, args=(selected, identity), daemon=True).start()
                except RuntimeError as exc:
                    self._result = KillIndex(3, message=('Replay reader could not start: ' + str(exc))[:160])
            return self._result

    def _read(self, path, identity):
        try:
            from .runtime import resource_root
            helper = resource_root()/'native/bin/win64/DollyReplayIndex.exe'
            if not helper.is_file():
                raise ValueError('Replay reader missing. Build DollyReplayIndex to show kills.')
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            process = subprocess.run([str(helper), str(path)], capture_output=True,
                                     timeout=120, creationflags=flags)
            if process.returncode:
                detail = process.stderr.decode('utf-8', 'replace').strip()[:160]
                raise ValueError(detail or 'This replay has no supported kill index.')
            if len(process.stdout) > 512*1024:
                raise ValueError('Replay event index is too large')
            result = decode_index(process.stdout)
            info = path.stat()
            if identity != (str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                raise ValueError('Replay changed while reading kills. Open the completed replay again.')
        except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.TimeoutExpired) as error:
            result = KillIndex(3, message=str(error)[:160])
        with self._lock:
            if self._identity == identity:
                self._result = result
