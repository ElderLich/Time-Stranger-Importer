"""Prepare geometry, armatures and transforms for native export."""

import bpy
from bpy.types import Operator
from mathutils import Matrix

from .context import (
    _GEOM_METADATA_KEYS,
    _active_geom_collection,
    _find_workflow_armature,
    _target_chr_from_context,
)
from .nla import _action_has_object_transform_curves, _action_has_pose_curves


_IDENTITY_4X4 = Matrix.Identity(4)


def _matrix_is_identity(matrix, epsilon=1e-5):
    for row in range(4):
        for col in range(4):
            if abs(matrix[row][col] - _IDENTITY_4X4[row][col]) > epsilon:
                return False
    return True


def _posed_bones(armature):
    if not armature or armature.type != "ARMATURE" or not armature.pose:
        return []
    return [pose_bone for pose_bone in armature.pose.bones if not _matrix_is_identity(pose_bone.matrix_basis)]


def _objects_with_unapplied_transforms(collection, object_types):
    if not collection:
        return []
    return [
        obj for obj in collection.objects
        if obj.type in object_types and not _matrix_is_identity(obj.matrix_basis)
    ]


def _ensure_object_mode(context):
    active = getattr(context, "object", None)
    if active and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode='OBJECT')


def _link_selected_to_collection(context, collection):
    candidates = [obj for obj in context.selected_objects if obj.type in {"MESH", "ARMATURE"}]
    linked = 0
    for obj in candidates:
        if not any(existing == obj for existing in collection.objects):
            collection.objects.link(obj)
            linked += 1
    return len(candidates), linked


def _apply_pose_as_rest(context, armature):
    posed_count = len(_posed_bones(armature))
    if posed_count == 0:
        return 0
    _ensure_object_mode(context)
    previous_selection = list(context.selected_objects)
    previous_active = context.view_layer.objects.active
    bpy.ops.object.select_all(action='DESELECT')
    armature.select_set(True)
    context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    for obj in previous_selection:
        if obj.name in bpy.data.objects:
            obj.select_set(True)
    if previous_active and previous_active.name in bpy.data.objects:
        context.view_layer.objects.active = previous_active
    return posed_count


def _clear_armature_pose(armature):
    changed = 0
    for pose_bone in armature.pose.bones:
        if _matrix_is_identity(pose_bone.matrix_basis):
            continue
        pose_bone.location = (0.0, 0.0, 0.0)
        pose_bone.scale = (1.0, 1.0, 1.0)
        if pose_bone.rotation_mode == 'QUATERNION':
            pose_bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        elif pose_bone.rotation_mode == 'AXIS_ANGLE':
            pose_bone.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        else:
            pose_bone.rotation_euler = (0.0, 0.0, 0.0)
        changed += 1
    return changed


def _apply_mesh_object_transforms(context, collection):
    meshes = _objects_with_unapplied_transforms(collection, {"MESH"})
    if not meshes:
        return 0
    _ensure_object_mode(context)
    previous_selection = list(context.selected_objects)
    previous_active = context.view_layer.objects.active
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes:
        obj.select_set(True)
    context.view_layer.objects.active = meshes[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True, properties=True)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in previous_selection:
        if obj.name in bpy.data.objects:
            obj.select_set(True)
    if previous_active and previous_active.name in bpy.data.objects:
        context.view_layer.objects.active = previous_active
    return len(meshes)


def _apply_armature_object_transforms(context, collection):
    if not _objects_with_unapplied_transforms(collection, {"ARMATURE"}):
        return 0
    _ensure_object_mode(context)
    changed = 0

    # Standard DSTS import parents the armature ("root") to a +90Â° "chr###"
    # orientation empty and gives it a -90Â° LOCAL rotation, so its WORLD transform
    # is identity (upright). transform_apply() here would BAKE that -90Â° into every
    # bone's rest pose, flipping the exported skeleton from Z-up to Y-up and
    # deforming the model in-game (while still looking correct in Blender). The
    # correct move is to CLEAR THE PARENT while keeping the world transform: the
    # object's local transform becomes identity (satisfying the export check) and
    # the bone data is left untouched, so the skeleton keeps its correct
    # orientation. This mirrors the known-good reference rig (no parent, basis and
    # world identity, Z-up bones).
    for arm in list(_objects_with_unapplied_transforms(collection, {"ARMATURE"})):
        if arm.parent is not None and _matrix_is_identity(arm.matrix_world):
            world = arm.matrix_world.copy()
            arm.parent = None
            arm.matrix_world = world
            arm.rotation_mode = 'XYZ'
            arm.location = (0.0, 0.0, 0.0)
            arm.rotation_euler = (0.0, 0.0, 0.0)
            arm.scale = (1.0, 1.0, 1.0)
            changed += 1

    # Bake only armatures that STILL carry a genuine (non-parented) non-identity
    # world transform â€” a real user rotation/scale that belongs in the bone data.
    to_bake = [a for a in _objects_with_unapplied_transforms(collection, {"ARMATURE"})
               if not _matrix_is_identity(a.matrix_world)]
    if to_bake:
        previous_selection = list(context.selected_objects)
        previous_active = context.view_layer.objects.active
        bpy.ops.object.select_all(action='DESELECT')
        for obj in to_bake:
            obj.select_set(True)
        context.view_layer.objects.active = to_bake[0]
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True, properties=True)
        bpy.ops.object.select_all(action='DESELECT')
        for obj in previous_selection:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if previous_active and previous_active.name in bpy.data.objects:
            context.view_layer.objects.active = previous_active
        changed += len(to_bake)

    return changed


