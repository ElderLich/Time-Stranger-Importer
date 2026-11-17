"""Import and export native motions and their animation channels."""

import math
import os
import re

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector

from ..animation import animation_path, single_strip, validate_strip
from ..blender_compat import create_nla_track, iter_action_fcurves, scene_fps

from .context import (
    _DEFAULT_EXTRACTED_ROOT,
    _active_geom_collection,
    _find_geom_workflow_armature,
    _geom_collection_items,
    _normalize_chr_stem,
    _resolve_donor_geom_path,
    _scene_extracted_root,
)
from .nla import _action_has_object_transform_curves


def build_basic_hundred_line_anim_from_action(armature_obj, action, playback_rate=30.0, slot=None, frame_range=None):
    from ..animation import build_from_action
    anim_file = build_from_action(armature_obj, action, playback_rate, slot, frame_range)
    bone_names = [bone.name for bone in armature_obj.data.bones]
    _compose_object_motion_onto_root(armature_obj, action, anim_file, bone_names,
                                     float((frame_range or action.frame_range)[0]), slot=slot)
    return anim_file


def _compose_object_motion_onto_root(armature_obj, action, anim_file, bone_names, action_frame_start, slot=None):
    """Fold object-level animation of ``action`` into the root bone's tracks.

    Native motion blending adds parent-space translations, multiplies rest and
    delta quaternions, and multiplies scale components. Build that absolute root
    pose in Blender axes, apply the object motion, then recover native deltas.
    The geometry exporter requires identity object rest transforms.
    """
    from ..nc_glb import (
        OBJECT_TRANSFORM_PATHS,
        _channel_defaults,
        _pose_slot_and_fcurves,
        object_basis_matrix_from_fcurves,
    )

    if not _action_has_object_transform_curves(
        action, include_location=True, include_rotation=True, include_scale=True
    ):
        return False

    # Only object curves living alongside the pose curves (same action slot)
    # belong to this armature; other slots animate other objects.
    slot_fcurves = list(iter_action_fcurves(action, armature_obj, slot))
    if not slot_fcurves:
        return False
    object_fcurves = {}
    for fc in slot_fcurves:
        data_path = str(getattr(fc, "data_path", ""))
        if data_path in OBJECT_TRANSFORM_PATHS:
            object_fcurves[(data_path, int(getattr(fc, "array_index", 0)))] = fc
    if not object_fcurves:
        return False

    root_bone = next((b for b in armature_obj.data.bones if b.parent is None), None)
    if root_bone is None or root_bone.name not in bone_names:
        return False
    root_idx = bone_names.index(root_bone.name)
    rest_root = root_bone.matrix_local.copy()
    rest_position, rest_rotation, rest_scale = rest_root.decompose()
    native_to_blender = Matrix.Rotation(math.radians(90), 3, 'X')
    blender_to_native = native_to_blender.inverted()

    frames = range(anim_file.frame_count)
    obj_defaults = _channel_defaults(armature_obj)
    rotation_mode = armature_obj.rotation_mode
    parent_world = armature_obj.parent.matrix_world.copy() if armature_obj.parent else Matrix.Identity(4)
    parent_inverse = armature_obj.matrix_parent_inverse.copy()

    from mathutils import Quaternion, Vector
    new_rot, new_pos, new_scl = {}, {}, {}
    previous_quat = None
    for frame_anim in sorted(frames):
        frame_abs = float(frame_anim + action_frame_start)
        object_basis = object_basis_matrix_from_fcurves(obj_defaults, rotation_mode, object_fcurves, frame_abs)
        object_world = parent_world @ parent_inverse @ object_basis

        old_loc = Vector(anim_file.positions[root_idx].get(frame_anim, [0, 0, 0]))
        native_quat = anim_file.rotations[root_idx].get(frame_anim, [0, 0, 0, 1])
        old_quat = Quaternion((native_quat[3], *native_quat[:3]))
        old_scale = Vector(anim_file.scales[root_idx].get(frame_anim, [1, 1, 1]))
        old_pose = Matrix.LocRotScale(
            rest_position + native_to_blender @ old_loc,
            rest_rotation @ old_quat,
            Vector([rest_scale[i] * old_scale[i] for i in range(3)]))
        new_pose = object_world @ old_pose
        position, rotation, pose_scale = new_pose.decompose()
        loc = blender_to_native @ (position - rest_position)
        quat = rest_rotation.inverted() @ rotation
        scale = Vector([pose_scale[i] / rest_scale[i] for i in range(3)])
        if previous_quat is not None and previous_quat.dot(quat) < 0.0:
            quat = -quat
        previous_quat = quat
        new_rot[frame_anim] = [quat.x, quat.y, quat.z, quat.w]
        new_pos[frame_anim] = [loc.x, loc.y, loc.z]
        new_scl[frame_anim] = [scale.x, scale.y, scale.z]

    anim_file.rotations[root_idx] = new_rot
    anim_file.positions[root_idx] = new_pos
    anim_file.scales[root_idx] = new_scl
    print(f"[DSTS] {action.name}: composed object-level motion onto root bone '{root_bone.name}' ({len(frames)} keys)")
    return True


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
        if not track.mute and not track.lock and track.strips
    ]
    return tuple(track_list) if track_list else (("NONE", "No Active Tracks", "", "ERROR", 0),)


