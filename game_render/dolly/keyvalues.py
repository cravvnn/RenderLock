"""Valve KeyValues syntax with source offsets for localized configuration edits."""
from __future__ import annotations

from dataclasses import dataclass

from .launch_errors import LaunchError


@dataclass(frozen=True)
class _Token:
    value: str
    start: int
    end: int
    kind: str = "text"


@dataclass
class _Entry:
    key: _Token
    value: _Token | None = None
    children: list["_Entry"] | None = None
    opening: _Token | None = None
    closing: _Token | None = None
    end: int = 0


def _tokens(text: str) -> list[_Token]:
    """Tokenize Valve KeyValues, retaining offsets for localized edits."""
    out: list[_Token] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace() or text[pos] == "\ufeff":
            pos += 1
            continue
        if text.startswith("//", pos):
            end = text.find("\n", pos + 2)
            pos = len(text) if end < 0 else end + 1
            continue
        if text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            if end < 0:
                raise LaunchError("gameinfo.gi contains an unterminated block comment.")
            pos = end + 2
            continue
        start = pos
        if text[pos] in "{}":
            out.append(_Token(text[pos], pos, pos + 1, "brace"))
            pos += 1
        elif text[pos] == '"':
            pos += 1
            value = []
            while pos < len(text) and text[pos] != '"':
                if text[pos] == "\\" and pos + 1 < len(text) and text[pos + 1] in '\\"':
                    pos += 1
                value.append(text[pos])
                pos += 1
            if pos == len(text):
                raise LaunchError("gameinfo.gi contains an unterminated quoted value.")
            pos += 1
            out.append(_Token("".join(value), start, pos))
        elif text[pos] == "[":
            end = text.find("]", pos + 1)
            if end < 0:
                raise LaunchError("gameinfo.gi contains an unterminated condition.")
            pos = end + 1
            out.append(_Token(text[start:pos], start, pos, "condition"))
        else:
            while pos < len(text) and not text[pos].isspace() and text[pos] not in '{}"':
                if text.startswith("//", pos) or text.startswith("/*", pos):
                    break
                pos += 1
            if pos == start:
                raise LaunchError("Unsupported gameinfo.gi syntax.")
            out.append(_Token(text[start:pos], start, pos))
    return out


def _parse(text: str) -> list[_Entry]:
    tokens = _tokens(text)
    index = 0

    def block(nested: bool = False) -> tuple[list[_Entry], _Token | None]:
        nonlocal index
        entries: list[_Entry] = []
        while index < len(tokens):
            key = tokens[index]
            if key.value == "}" and key.kind == "brace":
                if not nested:
                    raise LaunchError("gameinfo.gi has an unmatched closing brace.")
                index += 1
                return entries, key
            if key.kind != "text":
                raise LaunchError("Unsupported gameinfo.gi key syntax.")
            index += 1
            if index >= len(tokens):
                raise LaunchError("gameinfo.gi ends before a key's value.")
            value = tokens[index]
            index += 1
            if value.value == "{" and value.kind == "brace":
                children, closing = block(True)
                assert closing is not None
                entry = _Entry(key, children=children, opening=value, closing=closing, end=closing.end)
            elif value.kind == "text":
                entry = _Entry(key, value=value, end=value.end)
            else:
                raise LaunchError("Unsupported gameinfo.gi value syntax.")
            while index < len(tokens) and tokens[index].kind == "condition":
                entry.end = tokens[index].end
                index += 1
            entries.append(entry)
        if nested:
            raise LaunchError("gameinfo.gi has an unclosed block.")
        return entries, None

    try:
        return block()[0]
    except RecursionError as exc:
        raise LaunchError("Configuration nesting exceeds the parser capacity.") from exc


def _named(entries: list[_Entry], name: str) -> list[_Entry]:
    return [entry for entry in entries if entry.key.value.casefold() == name.casefold()]