def _readiness_report(context, collection):
    lines = []
    warnings = []
    if not collection:
        return ["No Geom collection found."], ["Import a .geom first."]

    lines.append(f"Collection: {collection.name}")
    missing_metadata = [key for key in _GEOM_METADATA_KEYS if key not in collection]
    if missing_metadata:
        warnings.append(f"Missing DSTS metadata: {', '.join(missing_metadata)}")

    meshes = [obj for obj in collection.objects if obj.type == "MESH"]
    armature = _find_workflow_armature(context, collection)
    lines.append(f"Meshes: {len(meshes)}")
    lines.append(f"Armature: {armature.name if armature else '(none)'}")

    for obj in _objects_with_unapplied_transforms(collection, {"MESH", "ARMATURE"}):
        warnings.append(f"{obj.name}: object transform is not applied")

    if armature:
        posed = _posed_bones(armature)
        if posed:
            warnings.append(f"{armature.name}: {len(posed)} pose bone(s) are not rest-pose")
        action_warnings = [
            action.name for action in bpy.data.actions
            if _action_has_pose_curves(action)
            and _action_has_object_transform_curves(action, include_rotation=True, include_scale=True)
        ]
        if action_warnings:
            preview = ", ".join(action_warnings[:4])
            if len(action_warnings) > 4:
                preview += f", ... +{len(action_warnings) - 4}"
            warnings.append(f"{len(action_warnings)} pose action(s) have armature object rotation/scale curves: {preview}")
    else:
        warnings.append("No export armature found")

    for mesh_obj in meshes:
        if not mesh_obj.data.materials or not mesh_obj.data.materials[0]:
            warnings.append(f"{mesh_obj.name}: no material assigned")
        if not mesh_obj.data.uv_layers:
            warnings.append(f"{mesh_obj.name}: no UV layers")
        elif mesh_obj.data.uv_layers[0].name != "uv1":
            warnings.append(f"{mesh_obj.name}: first UV is '{mesh_obj.data.uv_layers[0].name}', expected 'uv1'")
        if not mesh_obj.vertex_groups:
            warnings.append(f"{mesh_obj.name}: no vertex groups")

    return lines, warnings


def _write_readiness_text(lines, warnings):
    text = bpy.data.texts.get("DSTS Export Readiness") or bpy.data.texts.new("DSTS Export Readiness")
    text.clear()
    text.write("DSTS Export Readiness\n\n")
    for line in lines:
        text.write(line + "\n")
    text.write("\nWarnings\n")
    if warnings:
        for warning in warnings:
            text.write(f"- {warning}\n")
    else:
        text.write("- None\n")
    return text


def _rename_primary_uvs_to_uv1(collection):
    renamed = 0
    skipped = 0
    if not collection:
        return renamed, skipped

    for obj in collection.objects:
        if obj.type != "MESH":
            continue
        uv_layers = obj.data.uv_layers
        if not uv_layers:
            skipped += 1
            continue
        if uv_layers[0].name != "uv1":
            uv_layers[0].name = "uv1"
            renamed += 1
    return renamed, skipped


