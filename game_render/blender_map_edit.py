"""Blender operators that hide static map pieces in the connected game.

A piece is hidden when its whole game box lies inside the world bounds of a
selected object: select a map piece to hide what it covers, or scale an empty
cube around anything to hide everything inside it. Scene coordinates are the
game's (metres = Source units x 0.0254), as everywhere in the game view.
"""
from __future__ import annotations

import bpy
from mathutils import Vector

import map_edit

UNIT = 0.0254


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


def _editor(operator):
    session = _session()
    editor = getattr(session, "map_edit", None)
    if session.state != "ready" or editor is None:
        operator.report({'WARNING'}, "Connect to the game first.")
        return None, None
    records = editor.records()
    if records is None:
        operator.report({'WARNING'}, "The game has not loaded its map table yet.")
        return None, None
    return editor, records


class RENDERLOCK_OT_game_hide_pieces(bpy.types.Operator):
    """Stop the game drawing static map pieces that lie inside the selected objects' bounds
    (props and merged map geometry; their baked shadows stay)"""
    bl_idname = "renderlock.game_hide_pieces"
    bl_label = "Hide in game"
    bl_options = {'REGISTER'}

    def execute(self, context):
        editor, records = _editor(self)
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
        editor.set_hidden(set(editor.hidden) | found)
        self.report({'INFO'}, f"Hid {len(found)} game map pieces ({len(editor.hidden)} hidden in all).")
        return {'FINISHED'}


class RENDERLOCK_OT_game_show_pieces(bpy.types.Operator):
    """Draw every static map piece in the game again"""
    bl_idname = "renderlock.game_show_pieces"
    bl_label = "Show all in game"
    bl_options = {'REGISTER'}

    def execute(self, context):
        editor, _records = _editor(self)
        if editor is None:
            return {'CANCELLED'}
        editor.set_hidden([])
        return {'FINISHED'}


CLASSES = (RENDERLOCK_OT_game_hide_pieces, RENDERLOCK_OT_game_show_pieces)
