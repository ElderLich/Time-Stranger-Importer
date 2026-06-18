"""
Time Stranger Importer - Digimon Time Stranger Blender Tools.
Combines geom import/export with animation import/export support.
"""

import bpy
import os
import re
from bpy_extras.io_utils import ImportHelper, ExportHelper
from bpy.props import StringProperty, EnumProperty, BoolProperty
from bpy.types import Operator
from .src.blender_compat import create_action_fcurve, create_nla_track, iter_action_fcurves

bl_info = {
    "name": "Time Stranger Importer",
    "author": "Senator John, Nymic_Razor, Pherakki (Animation), Zeak",
    "version": (1, 2, 19),
    "blender": (4, 5, 0),
    "category": "Import-Export",
    "description": "Import/Export .geom and .anim files for Digimon Time Stranger",
    "location": "File > Import/Export",
}

_ACTION_BONE_RE = re.compile(r'pose\.bones\["([^"]+)"\]')


def build_basic_hundred_line_anim_from_action(armature_obj, action, playback_rate=30.0):
    from .src.anim import AnimFileHundredLine

    bone_names = [bone.name for bone in armature_obj.data.bones]
    bone_count = len(bone_names)
    bone_index_by_name = {}
    for index, name in enumerate(bone_names):
        bone_index_by_name[name] = index
        cleaned = re.sub(r"(\.[0-9]{3})?$", "", name).strip().lower()
        bone_index_by_name.setdefault(cleaned, index)

    anim_file = AnimFileHundredLine()
    anim_file.playback_rate = playback_rate
    anim_file.bone_count = bone_count
    anim_file.float_channel_count = 0
    anim_file.bone_blend_factors = [255] * bone_count
    anim_file.float_channel_blend_factors = []

    for bone_idx in range(bone_count):
        anim_file.rotations[bone_idx] = {}
        anim_file.positions[bone_idx] = {}
        anim_file.scales[bone_idx] = {}

    action_frame_start = int(action.frame_range[0]) if action.frame_range else 1
    for fcurve in iter_action_fcurves(action):
        data_path = getattr(fcurve, "data_path", "")
        match = _ACTION_BONE_RE.search(data_path)
        if not match:
            continue
        bone_name = match.group(1)
        bone_idx = bone_index_by_name.get(bone_name)
        if bone_idx is None:
            cleaned = re.sub(r"(\.[0-9]{3})?$", "", bone_name).strip().lower()
            bone_idx = bone_index_by_name.get(cleaned)
        if bone_idx is None:
            continue

        array_index = int(getattr(fcurve, "array_index", 0))
        for keyframe in getattr(fcurve, "keyframe_points", []):
            frame_anim = int(keyframe.co[0]) - action_frame_start
            if frame_anim < 0:
                continue
            value = float(keyframe.co[1])
            if "rotation_quaternion" in data_path:
                if not 0 <= array_index < 4:
                    continue
                if frame_anim not in anim_file.rotations[bone_idx]:
                    anim_file.rotations[bone_idx][frame_anim] = [0.0, 0.0, 0.0, 1.0]
                if array_index == 0:
                    anim_file.rotations[bone_idx][frame_anim][3] = value
                else:
                    anim_file.rotations[bone_idx][frame_anim][array_index - 1] = value
            elif "location" in data_path:
                if not 0 <= array_index < 3:
                    continue
                if frame_anim not in anim_file.positions[bone_idx]:
                    anim_file.positions[bone_idx][frame_anim] = [0.0, 0.0, 0.0]
                anim_file.positions[bone_idx][frame_anim][array_index] = value
            elif "scale" in data_path:
                if not 0 <= array_index < 3:
                    continue
                if frame_anim not in anim_file.scales[bone_idx]:
                    anim_file.scales[bone_idx][frame_anim] = [1.0, 1.0, 1.0]
                anim_file.scales[bone_idx][frame_anim][array_index] = value

    return anim_file


