"""Set up base NLA tracks and clean action transforms."""

import re

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.types import Operator

from ..blender_compat import create_action_fcurve, create_nla_track, iter_action_fcurves

from .context import (
    _active_geom_collection,
    _find_geom_workflow_armature,
    _find_workflow_armature,
    _normalize_chr_stem,
)


_ACTION_BONE_RE = re.compile(r'pose\.bones\["([^"]+)"\]')


def _find_action_by_name(action_name):
    action_name = str(action_name or "").strip()
    if not action_name:
        return None

    action = bpy.data.actions.get(action_name)
    if action is not None:
        return action

    lowered = action_name.lower()
    for action in bpy.data.actions:
        if action.name.lower() == lowered:
            return action
    return None


def _find_base_action(target_stem, explicit_action_name=""):
    explicit = _find_action_by_name(explicit_action_name)
    if explicit is not None:
        return explicit

    expected = f"{target_stem}_base"
    action = _find_action_by_name(expected)
    if action is not None:
        return action

    lowered_expected = expected.lower()
    for action in bpy.data.actions:
        name = action.name.lower()
        if name == lowered_expected or name.endswith(f"|{lowered_expected}") or name.endswith(f"/{lowered_expected}"):
            return action
    return None


def _remove_nla_tracks_named(armature, track_name):
    if not armature or not armature.animation_data:
        return 0
    removed = 0
    for track in list(armature.animation_data.nla_tracks):
        if track.name == track_name:
            armature.animation_data.nla_tracks.remove(track)
            removed += 1
    return removed


def _nla_track_index(nla_tracks, target_track):
    for index, track in enumerate(nla_tracks):
        if track == target_track:
            return index
    return None


def _move_nla_track_to_index(nla_tracks, track, index):
    if track is None or not hasattr(nla_tracks, "move"):
        return False

    current_index = _nla_track_index(nla_tracks, track)
    if current_index is None:
        return False

    target_index = max(0, min(int(index), max(0, len(nla_tracks) - 1)))
    if current_index == target_index:
        return False

    try:
        nla_tracks.move(current_index, target_index)
        return True
    except Exception as exc:
        print(f"[DSTS] Could not reorder NLA track '{track.name}': {exc}")
        return False


def _configure_workflow_nla_track(
    armature,
    action,
    track_name,
    frame_start,
    frame_end,
    replace_existing=True,
    blend_type='REPLACE',
):
    if not armature.animation_data:
        armature.animation_data_create()

    if replace_existing:
        _remove_nla_tracks_named(armature, track_name)
    else:
        existing = next((track for track in armature.animation_data.nla_tracks if track.name == track_name), None)
        if existing and existing.strips:
            strip = existing.strips[0]
            existing.mute = False
            if hasattr(strip, "mute"):
                strip.mute = False
            strip.extrapolation = 'HOLD'
            strip.blend_type = blend_type
            return existing, strip, False

    start = float(frame_start)
    end = float(frame_end)
    if end <= start:
        end = start + 1.0

    track, strip = create_nla_track(armature, action, track_name, int(start), scale=1.0, blend_type=blend_type)
    track.mute = False
    if hasattr(strip, "mute"):
        strip.mute = False
    strip.extrapolation = 'HOLD'
    strip.blend_type = blend_type
    strip.frame_start = start
    strip.frame_end = end
    return track, strip, True


def _action_has_pose_curves(action):
    return any(str(getattr(fc, "data_path", "")).startswith("pose.bones") for fc in iter_action_fcurves(action))


