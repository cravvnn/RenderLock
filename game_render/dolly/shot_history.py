"""Bounded, in-memory undo/redo for authored shot data, never game state."""
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic
import secrets


@dataclass
class ShotState:
    project: object
    selected_time: float | None = None
    size: int = 0


class ShotHistory:
    def __init__(self, project, *, max_entries=100, max_bytes=16 * 1024 * 1024):
        if max_entries < 2 or max_bytes < 1:
            raise ValueError("Invalid shot history limits")
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.revision = 0
        self.reset(project)

    def _state(self, project, selected_time=None):
        # History also preserves unfinished authoring states; validation belongs
        # to the editor/save operation, not the undo journal.
        size = len(repr(project).encode('utf-8'))
        return ShotState(deepcopy(project), selected_time, size)

    def reset(self, project):
        # An opened/replaced shot needs a distinct identity even if its revision
        # and key timestamps match a previously opened project.
        self.session_id = secrets.randbits(64) or 1
        self._states = [self._state(project)]
        self._index = 0
        self._saved = self._states[0].project
        self._merge_key = None
        self._merge_time = 0
        self._advance()

    def _advance(self):
        self.revision = (self.revision + 1) & 0xffffffff or 1

    @property
    def can_undo(self):
        return self._index > 0

    @property
    def can_redo(self):
        return self._index + 1 < len(self._states)

    def select(self, selected_time):
        self._states[self._index].selected_time = selected_time

    def record(self, project, selected_time=None, *, merge_key=None):
        if project == self._states[self._index].project:
            return False
        state = self._state(project, selected_time)
        now = monotonic()
        if (merge_key is not None and merge_key == self._merge_key and self._index > 0
                and self._index == len(self._states) - 1 and now - self._merge_time <= .35):
            # A stream of updates from one native slider is one authored edit.
            # Its preceding state remains intact; each update still invalidates
            # queued actions carrying an older camera-list revision.
            self._states[self._index] = state
        else:
            self._states[self._index + 1:] = [state]
            self._index += 1
        self._merge_key, self._merge_time = merge_key, now
        total = sum(item.size for item in self._states)
        while len(self._states) > 1 and (len(self._states) > self.max_entries or total > self.max_bytes):
            total -= self._states.pop(0).size
            self._index -= 1
        self._advance()
        return True

    def mark_saved(self, project):
        current = self._states[self._index].project
        self._saved = current if project == current else deepcopy(project)
        self._merge_key = None

    def is_dirty(self, project):
        return project != self._saved

    def move(self, redo=False):
        if not (self.can_redo if redo else self.can_undo):
            return None
        self._merge_key = None
        self._index += 1 if redo else -1
        self._advance()
        return deepcopy(self._states[self._index])
