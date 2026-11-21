"""Bounded, plain-text previews of the publisher's Before/After release bullets."""
from __future__ import annotations

import re

MAX_BODY_BYTES = 32 * 1024
MAX_ENTRIES = 4
MAX_TITLE = 120
MAX_DETAIL = 600

_VERSION = re.compile(r"v?[0-9]+\.[0-9]+\.[0-9]+(?:-(?:alpha|beta|rc)(?:[.-]?[0-9]+)?)?")
_HEADING = re.compile(r"\s*(?:#{1,6}\s+)?\*\*([^*]+)\*\*\s*")
_PLAIN_HEADING = re.compile(r"\s*#{1,6}\s+(.*?)\s*")
_VERSION_TOKEN = re.compile(r"(?<![0-9A-Za-z])" + _VERSION.pattern + r"(?![0-9A-Za-z])")
_BULLET = re.compile(r" {0,3}[-+*]\s+(.+)")
_ENTRY = re.compile(r"\*\*(?P<title>.+?)\*\*\s+\*Before:\*\s*(?P<before>.+?)\s+\*After:\*\s*(?P<after>.+)")
_FENCE = re.compile(r"\s*(`{3,}|~{3,})(.*)")
_LINK = re.compile(r"!?\[([^\]\n]+)\]\([^\n)]*\)")


def _plain(value, limit):
    value = _LINK.sub(r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    for delimiter in (r"\*\*", "__", "~~"):
        value = re.sub(delimiter + r"(.+?)" + delimiter, r"\1", value)
    value = re.sub(r"(?<!\w)([*_])(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def parse_release_notes(body, version):
    """Read only matching-version authored bullets; unavailable previews are [].

    This consumes optional metadata already returned by GitHub. It neither
    fetches a page nor interprets HTML, Markdown links, or executable content.
    Oversized bodies and unsupported formats have no prose fallback.
    """
    if not isinstance(body, str) or not isinstance(version, str) or not _VERSION.fullmatch(version):
        return []
    if len(body) > MAX_BODY_BYTES:
        return []
    try:
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            return []
    except UnicodeError:
        return []
    version = version.removeprefix("v")
    entries = []
    pending = []
    active = False
    fence = None

    def finish():
        if not pending:
            return
        entry = _ENTRY.fullmatch(" ".join(pending))
        pending.clear()
        if entry is None or len(entries) >= MAX_ENTRIES:
            return
        fields = entry.groupdict()
        # Multiple or reversed markers are malformed, rather than extra prose
        # to guess at or silently assign to a different Before/After field.
        if any("*Before:*" in value or "*After:*" in value for value in fields.values()):
            return
        result = {name: _plain(value, MAX_TITLE if name == "title" else MAX_DETAIL)
                  for name, value in fields.items()}
        if all(result.values()):
            entries.append(result)

    for line in body.splitlines():
        marker = _FENCE.fullmatch(line)
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            finish()
            fence = marker[1]
            continue
        heading = _HEADING.fullmatch(line)
        if heading:
            finish()
            heading_version = heading[1].strip()
            active = bool(_VERSION.fullmatch(heading_version) and
                          heading_version.removeprefix("v") == version)
            continue
        plain = _PLAIN_HEADING.fullmatch(line)
        if plain:
            finish()
            # The publisher's long-standing body style opens with a plain
            # heading that names the release ("### Deadlock Dolly
            # v0.6.54-alpha"); it starts the preview exactly like a bold
            # version line. A heading without the version still ends the
            # preview, so ordinary section headings stay out of the notes.
            active = any(token.group(0).removeprefix("v") == version
                         for token in _VERSION_TOKEN.finditer(plain[1]))
            continue
        if re.match(r"\s*#{1,6}(?:\s|$)", line):
            finish()
            active = False
            continue
        if not active:
            continue
        bullet = _BULLET.fullmatch(line)
        if bullet:
            finish()
            pending.append(bullet[1].strip())
        elif not line.strip():
            finish()
        elif pending:
            pending.append(line.strip())
    finish()
    return entries
