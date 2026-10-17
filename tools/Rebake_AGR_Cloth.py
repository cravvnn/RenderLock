"""Run in Blender's Text Editor after editing a supported garment rig's poses.

The recorded action remains untouched. This recomputes AGR-owned native cloth
caches after animation edits. Haze hose rebakes are not handled
by this garment tool. No external program is launched by this script.
"""
from pathlib import Path
import sys
import bpy


def run_from(program):
    root = Path(program).expanduser().resolve()
    if not (root/'agr/blender_surface_cloth.py').is_file():
        raise RuntimeError('Select the DeadBlend program folder containing Launch_DeadBlend.bat.')
    sys.path.insert(0, str(root))
    from agr.surface_cloth_rebake import rebake_surface_cloth_scene
    result = rebake_surface_cloth_scene()
    if not result:
        raise RuntimeError('No supported baked garment rig was found in this scene.')
    failed = sum(r.get('status') not in ('baked', 'baked_partial') for r in result.values())
    print('AGR garment rebake:', len(result)-failed, 'completed;', failed, 'failed. See AGR_CLOTH_REBAKE_REPORT.')
    def draw(self, context):
        self.layout.label(text=f'{len(result)-failed} garment rig(s) rebaked; {failed} failed.')
        self.layout.label(text='Details: AGR_CLOTH_REBAKE_REPORT in the Text Editor.')
    bpy.context.window_manager.popup_menu(draw, title='AGR cloth rebake')


class AGR_OT_pick_cloth_program(bpy.types.Operator):
    bl_idname = 'agr.pick_cloth_program'
    bl_label = 'Select AGR program folder'
    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        try:
            run_from(self.directory)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


candidates = [o.get('agr_cloth_source_root', '') for o in bpy.context.scene.objects
              if o.type == 'ARMATURE' and o.get('agr_cloth_definition')]
program = next((Path(p) for p in candidates if p and (Path(p)/'agr/blender_surface_cloth.py').is_file()), None)
if program is not None:
    run_from(program)
else:
    old = getattr(bpy.types, AGR_OT_pick_cloth_program.__name__, None)
    if old is not None:
        bpy.utils.unregister_class(old)
    bpy.utils.register_class(AGR_OT_pick_cloth_program)
    bpy.ops.agr.pick_cloth_program('INVOKE_DEFAULT')
