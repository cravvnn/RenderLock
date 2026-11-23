"""The prepared RenderLock map as an invisible light and shadow rig for Blender objects.

The game draws the world. This map only lights, shadows and reflects the Blender
objects that are merged into the game frame. It lines up with the game exactly
(same Source coordinates, RenderLock's standard map basis), so its lights and walls
sit where the game's do.

Once per map package, a background Blender opens RenderLockMap.blend (its data is
already local there), drops what lighting doesn't need (particles, fog), puts every
piece under the game-alignment basis, hides map geometry from the camera, points
images at the package's 1024 px copies, and saves a "lighting map" file plus an
index of every piece's bounds beside the package:

    blender -b --factory-startup --python game_map.py -- prepare <RenderLockMap.blend> <1|0>

Per shot, only the pieces near the camera path are *linked* from that file (fast:
nothing has to be made local). A few pieces near the Blender objects are made local
when Cycles should catch their shadows on the game.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

MAP_COLLECTION = "RenderLockMap"
PROXY_COLLECTION = "RenderLockProxy"
MANIFEST = "map-package.json"
INDEX_VERSION = 2
LIBRARY_VERSION = 2
# Prepared maps keep VRF's glTF axes; RenderLock turns them +90 degrees about Z into
# Source axes (agr.installed_map.blender_map_matrix with no scene origin or yaw).
BASIS = ((0.0, -1.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))
SKIPPED_GROUPS = ("particles",)   # effects: the game draws its own
SKIPPED_TYPES = {"VOLUME"}         # map fog would cover the objects layer a second time
GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD"}
MAP_GROUP = "Game map"                     # objects scene: lights and shadows
GUIDE_GROUP = "Game map guide"             # game scene: viewport-only placement guide
ALIGNMENT = "RenderLock | game map alignment"
PROBE_TAG = "renderlock_map_probe"


# --------------------------------------------------------------------------- finding a map

def _appdata():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _remembered_file():
    return _appdata() / "RenderLock" / "game-render.json"


def remember_map(path):
    """Remember the last map so new scenes start with it."""
    try:
        target = _remembered_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
        data["map"] = str(path)
        target.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass


def find_map():
    """The map this user last used here, or the one RenderLock has selected, if any."""
    candidates = []
    for source in (_remembered_file(), _appdata() / "RenderLock" / "settings.json"):
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        candidates += [data.get("map", ""), data.get("external_map", "")]
    for candidate in candidates:
        if candidate and is_map(candidate):
            return str(candidate)
    return ""


def is_map(path):
    path = Path(path)
    return path.suffix.lower() == ".blend" and path.is_file() and (path.parent / MANIFEST).is_file()


# --------------------------------------------------------------------------- prepared files

def cache_dir(path):
    """Beside the package (same drive), never inside it: the package stays unchanged."""
    root = Path(path).resolve().parent
    return root.parent / f"{root.name}-game-render"


def index_file(path):
    return cache_dir(path) / f"index-v{INDEX_VERSION}.json"


def library_file(path, small=True):
    """Per Blender version: an older Blender can't reliably link a file saved by a newer one."""
    try:
        import bpy
        version = "b%d%d" % bpy.app.version[:2]
    except ImportError:
        version = "b"
    return cache_dir(path) / f"lighting-v{LIBRARY_VERSION}-{'1024' if small else 'full'}-{version}.blend"


def log_file(path):
    return cache_dir(path) / "prepare.log"


