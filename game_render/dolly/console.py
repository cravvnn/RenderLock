"""Local Source 2 console transport for the replay controller.

VConsole packet framing is independently implemented from the protocol described
by theokyr/CS2RemoteConsole (libvconsole/src/vconsole.cpp) and
uilton-oliveira/VConsoleLib.python.  No engine addresses or game-memory access.
This module does not establish replay authorization: the caller must do that
before it sends camera commands. ``send`` / ``request`` are internal transports,
never endpoints accepting arbitrary text from project files or a web UI.
"""
from __future__ import annotations

from collections import deque
from contextlib import ExitStack
import codecs
import ipaddress
import logging
import math
import re
import secrets
import socket
import struct
import threading
import time
from typing import Literal


LOG = logging.getLogger(__name__)

VCONSOLE_VERSION = 0x00D40000
MAX_PACKET_SIZE = 65535
_HEADER = struct.Struct("!4sIHH")

# These two engine warnings can repeat continuously while a replay plays. The
# first has no terminating newline, so the game's next print can be attached to
# it. Keep the unmodified stream in recent_output(); only command replies omit
# these exact messages so unrelated errors and the normal response limit remain.
_REPLAY_COMMAND_WARNINGS = (
    "Ignoring CSVCMsg_UserCommands_t with no command data",
    "Ignoring CSVCMsg_UserCommands_t: Missing command to delta from",
)


class _ReplayWarningFilter:
    """Remove known replay-warning text across arbitrary received chunks."""

    def __init__(self):
        self.pending = ""
        self.suppressed = 0

    def feed(self, chunk: str) -> str:
        text = self.pending + chunk
        self.pending = ""
        for warning in _REPLAY_COMMAND_WARNINGS:
            self.suppressed += text.count(warning)
            text = text.replace(warning, "")
        # Both known messages start with I. Retain a possible incomplete
        # prefix so a warning split across TCP reads is not leaked piecemeal.
        index = text.rfind("I")
        if index >= 0 and any(warning.startswith(text[index:]) for warning in _REPLAY_COMMAND_WARNINGS):
            self.pending, text = text[index:], text[:index]
        return text


def _console_fallback_encoding() -> str:
    """Active Windows ANSI code page, used when console bytes are not UTF-8."""
    try:
        import ctypes
        acp = ctypes.windll.kernel32.GetACP()
        if isinstance(acp, int) and acp > 0:
            return f"cp{acp}"
    except Exception:  # noqa: BLE001 - optional platform detail
        pass
    import locale
    return locale.getpreferredencoding(False) or "cp1252"