def _seed_rest_pose_into_action(armature, action, frame=1.0):
    """Insert one rest-pose (identity basis) key per pose bone into ``action``.

    This is an editable Blender action: identity basis displays the rig's rest
    pose regardless of its current pose. Native stem.anim files use sparse
    channels instead; see _build_rest_pose_anim_file. Returns the bone count.
    """
    if not armature or armature.type != "ARMATURE" or not armature.pose:
        return 0
    frame = float(frame)
    seeded = 0
    for pose_bone in armature.pose.bones:
        base_path = f'pose.bones["{pose_bone.name}"]'
        channels = [(f"{base_path}.location", (0.0, 0.0, 0.0))]
        mode = pose_bone.rotation_mode
        if mode == 'AXIS_ANGLE':
            channels.append((f"{base_path}.rotation_axis_angle", (0.0, 0.0, 1.0, 0.0)))
        elif mode == 'QUATERNION':
            channels.append((f"{base_path}.rotation_quaternion", (1.0, 0.0, 0.0, 0.0)))
        else:
            channels.append((f"{base_path}.rotation_euler", (0.0, 0.0, 0.0)))
        channels.append((f"{base_path}.scale", (1.0, 1.0, 1.0)))
        for data_path, values in channels:
            for index, value in enumerate(values):
                fcurve = create_action_fcurve(action, armature, data_path, index=index, group_name=pose_bone.name)
                fcurve.keyframe_points.insert(frame, value)
        seeded += 1
    return seeded


def _action_has_object_transform_curves(action, include_location=False, include_rotation=True, include_scale=True, epsilon=1e-5):
    wanted = set()
    if include_location:
        wanted.add("location")
    if include_rotation:
        wanted.update(("rotation_quaternion", "rotation_euler", "rotation_axis_angle"))
    if include_scale:
        wanted.add("scale")

    if not wanted:
        return False

    identity = {
        "location": (0.0, 0.0, 0.0),
        "rotation_quaternion": (1.0, 0.0, 0.0, 0.0),
        "rotation_euler": (0.0, 0.0, 0.0),
        "rotation_axis_angle": (0.0, 0.0, 1.0, 0.0),
        "scale": (1.0, 1.0, 1.0),
    }
    for fc in iter_action_fcurves(action):
        data_path = str(getattr(fc, "data_path", ""))
        if data_path not in wanted:
            continue
        array_index = int(getattr(fc, "array_index", 0))
        defaults = identity.get(data_path, ())
        expected = defaults[array_index] if array_index < len(defaults) else 0.0
        for keyframe in getattr(fc, "keyframe_points", []):
            if abs(float(keyframe.co[1]) - expected) > epsilon:
                return True
    return False


def _clear_fcurve_to_constant(fc, frame_start, frame_end, value):
    keyframes = fc.keyframe_points
    try:
        keyframes.clear()
    except AttributeError:
        for keyframe in reversed(keyframes):
            keyframes.remove(keyframe)
    for frame in sorted({float(frame_start), float(frame_end)}):
        keyframe = keyframes.insert(frame, float(value), options={'FAST'})
        keyframe.interpolation = 'CONSTANT'
    fc.update()


def _clean_action_object_transforms(armature_obj, action, include_location=False, include_rotation=True, include_scale=True):
    if not _action_has_pose_curves(action):
        return False

    if not _action_has_object_transform_curves(
        action,
        include_location=include_location,
        include_rotation=include_rotation,
        include_scale=include_scale,
    ):
        return False

    frame_start = int(action.frame_range[0]) if action.frame_range else 1
    frame_end = int(action.frame_range[1]) if action.frame_range else frame_start
    cleaned_paths = []

    if include_location:
        for index, value in enumerate((0.0, 0.0, 0.0)):
            fc = create_action_fcurve(action, armature_obj, "location", index=index, group_name="Object Transform")
            _clear_fcurve_to_constant(fc, frame_start, frame_end, value)
        cleaned_paths.append("location")

    if include_rotation:
        for index, value in enumerate((1.0, 0.0, 0.0, 0.0)):
            fc = create_action_fcurve(action, armature_obj, "rotation_quaternion", index=index, group_name="Object Transform")
            _clear_fcurve_to_constant(fc, frame_start, frame_end, value)
        for data_path, values in (
            ("rotation_euler", (0.0, 0.0, 0.0)),
            ("rotation_axis_angle", (0.0, 0.0, 1.0, 0.0)),
        ):
            for fc in iter_action_fcurves(action):
                if getattr(fc, "data_path", "") == data_path:
                    array_index = int(getattr(fc, "array_index", 0))
                    value = values[array_index] if array_index < len(values) else 0.0
                    _clear_fcurve_to_constant(fc, frame_start, frame_end, value)
        cleaned_paths.append("rotation")

    if include_scale:
        for index, value in enumerate((1.0, 1.0, 1.0)):
            fc = create_action_fcurve(action, armature_obj, "scale", index=index, group_name="Object Transform")
            _clear_fcurve_to_constant(fc, frame_start, frame_end, value)
        cleaned_paths.append("scale")

    action["DSTS_cleaned_object_transforms"] = ", ".join(cleaned_paths)
    return True