def load_index(path):
    try:
        index = json.loads(index_file(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if index.get("version") != INDEX_VERSION or index.get("size") != Path(path).stat().st_size:
        return None
    return index


def prepared(path, small=True):
    return load_index(path) is not None and library_file(path, small).is_file()


def start_prepare(path, small, blender):
    """Prepare the lighting map in a background Blender (the full map is large; this keeps
    it out of the user's session). Returns the process."""
    target = cache_dir(path)
    target.mkdir(parents=True, exist_ok=True)
    log = open(log_file(path), "w", encoding="utf-8")
    command = [blender, "-b", "--factory-startup", "--python", str(Path(__file__).resolve()), "--",
               "prepare", str(Path(path).resolve()), "1" if small else "0"]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)


def _bounds(obj, prefix=None):
    from mathutils import Vector
    matrix = obj.matrix_world if prefix is None else prefix @ obj.matrix_world
    if obj.type in GEOMETRY_TYPES:
        corners = [matrix @ Vector(corner) for corner in obj.bound_box]
    else:
        corners = [matrix.translation]
    return ([min(c[i] for c in corners) for i in range(3)], [max(c[i] for c in corners) for i in range(3)])


def prepare(path, small=True):
    """Run inside a background Blender: write the lighting map and the index."""
    import bpy
    from mathutils import Matrix
    path = Path(path).resolve()
    root = path.parent
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    maps = bpy.data.collections.get(MAP_COLLECTION)
    if maps is None:
        raise RuntimeError("This file has no RenderLockMap collection.")
    proxy = bpy.data.collections.get(PROXY_COLLECTION)
    groups = {}
    for child in maps.children:
        for obj in child.all_objects:
            groups.setdefault(obj.name, child.name)
    dropped = {obj for obj in maps.all_objects
               if obj.type in SKIPPED_TYPES or any(s in groups.get(obj.name, "").lower() for s in SKIPPED_GROUPS)}
    dropped -= {obj.parent for obj in maps.all_objects if obj not in dropped and obj.parent is not None}
    if dropped:
        bpy.data.batch_remove(list(dropped))
    aligned = Matrix(BASIS)

    def entries(collection):
        # Every aligned world matrix is the basis times the authored one.
        out = []
        for obj in collection.all_objects:
            lo, hi = _bounds(obj, aligned)
            entry = dict(name=obj.name, type=obj.type, parent=obj.parent.name if obj.parent else None,
                         group=groups.get(obj.name, ""), lo=lo, hi=hi)
            if obj.type == "LIGHT":
                entry["light"] = obj.data.type
            out.append(entry)
        return out

    map_entries = entries(maps)
    proxy_entries = entries(proxy) if proxy is not None else []
    basis = bpy.data.objects.new(ALIGNMENT, None)
    maps.objects.link(basis)
    basis.matrix_world = aligned
    for collection in (maps, proxy):
        if collection is None:
            continue
        for obj in collection.all_objects:
            if obj is basis or obj.parent is not None:
                continue
            authored = obj.matrix_world.copy()
            obj.parent = basis
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = authored
    for obj in maps.all_objects:
        if obj.type in GEOMETRY_TYPES:
            obj.visible_camera = False
            # Every map piece is a shadow catcher, so in Cycles' catcher pass only the Blender
            # objects count as shadow casters (catchers don't shadow each other there). Hidden from
            # the camera, a catcher still lights, shadows and reflects the objects as usual.
            obj.is_shadow_catcher = True
    swapped = 0
    full, smaller = root / "textures", root / "textures-1024"
    for image in bpy.data.images:
        if image.source not in {'FILE', 'SEQUENCE', 'TILED'} or not image.filepath or image.packed_file:
            continue
        resolved = Path(bpy.path.abspath(image.filepath)).resolve()
        if small and resolved.parent == full.resolve() and (smaller / resolved.name).is_file():
            resolved = smaller / resolved.name
            swapped += 1
        image.filepath = str(resolved)  # absolute: the lighting map lives in another folder

    scene = bpy.context.scene
    eevee = {}
    for prop in scene.eevee.bl_rna.properties:
        if prop.is_readonly or prop.type not in {"BOOLEAN", "INT", "FLOAT", "ENUM"} or getattr(prop, "is_array", False):
            continue
        eevee[prop.identifier] = getattr(scene.eevee, prop.identifier)
    index = dict(version=INDEX_VERSION, map=str(path), size=path.stat().st_size,
                 objects=map_entries, proxy=proxy_entries,
                 world=scene.world.name if scene.world else None,
                 view_transform=scene.view_settings.view_transform, look=scene.view_settings.look,
                 exposure=scene.view_settings.exposure, eevee=eevee, alignment=ALIGNMENT)
    target = library_file(path, small)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.stem + ".part.blend")
    bpy.ops.wm.save_as_mainfile(filepath=str(partial), compress=True, relative_remap=False, copy=True)
    os.replace(partial, target)
    temporary = Path(str(index_file(path)) + ".part")
    temporary.write_text(json.dumps(index), encoding="utf-8")
    os.replace(temporary, index_file(path))
    print(f"RenderLock lighting map: {len(index['objects'])} pieces, {len(index['proxy'])} proxy cells, "
          f"{swapped} images at 1024 px, removed {len(dropped)}", flush=True)


