"""File menus, the DSTS sidebar and error reporting."""

from collections import Counter
from textwrap import wrap

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel

from .animation import (
    DSTS_OT_AnimExport,
    DSTS_OT_AnimExportAll,
    DSTS_OT_AnimImport,
    DSTS_OT_ImportDonorAnimations,
)
from .context import _active_geom_collection, _target_chr_from_context
from .geometry import (
    DSTS_OT_ExportAll,
    DSTS_OT_GeomExport,
    DSTS_OT_GeomImport,
    DSTS_OT_PackageNativeModel,
)
from .materials import DSTS_OT_BorrowMaterialSetup, DSTS_OT_RetargetMaterialTextures
from .new_century import (
    DSTS_OT_CinematicImport,
    DSTS_OT_ExportNcMoveset,
    DSTS_OT_NativeSkillSequence,
    DSTS_OT_NcGlbImport,
)
from .nla import DSTS_OT_SetupBaseAnimationNla
from .rig import (
    DSTS_OT_ApplyArmatureObjectTransforms,
    DSTS_OT_PrepareSelectedAsGeom,
    DSTS_OT_RenamePrimaryUvsToUv1,
)


class DSTS_PT_WorkflowPanel(Panel):
    """DSTS workflow tools in the 3D View sidebar"""
    bl_idname = "DSTS_PT_workflow"
    bl_label = "Time Stranger"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "DSTS"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        collection = _active_geom_collection(context)
        collection_name = collection.name if collection else "__NONE__"
        if collection and 'DSTS_camera_source' in collection:
            lens = layout.box()
            lens.label(text='Native Camera Lens')
            for key in collection.keys():
                if key.startswith('DSTS_camera_') and key.endswith(('_fov_degrees', '_near', '_far')):
                    lens.prop(collection, '["' + key + '"]', text=key.removeprefix('DSTS_camera_').replace('_', ' ').title())
            lens.label(text='ANIM lens tracks can override static FOV.', icon='INFO')
        target_chr = _target_chr_from_context(context, collection, scene.dsts_workflow_target_chr) or scene.dsts_workflow_target_chr
        if target_chr and target_chr != scene.dsts_workflow_target_chr:
            scene.dsts_workflow_target_chr = target_chr

        box = layout.box()
        box.label(text="Workflow")
        box.operator(DSTS_OT_NcGlbImport.bl_idname, text="Import NC Model (.glb)", icon='IMPORT')
        box.operator(DSTS_OT_PrepareSelectedAsGeom.bl_idname, text="Prepare Selected As Geom")
        box.prop(scene, "dsts_extracted_root", text="Extracted Root")
        box.prop(scene, "dsts_workflow_donor_chr", text="Donor Chr")
        box.prop(scene, "dsts_workflow_donor_geom", text="Donor Geom")
        box.prop(scene, "dsts_workflow_apply_all", text="Apply All Meshes")
        borrow = box.operator(DSTS_OT_BorrowMaterialSetup.bl_idname, text="Borrow Material Setup")
        borrow.collection_name = collection_name
        borrow.source_chr = scene.dsts_workflow_donor_chr
        borrow.source_geom_path = scene.dsts_workflow_donor_geom
        borrow.extracted_root = scene.dsts_extracted_root
        borrow.apply_to_all_geom_meshes = scene.dsts_workflow_apply_all

        box.separator()
        box.prop(scene, "dsts_workflow_target_chr", text="Target Chr")
        box.prop(scene, "dsts_target_images_folder", text="Images Folder")
        retarget = box.operator(DSTS_OT_RetargetMaterialTextures.bl_idname, text="Retarget Material Textures")
        retarget.collection_name = collection_name
        retarget.target_chr = target_chr
        retarget.images_path = scene.dsts_target_images_folder
        retarget.apply_to_all_geom_meshes = scene.dsts_workflow_apply_all
        box.operator(DSTS_OT_RenamePrimaryUvsToUv1.bl_idname, text="Rename UVs To uv1")

        box.separator()
        # Import all of the donor's animation clips onto the Geom armature as NLA
        # tracks (borrowing materials/flags does NOT bring animations â€” this does).
        anim = box.operator(DSTS_OT_ImportDonorAnimations.bl_idname, text="Import Donor Animations")
        anim.collection_name = collection_name
        anim.source_chr = scene.dsts_workflow_donor_chr
        anim.source_geom_path = scene.dsts_workflow_donor_geom
        anim.extracted_root = scene.dsts_extracted_root

        box.separator()
        box.prop(scene, "dsts_workflow_replace_actions", text="Replace Base")
        base = box.operator(DSTS_OT_SetupBaseAnimationNla.bl_idname, text="Setup Base NLA")
        base.target_chr = target_chr
        base.replace_existing = scene.dsts_workflow_replace_actions

        # Mesh flags for the active mesh â€” the TS .geom per-mesh flag byte. These
        # are populated by import or by Borrow Material Setup; shown here so the
        # user can SEE and tweak the flags they copied from a donor model.
        active = getattr(context, "object", None)
        if active and active.type == 'MESH':
            box = layout.box()
            box.label(text="Mesh Flags (active mesh)")
            mesh_data = active.data
            flag_labels = ("isRendered", "isWireframe", "Contiguous bone palette", "flag 3",
                           "flag 4", "flag 5", "flag 6", "flag 7")
            if "DSTS_flag_0" in mesh_data.keys():
                for i, label_text in enumerate(flag_labels):
                    key = f"DSTS_flag_{i}"
                    if key in mesh_data.keys():
                        box.prop(mesh_data, f'["{key}"]', text=label_text)
            else:
                box.label(text="No flags yet â€” Import or Borrow Material Setup", icon='INFO')

        box = layout.box()
        box.label(text="Export Prep")
        # Manual "Apply Armature Transform" is exposed for the common single fix
        # (it CLEARS the identity-world parent instead of baking the -90 into the
        # bones). Run Basic Prep, along with the other pose/action/mesh operators,
        # is still registered and reachable via F3 search, just kept off the panel.
        box.operator(DSTS_OT_ApplyArmatureObjectTransforms.bl_idname, text="Apply Armature Transform")


