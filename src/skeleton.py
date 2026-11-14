import bpy
import mathutils
from mathutils import Matrix, Vector
import sys
import statistics
import math
import json
import base64
from . import dsts_formats

def import_skeleton(skeleton, target_collection=None, coordinate_remap=None):
    """
    Imports a Skeleton object into Blender as an armature.
    Bone transforms are parent-relative (bind pose).

    Leaf bones (no children) will get a tail offset equal to the median bone length
    to keep proportions consistent.
    """

    # Create Armature Data and Object
    armature_data = bpy.data.armatures.new("Skeleton")
    armature_obj = bpy.data.objects.new("Geom", armature_data)
    if hasattr(skeleton, 'to_bytes'):
        # This compact native skeleton also retains flagged/duplicate parent
        # records which are not expressible through Blender's single parent.
        armature_obj['DSTS_source_skeleton'] = base64.b64encode(skeleton.to_bytes()).decode('ascii')
    
    # Link to the target collection, fallback to current collection
    if target_collection is not None:
        target_collection.objects.link(armature_obj)
    else:
        bpy.context.collection.objects.link(armature_obj)

    # Set the new armature as active and switch to Edit Mode
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='EDIT')

    bone_map = {}

    # ---------------------------------------------------------
    # Coordinate remapping helper
    # ---------------------------------------------------------
    def apply_remap_matrix(matrix):
        """Applies a coordinate remap matrix if provided."""
        if coordinate_remap is None:
            return matrix
        if callable(coordinate_remap):
            return coordinate_remap(matrix)
        elif isinstance(coordinate_remap, mathutils.Matrix):
            return coordinate_remap @ matrix
        return matrix

    # ---------------------------------------------------------
    # Build global (bind) transforms
    # ---------------------------------------------------------
    def local_matrix(bone):
        """Calculates the bone's local transformation matrix."""
        q = bone.transform.quaternion
        p = bone.transform.position
        s = bone.transform.scale

        # Note: The quaternion is assumed to be (x,y,z,w) in the source data, 
        # converted to Blender's (w,x,y,z) order here.
        pos = mathutils.Vector(p[:3])
        scl = mathutils.Vector(s[:3])
        quat = mathutils.Quaternion((q[3], q[0], q[1], q[2])) 

        mat_loc = mathutils.Matrix.Translation(pos)
        mat_rot = quat.to_matrix().to_4x4()
        mat_scl = mathutils.Matrix.Diagonal((*scl, 1.0))
        # Local matrix composition: Translation * Rotation * Scale (T * R * S)
        return mat_loc @ mat_rot @ mat_scl

    global_mats = {}

    def compute_global(bone):
        """Recursively computes the global bind pose matrix for a bone."""
        if bone.name in global_mats:
            return global_mats[bone.name]
        if bone.parent:
            parent_mat = compute_global(bone.parent)
            global_mats[bone.name] = parent_mat @ local_matrix(bone)
        else:
            global_mats[bone.name] = local_matrix(bone)
        return global_mats[bone.name]

    # Compute global matrices for all bones
    for bone in skeleton.bones:
        compute_global(bone)

    # ---------------------------------------------------------
    # Create edit bones
    # ---------------------------------------------------------
    original_to_new_name = {}

    for bone in skeleton.bones:
        original_name = bone.name
        edit_bone = armature_data.edit_bones.new(original_name)
        bone_map[edit_bone.name] = edit_bone
        original_to_new_name[original_name] = edit_bone.name
        if original_name != edit_bone.name:
            bone_map[original_name] = edit_bone
        # Ensure the name is updated in the source object for later lookups
        bone.name = edit_bone.name 

    # ---------------------------------------------------------
    # Compute head positions and children map
    # ---------------------------------------------------------
    new_to_original = {new: original for original, new in original_to_new_name.items()}

    head_positions = {}
    children_map = {}
    for bone in skeleton.bones:
        original_name = new_to_original.get(bone.name, bone.name)
        # Apply remap to the global matrix before extracting the position
        gmat = apply_remap_matrix(global_mats[original_name])
        head_positions[bone.name] = gmat.to_translation()
        if bone.parent:
            children_map.setdefault(bone.parent.name, []).append(bone.name)

    # Compute lengths of bones with children (to find median length)
    bone_lengths = []
    for bone_name, edit_bone in bone_map.items():
        child_names = children_map.get(bone_name, [])
        if child_names:
            # Average vector to children
            avg = mathutils.Vector((0, 0, 0))
            for cname in child_names:
                avg += head_positions[cname]
            avg /= len(child_names)
            length = (avg - head_positions[bone_name]).length
            if length > 0:
                bone_lengths.append(length)

    # Use median length for leaf bones (no children)
    median_length = statistics.median(bone_lengths) if bone_lengths else 0.1
    median_length = median_length if median_length > 0.1 else 0.1

    # ---------------------------------------------------------
    # Assign heads, tails, parents, and roll
    # ---------------------------------------------------------
    for bone in skeleton.bones:
        if bone.name not in bone_map:
            print(f"[DSTS] Warning: Bone '{bone.name}' not found in bone_map, skipping")
            continue

        edit_bone = bone_map[bone.name]

        if bone.parent:
            if bone.parent.name in bone_map:
                edit_bone.parent = bone_map[bone.parent.name]
            else:
                print(f"[DSTS] Warning: Parent bone '{bone.parent.name}' not found for '{bone.name}'")

        # 2. Assign Head
        edit_bone.head = mathutils.Vector((0, 0, 0))
        edit_bone.tail = mathutils.Vector((0, 0, median_length))

        original_name = new_to_original.get(bone.name, bone.name)
        gmat = apply_remap_matrix(global_mats[original_name])
        edit_bone.matrix = gmat

    # Exit Edit Mode
    bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.object.mode_set(mode='POSE')
    for pose_bone in armature_obj.pose.bones:
        pose_bone.bone.use_local_location = False
    bpy.ops.object.mode_set(mode='OBJECT')

    
    # Add custom geometry flag to Bone data (in Pose Mode/Object Mode)
    source_indices = {bone.name: index for index, bone in enumerate(skeleton.bones)}
    for bone in skeleton.bones:
        if bone.name not in armature_data.bones:
            print(f"[DSTS] Warning: Bone '{bone.name}' not found in armature, skipping")
            continue

        blender_bone = armature_data.bones[bone.name]
        blender_bone["DSTS_geometry"] = bone.is_geometry
        blender_bone['DSTS_source_bone_index'] = source_indices[bone.name]
        blender_bone['DSTS_source_rest'] = json.dumps({
            'matrix': [value for row in blender_bone.matrix_local for value in row],
            'parent': source_indices[bone.parent.name] if bone.parent else -1,
            'position': [float(v) for v in bone.transform.position],
            'quaternion': [float(v) for v in bone.transform.quaternion],
            'scale': [float(v) for v in bone.transform.scale],
        })

        rna_prop = blender_bone.id_properties_ui("DSTS_geometry")
        rna_prop.update(
            description="Marks this bone as geometry",
            default=bone.is_geometry
        )

    if hasattr(skeleton, 'float_channels'):
        armature_obj['DSTS_float_bindings'] = json.dumps([
            {'name_hash':int(channel.name_hash),'array_index':int(channel.array_index),'flags':int(channel.flags)}
            for channel in skeleton.float_channels])
    inverse_remap = apply_remap_matrix(Matrix.Identity(4)).inverted()
    armature_obj['DSTS_source_export_transform'] = [value for row in inverse_remap for value in row]
    return armature_obj, original_to_new_name