def _build_anim_action_from_data(armature_obj, anim_name, anim_data, bone_names):
    from ..animation import build_action
    return build_action(armature_obj, anim_name, anim_data, bone_names)


def _build_anim_action_and_nla(armature_obj, anim_name, anim_data, bone_names, replace_existing=False):
    """Stage a valid replacement before removing an existing working clip."""
    ad = armature_obj.animation_data or armature_obj.animation_data_create()
    previous_action, previous_slot = ad.action, getattr(ad, 'action_slot', None)
    old_tracks = [t for t in ad.nla_tracks if replace_existing and t.name == anim_name]
    before_tracks = set(ad.nla_tracks)
    before_actions = set(bpy.data.actions)
    try:
        action = _build_anim_action_from_data(armature_obj, anim_name, anim_data, bone_names)
        start_frame = int(action.frame_range[0]) if action.frame_range[0] > 0 else 1
        scale = scene_fps(bpy.context.scene) / anim_data.playback_rate if anim_data.playback_rate > 0 else 1.0
        from ..animation import native_blend_type
        new_track, _ = create_nla_track(armature_obj, action, anim_name, start_frame,
                                        scale=scale, blend_type=native_blend_type(armature_obj))
    except Exception:
        for track in list(ad.nla_tracks):
            if track not in before_tracks:
                ad.nla_tracks.remove(track)
        ad.action = previous_action
        if previous_slot is not None:
            ad.action_slot = previous_slot
        for created in list(bpy.data.actions):
            if created not in before_actions and created.users == 0:
                bpy.data.actions.remove(created)
        raise
    for track in old_tracks:
        old_actions = [strip.action for strip in track.strips if strip.action]
        ad.nla_tracks.remove(track)
        for old_action in old_actions:
            if old_action != action and old_action.users == 0:
                bpy.data.actions.remove(old_action)
    if old_tracks:
        new_track.name = anim_name
        action.name = anim_name
    return action


def _read_anim_file(filepath):
    """Parse a .anim file into interface AnimData (HundredLine/Time Stranger)."""
    from ..anim import AnimFileHundredLine
    from ..anim.BinaryHundredLine import AnimFileBinary as AnimFileBinaryHundredLine
    ab = AnimFileBinaryHundredLine()
    ab.read(filepath)
    return AnimFileHundredLine.from_binary(ab)


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
        from ..anim import AnimFileHundredLine
        from ..anim.BinaryHundredLine import AnimFileBinary as AnimFileBinaryHundredLine

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
                anim_data = _read_anim_file(filepath)
                _build_anim_action_and_nla(armature_obj, anim_name, anim_data, bone_names)
            except Exception as e:
                self.report({'WARNING'}, f"Failed to import {f.name}: {str(e)}")

        self.report({'INFO'}, f"Imported {len(self.files)} animation(s)")
        return {'FINISHED'}

    def _import_animation(self, armature_obj, anim_name, anim_data, bone_names):
        # Kept for backward compatibility; delegates to the shared builder.
        return _build_anim_action_and_nla(armature_obj, anim_name, anim_data, bone_names)