# =============================================================================
# GEOM IMPORT/EXPORT OPERATORS
# =============================================================================

class DSTS_OT_GeomImport(Operator, ImportHelper):
    """Import Digimon Time Stranger .geom file"""
    bl_idname = "import_scene.dsts_geom"
    bl_label = "Digimon Time Stranger (.geom)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".geom"
    filter_glob: StringProperty(default="*.geom", options={'HIDDEN'})

    use_custom_images_path: BoolProperty(
        name="Custom Images Folder",
        description="Search this folder first; otherwise textures are auto-detected from nearby images folders",
        default=False,
    )
    images_path: StringProperty(
        name="Images Folder",
        description="Folder containing texture files (.img/.dds)",
        subtype='DIR_PATH',
        default="",
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "use_custom_images_path")
        row = layout.row()
        row.enabled = self.use_custom_images_path
        row.prop(self, "images_path")

    def execute(self, context):
        try:
            from .src.geom import import_geom
            images_folder = bpy.path.abspath(self.images_path) if self.use_custom_images_path and self.images_path else None
            imported_collection = import_geom(context, self.filepath, images_folder)
            self.report({'INFO'}, f"Imported: {imported_collection.name}")
            return {'FINISHED'}
        except Exception as exc:
            print(f"[DSTS] Geom import failed: {exc}")
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class DSTS_OT_GeomExport(Operator, ExportHelper):
    """Export Digimon Time Stranger .geom file"""
    bl_idname = "export_scene.dsts_geom"
    bl_label = "Digimon Time Stranger (.geom)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".geom"
    filter_glob: StringProperty(default="*.geom", options={'HIDDEN'})

    def collection_items(self, context):
        items = [(col.name, col.name, "") for col in bpy.data.collections if "unknown_0x10" in col]
        return items or [("__NONE__", "No imported Geom collection found", "")]

    collection_name: EnumProperty(
        name="Collection",
        description="Select collection to export",
        items=collection_items,
    )
    export_textures: BoolProperty(
        name="Export Textures",
        description="Export all textures used by materials to an 'images' folder",
        default=True,
    )
    export_textures_as_png: BoolProperty(
        name="Export as PNG",
        description="Export textures as PNG files instead of .img (DDS) format",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "collection_name")
        layout.prop(self, "export_textures")
        row = layout.row()
        row.enabled = self.export_textures
        row.prop(self, "export_textures_as_png")

    def execute(self, context):
        from .src.geom import export_geom, export_textures
        from .src.nlst import write_nlst

        if self.collection_name == "__NONE__":
            self.report({'ERROR'}, "No imported Geom collection found")
            return {'CANCELLED'}
        collection = bpy.data.collections.get(self.collection_name)
        if not collection:
            self.report({'ERROR'}, f"Collection '{self.collection_name}' not found")
            return {'CANCELLED'}

        try:
            geom = export_geom(collection)
            geom.to_file(self.filepath)
        except Exception as exc:
            print(f"[DSTS] Geom export failed: {exc}")
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        export_note = getattr(geom, "export_note", None)
        if export_note:
            self.report({'WARNING'}, export_note)

        with open(os.path.splitext(self.filepath)[0] + ".nlst", "wb") as f:
            f.write(write_nlst(geom).encode("utf-8"))

        if self.export_textures:
            output_dir = os.path.dirname(self.filepath)
            exported_textures = export_textures(collection, output_dir, as_png=self.export_textures_as_png)
            if exported_textures:
                fmt = "PNG" if self.export_textures_as_png else "IMG"
                self.report({'INFO'}, f"Exported: {self.filepath} + {len(exported_textures)} {fmt} texture(s)")
            else:
                self.report({'INFO'}, f"Exported: {self.filepath} (no textures found)")
        else:
            self.report({'INFO'}, f"Exported: {self.filepath}")
        return {'FINISHED'}

    def invoke(self, context, event):
        collection = bpy.data.collections.get(self.collection_name)
        if collection:
            blend_dir = os.path.dirname(bpy.data.filepath)
            self.filepath = os.path.join(blend_dir, f"{collection.name}.geom")
        else:
            self.filepath = ""
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# =============================================================================
# ANIMATION IMPORT/EXPORT OPERATORS
# =============================================================================

