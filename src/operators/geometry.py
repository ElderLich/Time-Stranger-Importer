"""Import, export and package Time Stranger models."""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..animation import animation_path, single_strip

from .animation import _export_action_as_anim
from .context import _ensure_collection_export_metadata, _exportable_geom_collections


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
    include_scene_records: BoolProperty(
        name="Camera and Light Previews", default=False,
        description="Import owned native camera bindings and light controls with the model; records are preserved even when previews are disabled",
    )
    inspect_rigid_components: BoolProperty(
        name="Inspect Rigid Effect Components", default=False,
        description="For fully rigid effects, open a static view of their local meshes and color textures after importing",
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "use_custom_images_path")
        row = layout.row()
        row.enabled = self.use_custom_images_path
        row.prop(self, "images_path")
        layout.prop(self, "include_scene_records")
        layout.prop(self, "inspect_rigid_components")

    def execute(self, context):
        try:
            from ..geom import import_geom
            images_folder = bpy.path.abspath(self.images_path) if self.use_custom_images_path and self.images_path else None
            imported_collection = import_geom(context, self.filepath, images_folder,
                                              include_scene_records=self.include_scene_records)
            if self.inspect_rigid_components:
                from .rigid_components import is_rigid_effect, open_components
                if is_rigid_effect(imported_collection):
                    try:
                        open_components(context, imported_collection)
                    except Exception as exc:
                        self.report({'WARNING'}, f"Imported geometry. Could not open the component view: {exc}")
                        return {'FINISHED'}
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
        items = [(col.name, col.name, "") for col in _exportable_geom_collections()]
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
        from ..geom import export_geom, export_textures
        from ..nlst import write_nlst

        if self.collection_name == "__NONE__":
            self.report({'ERROR'}, "No imported Geom collection found")
            return {'CANCELLED'}
        collection = bpy.data.collections.get(self.collection_name)
        if not collection:
            self.report({'ERROR'}, f"Collection '{self.collection_name}' not found")
            return {'CANCELLED'}

        try:
            _ensure_collection_export_metadata(collection)
            geom = export_geom(collection)
            geom.to_file(self.filepath)
        except Exception as exc:
            print(f"[DSTS] Geom export failed: {exc}")
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        export_note = getattr(geom, "export_note", None)
        if export_note:
            self.report({'WARNING'}, export_note)

        try:
            from ..spark import atomic_write
            atomic_write(os.path.splitext(self.filepath)[0] + ".nlst",
                         write_nlst(geom).encode("utf-8"), overwrite=True)

            if self.export_textures:
                output_dir = os.path.dirname(self.filepath)
                exported_textures = export_textures(collection, output_dir, as_png=self.export_textures_as_png)
                if exported_textures:
                    fmt = "PNG" if self.export_textures_as_png else "DDS"
                    self.report({'INFO'}, f"Exported: {self.filepath} + {len(exported_textures)} {fmt} texture(s)")
                else:
                    self.report({'INFO'}, f"Exported: {self.filepath} (no textures found)")
            else:
                self.report({'INFO'}, f"Exported: {self.filepath}")
        except Exception as exc:
            self.report({'ERROR'}, f"Geometry exported, but a dependent file failed: {exc}")
            return {'CANCELLED'}
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


def export_all_collection(collection, filepath, export_textures_as_png=False):
    from ..geom import export_geom, export_textures
    from ..nlst import write_nlst

    output_dir = os.path.dirname(filepath)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    _ensure_collection_export_metadata(collection)
    geom = export_geom(collection)
    geom.to_file(filepath)
    export_note = getattr(geom, "export_note", None)

    nlst_path = os.path.splitext(filepath)[0] + ".nlst"
    from ..spark import atomic_write
    atomic_write(nlst_path, write_nlst(geom).encode("utf-8"), overwrite=True)

    exported_textures = export_textures(collection, output_dir, as_png=export_textures_as_png)

    armatures = [obj for obj in collection.objects if obj.type == "ARMATURE"]
    exported_anims = 0
    if armatures:
        armature_obj = armatures[0]
        if collection.get("DSTS_source_rearise_glb"):
            from .animation import _build_rest_pose_anim_file
            base_animation = _build_rest_pose_anim_file(armature_obj)
            base_animation.to_file(os.path.splitext(filepath)[0] + ".anim")
            exported_anims += 1
        if armature_obj.animation_data and armature_obj.animation_data.nla_tracks:
            for nla_track in armature_obj.animation_data.nla_tracks:
                from ..native_rest_scale import is_rest_track
                if is_rest_track(nla_track):
                    continue
                if not nla_track.strips:
                    continue
                try:
                    nla_strip = single_strip(nla_track)
                    anim_path = animation_path(output_dir, nla_track.name)
                    _export_action_as_anim(armature_obj, nla_strip.action, nla_strip, anim_path)
                    exported_anims += 1
                except Exception as exc:
                    raise RuntimeError(f"Geometry was exported, but animation '{nla_track.name}' failed: {exc}") from exc

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
        items = [(col.name, col.name, "") for col in _exportable_geom_collections()]
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
        tex_fmt = "PNG" if self.export_textures_as_png else "DDS"
        layout.label(text="Will export:")
        layout.label(text="  â€¢ Geom file (.geom)")
        layout.label(text="  â€¢ Name list (.nlst)")
        layout.label(text=f"  â€¢ All textures (images/*.{tex_fmt.lower()})")
        layout.label(text="  â€¢ All animations (.anim)")

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


class DSTS_OT_PackageNativeModel(Operator, ImportHelper):
    """Validate an exported GEOM and its existing native dependencies in a new package"""
    bl_idname = "export_scene.dsts_model_package"
    bl_label = "Package Exported Native Model"
    filename_ext = ".geom"
    filter_glob: StringProperty(default="*.geom", options={'HIDDEN'})
    output_directory: StringProperty(name="New Package Folder", subtype='DIR_PATH')
    resource_root: StringProperty(name="Native Resource Folder", subtype='DIR_PATH',
        description="Optional extracted folder containing images/ and shaders/; these native dependencies are recorded, not copied")
    include_motions: BoolProperty(name="Include Same-Stem Motions", default=True,
        description="Include matching model_*.anim files, excluding separate LOD assets; every clip must match the model's channel counts")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'output_directory')
        layout.prop(self, 'resource_root')
        layout.prop(self, 'include_motions')
        layout.label(text="Select an exported GEOM with base ANIM and NLST.", icon='INFO')
        layout.label(text="Effects, cinematics and table hooks are separate.")

    def execute(self, context):
        from ..model_package import package_native_model
        if not self.output_directory:
            self.report({'ERROR'}, 'Choose a new package folder')
            return {'CANCELLED'}
        try:
            report = package_native_model(self.filepath, bpy.path.abspath(self.output_directory),
                bpy.path.abspath(self.resource_root) if self.resource_root else None, self.include_motions)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Packaged {len(report['files'])} files; "
            f"{len(report['external_native_dependencies'])} native dependencies. In-game validation remains required.")
        return {'FINISHED'}