class DSTS_OT_ImportDonorAnimations(Operator):
    """Import every donor clip (<donor>*.anim, next to the donor .geom) onto the Geom armature as NLA tracks"""
    bl_idname = "object.dsts_import_donor_animations"
    bl_label = "Import Donor Animations"
    bl_options = {'REGISTER', 'UNDO'}

    collection_name: EnumProperty(
        name="Collection",
        description="Geom collection whose armature receives the donor animations",
        items=_geom_collection_items,
    )
    source_chr: StringProperty(
        name="Donor Chr",
        description="Donor Digimon stem whose .anim clips to import, for example chr050",
        default="chr050",
    )
    source_geom_path: StringProperty(
        name="Donor Geom",
        description="Optional direct donor .geom path; its folder is scanned for <donor>*.anim",
        subtype='FILE_PATH',
        default="",
    )
    extracted_root: StringProperty(
        name="Extracted Assets Root",
        description="Root folder containing extracted Time Stranger assets",
        subtype='DIR_PATH',
        default=_DEFAULT_EXTRACTED_ROOT,
    )
    replace_existing: BoolProperty(
        name="Replace Existing Tracks",
        description="Remove an NLA track of the same clip name before importing so re-running does not stack duplicates",
        default=True,
    )
    def invoke(self, context, event):
        collection = _active_geom_collection(context)
        if collection:
            self.collection_name = collection.name
            self.source_chr = collection.get("DSTS_material_donor_chr", self.source_chr)
            self.source_geom_path = collection.get("DSTS_material_donor_geom", self.source_geom_path)
        self.extracted_root = _scene_extracted_root(context)
        return context.window_manager.invoke_props_dialog(self, width=650)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "collection_name")
        layout.prop(self, "source_chr")
        layout.prop(self, "source_geom_path")
        layout.prop(self, "extracted_root")
        layout.prop(self, "replace_existing")

    def execute(self, context):
        if self.collection_name not in ("__NONE__", ""):
            collection = bpy.data.collections.get(self.collection_name)
        else:
            collection = _active_geom_collection(context)
        armature = _find_geom_workflow_armature(context, collection)
        if armature is None:
            self.report({'ERROR'}, "No target Geom armature found")
            return {'CANCELLED'}

        try:
            donor_path = _resolve_donor_geom_path(self.extracted_root, self.source_chr, self.source_geom_path)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        stem = _normalize_chr_stem(self.source_chr)[0]
        folder = os.path.dirname(donor_path)
        # Match "<stem>.anim" (the base) and "<stem>_<clip>.anim", but NOT a longer
        # stem like chr0500 â€” the char after the stem must be "_" or ".".
        pattern = re.compile(r'^' + re.escape(stem) + r'(_.*)?\.anim$', re.IGNORECASE)
        try:
            anim_files = sorted(f for f in os.listdir(folder) if pattern.match(f))
        except OSError as exc:
            self.report({'ERROR'}, f"Could not read donor folder: {exc}")
            return {'CANCELLED'}
        if not anim_files:
            self.report({'ERROR'}, f"No {stem}*.anim files found next to {os.path.basename(donor_path)}")
            return {'CANCELLED'}

        if not armature.animation_data:
            armature.animation_data_create()
        bone_names = [b.name for b in armature.data.bones]

        imported = 0
        failed = 0
        for name in anim_files:
            anim_name = os.path.splitext(name)[0]
            try:
                anim_data = _read_anim_file(os.path.join(folder, name))
                _build_anim_action_and_nla(armature, anim_name, anim_data, bone_names, replace_existing=self.replace_existing)
                imported += 1
            except Exception as exc:
                print(f"[DSTS] Failed to import donor anim {name}: {exc}")
                failed += 1

        if collection is not None:
            collection["DSTS_material_donor_chr"] = stem
        self.report(
            {'INFO'},
            f"Imported {imported} donor animation(s) onto '{armature.name}'"
            + (f"; {failed} failed (see console)" if failed else ""),
        )
        return {'FINISHED'}


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

        try:
            nla_strip = single_strip(nla_track)
            _export_action_as_anim(armature_obj, nla_strip.action, nla_strip, self.filepath)
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
            from ..native_rest_scale import is_rest_track
            if is_rest_track(nla_track):
                continue
            if self.skip_muted and nla_track.mute:
                continue
            if not nla_track.strips:
                continue
            try:
                nla_strip = single_strip(nla_track)
                filepath = animation_path(self.directory, nla_track.name)
                _export_action_as_anim(armature_obj, nla_strip.action, nla_strip, filepath)
                exported_count += 1
            except Exception as e:
                print(f"[DSTS] Failed to export {nla_track.name}: {e}")
                failed_count += 1

        if exported_count > 0:
            self.report({'INFO'}, f"Exported {exported_count} animation(s) to {self.directory}")
        if failed_count > 0:
            self.report({'WARNING'}, f"Failed to export {failed_count} animation(s)")
        if exported_count == 0:
            self.report({'WARNING'}, "No animations were exported")
            return {'CANCELLED'}
        return {'FINISHED'}


def _build_rest_pose_anim_file(armature_obj, playback_rate=30.0):
    """Keep GEOM's native bind pose by leaving base ANIM channels absent.

    Native 0x140364070 copies the GEOM rest transforms before evaluating an
    animation. Explicit identity keys overwrite those transforms; unlike an
    empty native base clip, they collapse nonzero bone offsets. Genuine chr050
    and chr395 base files use this sparse one-frame representation.
    """
    from ..anim import AnimFileHundredLine
    if not armature_obj or armature_obj.type != 'ARMATURE' or not armature_obj.data.bones:
        raise RuntimeError('Armature has no bones to export as a base rest pose')
    if not math.isfinite(playback_rate) or playback_rate <= 0:
        raise ValueError('Animation playback rate must be finite and positive')
    result = AnimFileHundredLine()
    result.bone_count = len(armature_obj.data.bones)
    result.bone_blend_factors = [255] * result.bone_count
    result.frame_count = 1
    result.playback_rate = playback_rate
    return result


def _export_action_as_anim(armature_obj, action, strip, filepath):
    validate_strip(strip)
    playback_rate = scene_fps(bpy.context.scene) / strip.scale if strip and strip.scale > 0 else 30.0
    anim_file = build_basic_hundred_line_anim_from_action(armature_obj, action, playback_rate=playback_rate, slot=strip.action_slot, frame_range=(strip.action_frame_start, strip.action_frame_end))
    anim_file.to_file(filepath)