def fetch_armatures(self, context):
    armature_list = []
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            armature_list.append((obj.name, obj.name, obj.name, "OUTLINER_OB_ARMATURE", len(armature_list)))
    return tuple(armature_list) if armature_list else (("NONE", "No Armatures", "No armatures in scene", "ERROR", 0),)


def fetch_nla_tracks(self, context):
    if self.armature_name == "NONE" or self.armature_name not in bpy.data.objects:
        return (("NONE", "No Armature Selected", "", "ERROR", 0),)
    armature_obj = bpy.data.objects[self.armature_name]
    if armature_obj.type != "ARMATURE" or not armature_obj.animation_data or not armature_obj.animation_data.nla_tracks:
        return (("NONE", "No NLA Tracks", "", "ERROR", 0),)
    track_list = [
        (track.name, track.name, track.name, "ACTION", i)
        for i, track in enumerate(armature_obj.animation_data.nla_tracks)
        if not track.mute and track.strips
    ]
    return tuple(track_list) if track_list else (("NONE", "No Active Tracks", "", "ERROR", 0),)


class DSTS_OT_AnimImport(Operator, ImportHelper):
    """Import Digimon Time Stranger .anim file"""
    bl_idname = "import_scene.dsts_anim"
    bl_label = "Digimon Time Stranger (.anim)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".anim"
    filter_glob: StringProperty(default="*.anim", options={'HIDDEN'})
    files: bpy.props.CollectionProperty(type=bpy.types.PropertyGroup)
    armature_name: EnumProperty(items=fetch_armatures, name="Armature")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "armature_name")

    def execute(self, context):
        from .src.anim import AnimFileHundredLine
        from .src.anim.BinaryHundredLine import AnimFileBinary as AnimFileBinaryHundredLine

        if self.armature_name == "NONE":
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}
        armature_obj = bpy.data.objects.get(self.armature_name)
        if not armature_obj or armature_obj.type != "ARMATURE":
            self.report({'ERROR'}, f"Armature '{self.armature_name}' not found")
            return {'CANCELLED'}

        bone_names = [b.name for b in armature_obj.data.bones]
        if not armature_obj.animation_data:
            armature_obj.animation_data_create()

        folder = os.path.dirname(self.filepath)
        for f in self.files:
            filepath = os.path.join(folder, f.name)
            anim_name = os.path.splitext(f.name)[0]
            try:
                ab = AnimFileBinaryHundredLine()
                ab.read(filepath)
                anim_data = AnimFileHundredLine.from_binary(ab)
                self._import_animation(armature_obj, anim_name, anim_data, bone_names)
            except Exception as e:
                self.report({'WARNING'}, f"Failed to import {f.name}: {str(e)}")

        self.report({'INFO'}, f"Imported {len(self.files)} animation(s)")
        return {'FINISHED'}

    def _import_animation(self, armature_obj, anim_name, anim_data, bone_names):
        action = bpy.data.actions.new(anim_name)
        fps = 1

        for bone_idx, bone_name in enumerate(bone_names):
            if bone_idx >= anim_data.bone_count:
                break

            if bone_idx in anim_data.rotations:
                rotations = anim_data.rotations[bone_idx]
                if rotations:
                    for quat_idx in range(4):
                        fc = create_action_fcurve(
                            action, armature_obj,
                            data_path=f'pose.bones["{bone_name}"].rotation_quaternion',
                            index=quat_idx, group_name=bone_name,
                        )
                        for frame, rot in rotations.items():
                            blender_quat = [rot[3], rot[0], rot[1], rot[2]]
                            kf = fc.keyframe_points.insert(float(fps * frame + 1), blender_quat[quat_idx])
                            kf.interpolation = 'BEZIER'

            if bone_idx in anim_data.positions:
                positions = anim_data.positions[bone_idx]
                if positions:
                    for pos_idx in range(3):
                        fc = create_action_fcurve(
                            action, armature_obj,
                            data_path=f'pose.bones["{bone_name}"].location',
                            index=pos_idx, group_name=bone_name,
                        )
                        for frame, pos in positions.items():
                            kf = fc.keyframe_points.insert(float(fps * frame + 1), pos[pos_idx])
                            kf.interpolation = 'LINEAR'

            if bone_idx in anim_data.scales:
                scales = anim_data.scales[bone_idx]
                if scales:
                    for scale_idx in range(3):
                        fc = create_action_fcurve(
                            action, armature_obj,
                            data_path=f'pose.bones["{bone_name}"].scale',
                            index=scale_idx, group_name=bone_name,
                        )
                        for frame, scl in scales.items():
                            kf = fc.keyframe_points.insert(float(fps * frame + 1), scl[scale_idx])
                            kf.interpolation = 'LINEAR'

        start_frame = int(action.frame_range[0]) if action.frame_range[0] > 0 else 1
        scale = 24.0 / anim_data.playback_rate if anim_data.playback_rate > 0 else 1.0
        create_nla_track(armature_obj, action, anim_name, start_frame, scale=scale, blend_type='COMBINE')