class DSTS_OT_CleanActionObjectTransforms(Operator):
    """Clear armature object transform curves from pose actions"""
    bl_idname = "object.dsts_clean_action_object_transforms"
    bl_label = "Clean Action Object Transforms"
    bl_options = {'REGISTER', 'UNDO'}

    include_location: BoolProperty(
        name="Clear Location Curves",
        description="Also reset armature object location curves; leave off to preserve possible root motion",
        default=False,
    )

    include_rotation: BoolProperty(
        name="Clear Rotation Curves",
        description="Reset armature object rotation curves to identity",
        default=True,
    )

    include_scale: BoolProperty(
        name="Clear Scale Curves",
        description="Reset armature object scale curves to identity",
        default=True,
    )

    all_actions: BoolProperty(
        name="All Pose Actions",
        description="Clean every action that contains pose-bone animation",
        default=True,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "all_actions")
        layout.prop(self, "include_rotation")
        layout.prop(self, "include_scale")
        layout.prop(self, "include_location")

    def execute(self, context):
        collection = _active_geom_collection(context)
        armature = _find_workflow_armature(context, collection)
        if armature is None:
            self.report({'ERROR'}, "No armature found")
            return {'CANCELLED'}

        if self.all_actions:
            actions = [action for action in bpy.data.actions if _action_has_pose_curves(action)]
        elif armature.animation_data and armature.animation_data.action:
            actions = [armature.animation_data.action]
        else:
            self.report({'ERROR'}, "No active action found")
            return {'CANCELLED'}

        cleaned = 0
        for action in actions:
            if _clean_action_object_transforms(
                armature,
                action,
                include_location=self.include_location,
                include_rotation=self.include_rotation,
                include_scale=self.include_scale,
            ):
                cleaned += 1

        if cleaned == 0:
            self.report({'INFO'}, "No pose actions had non-identity object transform curves")
        else:
            self.report({'INFO'}, f"Cleaned object transform curves on {cleaned} action(s)")
        return {'FINISHED'}


