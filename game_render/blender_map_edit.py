"""Blender side of hiding static map pieces in the connected game.

A game map piece is one record of the game's fragment table (see map_edit.py):
a static prop instance or a chunk of merged map geometry. Pieces are chosen in
Blender by world bounds (scene coordinates are the game's, metres = Source
units x 0.0254, as everywhere in the game view):

- Hide in game: every piece whose whole box lies inside a selected object's
  bounds. Select a map piece to hide what it covers, or scale an empty cube
  around anything to hide everything inside it.
- Hide at cursor: the smallest piece whose box contains the 3D cursor (snap the
  cursor onto the surface you want gone).

The list is saved with the scene together with the game table's fingerprint,
re-applied whenever the game connects, and ignored for a different map build.
"""
from __future__ import annotations

import json

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


def saved(scene):
    """(table hash, [record ids]) saved with the scene, or (None, [])."""
    try:
        data = json.loads(scene.get(PROP, "") or "{}")
        return data.get("hash"), [int(i) for i in data.get("ids", [])]
    except (ValueError, TypeError):
        return None, []


def save(scene, digest, ids):
    scene[PROP] = json.dumps({"hash": digest, "ids": sorted(set(int(i) for i in ids))})


def sync(scene):
    """Live timer: send the scene's saved list to a newly connected game once its table is known."""
    session = _session()
    editor = getattr(session, "map_edit", None)
    if session.state != "ready" or editor is None or getattr(editor, "synced", False):
        return
    state = editor.status()
    if not state["records"]:
        return
    digest, ids = saved(scene)
    if ids and digest == state["hash"]:
        editor.set_hidden(ids)
    editor.synced = True


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
    digest = editor.status()["hash"]
    old_digest, ids = saved(scene)
    if old_digest != digest:
        ids = []  # saved for another map build
    return editor, records, (digest, ids)


def _apply(operator, scene, editor, digest, ids, added):
    save(scene, digest, ids)
    editor.set_hidden(ids)
    editor.synced = True
    operator.report({'INFO'}, f"Hid {added} game map pieces ({len(set(ids))} hidden in all).")


class RENDERLOCK_OT_game_hide_pieces(bpy.types.Operator):
    """Stop the game drawing static map pieces that lie inside the selected objects' bounds
    (props and merged map geometry; their baked shadows stay)"""
    bl_idname = "renderlock.game_hide_pieces"
    bl_label = "Hide in game"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        editor, records, saved_list = _editor(self, scene)
        if editor is None:
            return {'CANCELLED'}
        lo, hi = records
        found = set()
        for obj in context.selected_objects:
            box_lo, box_hi = _object_bounds(obj)
            found.update(int(i) for i in map_edit.records_inside(lo, hi, box_lo, box_hi))
        if not found:
            self.report({'INFO'}, "No game map piece lies inside the selection.")
            return {'CANCELLED'}
        digest, ids = saved_list
        _apply(self, scene, editor, digest, set(ids) | found, len(found - set(ids)))
        return {'FINISHED'}


class RENDERLOCK_OT_game_hide_at_cursor(bpy.types.Operator):
    """Stop the game drawing the smallest static map piece at the 3D cursor"""
    bl_idname = "renderlock.game_hide_at_cursor"
    bl_label = "Hide at cursor"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        editor, records, saved_list = _editor(self, scene)
        if editor is None:
            return {'CANCELLED'}
        lo, hi = records
        digest, ids = saved_list
        point = np.asarray(scene.cursor.location, dtype=np.float32) / UNIT
        hits = map_edit.records_containing(lo, hi, point, exclude=ids)
        if not len(hits):
            self.report({'INFO'}, "No visible game map piece contains the 3D cursor.")
            return {'CANCELLED'}
        _apply(self, scene, editor, digest, set(ids) | {int(hits[0])}, 1)
        return {'FINISHED'}


class RENDERLOCK_OT_game_show_pieces(bpy.types.Operator):
    """Draw every static map piece in the game again"""
    bl_idname = "renderlock.game_show_pieces"
    bl_label = "Show all in game"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        scene[PROP] = ""
        editor = getattr(_session(), "map_edit", None)
        if editor is not None:
            editor.set_hidden([])
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
        _digest, ids = saved(context.scene)
        editor = getattr(_session(), "map_edit", None)
        state = editor.status() if editor is not None else None
        if state and state["records"]:
            layout.label(text=f"{len(ids)} of {state['records']} pieces hidden in the game")
        elif ids:
            layout.label(text=f"{len(ids)} pieces hidden (applied when the game connects)")
        layout.label(text="Baked shadows of hidden pieces stay.", icon='INFO')


CLASSES = (RENDERLOCK_OT_game_hide_pieces, RENDERLOCK_OT_game_hide_at_cursor,
           RENDERLOCK_OT_game_show_pieces, RENDERLOCK_PT_game_map)
