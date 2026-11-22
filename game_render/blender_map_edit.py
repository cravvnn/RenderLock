"""Blender side of hiding static map pieces in the connected game.

Two kinds of game pieces can be hidden (see map_edit.py): aggregate fragments
(static props and merged map geometry, one record each in the game's fragment
table) and standalone scene objects (lamps, signs, glass...). Both are chosen in
Blender by world bounds; scene coordinates are the game's (metres = Source units
x 0.0254), as everywhere in the game view:

- Hide in game: every piece whose whole box lies inside a selected object's
  bounds. Select a map piece to hide what it covers, or scale an empty cube
  around anything to hide everything inside it.
- Hide at cursor: the smallest piece whose box contains the 3D cursor (snap the
  cursor onto the surface you want gone).

The lists are saved with the scene: fragments by record index together with
the game table's fingerprint (ignored for a different map build), scene objects
by origin (found again in each new game session). They are re-applied whenever
the game connects.
"""
from __future__ import annotations

import json
import traceback

import bpy
import numpy as np
from mathutils import Vector

import map_edit

UNIT = 0.0254
PROP = "renderlock_hidden_pieces"


def _session():
    import blender_game_view
    return blender_game_view.SESSION


def _object_bounds(obj):
    if obj.type == 'EMPTY':
        size = obj.empty_display_size
        corners = [Vector((x, y, z)) * size for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    else:
        corners = [Vector(c) for c in obj.bound_box]
    world = [obj.matrix_world @ c for c in corners]
    lo = [min(p[i] for p in world) / UNIT for i in range(3)]
    hi = [max(p[i] for p in world) / UNIT for i in range(3)]
    return lo, hi


def _key(o):
    return {k: [round(float(v), 3) for v in o[k]] for k in ("origin", "lo", "hi")}


def _text(key):
    return json.dumps(key, sort_keys=True)


def saved(scene):
    """{"hash", "ids", "objects"} saved with the scene; objects as {"origin", "lo", "hi"}."""
    try:
        data = json.loads(scene.get(PROP, "") or "{}")
        objects = [o for o in data.get("objects", []) if isinstance(o, dict)]
        return {"hash": data.get("hash"), "ids": [int(i) for i in data.get("ids", [])],
                "objects": [_key(o) for o in objects]}
    except (ValueError, TypeError, KeyError):
        return {"hash": None, "ids": [], "objects": []}


def save(scene, digest, ids, objects):
    unique = {_text(_key(o)): _key(o) for o in objects}
    scene[PROP] = json.dumps({"hash": digest, "ids": sorted(set(int(i) for i in ids)),
                              "objects": [unique[k] for k in sorted(unique)]})


def _request(session):
    controller = session.controller
    return lambda command: controller._request(command, timeout=10, allow_error=True,
                                               allow_truncated=True)


def scene_objects(session, refresh=False):
    """The game's standalone scene objects (a complete listing, re-read when the map changes)."""
    editor = session.map_edit
    serial = editor.status()["serial"]
    if (refresh or getattr(editor, "scene_objects", None) is None
            or getattr(editor, "scene_objects_serial", None) != serial):
        log = session.controller._session.session_dir / "engine_console.log"
        objects = map_edit.dump_scene_objects(_request(session), log)
        editor.scene_objects = objects  # None when the listing was incomplete: read again next time
        editor.scene_objects_serial = serial if objects is not None else None
    return editor.scene_objects or []


def _resolve(objects, keys):
    chosen, missing = [], 0
    for key in keys:
        match = [o for o in objects if o["class"] == "Default" and map_edit.identifies(o)
                 and map_edit.same_object(o, key)]
        if len(match) == 1:
            chosen.append((match[0]["address"], match[0]["origin"]))
        else:
            missing += 1
    return chosen, missing


def _send_objects(session, keys):
    """Find the saved objects in this game session and hide them; returns how many are hidden."""
    editor = session.map_edit
    if not keys and not editor.objects:
        return 0
    spare = editor.reserve_class(_request(session)) if keys else editor.spare_class
    if keys and spare is None:
        return 0
    chosen, missing = _resolve(scene_objects(session), keys)
    if missing:  # objects can be re-created: read the listing again once
        chosen, missing = _resolve(scene_objects(session, refresh=True), keys)
    editor.set_objects(chosen, spare)
    return min(len(chosen), map_edit.OBJECT_CAPACITY)


def sync(scene):
    """Live timer: send the scene's saved lists to a newly connected game once its table is known."""
    session = _session()
    editor = getattr(session, "map_edit", None)
    if session.state != "ready" or editor is None or getattr(editor, "synced", False):
        return
    state = editor.status()
    if not state["records"]:
        return
    editor.synced = True
    data = saved(scene)
    if data["ids"] and data["hash"] == state["hash"]:
        editor.set_hidden(data["ids"], data["hash"])
    if data["objects"]:
        try:
            _send_objects(session, data["objects"])
        except Exception:
            traceback.print_exc()


def _editor(operator, scene):
    session = _session()
    editor = getattr(session, "map_edit", None)
    if session.state != "ready" or editor is None:
        operator.report({'WARNING'}, "Connect to the game first.")
        return None, None, None
    records = editor.records()
    if records is None:
        operator.report({'WARNING'}, "The game has not loaded its map table yet.")
        return None, None, None
    data = saved(scene)
    data["hash_now"] = editor.status()["hash"]
    if data["hash"] != data["hash_now"]:
        data["ids"] = []  # saved for another map build
    return editor, records, data


def _apply(operator, scene, editor, data, ids, origins, added):
    session = _session()
    save(scene, data["hash_now"], ids, origins)
    editor.set_hidden(ids, data["hash_now"])
    objects = _send_objects(session, origins)
    editor.synced = True
    if origins and objects < len(origins):
        operator.report({'WARNING'}, f"Hid {added} game map pieces; {len(origins) - objects} scene "
                                     "objects could not be hidden in this game session "
                                     f"(at most {map_edit.OBJECT_CAPACITY} at a time).")
    else:
        operator.report({'INFO'}, f"Hid {added} game map pieces "
                                  f"({len(set(ids)) + len(origins)} hidden in all).")


class RENDERLOCK_OT_game_hide_pieces(bpy.types.Operator):
    """Stop the game drawing static map pieces that lie inside the selected objects' bounds
    (props, merged map geometry and standalone objects; their baked shadows stay)"""
    bl_idname = "renderlock.game_hide_pieces"
    bl_label = "Hide in game"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        editor, records, data = _editor(self, scene)
        if editor is None:
            return {'CANCELLED'}
        lo, hi = records
        objects = scene_objects(_session())
        found, origins = set(), set()
        for obj in context.selected_objects:
            box_lo, box_hi = _object_bounds(obj)
            found.update(int(i) for i in map_edit.records_inside(lo, hi, box_lo, box_hi))
            origins.update(_text(_key(o)) for o in map_edit.objects_inside(objects, box_lo, box_hi))
        known = {_text(k) for k in data["objects"]}
        if not found and not origins:
            self.report({'INFO'}, "No game map piece lies inside the selection.")
            return {'CANCELLED'}
        keys = data["objects"] + [json.loads(k) for k in sorted(origins - known)]
        _apply(self, scene, editor, data, set(data["ids"]) | found, keys,
               len(found - set(data["ids"])) + len(origins - known))
        return {'FINISHED'}


class RENDERLOCK_OT_game_hide_at_cursor(bpy.types.Operator):
    """Stop the game drawing the smallest static map piece at the 3D cursor"""
    bl_idname = "renderlock.game_hide_at_cursor"
    bl_label = "Hide at cursor"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        editor, records, data = _editor(self, scene)
        if editor is None:
            return {'CANCELLED'}
        lo, hi = records
        point = np.asarray(scene.cursor.location, dtype=np.float32) / UNIT
        hits = map_edit.records_containing(lo, hi, point, exclude=data["ids"])
        known = {_text(k) for k in data["objects"]}
        shown = [o for o in map_edit.objects_containing(scene_objects(_session()), point)
                 if _text(_key(o)) not in known]
        record_size = float(np.prod(np.maximum(hi[hits[0]] - lo[hits[0]], 1.0))) if len(hits) else None
        object_size = (float(np.prod(np.maximum(np.subtract(shown[0]["hi"], shown[0]["lo"]), 1.0)))
                       if shown else None)
        if record_size is None and object_size is None:
            self.report({'INFO'}, "No visible game map piece contains the 3D cursor.")
            return {'CANCELLED'}
        ids, keys = set(data["ids"]), list(data["objects"])
        if object_size is not None and (record_size is None or object_size <= record_size):
            keys.append(_key(shown[0]))
        else:
            ids.add(int(hits[0]))
        _apply(self, scene, editor, data, ids, keys, 1)
        return {'FINISHED'}


class RENDERLOCK_OT_game_show_pieces(bpy.types.Operator):
    """Draw every static map piece in the game again"""
    bl_idname = "renderlock.game_show_pieces"
    bl_label = "Show all in game"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene[PROP] = ""
        editor = getattr(_session(), "map_edit", None)
        if editor is not None:
            editor.set_hidden([])
            editor.set_objects([])
        return {'FINISHED'}


class RENDERLOCK_PT_game_map(bpy.types.Panel):
    bl_label = "Game map"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RenderLock"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        import blender_game_view
        return context.engine == blender_game_view.ENGINE

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator(RENDERLOCK_OT_game_hide_pieces.bl_idname, icon='HIDE_ON')
        col.operator(RENDERLOCK_OT_game_hide_at_cursor.bl_idname, icon='PIVOT_CURSOR')
        col.operator(RENDERLOCK_OT_game_show_pieces.bl_idname, icon='HIDE_OFF')
        data = saved(context.scene)
        count = len(data["ids"]) + len(data["objects"])
        editor = getattr(_session(), "map_edit", None)
        state = editor.status() if editor is not None else None
        if state and state["records"]:
            layout.label(text=f"{count} pieces hidden in the game")
        elif count:
            layout.label(text=f"{count} pieces hidden (applied when the game connects)")
        layout.label(text="Baked shadows of hidden pieces stay.", icon='INFO')


CLASSES = (RENDERLOCK_OT_game_hide_pieces, RENDERLOCK_OT_game_hide_at_cursor,
           RENDERLOCK_OT_game_show_pieces, RENDERLOCK_PT_game_map)
