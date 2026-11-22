import json
from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from .context import _DEFAULT_EXTRACTED_ROOT, _resolve_donor_geom_path, _scene_extracted_root


class DSTS_OT_ReAriseImport(bpy.types.Operator, ImportHelper):
    """Build a native export rig from a ReArise library character and its captured clips"""
    bl_idname = 'import_scene.dsts_rearise'
    bl_label = 'Digimon ReArise Model and Motions (.glb)'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.glb'
    filter_glob: StringProperty(default='*.glb', options={'HIDDEN'})
    donor_chr: StringProperty(name='Donor Chr', default='chr027')
    donor_geom_path: StringProperty(name='Donor Geometry', subtype='FILE_PATH')
    extracted_root: StringProperty(name='Extracted Assets Root', subtype='DIR_PATH', default=_DEFAULT_EXTRACTED_ROOT)
    size_factor: FloatProperty(name='Model and Motion Scale', default=1, min=0.001)
    build_outline: BoolProperty(name='Build Native Outline', default=True)
    battle_plan_only: BoolProperty(name='Battle Plan Only', default=False,
                                   description='List the captured actor motions, cameras, effects and sounds in the Text Editor')

    def invoke(self, context, event):
        self.extracted_root = _scene_extracted_root(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from ..rearise_plan import inspect_actor
        try:
            if self.battle_plan_only:
                plan = inspect_actor(self.filepath)
                report = bpy.data.texts.new(plan['source_model'] + ' ReArise battle plan.json')
                report.write(json.dumps(plan, indent=2, ensure_ascii=False))
                self.report({'INFO'}, f'Open {report.name} in the Text Editor ({len(plan["actions"])} captured actions)')
                return {'FINISHED'}
            from ..rearise import import_model
            donor = _resolve_donor_geom_path(self.extracted_root, self.donor_chr, self.donor_geom_path)
            collection, rig = import_model(context, Path(self.filepath), donor, self.size_factor, self.build_outline)
        except (OSError, RuntimeError, ValueError, KeyError, IndexError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Imported {collection.name} with {len(rig.animation_data.nla_tracks)} motions')
        return {'FINISHED'}