def export_skeleton(skeleton_obj, coord_transform = Matrix.Rotation(math.radians(-90), 4, 'X')):
    skeleton = skeleton_obj.data
    if 'DSTS_source_skeleton' in skeleton_obj:
        skeleton_out = dsts_formats.Skeleton.from_bytes(
            base64.b64decode(skeleton_obj['DSTS_source_skeleton'], validate=True))
        skeleton_out.bones.clear()
        if hasattr(skeleton_out, 'float_channels'):
            skeleton_out.float_channels.clear()
    else:
        skeleton_out = dsts_formats.Skeleton()
    original_transform = skeleton_obj.get('DSTS_source_export_transform', [])
    current_transform = [value for row in coord_transform for value in row]
    matching_transform = len(original_transform) == 16 and all(
        abs(a - b) < 1e-6 for a, b in zip(original_transform, current_transform))
    bindings = json.loads(skeleton_obj.get('DSTS_float_bindings', '[]'))
    if bindings and not hasattr(dsts_formats, 'FloatChannel'):
        raise ValueError('This rig has native float bindings; install the updated geometry backend before exporting')
    for row in bindings:
        channel = dsts_formats.FloatChannel()
        channel.name_hash = row['name_hash']
        channel.array_index = row['array_index']
        channel.flags = row['flags']
        skeleton_out.float_channels.append(channel)

    name_to_bone = {}
    for bone in skeleton.bones:
        bone_out = dsts_formats.Bone()
        bone_out.name = bone.name
        name_to_bone[bone.name] = bone_out

    for bone in skeleton.bones:
        bone_out = name_to_bone[bone.name]

        if bone.parent is None:
            mat = coord_transform @ bone.matrix_local
        else:
            bone_out.parent = name_to_bone[bone.parent.name]
            parent_mat = coord_transform @ bone.parent.matrix_local
            mat = parent_mat.inverted() @ (coord_transform @ bone.matrix_local)

        position, quaternion, scale = mat.decompose()

        bone_out.transform.position = position.to_4d()
        bone_out.transform.quaternion = (quaternion.x,quaternion.y,quaternion.z,quaternion.w)
        bone_out.transform.scale = scale.to_4d()

        # Blender reconstructs rest matrices in float32. Keep the exact native
        # TRS for an untouched bone, so source bind matrices remain reusable.
        if matching_transform and 'DSTS_source_rest' in bone:
            original = json.loads(bone['DSTS_source_rest'])
            current_matrix = [value for row in bone.matrix_local for value in row]
            parent_index = bone.parent.get('DSTS_source_bone_index', -2) if bone.parent else -1
            parent_unchanged = bone.parent is None or (
                'DSTS_source_rest' in bone.parent and
                [value for row in bone.parent.matrix_local for value in row] ==
                json.loads(bone.parent['DSTS_source_rest'])['matrix'])
            if current_matrix == original['matrix'] and parent_index == original['parent'] and parent_unchanged:
                bone_out.transform.position = original['position']
                bone_out.transform.quaternion = original['quaternion']
                bone_out.transform.scale = original['scale']

        bone_out.is_geometry = bone.get("DSTS_geometry", False)

        skeleton_out.bones.append(bone_out)

    return skeleton_out