class DSTS_OT_AnimExport(Operator, ExportHelper):
    """Export Digimon Time Stranger .anim file"""
    bl_idname = "export_scene.dsts_anim"
    bl_label = "Digimon Time Stranger (.anim)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".anim"
    filter_glob: StringProperty(default="*.anim", options={'HIDDEN'})
    armature_name: EnumProperty(items=fetch_armatures, name="Armature")
    nla_track_name: EnumProperty(items=fetch_nla_tracks, name="NLA Track")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "armature_name")
        layout.prop(self, "nla_track_name")

    def execute(self, context):
        if self.armature_name == "NONE":
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}
        armature_obj = bpy.data.objects.get(self.armature_name)
        if not armature_obj or armature_obj.type != "ARMATURE":
            self.report({'ERROR'}, f"Armature '{self.armature_name}' not found")
            return {'CANCELLED'}
        if not armature_obj.animation_data:
            self.report({'ERROR'}, "Armature has no animation data")
            return {'CANCELLED'}

        nla_track = next((t for t in armature_obj.animation_data.nla_tracks if t.name == self.nla_track_name), None)
        if not nla_track or not nla_track.strips:
            self.report({'ERROR'}, f"NLA track '{self.nla_track_name}' not found or empty")
            return {'CANCELLED'}

        nla_strip = nla_track.strips[0]
        action = nla_strip.action
        if not action:
            self.report({'ERROR'}, "NLA strip has no action")
            return {'CANCELLED'}

        playback_rate = 24.0 / nla_strip.scale if nla_strip.scale > 0 else 30.0
        anim_file = build_basic_hundred_line_anim_from_action(armature_obj, action, playback_rate=playback_rate)

        try:
            anim_file.to_file(self.filepath)
            self.report({'INFO'}, f"Exported animation to {self.filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to export: {str(e)}")
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_AnimExportAll(Operator):
    """Export all NLA tracks as separate .anim files"""
    bl_idname = "export_scene.dsts_anim_all"
    bl_label = "Digimon Time Stranger Export All Animations"
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(name="Output Directory", subtype='DIR_PATH')
    armature_name: EnumProperty(items=fetch_armatures, name="Armature")
    skip_muted: BoolProperty(
        name="Skip Muted Tracks",
        description="Skip NLA tracks that are muted",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "armature_name")
        layout.prop(self, "skip_muted")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.armature_name == "NONE":
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}
        armature_obj = bpy.data.objects.get(self.armature_name)
        if not armature_obj or armature_obj.type != "ARMATURE":
            self.report({'ERROR'}, f"Armature '{self.armature_name}' not found")
            return {'CANCELLED'}
        if not armature_obj.animation_data or not armature_obj.animation_data.nla_tracks:
            self.report({'ERROR'}, "Armature has no NLA tracks")
            return {'CANCELLED'}

        exported_count = 0
        failed_count = 0
        for nla_track in armature_obj.animation_data.nla_tracks:
            if self.skip_muted and nla_track.mute:
                continue
            if not nla_track.strips:
                continue
            nla_strip = nla_track.strips[0]
            action = nla_strip.action
            if not action:
                continue
            playback_rate = 24.0 / nla_strip.scale if nla_strip.scale > 0 else 30.0
            anim_file = build_basic_hundred_line_anim_from_action(armature_obj, action, playback_rate=playback_rate)
            filepath = os.path.join(self.directory, nla_track.name + ".anim")
            try:
                anim_file.to_file(filepath)
                exported_count += 1
            except Exception as e:
                print(f"[DSTS] Failed to export {nla_track.name}: {e}")
                failed_count += 1

        if exported_count > 0:
            self.report({'INFO'}, f"Exported {exported_count} animation(s) to {self.directory}")
        if failed_count > 0:
            self.report({'WARNING'}, f"Failed to export {failed_count} animation(s)")
        if exported_count == 0 and failed_count == 0:
            self.report({'WARNING'}, "No animations to export")
            return {'CANCELLED'}
        return {'FINISHED'}