def _decode_console_text(data: bytes) -> str:
    """Decode console text, falling back to the active ANSI code page.

    Source 2's Windows console can emit non-ASCII paths (for example a
    Cyrillic OneDrive folder) in the active ANSI code page rather than UTF-8.
    Strict UTF-8 is tried first so ordinary output is unchanged.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(_console_fallback_encoding(), errors="replace")


class _ConsoleTextDecoder:
    """Incremental netcon decoder with an ANSI fallback for non-UTF-8 text.

    Buffers an incomplete UTF-8 tail across TCP reads. When the pending bytes
    are genuinely invalid UTF-8, decodes them with the active ANSI code page.
    """

    def __init__(self) -> None:
        self._pending = b""

    def feed(self, data: bytes) -> str:
        self._pending += data
        if not self._pending:
            return ""
        try:
            text = self._pending.decode("utf-8")
            self._pending = b""
            return text
        except UnicodeDecodeError as exc:
            if exc.reason == "unexpected end of data":
                prefix = self._pending[:exc.start].decode("utf-8")
                self._pending = self._pending[exc.start:]
                return prefix
            text = self._pending.decode(_console_fallback_encoding(), errors="replace")
            self._pending = b""
            return text

    def flush(self) -> str:
        text = _decode_console_text(self._pending)
        self._pending = b""
        return text
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class ConsoleError(RuntimeError):
    """Connection, protocol, or command-response error."""


class ConsoleTimeout(ConsoleError, TimeoutError):
    """A response was not completed within the requested deadline."""


def error_text(output: str) -> str | None:
    """Return a recognizable console rejection, or None (not proof of success)."""
    patterns = (
        r"\bunknown (?:command|convar|cvar)\b", r"\bno such (?:command|convar|cvar)\b",
        r"\bno (?:cvar|convar) or command named\b",
        r"\b(?:command|convar|cvar).+\bnot found\b", r"\bis cheat protected\b",
        r"\bcheat command.+\bignored\b", r"\b(?:cannot|can't) (?:change|set|use)\b",
        r"\bnot currently playing back a demo\b", r"^\s*(?:\[[^\]]*\]\s*)*error\s*[:\-]",
        r"\bnot allowed\b", r"\brequires?\s+sv_cheats\b",
    )
    for line in _ANSI.sub("", output).splitlines():
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns):
            return line.strip()
    return None


def parse_demo_info(output: str) -> dict:
    """Parse replay identity and metadata, without inventing a playback tick.

    The ``Demo contents for`` / protobuf metadata format was observed in a
    Deadlock 2026-09-07 runtime report. Its playback_ticks and playback_time are
    TOTAL duration, not the current playback position. ``tick`` remains None.
    A metadata-derived tick_rate is descriptive, never a live clock.

    The legacy status and pause formats appear in GameTracking-Deadlock,
    game/bin/win64/engine2_strings.txt at 70a264134418d4546776e3a993245fdb12138200:
      Playing back demo: '%s' at tick %u
      Error - Not currently playing back a demo.
      Demo paused at engine time %g, demo tick %d

    A pause notification alone is not a fresh demo_info response. No pause or
    speed state is inferred from a stationary tick.
    """
    clean = _ANSI.sub("", output)
    if re.search(r"Error\s*-\s*Not currently playing back a demo\.", clean, re.I):
        return {"playing": False, "tick": None, "name": None, "paused": None}
    matches = list(re.finditer(r"Playing back demo:\s*'([^\r\n]+)'\s+at tick\s+(\d+)\b", clean))
    if not matches:
        headers = list(re.finditer(r"^\s*Demo contents for\s+([^\r\n]+):\s*$", clean, re.M))
        if not headers:
            raise ValueError("Unrecognized demo_info output; capture diagnostics to update compatibility.")
        header = headers[-1]
        metadata = clean[header.end():]
        if not re.search(r"^\s*DemoFileHeader:", metadata, re.M):
            raise ValueError("Incomplete demo_info response: missing DemoFileHeader.")
        info = re.search(r"^\s*DemoFileInfo:\s*(.*)", metadata, re.M | re.S)
        if not info:
            raise ValueError("Incomplete demo_info response: missing DemoFileInfo.")
        fields = info[1]
        tick_field = re.search(r"(?:^|\n)\s*playback_ticks:\s*([^\s]+)", fields)
        time_field = re.search(r"(?:^|\n)\s*playback_time:\s*([^\s]+)", fields)
        total_ticks = None
        playback_time = None
        if tick_field:
            if not re.fullmatch(r"\d+", tick_field[1]):
                raise ValueError("Invalid total playback_ticks in demo_info metadata.")
            total_ticks = int(tick_field[1])
        if time_field:
            try:
                playback_time = float(time_field[1])
            except ValueError as exc:
                raise ValueError("Invalid total playback_time in demo_info metadata.") from exc
            if not math.isfinite(playback_time) or playback_time < 0:
                raise ValueError("Invalid total playback_time in demo_info metadata.")
        tick_rate = (total_ticks / playback_time
                     if total_ticks is not None and total_ticks > 0 and playback_time
                     else None)
        return {"playing": True, "tick": None, "name": header[1].strip(),
                "paused": None, "total_ticks": total_ticks,
                "playback_time": playback_time, "tick_rate": tick_rate}
    match = matches[-1]
    paused = None
    pause_matches = list(re.finditer(r"Demo paused at engine time\s+[-+0-9.eE]+,\s*demo tick\s+(\d+)\b", clean))
    if pause_matches and pause_matches[-1].start() > match.start() and int(pause_matches[-1][1]) == int(match[2]):
        paused = True
    return {"playing": True, "tick": int(match[2]), "name": match[1], "paused": paused}


def parse_demo_tick(output: str) -> dict:
    """Read only the live status line returned by a bare ``demo_goto`` query.

    The current Deadlock engine string is:
      Currently playing %d of %d ticks. Minutes:%.2f File:%s
    The older Source status line lacks File. In that case callers must obtain
    fresh replay identity separately. Minutes and the second tick count describe
    total length and cannot replace the first, current tick count.
    """
    clean = _ANSI.sub("", output)
    if re.search(r"Error\s*-\s*Not currently playing back a demo\.", clean, re.I):
        return {"playing": False, "tick": None, "total_ticks": None,
                "name": None, "paused": None}
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    pattern = (r"^[ \t]*(?:\[[^\]\r\n]*\][ \t]*)*Currently playing[ \t]+(\d+)[ \t]+of[ \t]+(\d+)"
               r"[ \t]+ticks\.[ \t]+Minutes:[ \t]*(" + number + r")"
               r"(?:[ \t]+File:[ \t]*([^\r\n]+))?[ \t]*\r?$")
    matches = list(re.finditer(pattern, clean, re.M))
    if not matches:
        raise ValueError("Unrecognized live replay status from demo_goto; export diagnostics to update compatibility.")
    match = matches[-1]
    current, total, minutes = int(match[1]), int(match[2]), float(match[3])
    if current > total or not math.isfinite(minutes) or minutes < 0:
        raise ValueError("Invalid live replay tick range in demo_goto response.")
    name = match[4].strip() if match[4] else None
    if match[4] is not None and not name:
        raise ValueError("Missing replay filename in demo_goto response.")
    if name and name[0] in "\"'" and name[-1] == name[0]:
        name = name[1:-1]
    return {"playing": True, "tick": current, "total_ticks": total,
            "name": name or None, "paused": None}


_GAME_BUILD = re.compile(r"^\s*Build:\s*(\d+)\s*\(revision\s+\d+\)\s*$", re.MULTILINE)


def parse_game_build(output: str) -> int | None:
    """Read the installed game's internal build from its console banner.

    The engine prints ``Build: <n> (revision <n>)`` once the client joins its
    local server (the hideout or a replay). A replay header records that same
    number in ``build_num``, so this distinguishes a replay recorded on a
    different build from one this build simply cannot reconstruct. Returns the
    last observed build, or None when the banner is absent.
    """
    if not isinstance(output, str):
        return None
    builds = _GAME_BUILD.findall(_ANSI.sub("", output))
    return int(builds[-1]) if builds else None


def encode_command(command: str, version: int = VCONSOLE_VERSION) -> bytes:
    """Encode a single trusted console command or semicolon command batch."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Command must be a nonempty string.")
    if any(char in command for char in ("\x00", "\r", "\n")):
        raise ValueError("Console command cannot contain NUL or line breaks.")
    payload = command.encode("utf-8") + b"\x00"
    length = _HEADER.size + len(payload)
    if length > MAX_PACKET_SIZE:
        raise ValueError("Console command exceeds the VConsole packet limit.")
    return _HEADER.pack(b"CMND", version, length, 0) + payload


