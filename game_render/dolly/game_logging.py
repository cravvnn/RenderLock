"""DLSS-compatible Windows startup and support logs, without a stdout handle.

The engine classifies any valid stdout file/pipe as a console application and
disables NVIDIA DLSS. Keep stderr, an engine-owned console file and received
console text instead. Output written only to stdout before the engine logger
starts cannot be recovered by these channels.
"""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import re
import subprocess
import threading
import time

ENGINE_LOG = "engine_console.log"
CONSOLE_LOG = "console_transcript.log"
SESSION_LOGS = (ENGINE_LOG, CONSOLE_LOG)
# Sessions whose game logs are kept, and how many a diagnostics export reads.
RETAINED_SESSIONS = 8
_SESSION_NAME = re.compile(r"\d{8}_\d{6}_[0-9a-f]{10}", re.IGNORECASE)
TRANSCRIPT_LIMIT = 8 * 1024 * 1024
_HANDLE_LOCK = threading.Lock()


@contextmanager
def gui_stdio(stderr_file):
    import msvcrt
    # Only these two handles may be inherited, and their original flags must
    # survive failed creation too. Never change the parent's standard handles.
    with _HANDLE_LOCK, open(os.devnull, "rb") as input_file:
        handles = [msvcrt.get_osfhandle(input_file.fileno()), msvcrt.get_osfhandle(stderr_file.fileno())]
        original = [os.get_handle_inheritable(handle) for handle in handles]
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESTDHANDLES
        info.hStdInput, info.hStdOutput, info.hStdError = handles[0], 0, handles[1]
        info.lpAttributeList = {"handle_list": handles}
        with ExitStack() as restore:
            for handle, inherited in reversed(list(zip(handles, original))):
                restore.callback(os.set_handle_inheritable, handle, inherited)
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            yield {"startupinfo": info, "close_fds": True}


def spawn(command, stderr_file, **options):
    # Supplying any ordinary Popen stream argument makes CPython construct a
    # replacement stdout pipe, defeating the engine's GUI-application test.
    if any(name in options for name in ("stdin", "stdout", "stderr", "startupinfo", "close_fds")):
        raise ValueError("DLSS startup owns its standard handles.")
    with gui_stdio(stderr_file) as windows_options:
        return subprocess.Popen(command, **options, **windows_options)


def append_transcript(path, text, *, limit=TRANSCRIPT_LIMIT):
    """Preserve early received output within a disk cap; recent_output keeps tails.

    Called only by a connection's reader. Reconnects append without erasing the
    first attempt. Open/close per chunk keeps evidence after abrupt app exit.
    """
    marker = b"\n[Dolly console transcript limit reached; see recent console output.]\n"
    if limit < len(marker):
        raise ValueError("Transcript limit is too small.")
    path = Path(path)
    with path.open("ab") as stream:
        remaining = max(0, limit - stream.tell())
        if not remaining:
            return
        data = text.encode("utf-8")
        if len(data) <= max(0, remaining - len(marker)):
            stream.write(data)
        else:
            prefix = data[:max(0, remaining - len(marker))].decode("utf-8", errors="ignore").encode("utf-8")
            stream.write(prefix + marker[:remaining - len(prefix)])
            # Consume spare bytes after a split multibyte character so a later
            # chunk cannot append a second marker or partial character.
            stream.write(b"\n" * max(0, remaining - len(prefix) - len(marker)))


def newest_sessions(logs_root):
    """Session folders, newest first by the launch time in their names.

    A name later than tomorrow was made while the clock was set ahead. Those
    go last, or they would outrank every real session until that date arrives.
    """
    limit = time.strftime("%Y%m%d_%H%M%S", time.gmtime(time.time() + 86400))
    folders = [path.parent for path in Path(logs_root).glob("*/session.json")
               if _SESSION_NAME.fullmatch(path.parent.name)]
    return sorted(folders, key=lambda folder: (folder.name[:15] <= limit, folder.name), reverse=True)


def prune_session_logs(logs_root, *, keep=RETAINED_SESSIONS):
    """Delete game logs from all but the newest ``keep`` session folders.

    Only the engine log and console transcript are removed; journals and the
    other small session files stay for recovery. ``keep`` matches how many
    sessions a diagnostics export reads, so nothing it would include is lost.
    Best-effort: a file that cannot be removed is left for the next launch.
    """
    try:
        folders = newest_sessions(logs_root)
    except OSError:
        return
    for folder in folders[keep:]:
        for name in SESSION_LOGS:
            try:
                (folder / name).unlink(missing_ok=True)
            except OSError:
                continue


def fatal_error(session_dir, *, tail=256_000, limit=300):
    """The last reason the engine logged for stopping itself, or None.

    Deadlock ends some crashes on purpose and says why first, for example a
    damaged content file. Read only the end of this launch's logs.
    """
    for name in SESSION_LOGS:
        try:
            with (Path(session_dir) / name).open("rb") as stream:
                size = stream.seek(0, os.SEEK_END)
                stream.seek(max(0, size - tail))
                text = stream.read(tail).decode("utf-8", errors="replace")
        except OSError:
            continue
        found = text.rfind("FATAL ERROR:")
        if found < 0:
            continue
        reason = " ".join(text[found + len("FATAL ERROR:"):].splitlines()[0].split())
        if reason:
            return reason if len(reason) <= limit else reason[:limit].rstrip() + "..."
    return None


def diagnostic_log(path, *, limit=2_000_000):
    """Keep startup and the latest failure when an engine log grows large."""
    path = Path(path)
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        stream.seek(0)
        if size <= limit:
            data = stream.read(limit)
        else:
            marker = b"\n[Dolly omitted the middle of this log; startup and latest output follow.]\n"
            head = min(64_000, limit // 2)
            data = stream.read(head)
            stream.seek(-(limit - head - len(marker)), os.SEEK_END)
            data += marker + stream.read(limit - head - len(marker))
    return data.decode("utf-8-sig", errors="replace")
