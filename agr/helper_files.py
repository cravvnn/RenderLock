"""Content identities for managed helpers and their platform-native companions."""
from pathlib import Path
import re

from .asset_converter import ConversionError, sha


def helper_file_inventory(directory):
    """Include Linux native libraries in copies and cache keys, without loading them.

    This records bytes; it does not qualify a new decoder or native library.
    Keep the Windows inventory unchanged when no Unix companions are present.
    """
    root = Path(directory).resolve()
    files = {}
    for path in sorted(root.rglob('*')):
        name = path.name.lower()
        included = path.suffix.lower() in ('.dll', '.json', '.exe', '.dylib') or bool(
            re.search(r'\.so(?:\.\d+)*$', name))
        if not included or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            raise ConversionError('Helper companion escapes its source directory: ' + path.name)
        files[path.relative_to(root).as_posix()] = sha(path)
    return files