class VConsoleDecoder:
    """Bounded streaming decoder; accepts split and coalesced TCP packets."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[str]:
        self._buffer.extend(data)
        result = []
        while len(self._buffer) >= _HEADER.size:
            kind, _version, length, _handle = _HEADER.unpack_from(self._buffer)
            if not all(65 <= byte <= 90 or 48 <= byte <= 57 for byte in kind):
                raise ConsoleError("Invalid VConsole packet type. Check console protocol/launch port.")
            if not _HEADER.size <= length <= MAX_PACKET_SIZE:
                raise ConsoleError("Invalid VConsole packet length.")
            if len(self._buffer) < length:
                break
            payload = bytes(self._buffer[_HEADER.size:length])
            del self._buffer[:length]
            if kind == b"PRNT":
                if len(payload) < 28:
                    raise ConsoleError("Truncated VConsole PRNT metadata.")
                # channel int32 + 20 unknown bytes + RGBA uint32 = 28 bytes.
                result.append(_decode_console_text(payload[28:].rstrip(b"\x00")))
            # AINF, CHAN, ADON, CVAR, CFGV, etc. are framed and ignored.
        return result


class ConsoleClient:
    """Thread-safe console connection restricted to numeric loopback addresses.

    A background reader drains game logs even between requests. Request output
    is delimited by random begin/end echoes; TCP fragmentation and echoed command
    text do not end a request early. Requests execute serially. An echo marks
    console processing, not completion of asynchronous work or proof that a
    renderer visibly applied a setting. Empty output is not proof of failure or
    success. ``recent_output`` retains received text outside request boundaries
    for diagnostics, including logs that arrive after an end marker. A caller
    that knows a command's completion messages may supply ``completion_patterns``
    to keep collecting that request until those messages actually arrive.
    """

    def __init__(self, protocol: Literal["vconsole", "netcon"] = "vconsole", *,
                 max_response_bytes: int = 1024 * 1024, version: int = VCONSOLE_VERSION,
                 transcript_path=None) -> None:
        if protocol not in {"vconsole", "netcon"}:
            raise ValueError("Protocol must be vconsole or netcon.")
        if max_response_bytes < 1024:
            raise ValueError("Response limit must be at least 1024 bytes.")
        self.transcript_path = transcript_path
        self.transcript_error = None
        self.protocol = protocol
        self.version = version
        self.max_response_bytes = max_response_bytes
        self._socket: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.RLock()
        self._condition = threading.Condition()
        self._events: deque[tuple[int, str]] = deque()
        self._event_bytes = 0
        self._sequence = 0
        self._failure: str | None = None
        self._replay_warning_logged = False

    @property
    def is_connected(self) -> bool:
        with self._condition:
            return self._socket is not None and self._failure is None

    def recent_output(self, limit_bytes: int = 262144) -> str:
        """Return a bounded tail of this connection's received console text.

        This is diagnostic evidence only, not an acknowledgement for a new
        request: the tail may contain old responses or unrelated engine logs.
        Reading it neither consumes events nor changes a pending request.
        """
        if isinstance(limit_bytes, bool) or not isinstance(limit_bytes, int) or limit_bytes <= 0:
            raise ValueError("Console history limit must be a positive integer.")
        with self._condition:
            chunks = [chunk for _, chunk in self._events]
        # The event queue is already bounded by max_response_bytes * 2. If a
        # slice starts inside a multibyte character, discard that partial prefix.
        return "".join(chunks).encode("utf-8")[-limit_bytes:].decode("utf-8", errors="ignore")

    def connect(self, host: str = "127.0.0.1", port: int = 29090, timeout: float = 3.0) -> None:
        if host == "localhost":
            host = "127.0.0.1"
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError as exc:
            raise ValueError("Only numeric loopback console addresses are supported.") from exc
        if not is_loopback:
            raise ValueError("The dolly console must be on this computer's loopback address.")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Invalid console port.")
        _validate_timeout(timeout)
        with self._lock:
            self.close()
            try:
                with ExitStack() as setup:
                    sock = socket.create_connection((host, port), timeout=timeout)
                    setup.callback(sock.close)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.settimeout(0.25)
                    setup.pop_all()
            except OSError as exc:
                raise ConsoleError(f"Cannot connect to local {self.protocol} on {host}:{port}: {exc}") from exc
            with self._condition:
                self._socket = sock
                self._failure = None
                self._events.clear()
                self._event_bytes = 0
                self._sequence = 0
                self._replay_warning_logged = False
            try:
                self._reader = threading.Thread(target=self._read_loop, args=(sock,), daemon=True, name="dolly-console")
                self._reader.start()
            except RuntimeError:
                self._reader = None
                self.close()
                raise

    def close(self) -> None:
        # May be called during a pending request to cancel it immediately.
        with self._condition:
            sock = self._socket
            self._socket = None
            self._failure = "Console connection closed."
            self._condition.notify_all()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=0.5)
        self._reader = None

    def _read_loop(self, sock: socket.socket) -> None:
        decoder = VConsoleDecoder()
        text_decoder = _ConsoleTextDecoder()
        try:
            while True:
                with self._condition:
                    if self._socket is not sock:
                        return
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    raise ConsoleError("Game closed the console connection.")
                chunks = decoder.feed(data) if self.protocol == "vconsole" else [text_decoder.feed(data)]
                if self.transcript_path is not None and self.transcript_error is None:
                    try:
                        from .game_logging import append_transcript
                        append_transcript(self.transcript_path, "".join(chunks))
                    except OSError as exc:
                        self.transcript_error = str(exc)
                        LOG.warning("Could not save game console transcript: %s", exc)
                with self._condition:
                    if self._socket is not sock:
                        return
                    for chunk in chunks:
                        if not chunk:
                            continue
                        self._sequence += 1
                        self._events.append((self._sequence, chunk))
                        self._event_bytes += len(chunk.encode("utf-8"))
                    while self._event_bytes > self.max_response_bytes * 2 and self._events:
                        _, old = self._events.popleft()
                        self._event_bytes -= len(old.encode("utf-8"))
                    self._condition.notify_all()
        except (OSError, ConsoleError) as exc:
            with self._condition:
                if self._socket is sock:
                    self._failure = str(exc)
                    self._condition.notify_all()
        except Exception as exc:  # noqa: BLE001 - a decoder failure must fail requests, not silently kill the reader
            # Any unexpected error used to kill this thread while is_connected
            # stayed true, so every later request burned its full timeout and
            # reported a misleading timeout. Record a real failure instead.
            with self._condition:
                if self._socket is sock:
                    self._failure = f"Console reader failed: {exc}"
                    self._condition.notify_all()

    def _send_locked(self, command: str) -> None:
        self._send_commands_locked((command,))

    def _send_commands_locked(self, commands: tuple[str, ...]) -> None:
        # Validate every command before writing any bytes. Keep the individual
        # protocol frames intact, but submit a request's delimiters/command in
        # one write so separate sends do not introduce extra polling delays.
        encoded = [encode_command(command, self.version) for command in commands]
        data = (b"".join(encoded) if self.protocol == "vconsole" else
                b"".join(command.encode("utf-8") + b"\n" for command in commands))
        with self._condition:
            sock, failure = self._socket, self._failure
        if sock is None or failure:
            raise ConsoleError(failure or "Console is not connected.")
        try:
            sock.sendall(data)
        except OSError as exc:
            with self._condition:
                self._failure = f"Console send failed: {exc}"
                self._condition.notify_all()
            raise ConsoleError(self._failure) from exc

    def send(self, command: str) -> None:
        """Send a trusted batch without waiting. Caller owns replay guards."""
        with self._lock:
            self._send_locked(command)

    def request(self, command: str, timeout: float = 2.0, *,
                completion_patterns: tuple[str, ...] | None = None,
                allow_truncated: bool = False) -> str:
        """Send a trusted command and collect its fresh, bounded response.

        Normally the end echo finishes collection. With ``completion_patterns``,
        all trusted regexes must also match response text, or a recognizable
        rejection must arrive. This permits an initialization command's delayed
        log messages without delaying ordinary camera-frame requests. The same
        timeout covers echo acknowledgement and semantic completion together;
        the serial request lock is held throughout. Historical output is never
        used to satisfy these patterns.

        When ``allow_truncated`` is set and the collected text exceeds
        ``max_response_bytes`` (a developer build can flood the console with its
        own data-validation dump), collection stops and the bounded tail is
        returned instead of raising. The tail starts after the last known
        response marker so a normal response printed at the end of the flood is
        still present for parsing. Use this for startup probes whose caller can
        treat an unusable response as "not ready yet".
        """
        _validate_timeout(timeout)
        encode_command(command, self.version)
        patterns = _compile_completion_patterns(completion_patterns)
        with self._lock:
            begin = "DOLLY_BEGIN_" + secrets.token_hex(12)
            end = "DOLLY_END_" + secrets.token_hex(12)
            # Match a printed marker, never `echo MARKER` in a command echo.
            begin_re = _marker_re(begin)
            end_re = _marker_re(end)
            with self._condition:
                cursor = self._sequence
            deadline = time.monotonic() + timeout
            # Panorama can print warnings without a terminating newline.
            # Put each printed marker on its own line instead of accepting
            # arbitrary prefixes (which would also accept command echoes).
            self._send_commands_locked(('echo ""', "echo " + begin, command,
                                        'echo ""', "echo " + end))
            output = ""
            began = False
            ended = False
            warning_filter = _ReplayWarningFilter()
            while True:
                with self._condition:
                    if self._events and cursor < self._events[0][0] - 1:
                        raise ConsoleError("Console log overflowed while waiting for a response.")
                    new = [(seq, text) for seq, text in self._events if seq > cursor]
                    if new:
                        cursor = new[-1][0]
                    failure = self._failure
                for _, chunk in new:
                    output += warning_filter.feed(chunk)
                if warning_filter.suppressed and not self._replay_warning_logged:
                    LOG.warning("Game reported missing replay user commands; repeated warnings are omitted "
                                "from command replies but remain in the bounded console diagnostics.")
                    self._replay_warning_logged = True
                if not began:
                    found = _find_printed_marker(output, begin_re)
                    if found:
                        output = output[found[1]:]
                        began = True
                if began and not ended:
                    found = _find_printed_marker(output, end_re)
                    if found:
                        response = output[:found[0]]
                        if patterns:
                            # A worker may print its completion after the echo.
                            # Keep that fresh suffix while discarding delimiters.
                            response += output[found[1]:]
                        # A line console can print the entered `echo END` before
                        # it prints END itself. Keep delimiters out of results.
                        output = "".join(line for line in response.splitlines(keepends=True)
                                         if not (end in line and "echo" in line))
                        ended = True
                if len(output.encode("utf-8")) > self.max_response_bytes:
                    if not allow_truncated:
                        raise ConsoleError(
                            "Console response exceeds the configured size limit "
                            f"({self.max_response_bytes} bytes) for {command.split()[0]!r}; "
                            "the game console is flooding. Repair or verify the game files, "
                            "or restart the session.")
                    # A flooded console may still end with the real response. Keep
                    # only a bounded rolling tail while waiting for the end echo or
                    # deadline, so a normal response printed after the flood is
                    # preserved without unbounded growth.
                    if len(output) > 2 * self.max_response_bytes:
                        output = output[-self.max_response_bytes:]
                if ended and (not patterns or error_text(output)
                              or all(pattern.search(output) for pattern in patterns)):
                    if allow_truncated:
                        # Prefer the tail after the last known response marker so a
                        # normal response printed after the flood still parses.
                        marker = max(output.rfind("Demo contents for"),
                                     output.rfind("Playing back demo:"))
                        if marker > 0:
                            output = output[marker:]
                        if len(output.encode("utf-8")) > self.max_response_bytes:
                            output = output.encode("utf-8")[-self.max_response_bytes:].decode(
                                "utf-8", errors="ignore")
                    return output.strip("\r\n")
                if failure:
                    raise ConsoleError(failure)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if ended and patterns:
                        raise ConsoleTimeout(
                            f"{self.protocol} acknowledged {command.split()[0]!r}, but the expected "
                            f"completion messages did not arrive within {timeout:g}s. "
                            "Export diagnostics to inspect the received console text.")
                    raise ConsoleTimeout(f"No complete {self.protocol} response to {command.split()[0]!r} within {timeout:g}s.")
                with self._condition:
                    if self._sequence == cursor and not self._failure:
                        self._condition.wait(timeout=remaining)

    def supports(self, name: str, timeout: float = 2.0) -> bool | None:
        """Conservative name availability check: True, False, or None if unknown.

        False means the console explicitly rejected the name. None means the
        check could not be completed (a developer data-validation flood delayed
        the echo past the window, or the response was truncated/overflowed),
        which is NOT evidence the name is missing; a caller must not treat None
        as a rejection.
        """
        if not _NAME.fullmatch(name):
            raise ValueError("Expected one plain console variable/command name.")
        try:
            output = self.request("help " + name, timeout, allow_truncated=True)
        except ConsoleError:
            # A flooded console (developer data-validation dump) is not proof the
            # name is unusable; report availability as unknown, not rejected.
            return None
        if error_text(output):
            return False
        lines = [line for line in output.splitlines() if not re.search(r"\bhelp\s+" + re.escape(name) + r"\b", line)]
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", "\n".join(lines)):
            return True
        # Neither a printed help line nor an explicit rejection: a -dev flood
        # can swallow the response, so this is unknown, never a rejection.
        return None


def _marker_re(marker: str) -> re.Pattern:
    return re.compile(re.escape(marker) + r"[ \t\r]*\n")


def _find_printed_marker(output: str, marker: re.Pattern) -> tuple[int, int] | None:
    """Find an actual echo, including one glued to a known warning's tail.

    A request starts at a fresh history cursor, which can cut into the warning
    already being printed. Accept only a line prefix that is a suffix of one of
    those exact warnings; an echoed command such as ``> echo MARKER`` still
    cannot acknowledge a request. Never use earlier history as a response.
    """
    for match in marker.finditer(output):
        line_start = output.rfind("\n", 0, match.start()) + 1
        prefix = output[line_start:match.start()]
        normal = re.fullmatch(r"[ \t]*(?:\[[^\]\r\n]*\][ \t]*)*", prefix)
        warning_tail = prefix and any(warning.endswith(prefix) for warning in _REPLAY_COMMAND_WARNINGS)
        if normal or warning_tail:
            return line_start, match.end()
    return None


def _compile_completion_patterns(values: tuple[str, ...] | None) -> tuple[re.Pattern, ...]:
    if values is None:
        return ()
    if not isinstance(values, tuple) or not 1 <= len(values) <= 8:
        raise ValueError("Completion patterns must be a tuple containing 1 to 8 trusted regexes.")
    if any(not isinstance(value, str) or not value or len(value) > 512 for value in values):
        raise ValueError("Each completion pattern must contain 1 to 512 characters.")
    try:
        return tuple(re.compile(value, re.MULTILINE) for value in values)
    except re.error as exc:
        raise ValueError(f"Invalid completion pattern: {exc}") from exc


def _validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and greater than zero.")
