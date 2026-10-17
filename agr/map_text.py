"""Strict text-KV3 reader for Source 2 Viewer's map DATA dumps.

The CLI emits ValveKeyValue's text format, not JSON. In particular WriteText
uses a raw triple-quoted block for strings containing newlines. Its matching
KV3TokenReader strips the opening line break and one final line break, leaves
backslashes intact, and uses backslash parity to recognize the closing quotes.
See ValveKeyValue/Serialization/KeyValues3/KV3TextSerializer.cs (WriteText) and
Deserialization/KeyValues3/KV3TokenReader.cs (ReadQuotedStringRaw).

This map-specific parser does not alter the established hero converter/cache.
It tokenizes blobs in context, never by replacing text inside quoted strings.
"""
from __future__ import annotations

import math
import re


_NUMBER = re.compile(r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?')
_WORD = re.compile(r'[A-Za-z_.][A-Za-z_0-9.]*')
_HEADER = re.compile(r'<!--\s*kv3\s+encoding:text:version\{[0-9a-fA-F-]{36}\}'
                     r'\s+format:[A-Za-z_0-9]+:version\{[0-9a-fA-F-]{36}\}\s*-->')


class MapTextError(ValueError):
    """Location in the original CLI stdout, including its diagnostic preamble."""
    def __init__(self, message, text, offset):
        self.offset = max(0, min(offset, len(text)))
        self.line = text.count('\n', 0, self.offset) + 1
        previous = text.rfind('\n', 0, self.offset)
        self.column = self.offset - previous
        self.excerpt = text[max(0, self.offset - 80):self.offset + 120]
        super().__init__(f'{message} at line {self.line}, column {self.column} '
                         f'(offset {self.offset}); near {self.excerpt!r}')


class _Reader:
    def __init__(self, text):
        self.text, self.pos = text, 0
        self.pending = None

    def fail(self, message, position=None):
        raise MapTextError(message, self.text, self.pos if position is None else position)

    def skip(self):
        text = self.text
        while self.pos < len(text):
            if text[self.pos].isspace():
                self.pos += 1
            elif text.startswith('//', self.pos):
                end = text.find('\n', self.pos + 2)
                self.pos = len(text) if end < 0 else end + 1
            elif text.startswith('/*', self.pos):
                end = text.find('*/', self.pos + 2)
                if end < 0:
                    self.fail('Unterminated KV3 block comment')
                self.pos = end + 2
            else:
                break

    def quoted(self):
        text, start = self.text, self.pos
        if text.startswith('"""', start):
            self.pos += 3
            if text.startswith('\r\n', self.pos):
                self.pos += 2
            elif text.startswith('\n', self.pos):
                self.pos += 1
            else:
                self.fail('KV3 multiline opening quotes must be followed by a newline')
            begin, escaped = self.pos, False
            while self.pos < len(text):
                char = text[self.pos]
                if char == '\\':
                    escaped = not escaped
                    self.pos += 1
                    continue
                if not escaped and text.startswith('"""', self.pos):
                    value = text[begin:self.pos]
                    self.pos += 3
                    if value.endswith('\n'):
                        value = value[:-1]
                        if value.endswith('\r'):
                            value = value[:-1]
                    return value
                escaped = False
                self.pos += 1
            self.fail('Unterminated KV3 multiline string', start)
        # The CLI writer emits only double quotes. Single quotes and unknown
        # escapes are not silently reinterpreted as JSON or resource paths.
        self.pos += 1
        result = []
        escapes = {'n': '\n', 't': '\t', '\\': '\\', '"': '"', "'": "'"}
        while self.pos < len(text):
            char = text[self.pos]
            self.pos += 1
            if char == '"':
                return ''.join(result)
            if char == '\\':
                if self.pos >= len(text):
                    self.fail('Unterminated KV3 string escape', self.pos - 1)
                char = text[self.pos]
                self.pos += 1
                if char not in escapes:
                    self.fail('Unsupported KV3 string escape', self.pos - 2)
                result.append(escapes[char])
            else:
                # Unlike JSON, KV3 permits raw controls. The actual CLI writer
                # leaves CR, form-feed and vertical-tab unescaped; no newline
                # translation or blanket cleanup may change those values.
                result.append(char)
        self.fail('Unterminated KV3 string', start)

    def token(self):
        self.skip()
        text, start = self.text, self.pos
        if start == len(text):
            return ('end', None, start)
        if text[start] == '"':
            return ('string', self.quoted(), start)
        if text.startswith('#[', start):
            end = text.find(']', start + 2)
            if end < 0:
                self.fail('Unterminated KV3 byte blob', start)
            raw = text[start + 2:end]
            if not re.fullmatch(r'[0-9a-fA-F\s]*', raw):
                self.fail('Invalid KV3 byte blob', start)
            try:
                value = list(bytes.fromhex(raw))
            except ValueError:
                self.fail('Invalid KV3 byte pair', start)
            self.pos = end + 1
            return ('blob', value, start)
        if text[start] in '{}[]=,:':
            self.pos += 1
            return (text[start], text[start], start)
        number = _NUMBER.match(text, start)
        if number:
            self.pos = number.end()
            spelling = number.group()
            try:
                value = float(spelling) if any(c in spelling for c in '.eE') else int(spelling)
            except ValueError:
                self.fail('Invalid KV3 number', start)
            if isinstance(value, float) and not math.isfinite(value):
                self.fail('Non-finite KV3 number', start)
            return ('number', value, start)
        word = _WORD.match(text, start)
        if word:
            self.pos = word.end()
            return ('word', word.group(), start)
        self.fail('Unsupported KV3 token', start)

    def peek(self):
        if self.pending is None:
            self.pending = self.token()
        return self.pending

    def take(self, kind=None):
        item = self.peek()
        if kind is not None and item[0] != kind:
            self.fail(f'Expected {kind}, found {item[0]}', item[2])
        self.pending = None
        return item

    def value(self, depth=0):
        if depth >= 128:
            self.fail('KV3 nesting exceeds 128 levels', self.peek()[2])
        kind, value, start = self.take()
        if kind == '{':
            result = {}
            while self.peek()[0] != '}':
                key_kind, key, key_start = self.take()
                if key_kind not in ('word', 'string'):
                    self.fail('Expected KV3 property name', key_start)
                if key in result:
                    self.fail(f'Duplicate KV3 property {key!r}', key_start)
                self.take('=')
                result[key] = self.value(depth + 1)
                if self.peek()[0] == ',':
                    self.take(',')
            self.take('}')
            return result
        if kind == '[':
            result = []
            while self.peek()[0] != ']':
                result.append(self.value(depth + 1))
                if self.peek()[0] == ',':
                    self.take(',')
            self.take(']')
            return result
        if kind in ('string', 'number', 'blob'):
            return value
        if kind == 'word':
            if value in ('true', 'false', 'null'):
                return {'true': True, 'false': False, 'null': None}[value]
            if self.peek()[0] == ':':
                self.take(':')
                return self.value(depth + 1)  # Preserve flag payload, as before.
        self.fail('Unsupported KV3 value', start)


def parse_map_kv3(text):
    """Read exactly one map DATA object after the CLI's KV3 text header."""
    reader = _Reader(text)
    start = text.find('<!-- kv3')
    header = _HEADER.match(text, start) if start >= 0 else None
    if header is None:
        reader.fail('Missing or unsupported KV3 text header', max(0, start))
    reader.pos = header.end()
    result = reader.value()
    if not isinstance(result, dict):
        reader.fail('Map DATA must be a KV3 object', header.end())
    kind, _, start = reader.take()
    if kind != 'end':
        reader.fail('Trailing KV3 data', start)
    return result
