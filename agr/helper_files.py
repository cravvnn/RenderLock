"""Content identities for managed helpers and their platform-native companions."""
from pathlib import Path
import re
import sys

from .asset_converter import ConversionError, sha


def native_image_path(directory):
    """Locate a helper's host-native image companion; never select a foreign DLL.

    Finding/hashing a file establishes dependency identity, not pixel qualification.
    """
    root = Path(directory).resolve()
    if sys.platform == 'win32':
        relative = ('runtimes/win-x64/native/libSkiaSharp.dll', 'libSkiaSharp.dll')
    elif sys.platform.startswith('linux'):
        relative = ('runtimes/linux-x64/native/libSkiaSharp.so', 'libSkiaSharp.so')
    else:
        raise ConversionError('Material-group native image helper platform is unsupported')
    for name in relative:
        candidate = root / name
        if candidate.is_file():
            if not candidate.resolve().is_relative_to(root):
                raise ConversionError('Helper companion escapes its source directory: ' + candidate.name)
            return candidate
    raise ConversionError('Material-group exporter native image decoder is missing for this platform')


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