# --------------------------------------------------------------------------- choosing what to load

def _gaps(entries, points):
    import numpy as np
    lo = np.array([e["lo"] for e in entries], dtype=np.float64).reshape(-1, 3)
    hi = np.array([e["hi"] for e in entries], dtype=np.float64).reshape(-1, 3)
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    best = np.full(len(entries), np.inf)
    for start in range(0, len(points), 32):
        p = points[start:start + 32, None, :]
        gap = np.maximum(np.maximum(lo[None] - p, 0.0), p - hi[None])
        best = np.minimum(best, np.sqrt((gap * gap).sum(axis=2)).min(axis=0))
    return best


def _with_parents(entries, names):
    parents = {e["name"]: e["parent"] for e in entries}
    for name in list(names):
        parent = parents.get(name)
        while parent and parent not in names and parent != ALIGNMENT:
            names.add(parent)
            parent = parents.get(parent)
    return names


def select(index, points, radius):
    """Names to link: every map light (with its barn mask), and any other piece whose
    bounds come within `radius` metres of the camera anywhere in the shot, plus parents."""
    entries = index["objects"]
    gaps = _gaps(entries, points)
    names = set()
    for entry, gap in zip(entries, gaps):
        if entry["type"] == "LIGHT" or "barn mask" in entry["group"].lower() or gap <= radius:
            names.add(entry["name"])
    return _with_parents(entries, names)


def select_proxy(index, points, radius):
    entries = index.get("proxy", [])
    if not entries:
        return set()
    gaps = _gaps(entries, points)
    return _with_parents(entries, {e["name"] for e, gap in zip(entries, gaps) if gap <= radius})


# --------------------------------------------------------------------------- loading

def link(path, small, names, collection_name, scene, world=None):
    """Link the named pieces of the lighting map into a new collection in `scene`.
    Returns (collection, counts, world datablock or None)."""
    import bpy
    library = library_file(path, small)
    with bpy.data.libraries.load(str(library), link=True) as (source, loaded):
        available = set(source.objects)
        loaded.objects = sorted(name for name in names if name in available)
        if world and world in source.worlds:
            loaded.worlds = [world]
    objects = [obj for obj in loaded.objects if obj is not None]
    collection = bpy.data.collections.new(collection_name)
    scene.collection.children.link(collection)
    for obj in objects:
        collection.objects.link(obj)
    collection["renderlock_library"] = str(library)
    collection["renderlock_map"] = str(path)
    counts = dict(objects=len(objects), lights=sum(o.type == "LIGHT" for o in objects),
                  meshes=sum(o.type == "MESH" for o in objects), small_textures=bool(small))
    collection["renderlock_counts"] = json.dumps(counts)
    linked_world = next((w for w in loaded.worlds if w is not None), None) if world else None
    return collection, counts, linked_world


def _library_of(path):
    import bpy
    target = os.path.normcase(os.path.abspath(path))
    return next((lib for lib in bpy.data.libraries
                 if os.path.normcase(os.path.abspath(bpy.path.abspath(lib.filepath))) == target), None)


def remove(collection):
    """Remove a loaded map collection; free its library once nothing uses it."""
    import bpy
    library_path = collection.get("renderlock_library", "")
    local = [obj for obj in collection.all_objects if obj.library is None]
    probes = [obj.data for obj in local if obj.type == 'LIGHT_PROBE']
    for scene in bpy.data.scenes:
        if collection.name in scene.collection.children:
            scene.collection.children.unlink(collection)
    bpy.data.collections.remove(collection)
    if local:
        bpy.data.batch_remove(local)
    if probes:
        bpy.data.batch_remove([p for p in probes if p.users == 0])
    if library_path and not any(c.get("renderlock_library") == library_path for c in bpy.data.collections):
        library = _library_of(library_path)
        if library is not None:
            bpy.data.libraries.remove(library)


def _overlaps(lo_a, hi_a, lo_b, hi_b):
    return all(lo_a[i] <= hi_b[i] and lo_b[i] <= hi_a[i] for i in range(3))