class DSTS_OT_PrepareSelectedAsGeom(Operator):
    """Link selected mesh/armature objects into the active Geom collection"""
    bl_idname = "object.dsts_prepare_selected_as_geom"
    bl_label = "Prepare Selected As Geom"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        collection = _active_geom_collection(context)
        if collection is None:
            collection = bpy.data.collections.new("Geom")
            context.scene.collection.children.link(collection)

        total, linked = _link_selected_to_collection(context, collection)
        if total == 0:
            self.report({'ERROR'}, "Select at least one mesh or armature")
            return {'CANCELLED'}

        target_stem = _target_chr_from_context(context, collection, "")
        if target_stem:
            collection["DSTS_target_chr"] = target_stem
            try:
                context.scene.dsts_workflow_target_chr = target_stem
            except Exception:
                pass

        if not all(key in collection for key in _GEOM_METADATA_KEYS):
            self.report({'WARNING'}, f"Linked {linked} object(s), but '{collection.name}' has no imported .geom metadata")
        else:
            self.report({'INFO'}, f"Linked {linked} object(s) into '{collection.name}'")
        return {'FINISHED'}


class DSTS_OT_ApplyPoseAsRest(Operator):
    """Apply the current export-armature pose as the new rest pose"""
    bl_idname = "object.dsts_apply_pose_as_rest"
    bl_label = "Apply Visible Pose As Rest"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        collection = _active_geom_collection(context)
        armature = _find_workflow_armature(context, collection)
        if armature is None:
            self.report({'ERROR'}, "No armature found")
            return {'CANCELLED'}

        try:
            posed_count = _apply_pose_as_rest(context, armature)
        except Exception as exc:
            print(f"[DSTS] Apply pose as rest failed: {exc}")
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        if posed_count == 0:
            self.report({'INFO'}, f"{armature.name} already has a clear rest pose")
        else:
            self.report({'INFO'}, f"Applied {posed_count} posed bone(s) as rest pose on {armature.name}")
        return {'FINISHED'}


class DSTS_OT_ClearExportPose(Operator):
    """Clear pose transforms from the export armature"""
    bl_idname = "object.dsts_clear_export_pose"
    bl_label = "Clear Export Pose"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        collection = _active_geom_collection(context)
        armature = _find_workflow_armature(context, collection)
        if armature is None:
            self.report({'ERROR'}, "No armature found")
            return {'CANCELLED'}

        changed = _clear_armature_pose(armature)
        if changed == 0:
            self.report({'INFO'}, f"{armature.name} pose is already clear")
        else:
            self.report({'INFO'}, f"Cleared {changed} posed bone(s) on {armature.name}")
        return {'FINISHED'}


class DSTS_OT_ApplyMeshObjectTransforms(Operator):
    """Apply location, rotation, and scale on mesh objects in the Geom collection"""
    bl_idname = "object.dsts_apply_mesh_object_transforms"
    bl_label = "Apply Mesh Object Transforms"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        collection = _active_geom_collection(context)
        if collection is None:
            self.report({'ERROR'}, "No Geom collection found")
            return {'CANCELLED'}

        try:
            changed = _apply_mesh_object_transforms(context, collection)
        except Exception as exc:
            print(f"[DSTS] Apply mesh transforms failed: {exc}")
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        if changed == 0:
            self.report({'INFO'}, "Mesh object transforms are already applied")
        else:
            self.report({'INFO'}, f"Applied object transforms on {changed} mesh object(s)")
        return {'FINISHED'}


class DSTS_OT_ApplyArmatureObjectTransforms(Operator):
    """Apply object location, rotation, and scale on armatures in the Geom collection"""
    bl_idname = "object.dsts_apply_armature_object_transforms"
    bl_label = "Apply Armature Object Transform"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        collection = _active_geom_collection(context)
        if collection is None:
            self.report({'ERROR'}, "No Geom collection found")
            return {'CANCELLED'}

        try:
            changed = _apply_armature_object_transforms(context, collection)
        except Exception as exc:
            print(f"[DSTS] Apply armature transforms failed: {exc}")
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        if changed == 0:
            self.report({'INFO'}, "Armature object transforms are already applied")
        else:
            self.report({'INFO'}, f"Applied object transforms on {changed} armature object(s)")
        return {'FINISHED'}


class DSTS_OT_RenamePrimaryUvsToUv1(Operator):
    """Rename primary UV maps on Geom meshes to uv1"""
    bl_idname = "object.dsts_rename_primary_uvs_to_uv1"
    bl_label = "Rename Primary UVs To uv1"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        collection = _active_geom_collection(context)
        if collection is None:
            self.report({'ERROR'}, "No Geom collection found")
            return {'CANCELLED'}

        renamed, skipped = _rename_primary_uvs_to_uv1(collection)
        if renamed == 0:
            self.report({'INFO'}, f"Primary UV maps already named uv1; skipped {skipped} mesh(es) without UVs")
        else:
            self.report({'INFO'}, f"Renamed {renamed} primary UV map(s) to uv1; skipped {skipped} mesh(es) without UVs")
        return {'FINISHED'}