def export_all_collection(collection, filepath, export_textures_as_png=False):
    from .src.geom import export_geom, export_textures
    from .src.nlst import write_nlst

    output_dir = os.path.dirname(filepath)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    geom = export_geom(collection)
    geom.to_file(filepath)
    export_note = getattr(geom, "export_note", None)

    nlst_path = os.path.splitext(filepath)[0] + ".nlst"
    with open(nlst_path, "wb") as f:
        f.write(write_nlst(geom).encode("utf-8"))

    exported_textures = export_textures(collection, output_dir, as_png=export_textures_as_png)

    armatures = [obj for obj in collection.objects if obj.type == "ARMATURE"]
    exported_anims = 0
    if armatures:
        armature_obj = armatures[0]
        if armature_obj.animation_data and armature_obj.animation_data.nla_tracks:
            for nla_track in armature_obj.animation_data.nla_tracks:
                if not nla_track.strips:
                    continue
                nla_strip = nla_track.strips[0]
                action = nla_strip.action
                if not action:
                    continue
                playback_rate = 24.0 / nla_strip.scale if nla_strip.scale > 0 else 30.0
                anim_file = build_basic_hundred_line_anim_from_action(armature_obj, action, playback_rate=playback_rate)
                anim_path = os.path.join(output_dir, nla_track.name + ".anim")
                try:
                    anim_file.to_file(anim_path)
                    exported_anims += 1
                except Exception as exc:
                    print(f"[DSTS] Failed to export animation {nla_track.name}: {exc}")

    results = [f"Geom: {filepath}", f"NLST: {nlst_path}"]
    if exported_textures:
        results.append(f"Textures: {len(exported_textures)}")
    if exported_anims:
        results.append(f"Animations: {exported_anims}")
    return results, export_note