def set_catchers(collection, region, enable):
    """Cycles shadows on the game: map pieces overlapping `region` ((lo, hi) around the
    Blender objects) become camera-visible shadow catchers (made local first; a catcher
    renders transparent). Everything else stays hidden from the camera."""
    lo, hi = region if region else ((0, 0, 0), (0, 0, 0))
    catchers = 0
    for obj in list(collection.objects):
        if obj.type not in GEOMETRY_TYPES:
            continue
        close = bool(enable and region) and _overlaps(*_bounds(obj), lo, hi)
        if close and obj.library is not None:
            obj = obj.make_local() or obj
        if obj.library is None:
            obj.visible_camera = close  # camera-visible catchers render transparent, carrying the shadow
            obj.is_shadow_catcher = True
        catchers += close
    return catchers


def set_render_visible(collection, visible):
    """Show or hide the map's pieces and lamps in renders. The baked light probes always stay.
    EEVEE sets up every map material again for each frame (about a minute with the map around
    a shot, and nearly all of the graphics memory), so its renders leave the map out by default."""
    changed = 0
    for obj in collection.all_objects:
        if obj.type in ('MESH', 'LIGHT') and not obj.get(PROBE_TAG) and obj.hide_render == bool(visible):
            obj.hide_render = not visible
            changed += 1
    return changed


def add_light_probes(collection, region, scene, capture_distance=80.0, surfel_density=10, samples=1024, clamp=0.5,
                     strength=3.5, capture_world=False):
    """EEVEE: bake the map's light (including the game's baked lighting on its surfaces)
    into a probe volume around the Blender objects, plus a reflection probe there."""
    import bpy
    for obj in [o for o in collection.objects if o.get(PROBE_TAG)]:
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and data.users == 0:
            bpy.data.lightprobes.remove(data)
    lo, hi = region
    size = [max(hi[i] - lo[i], 1.0) for i in range(3)]
    center = [(hi[i] + lo[i]) / 2 for i in range(3)]
    spacing = max(max(size) / 24.0, 0.75)
    volume = bpy.data.lightprobes.new("RenderLock map light", 'VOLUME')
    volume.resolution_x, volume.resolution_y, volume.resolution_z = (
        max(2, min(32, math.ceil(size[i] / spacing) + 1)) for i in range(3))
    volume.capture_world = capture_world
    # The map's surfaces already emit the game's finished lighting, bounces included, so the bake
    # only gathers that emission. The clamp matters most: a few tiny, extremely bright emitters
    # otherwise tint objects blue once enough samples find them (measured against Cycles).
    volume.capture_indirect = False
    volume.capture_emission = True  # the map's surfaces carry the game's baked light
    # Light reaches the objects from across the room (a lit stage, lamps, windows), not only
    # from the walls next to them; a lighter surfel density keeps the bigger capture affordable.
    volume.capture_distance = capture_distance
    volume.surfel_density = surfel_density
    volume.bake_samples = samples
    # EEVEE's probe volume catches about half of the map light that Cycles path traces in enclosed
    # spaces (measured in the Midtown theatre: 1.8x and 2.3x); the strength makes up for it.
    volume.intensity = strength
    if clamp is not None:
        volume.clamp_direct = volume.clamp_indirect = clamp
    volume_obj = bpy.data.objects.new("RenderLock map light", volume)
    volume_obj.location = center
    volume_obj.scale = [s / 2 for s in size]
    sphere = bpy.data.lightprobes.new("RenderLock map reflections", 'SPHERE')
    sphere.influence_distance = max(size) * 0.75 + 2.0
    sphere_obj = bpy.data.objects.new("RenderLock map reflections", sphere)
    sphere_obj.location = center
    for obj in (volume_obj, sphere_obj):
        obj[PROBE_TAG] = 1
        obj.hide_select = True
        collection.objects.link(obj)
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        bpy.ops.object.lightprobe_cache_bake(subset='ALL')
    return volume_obj, sphere_obj


def set_light_strength(collection, strength):
    for obj in collection.objects:
        if obj.get(PROBE_TAG) and obj.type == 'LIGHT_PROBE' and obj.data.type == 'VOLUME':
            obj.data.intensity = strength


def apply_eevee(scene, settings):
    for name, value in (settings or {}).items():
        try:
            if hasattr(scene.eevee, name):
                setattr(scene.eevee, name, value)
        except (TypeError, ValueError, AttributeError):
            pass


if __name__ == "__main__" and "--" in sys.argv:
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) == 3 and args[0] == "prepare":
        prepare(args[1], args[2] == "1")
