"""Load source images through native paths and verify their packed payloads."""
from __future__ import annotations

import hashlib
from .path_io import native_path


def load_packed_image(path, *, check_existing=False, expected_sha256=None):
    """Load an actual source file, retaining exact bytes without placeholders.

    A missing/invalid image raises before assignment to a material. The optional
    hash binds the source to an existing qualified descriptor; packing itself
    must retain the same file bytes even when the Windows path exceeds MAX_PATH.
    """
    import bpy
    source = native_path(path)
    with open(source, "rb") as stream:
        raw = stream.read()
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Image source checksum changed: " + str(path))
    previous = set(bpy.data.images)
    image = None
    try:
        image = bpy.data.images.load(source, check_existing=check_existing)
        image.pack()
        if (not image.packed_file or bytes(image.packed_file.data) != raw
                or len(image.size) != 2 or any(size <= 0 for size in image.size)):
            raise ValueError("Image did not retain its exact source bytes: " + str(path))
        return image
    except BaseException:
        if image is not None and image not in previous:
            bpy.data.images.remove(image)
        raise


def finalize_packed_image_paths():
    """Use ordinary saved path spelling after all native reads/packing finish.

    Blender cannot remap an extended Windows path relative to the saved blend.
    filepath_raw changes its label without reloading the image. Only packed
    images qualify; their full source path remains explicit provenance.
    """
    import bpy
    changed = 0
    for image in bpy.data.images:
        path = image.filepath_raw
        if image.source != 'FILE' or not image.packed_file or not path.startswith('\\\\?\\'):
            continue
        ordinary = ('\\\\' + path[8:] if path[:8].upper() == '\\\\?\\UNC\\' else path[4:])
        image['agr_source_filepath'] = ordinary
        image.filepath_raw = ordinary
        changed += 1
    return changed