class DSTS_OT_SetupBaseAnimationNla(Operator):
    """Create (or reuse) the rest-pose base action, then push it down and lock it as an NLA reference strip"""
    bl_idname = "object.dsts_setup_base_animation_nla"
    bl_label = "Setup Base Animation NLA"
    bl_options = {'REGISTER', 'UNDO'}

    target_chr: StringProperty(
        name="Target Chr",
        description="Target/vanilla character stem used to find the base action, for example chr050",
        default="chr050",
    )

    base_action_name: StringProperty(
        name="Base Action",
        description="Optional exact base action name; leave blank to use target_chr_base",
        default="",
    )

    replace_existing: BoolProperty(
        name="Replace Existing Base",
        description="Remove and rebuild an existing target_chr_base NLA track",
        default=True,
    )

    lock_base: BoolProperty(
        name="Lock Base Track",
        description="Lock the base track after creating it so it is treated as a reference strip",
        default=True,
    )

    clear_active_action: BoolProperty(
        name="Clear Active Action",
        description="Set the armature action back to No Action after pushing down the base strip",
        default=True,
    )

    clean_action_object_transforms: BoolProperty(
        name="Clean Action Object Transforms",
        description="Reset armature object rotation/scale curves on the base action",
        default=True,
    )

    base_start_frame: FloatProperty(
        name="Base Start",
        description="NLA strip start for the base pose action",
        default=1.0,
    )

    base_end_frame: FloatProperty(
        name="Base End",
        description="NLA strip end for the base pose action",
        default=2.0,
    )

    def invoke(self, context, event):
        collection = _active_geom_collection(context)
        if collection:
            self.target_chr = collection.get("DSTS_target_chr", self.target_chr)
        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "target_chr")
        layout.prop(self, "base_action_name")
        layout.prop(self, "replace_existing")
        layout.prop(self, "lock_base")
        layout.prop(self, "clear_active_action")
        layout.prop(self, "clean_action_object_transforms")
        row = layout.row(align=True)
        row.prop(self, "base_start_frame")
        row.prop(self, "base_end_frame")

    def execute(self, context):
        collection = _active_geom_collection(context)
        armature = _find_geom_workflow_armature(context, collection)
        if armature is None:
            self.report({'ERROR'}, "No target Geom armature found")
            return {'CANCELLED'}

        target_stem, _target_number = _normalize_chr_stem(self.target_chr)
        base_action = _find_base_action(target_stem, self.base_action_name)

        # Create the base action if none exists, and seed a rest-pose key per bone
        # if it has none. This is the whole point of the button: the importer never
        # produces a "<stem>_base" action, so previously this always cancelled. Now
        # it builds the rest-pose base action itself (Romstar's add_rest_pose_to_base_anim
        # equivalent) so the workflow is genuinely one-click.
        base_action_created = False
        if base_action is None:
            base_action = bpy.data.actions.new(f"{target_stem}_base")
            base_action_created = True

        seeded_bones = 0
        if not _action_has_pose_curves(base_action):
            seeded_bones = _seed_rest_pose_into_action(armature, base_action, frame=self.base_start_frame)
            if seeded_bones == 0:
                self.report({'ERROR'}, "Armature has no pose bones to seed a base rest pose")
                return {'CANCELLED'}

        if not armature.animation_data:
            armature.animation_data_create()

        cleaned = 0
        if self.clean_action_object_transforms:
            if _clean_action_object_transforms(
                armature,
                base_action,
                include_location=False,
                include_rotation=True,
                include_scale=True,
            ):
                cleaned = 1

        base_track_name = f"{target_stem}_base"
        base_track, _base_strip, base_created = _configure_workflow_nla_track(
            armature,
            base_action,
            base_track_name,
            self.base_start_frame,
            self.base_end_frame,
            replace_existing=self.replace_existing,
            blend_type='REPLACE',
        )
        base_track.lock = bool(self.lock_base)
        if self.clear_active_action and armature.animation_data:
            armature.animation_data.action = None

        armature["DSTS_base_action"] = base_action.name
        armature["DSTS_base_track"] = base_track_name
        armature["DSTS_target_chr"] = target_stem
        if collection:
            collection["DSTS_target_chr"] = target_stem
            collection["DSTS_base_action"] = base_action.name
            collection["DSTS_base_track"] = base_track_name
        try:
            context.scene.dsts_workflow_target_chr = target_stem
        except Exception:
            pass

        state = "created" if base_created else "reused"
        lock_text = "locked" if base_track.lock else "unlocked"
        if base_action_created:
            action_state = f"new action seeded {seeded_bones} bones"
        elif seeded_bones:
            action_state = f"seeded {seeded_bones} bones into existing action"
        else:
            action_state = "existing action"
        self.report(
            {'INFO'},
            f"Base setup {state}: track {base_track_name} ({lock_text}) uses {base_action.name} [{action_state}]; cleaned {cleaned}; active action cleared {bool(self.clear_active_action)}"
        )
        return {'FINISHED'}