class DSTS_OT_ShowErrors(Operator):
    """Save import diagnostics in the Text Editor and show a compact summary"""
    bl_idname = "wm.show_errors_window"
    bl_label = "Import Warnings"

    errors: StringProperty()
    window_name: StringProperty(default="Import Errors Window")
    text_name: StringProperty(default="Import Warnings")

    def execute(self, context):
        text = bpy.data.texts.new(name=self.text_name)
        text.write("Warnings during import:\n\n" + self.errors)
        text.use_fake_user = True
        self.text_name = text.name
        self.report({'WARNING'}, f'Import warnings saved in Text Editor as "{text.name}"')
        return {'FINISHED'}

    def invoke(self, context, event):
        self.execute(context)
        if bpy.app.background or context.window is None:
            return {'FINISHED'}
        # File-browser callbacks and timers can have a window without an editor
        # area. A popup uses the window and does not need to duplicate an area.
        return context.window_manager.invoke_popup(self, width=640)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Import completed with warnings", icon='INFO')
        messages = Counter(line for line in self.errors.splitlines() if line.strip())
        for message, count in list(messages.items())[:8]:
            summary = f"{message} ({count} occurrences)" if count > 1 else message
            for line in wrap(summary, width=85):
                layout.label(text=line)
        if len(messages) > 8:
            layout.label(text=f"{len(messages) - 8} more warning types in the full report.")
        layout.separator()
        layout.label(text=f'Text Editor report: "{self.text_name}"')