class DSTS_OT_ExportAll(Operator, ExportHelper):
    """Export everything: Geom + Textures + All Animations"""
    bl_idname = "export_scene.dsts_all"
    bl_label = "Digimon Time Stranger Export All"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".geom"
    filter_glob: StringProperty(default="*.geom", options={'HIDDEN'})

    def collection_items(self, context):
        items = [(col.name, col.name, "") for col in bpy.data.collections if "unknown_0x10" in col]
        return items or [("__NONE__", "No imported Geom collection found", "")]

    collection_name: EnumProperty(
        name="Collection",
        description="Select collection to export",
        items=collection_items,
    )
    export_textures_as_png: BoolProperty(
        name="Export Textures as PNG",
        description="Export textures as PNG files instead of .img (DDS) format",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "collection_name")
        layout.prop(self, "export_textures_as_png")
        layout.separator()
        tex_fmt = "PNG" if self.export_textures_as_png else "IMG"
        layout.label(text="Will export:")
        layout.label(text="  • Geom file (.geom)")
        layout.label(text="  • Name list (.nlst)")
        layout.label(text=f"  • All textures (images/*.{tex_fmt.lower()})")
        layout.label(text="  • All animations (.anim)")

    def execute(self, context):
        if self.collection_name == "__NONE__":
            self.report({'ERROR'}, "No imported Geom collection found")
            return {'CANCELLED'}
        collection = bpy.data.collections.get(self.collection_name)
        if not collection:
            self.report({'ERROR'}, f"Collection '{self.collection_name}' not found")
            return {'CANCELLED'}

        try:
            results, export_note = export_all_collection(
                collection, self.filepath,
                export_textures_as_png=self.export_textures_as_png,
            )
        except Exception as exc:
            print(f"[DSTS] Export All failed: {exc}")
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        if export_note:
            self.report({'WARNING'}, export_note)
        self.report({'INFO'}, f"Exported: {', '.join(results)}")
        return {'FINISHED'}

    def invoke(self, context, event):
        collection = bpy.data.collections.get(self.collection_name)
        if collection:
            blend_dir = os.path.dirname(bpy.data.filepath)
            self.filepath = os.path.join(blend_dir, f"{collection.name}.geom")
        else:
            self.filepath = ""
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# =============================================================================
# ERROR WINDOW OPERATOR
# =============================================================================

class DSTS_OT_ShowErrors(Operator):
    """Show errors in a new window"""
    bl_idname = "wm.show_errors_window"
    bl_label = "Import Errors"

    errors: StringProperty()
    window_name: StringProperty(default="Import Errors Window")
    text_name: StringProperty(default="Import Errors")

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        bpy.ops.screen.area_dupli('INVOKE_DEFAULT')
        new_window = context.window_manager.windows[-1]
        for area in new_window.screen.areas:
            area.type = 'TEXT_EDITOR'
            for space in area.spaces:
                if space.type == 'TEXT_EDITOR':
                    text = bpy.data.texts.new(name=self.text_name)
                    text.clear()
                    text.write("Errors during import:\n\n" + self.errors)
                    space.text = text
                    text.current_line_index = 0
                    space.top = 0
        new_window.screen.name = self.window_name
        return {'FINISHED'}


# =============================================================================
# SUBMENUS
# =============================================================================

class DSTS_MT_ImportSubmenu(bpy.types.Menu):
    """Digimon Time Stranger Import Submenu"""
    bl_idname = "DSTS_MT_import_submenu"
    bl_label = "Digimon Time Stranger (.geom, .anim)"

    def draw(self, context):
        layout = self.layout
        layout.operator(DSTS_OT_GeomImport.bl_idname, text="Geometry (.geom)")
        layout.operator(DSTS_OT_AnimImport.bl_idname, text="Animation (.anim)")


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


def menu_func_import(self, context):
    self.layout.menu(DSTS_MT_ImportSubmenu.bl_idname)


def menu_func_export(self, context):
    self.layout.menu(DSTS_MT_ExportSubmenu.bl_idname)


# =============================================================================
# REGISTRATION
# =============================================================================

CLASSES = (
    DSTS_OT_GeomImport,
    DSTS_OT_GeomExport,
    DSTS_OT_AnimImport,
    DSTS_OT_AnimExport,
    DSTS_OT_AnimExportAll,
    DSTS_OT_ExportAll,
    DSTS_OT_ShowErrors,
    DSTS_MT_ImportSubmenu,
    DSTS_MT_ExportSubmenu,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    from .data import material_nodes
    material_nodes.register()


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    from .data import material_nodes
    material_nodes.unregister()


if __name__ == "__main__":
    register()
