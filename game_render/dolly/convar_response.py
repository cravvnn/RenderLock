"""Parse current ConVar values from console response text without game access."""
import math
import re

from .path import CVAR_COMPONENTS, parse_cvar_value


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def read_cvar_value(name, output):
    """Parse the current value, never a value from the default/range description."""
    escaped = re.escape(name)
    for line in output.splitlines():
        if name in CVAR_COMPONENTS:
            # Read all components from the current-value field. Never accept a
            # short vector, a fifth component, or numbers in a default caption.
            prefix = re.search(r'(?<![\w])"?' + escaped + r'"?\s*(?:=|:)\s*', line, re.I)
            if not prefix:
                continue
            raw = line[prefix.end():].strip()
            if raw.startswith('"'):
                if '"' not in raw[1:]:
                    continue
                raw = raw[1:raw.find('"', 1)]
            else:
                raw = re.split(r'\s*(?:\(|\[|//)', raw, maxsplit=1)[0].strip()
            try:
                return parse_cvar_value(name, raw)
            except ValueError:
                continue
        match = re.search(r'(?<![\w])"?' + escaped + r'"?\s*(?:=|:)\s*"?(' + NUMBER + r'|true|false)(?=["\s,)\]]|$)', line, re.I)
        if match:
            raw = match.group(1).lower()
            value = 1.0 if raw == "true" else 0.0 if raw == "false" else float(raw)
            if math.isfinite(value):
                return value
    raise ValueError(f"Could not read the current value of {name}. Export diagnostics.")