class DSTS_MT_ImportSubmenu(bpy.types.Menu):
    """Digimon Time Stranger Import Submenu"""
    bl_idname = "DSTS_MT_import_submenu"
    bl_label = "Digimon Time Stranger (.geom, .anim)"

    def draw(self, context):
        layout = self.layout
        layout.operator(DSTS_OT_GeomImport.bl_idname, text="Geometry (.geom)")
        layout.operator(DSTS_OT_AnimImport.bl_idname, text="Animation (.anim)")
        layout.separator()
        layout.operator('import_scene.dsts_spark', text="Native Effect Graph (.sprk)")
        layout.operator('import_scene.dsts_effect_timeline', text="Native Effect Timeline (.bson)")
        layout.operator('import_scene.dsts_collision', text="Object Collisions (.phys)")
        layout.operator('import_scene.dsts_native_lights', text="Native Light Records (.geom)")


class DSTS_MT_NewCenturyImport(bpy.types.Menu):
    """New Century imports kept separate from native Time Stranger formats"""
    bl_idname = "DSTS_MT_new_century_import"
    bl_label = "Digimon New Century"

    def draw(self, context):
        layout = self.layout
        layout.operator(DSTS_OT_NcGlbImport.bl_idname, text="Model (.glb)")
        layout.operator('import_scene.dsts_nc_skill', text="Skill Phases (.glb)")
        layout.operator('import_scene.dsts_native_skill_recipe', text="Native Skill Recipe (.glb)")
        layout.operator(DSTS_OT_CinematicImport.bl_idname, text="Cinematic Preview (.json)")


class DSTS_MT_CyberSleuthImport(bpy.types.Menu):
    bl_idname = 'DSTS_MT_cyber_sleuth_import'
    bl_label = 'Digimon Cyber Sleuth'

    def draw(self, context):
        from ..cyber_sleuth import draw_import_menu
        draw_import_menu(self, context)


class DSTS_MT_ExportSubmenu(bpy.types.Menu):
    """Digimon Time Stranger Export Submenu"""
    bl_idname = "DSTS_MT_export_submenu"
    bl_label = "Digimon Time Stranger (.geom, .anim)"

    def draw(self, context):
        layout = self.layout
        layout.operator(DSTS_OT_ExportAll.bl_idname, text="Export All (.geom + .anim)")
        layout.separator()
        layout.operator(DSTS_OT_GeomExport.bl_idname, text="Geometry Only (.geom)")
        layout.operator(DSTS_OT_AnimExport.bl_idname, text="Animation (.anim)")
        layout.operator(DSTS_OT_AnimExportAll.bl_idname, text="All Animations (.anim)")
        layout.operator(DSTS_OT_ExportNcMoveset.bl_idname, text="NC Moveset (.anim)")
        layout.operator(DSTS_OT_NativeSkillSequence.bl_idname, text="Native Skill Sequence (.json)")
        layout.separator()
        layout.operator(DSTS_OT_PackageNativeModel.bl_idname, text="Package Exported Native Model (.geom)")
        layout.operator('export_scene.dsts_spark', text="Native Effect Graph (.sprk)")
        layout.operator('export_scene.dsts_effect_timeline', text="Native Effect Timeline (.bson)")
        layout.operator('export_scene.dsts_collision', text="Object Collisions (.phys)")
        layout.operator('export_scene.dsts_native_lights', text="Native Light Edits (.geom)")


def menu_func_import(self, context):
    self.layout.menu(DSTS_MT_ImportSubmenu.bl_idname)
    self.layout.menu(DSTS_MT_CyberSleuthImport.bl_idname)
    self.layout.menu(DSTS_MT_NewCenturyImport.bl_idname)


def _order_digimon_import_menu():
    # Blender's append/remove API maintains this dynamic callback list. Keep our
    # adjacent categories above Next Order regardless of add-on enable order.
    callbacks = getattr(bpy.types.TOPBAR_MT_file_import.draw, '_draw_funcs', None)
    if callbacks is None or menu_func_import not in callbacks:
        return
    next_order = next((callback for callback in callbacks
                       if callback.__name__ == 'menu_import_next_order'
                       and 'next_order' in callback.__module__.lower()), None)
    if next_order is not None:
        callbacks.remove(menu_func_import)
        callbacks.insert(callbacks.index(next_order), menu_func_import)


def menu_func_export(self, context):
    self.layout.menu(DSTS_MT_ExportSubmenu.bl_idname)
