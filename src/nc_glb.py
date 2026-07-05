"""Digimon New Century .glb -> Time Stranger import pipeline.

One-click import of an nc-extractor model.glb into an export-ready DSTS scene:

1.  Import the .glb with Blender's glTF importer (scene fps is set to 30 first
    so the NC 30 fps sample keys land on integer frames).
2.  Keep only the skinned ``*_lod0`` body meshes and their armature; everything
    else (other submodels, empties, effect/LOD meshes) is deleted. Actions are
    kept.
3.  Re-add the missing root bone. NC glbs contain the full Bip001 rig but the
    top-level ``Bip001`` node is not in any skin's joint list, so the glTF
    importer drops it as a bone and folds its (animated) transform onto the
    armature OBJECT. We read the glb JSON chunk to find that node, create it as
    a bone, and rehome the object-level animation of every action onto the new
    root bone's pose curves (deltas vs its rest), then strip the object-level
    F-Curves. After this the exported .anim carries NC root motion.
4.  Re-seat everything in file-consistent space: the glTF importer leaves
    skinned vertices and bone rests in bind/armature space with a non-bind
    transform on the armature object. We derive the rigid axis rotation M that
    maps the imported bone rests onto the glb inverse-bind-matrix bind worlds
    (M is rotX(-90) for NC files, but it is fitted and verified numerically),
    bake M into mesh vertices and bone rests, and leave identity object
    transforms so the existing .geom exporter works unmodified.
5.  Borrow materials, per-mesh DSTS flags and Geom-collection metadata from a
    donor .geom (default chr050), rename the primary UV map to ``uv1``.
6.  Optionally build the ``outline`` mesh: a joined copy of the body meshes
    with the donor's MTR_line01 material and an all-white byte color attribute
    (the DSTS outline shader reads that color; zeros render the model as a
    solid black shell).

The math anchor points (validated against the chr110 fan port and the shipped
chr110_bn01.anim):

- TS .anim binds bones by index into the geom bone order and stores deltas vs
  the geom rest: ``local(t) = rest_local @ T(dp) @ R(dq) @ S(ds)``.
- Final bone rest (Blender space, identity object transform) = glb IBM bind
  world. Exporting applies rotX(-90), which lands the skeleton in the same
  Y-up file space as the glb node hierarchy.
- New root bone rest R = the armature object's static world transform with
  animation cleared (the importer's conversion of the root node's TRS).
- Root delta per frame: ``D(f) = R^-1 @ O(f) @ M^-1 @ R`` where O(f) is the
  armature object world transform animated by the action's object F-Curves.
"""

import bpy
import json
import math
import os
import re
import struct

from mathutils import Euler, Matrix, Quaternion, Vector

from .geom import import_geom

# Object-level transform channel paths (as opposed to pose.bones paths).
OBJECT_TRANSFORM_PATHS = {
    "location",
    "rotation_quaternion",
    "rotation_euler",
    "rotation_axis_angle",
    "scale",
    "delta_location",
    "delta_rotation_euler",
    "delta_rotation_quaternion",
    "delta_scale",
}

_IDENTITY = Matrix.Identity(4)
_FLAG_KEYS = tuple(f"DSTS_flag_{i}" for i in range(8))
_GEOM_METADATA_KEYS = ("unknown_0x10", "unknown_0x30", "unknown_0x34", "CLUT")
_OUTLINE_MATERIAL = "MTR_line01"


def _log(msg):
    print(f"[DSTS NC] {msg}")


class NcImportError(RuntimeError):
    pass


def _matrix_is_identity(matrix, epsilon=1e-5):
    for row in range(4):
        for col in range(4):
            if abs(matrix[row][col] - _IDENTITY[row][col]) > epsilon:
                return False
    return True


def _base_name(name):
    return re.sub(r"\.[0-9]{3}$", "", name)


# ---------------------------------------------------------------------------
# glb JSON/BIN chunk reading (structure + inverse bind matrices only)
# ---------------------------------------------------------------------------

def read_glb_chunks(filepath):
    with open(filepath, "rb") as handle:
        header = handle.read(12)
        if len(header) < 12:
            raise NcImportError("File too short to be a .glb")
        magic, _version, total = struct.unpack("<III", header)
        if magic != 0x46546C67:  # 'glTF'
            raise NcImportError("Not a binary glTF (.glb) file")
        gltf = None
        binary = b""
        while handle.tell() < total:
            chunk_header = handle.read(8)
            if len(chunk_header) < 8:
                break
            length, chunk_type = struct.unpack("<II", chunk_header)
            data = handle.read(length)
            if chunk_type == 0x4E4F534A:  # JSON
                gltf = json.loads(data)
            elif chunk_type == 0x004E4942:  # BIN
                binary = data
        if gltf is None:
            raise NcImportError("No JSON chunk found in .glb")
        return gltf, binary


def _glb_node_parents(gltf):
    parents = {}
    for index, node in enumerate(gltf.get("nodes", [])):
        for child in node.get("children", []):
            parents[child] = index
    return parents


def _glb_all_joints(gltf):
    joints = set()
    for skin in gltf.get("skins", []):
        joints.update(skin.get("joints", []))
    return joints


def find_missing_root_node(gltf):
    """Return (node_index, name) of the topmost skeleton node above the joints.

    That node (``Bip001`` for NC models) is not referenced by any skin, so
    Blender's glTF importer does not turn it into a bone. Returns (None, None)
    when there is no unambiguous single non-joint parent of the joint roots.
    """
    nodes = gltf.get("nodes", [])
    parents = _glb_node_parents(gltf)
    joints = _glb_all_joints(gltf)
    if not joints:
        return None, None

    root_parents = set()
    for joint in joints:
        parent = parents.get(joint)
        if parent not in joints:
            root_parents.add(parent)
    root_parents.discard(None)
    if len(root_parents) != 1:
        return None, None
    candidate = root_parents.pop()
    node = nodes[candidate]
    if "mesh" in node or "skin" in node:
        return None, None
    return candidate, node.get("name")


def _node_local_matrix(node):
    if "matrix" in node:
        c = node["matrix"]  # column-major
        return Matrix((
            (c[0], c[4], c[8], c[12]),
            (c[1], c[5], c[9], c[13]),
            (c[2], c[6], c[10], c[14]),
            (c[3], c[7], c[11], c[15]),
        ))
    translation = node.get("translation", (0.0, 0.0, 0.0))
    rotation = node.get("rotation", (0.0, 0.0, 0.0, 1.0))  # glTF xyzw
    scale = node.get("scale", (1.0, 1.0, 1.0))
    quat = Quaternion((rotation[3], rotation[0], rotation[1], rotation[2]))
    return (
        Matrix.Translation(Vector(translation))
        @ quat.to_matrix().to_4x4()
        @ Matrix.Diagonal((*scale, 1.0))
    )


def _glb_node_worlds(gltf, indices):
    """{node index: static world matrix} composed from the glb node hierarchy."""
    nodes = gltf.get("nodes", [])
    parents = _glb_node_parents(gltf)
    cache = {}

    def world(i):
        if i in cache:
            return cache[i]
        matrix = _node_local_matrix(nodes[i])
        parent = parents.get(i)
        cache[i] = matrix if parent is None else world(parent) @ matrix
        return cache[i]

    return {i: world(i) for i in indices}


def _read_ibm_bind_worlds(gltf, binary):
    """{joint node name: 4x4 bind world Matrix} from skin inverseBindMatrices."""
    nodes = gltf.get("nodes", [])
    accessors = gltf.get("accessors", [])
    views = gltf.get("bufferViews", [])
    bind_worlds = {}
    for skin in gltf.get("skins", []):
        joints = skin.get("joints", [])
        accessor_index = skin.get("inverseBindMatrices")
        if accessor_index is None:
            for joint in joints:
                bind_worlds[nodes[joint].get("name")] = Matrix.Identity(4)
            continue
        accessor = accessors[accessor_index]
        if accessor.get("componentType") != 5126 or accessor.get("type") != "MAT4":
            raise NcImportError("Unsupported inverseBindMatrices accessor format")
        view = views[accessor["bufferView"]]
        offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        count = accessor["count"]
        floats = struct.unpack_from(f"<{count * 16}f", binary, offset)
        for j, joint in enumerate(joints):
            columns = floats[j * 16:(j + 1) * 16]
            # glTF matrices are column-major.
            ibm = Matrix((
                (columns[0], columns[4], columns[8], columns[12]),
                (columns[1], columns[5], columns[9], columns[13]),
                (columns[2], columns[6], columns[10], columns[14]),
                (columns[3], columns[7], columns[11], columns[15]),
            ))
            bind_worlds[nodes[joint].get("name")] = ibm.inverted()
    return bind_worlds


# ---------------------------------------------------------------------------
# Rigid alignment fitting
# ---------------------------------------------------------------------------

def _axis_rotation_candidates():
    axes = (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
    candidates = []
    for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        for signs in ((sx, sy, sz) for sx in (1, -1) for sy in (1, -1) for sz in (1, -1)):
            matrix = Matrix.Identity(4)
            for col in range(3):
                vec = axes[perm[col]] * signs[col]
                matrix[0][col], matrix[1][col], matrix[2][col] = vec.x, vec.y, vec.z
            if matrix.to_3x3().determinant() > 0.5:  # proper rotations only
                candidates.append(matrix)
    return candidates


def fit_alignment_rotation(source_points, target_points):
    """Best axis-aligned proper rotation M with M @ source ~= target.

    Returns (matrix, max_error). Both dicts are keyed by bone name.
    """
    shared = sorted(set(source_points) & set(target_points))
    if not shared:
        return None, float("inf")
    best_matrix, best_error = None, float("inf")
    for candidate in _axis_rotation_candidates():
        error = max(
            ((candidate @ source_points[name].to_4d()).to_3d() - target_points[name]).length
            for name in shared
        )
        if error < best_error:
            best_matrix, best_error = candidate, error
    return best_matrix, best_error


# ---------------------------------------------------------------------------
# Action helpers (Blender 4.x legacy F-Curves and 5.x slotted actions)
# ---------------------------------------------------------------------------

def _action_channelbags(action):
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            yield from getattr(strip, "channelbags", ())


def _pose_slot_and_fcurves(action):
    """Return (slot, fcurve_list) of the channelbag holding pose-bone curves.

    For legacy actions the slot is None and the flat fcurve list is returned.
    """
    if getattr(action, "layers", None):
        for bag in _action_channelbags(action):
            fcurves = list(getattr(bag, "fcurves", ()))
            if any(fc.data_path.startswith("pose.bones") for fc in fcurves):
                slot = None
                bag_handle = getattr(bag, "slot_handle", None)
                if bag_handle is not None:
                    slot = next(
                        (s for s in action.slots if getattr(s, "handle", None) == bag_handle),
                        None,
                    )
                return slot, fcurves
        return None, []
    fcurves = list(getattr(action, "fcurves", ()))
    if any(fc.data_path.startswith("pose.bones") for fc in fcurves):
        return None, fcurves
    return None, []


def remove_object_transform_fcurves(action):
    """Delete every object-level transform F-Curve from the action (all slots)."""
    removed = 0
    if getattr(action, "layers", None):
        for bag in _action_channelbags(action):
            doomed = [fc for fc in bag.fcurves if fc.data_path in OBJECT_TRANSFORM_PATHS]
            for fc in doomed:
                bag.fcurves.remove(fc)
                removed += 1
        return removed
    doomed = [fc for fc in action.fcurves if fc.data_path in OBJECT_TRANSFORM_PATHS]
    for fc in doomed:
        action.fcurves.remove(fc)
        removed += 1
    return removed


def _channel_defaults(obj):
    return {
        ("location", 0): obj.location[0],
        ("location", 1): obj.location[1],
        ("location", 2): obj.location[2],
        ("rotation_quaternion", 0): obj.rotation_quaternion[0],
        ("rotation_quaternion", 1): obj.rotation_quaternion[1],
        ("rotation_quaternion", 2): obj.rotation_quaternion[2],
        ("rotation_quaternion", 3): obj.rotation_quaternion[3],
        ("rotation_euler", 0): obj.rotation_euler[0],
        ("rotation_euler", 1): obj.rotation_euler[1],
        ("rotation_euler", 2): obj.rotation_euler[2],
        ("scale", 0): obj.scale[0],
        ("scale", 1): obj.scale[1],
        ("scale", 2): obj.scale[2],
    }


def object_basis_matrix_from_fcurves(obj_defaults, rotation_mode, fcurve_map, frame):
    """Compose the object's local basis matrix at ``frame`` from its F-Curves.

    ``fcurve_map`` maps (data_path, array_index) -> fcurve; channels without a
    curve fall back to the authored values captured in ``obj_defaults``.
    """
    def value(path, index):
        fc = fcurve_map.get((path, index))
        if fc is not None:
            return fc.evaluate(frame)
        return obj_defaults.get((path, index), 1.0 if path == "scale" else 0.0)

    loc = Vector((value("location", 0), value("location", 1), value("location", 2)))
    scale = Vector((value("scale", 0), value("scale", 1), value("scale", 2)))
    if rotation_mode == "QUATERNION":
        quat = Quaternion((
            value("rotation_quaternion", 0),
            value("rotation_quaternion", 1),
            value("rotation_quaternion", 2),
            value("rotation_quaternion", 3),
        ))
        if quat.magnitude > 1e-8:
            quat.normalize()
        else:
            quat = Quaternion((1.0, 0.0, 0.0, 0.0))
        rot = quat.to_matrix().to_4x4()
    else:
        rot = Euler((
            value("rotation_euler", 0),
            value("rotation_euler", 1),
            value("rotation_euler", 2),
        ), rotation_mode if rotation_mode in {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"} else "XYZ").to_matrix().to_4x4()
    return Matrix.Translation(loc) @ rot @ Matrix.Diagonal((*scale, 1.0))


def _action_frame_span(action):
    frame_range = action.frame_range
    start = int(math.floor(frame_range[0] + 0.5))
    end = int(math.floor(frame_range[1] + 0.5))
    return start, max(start, end)


def _ensure_root_fcurve(action, armature_obj, data_path, index, group_name):
    if hasattr(action, "fcurve_ensure_for_datablock"):
        return action.fcurve_ensure_for_datablock(
            armature_obj, data_path, index=index, group_name=group_name
        )
    fcurve = action.fcurves.find(data_path, index=index)
    if fcurve is None:
        fcurve = action.fcurves.new(data_path=data_path, index=index)
        if group_name and hasattr(fcurve, "group"):
            group = action.groups.get(group_name) or action.groups.new(group_name)
            fcurve.group = group
    return fcurve


def resample_pose_fcurves(action, frame_start, frame_end):
    """Resample every pose-bone F-Curve onto the integer frame grid.

    NC clips are Unity keyframe-reduced: the glb (and hence the imported
    F-Curves) key at fractional frame times. The TS .anim format stores keys
    on an integer frame grid, so exporting raw keyframes truncates their
    timing (up to a full frame of drift mid-clip). Baking one key per integer
    frame keeps the exported values exactly equal to Blender's evaluated
    animation. Returns the number of resampled F-Curves.
    """
    frames = list(range(int(frame_start), int(frame_end) + 1))
    if not frames:
        return 0
    resampled = 0

    def _resample(fcurve):
        nonlocal resampled
        if not str(fcurve.data_path).startswith("pose.bones"):
            return
        values = [fcurve.evaluate(float(f)) for f in frames]
        _clear_fcurve_keys(fcurve)
        keyframes = fcurve.keyframe_points
        keyframes.add(len(frames))
        flat = [0.0] * (len(frames) * 2)
        for i, f in enumerate(frames):
            flat[i * 2] = float(f)
            flat[i * 2 + 1] = values[i]
        keyframes.foreach_set("co", flat)
        try:
            keyframes.foreach_set("interpolation", [1] * len(frames))  # LINEAR
        except Exception:
            for keyframe in keyframes:
                keyframe.interpolation = 'LINEAR'
        fcurve.update()
        resampled += 1

    if getattr(action, "layers", None):
        for bag in _action_channelbags(action):
            for fcurve in list(bag.fcurves):
                _resample(fcurve)
    else:
        for fcurve in list(action.fcurves):
            _resample(fcurve)
    return resampled


def _clear_fcurve_keys(fcurve):
    keyframes = fcurve.keyframe_points
    try:
        keyframes.clear()
    except AttributeError:
        for keyframe in reversed(list(keyframes)):
            keyframes.remove(keyframe)


def write_pose_matrix_keys(action, armature_obj, pose_slot, tracks):
    """(Re)key pose bones from per-frame basis matrices.

    ``tracks`` maps bone name -> {frame: basis Matrix}. Any existing keys on
    the affected channels are replaced.
    """
    anim_data = armature_obj.animation_data or armature_obj.animation_data_create()
    previous_action = anim_data.action
    anim_data.action = action
    if pose_slot is not None and hasattr(anim_data, "action_slot"):
        try:
            anim_data.action_slot = pose_slot
        except Exception as exc:  # pragma: no cover - defensive
            _log(f"could not assign action slot for {action.name}: {exc}")

    for bone_name, deltas in tracks.items():
        base_path = f'pose.bones["{bone_name}"]'
        loc_fcs = [_ensure_root_fcurve(action, armature_obj, f"{base_path}.location", i, bone_name) for i in range(3)]
        rot_fcs = [_ensure_root_fcurve(action, armature_obj, f"{base_path}.rotation_quaternion", i, bone_name) for i in range(4)]
        scl_fcs = [_ensure_root_fcurve(action, armature_obj, f"{base_path}.scale", i, bone_name) for i in range(3)]
        for fc in (*loc_fcs, *rot_fcs, *scl_fcs):
            _clear_fcurve_keys(fc)

        previous_quat = None
        for frame in sorted(deltas):
            loc, quat, scale = deltas[frame].decompose()
            if previous_quat is not None and previous_quat.dot(quat) < 0.0:
                quat = -quat
            previous_quat = quat
            for i in range(3):
                key = loc_fcs[i].keyframe_points.insert(float(frame), loc[i], options={'FAST'})
                key.interpolation = 'LINEAR'
            for i in range(4):
                key = rot_fcs[i].keyframe_points.insert(float(frame), quat[i], options={'FAST'})
                key.interpolation = 'LINEAR'
            for i in range(3):
                key = scl_fcs[i].keyframe_points.insert(float(frame), scale[i], options={'FAST'})
                key.interpolation = 'LINEAR'
        for fc in (*loc_fcs, *rot_fcs, *scl_fcs):
            fc.update()

    anim_data.action = previous_action


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _purge_orphans(skip_actions=True):
    collections = [bpy.data.meshes, bpy.data.materials, bpy.data.images,
                   bpy.data.armatures, bpy.data.node_groups]
    if not skip_actions:
        collections.append(bpy.data.actions)
    for _ in range(4):
        removed = 0
        for collection in collections:
            for block in list(collection):
                if block.users == 0 and not block.use_fake_user:
                    collection.remove(block)
                    removed += 1
        if removed == 0:
            break


def _select_only(context, objs, active=None):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objs:
        obj.select_set(True)
    context.view_layer.objects.active = active or (objs[0] if objs else None)


def _find_target_armature(new_objects):
    armatures = [o for o in new_objects if o.type == 'ARMATURE']
    best = None
    best_meshes = []
    for armature in armatures:
        lod0 = []
        for obj in new_objects:
            if obj.type != 'MESH':
                continue
            if not _base_name(obj.name).lower().endswith("_lod0"):
                continue
            if any(m.type == 'ARMATURE' and m.object == armature for m in obj.modifiers):
                lod0.append(obj)
        if lod0 and (best is None
                     or (len(lod0), len(armature.data.bones)) > (len(best_meshes), len(best.data.bones))):
            best, best_meshes = armature, lod0
    if best is None:
        raise NcImportError(
            "No armature with skinned *_lod0 meshes found in the glb "
            "(is this an nc-extractor model.glb?)"
        )
    best_meshes.sort(key=lambda o: _base_name(o.name))
    return best, best_meshes


def _import_donor(context, donor_path):
    """Import the donor .geom, harvest materials/flags/metadata, delete its objects.

    Returns (collection, body_infos, outline_info) where each info is a dict
    with 'material' and 'flags'.
    """
    pre_actions = set(bpy.data.actions)
    donor_collection = import_geom(context, donor_path, None)
    donor_meshes = [o for o in donor_collection.objects if o.type == 'MESH']
    if not donor_meshes:
        raise NcImportError(f"Donor geom has no meshes: {donor_path}")

    def mesh_info(obj):
        material = obj.data.materials[0] if obj.data.materials else None
        flags = {key: bool(obj.data.get(key, key == "DSTS_flag_0")) for key in _FLAG_KEYS}
        return {"material": material, "flags": flags, "name": obj.name}

    body_infos = []
    outline_info = None
    for obj in donor_meshes:
        info = mesh_info(obj)
        material_name = info["material"].name if info["material"] else ""
        if material_name.startswith(_OUTLINE_MATERIAL) or _base_name(obj.name) == "outline":
            if outline_info is None:
                outline_info = info
        else:
            body_infos.append(info)

    if not body_infos:
        raise NcImportError(f"Donor geom has no body materials: {donor_path}")

    # Materials must survive donor mesh deletion until they are assigned.
    for info in body_infos + ([outline_info] if outline_info else []):
        if info["material"]:
            info["material"].use_fake_user = True

    # Delete donor objects (meshes + armature); keep the collection + metadata.
    for obj in list(donor_collection.objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Armature):
            bpy.data.armatures.remove(data)

    # Donor import auto-loads the donor's .anim clips; those actions target the
    # (now deleted) donor armature and must not leak into the NC scene.
    for action in [a for a in bpy.data.actions if a not in pre_actions]:
        bpy.data.actions.remove(action)

    return donor_collection, body_infos, outline_info


def _build_new_armature(context, old_armature, root_name, root_rest, alignment):
    """Create the export armature: root bone first, then the NC bones re-seated.

    Bone order is root + the old armature's bone order (stable topological sort
    so parents always precede children); rests are ``alignment @ old rest`` and
    the root gets ``root_rest``. Returns the new armature object.
    """
    old_bones = list(old_armature.data.bones)
    by_name = {b.name: b for b in old_bones}

    ordered = []
    seen = set()

    def visit(bone):
        if bone.name in seen:
            return
        if bone.parent is not None:
            visit(bone.parent)
        seen.add(bone.name)
        ordered.append(bone)

    for bone in old_bones:
        visit(bone)

    lengths = [b.length for b in old_bones if b.length > 1e-6]
    median_length = sorted(lengths)[len(lengths) // 2] if lengths else 0.1

    armature_data = bpy.data.armatures.new("Skeleton")
    armature_obj = bpy.data.objects.new("Geom", armature_data)
    context.scene.collection.objects.link(armature_obj)
    _select_only(context, [armature_obj], armature_obj)
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature_data.edit_bones
    root_edit = None
    if root_name:
        root_edit = edit_bones.new(root_name)
        root_edit.head = (0.0, 0.0, 0.0)
        root_edit.tail = (0.0, 0.0, median_length)
        root_edit.matrix = root_rest.copy()

    created = {}
    for bone in ordered:
        edit_bone = edit_bones.new(bone.name)
        edit_bone.head = (0.0, 0.0, 0.0)
        edit_bone.tail = (0.0, 0.0, max(bone.length, 1e-3))
        edit_bone.matrix = alignment @ bone.matrix_local
        if bone.parent is not None:
            edit_bone.parent = created[bone.parent.name]
        elif root_edit is not None:
            edit_bone.parent = root_edit
        created[bone.name] = edit_bone

    bpy.ops.object.mode_set(mode='OBJECT')

    # Preserve the pose-channel semantics the glTF F-Curves were authored for.
    for bone in ordered:
        new_bone = armature_data.bones.get(bone.name)
        old_bone = by_name[bone.name]
        if new_bone is None:
            continue
        new_bone.use_local_location = old_bone.use_local_location
        new_bone.use_inherit_rotation = old_bone.use_inherit_rotation
        new_bone.inherit_scale = old_bone.inherit_scale
        new_bone.use_deform = old_bone.use_deform

    return armature_obj


def _bake_mesh_to_world(context, obj, alignment):
    """Bake ``alignment`` into the mesh vertices, leaving an identity transform."""
    for modifier in list(obj.modifiers):
        obj.modifiers.remove(modifier)
    if obj.parent is not None:
        obj.parent = None
    obj.matrix_world = alignment.copy()
    if obj.data.shape_keys:
        _log(f"{obj.name}: has shape keys; using Mesh.transform fallback")
        obj.data.transform(alignment, shape_keys=True)
        obj.matrix_world = Matrix.Identity(4)
    else:
        _select_only(context, [obj], obj)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if not _matrix_is_identity(obj.matrix_world):
        raise NcImportError(f"{obj.name}: object transform not identity after bake")


def _ensure_white_color_attribute(mesh_data, name="Color"):
    """Create (or overwrite) a per-corner byte color attribute, all white.

    The .geom exporter reads ``mesh.color_attributes['Color']`` per loop; the
    DSTS outline shader uses this color, and zeros make the outline shell
    swallow the model (the in-game black-model bug).
    """
    existing = mesh_data.color_attributes.get(name)
    if existing is not None:
        mesh_data.color_attributes.remove(existing)
    layer = mesh_data.color_attributes.new(name=name, type='BYTE_COLOR', domain='CORNER')
    layer.data.foreach_set("color", [1.0] * (len(mesh_data.loops) * 4))
    return layer


def _build_outline_mesh(context, body_objects, armature_obj, outline_info, collection):
    duplicates = []
    for source in body_objects:
        duplicate = source.copy()
        duplicate.data = source.data.copy()
        collection.objects.link(duplicate)
        duplicates.append(duplicate)
    _select_only(context, duplicates, duplicates[0])
    if len(duplicates) > 1:
        bpy.ops.object.join()
    outline = duplicates[0]
    outline.name = "outline"
    outline.data.name = "outline"

    outline.data.materials.clear()
    outline.data.materials.append(outline_info["material"])
    for key, value in outline_info["flags"].items():
        outline.data[key] = value

    outline.parent = armature_obj
    outline.matrix_parent_inverse = Matrix.Identity(4)
    for modifier in list(outline.modifiers):
        outline.modifiers.remove(modifier)
    modifier = outline.modifiers.new(name="Armature", type='ARMATURE')
    modifier.object = armature_obj

    _ensure_white_color_attribute(outline.data)
    return outline


def import_nc_model(context, filepath, donor_geom_path, build_outline=True):
    """Import an NC model.glb into an export-ready Geom collection.

    Returns (collection, armature_object, notes) where notes is a list of
    warning strings for the operator to report.
    """
    notes = []
    if not os.path.isfile(filepath):
        raise NcImportError(f"File not found: {filepath}")
    if not donor_geom_path or not os.path.isfile(donor_geom_path):
        raise NcImportError(f"Donor .geom not found: {donor_geom_path}")

    if context.object and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    context.view_layer.active_layer_collection = context.view_layer.layer_collection

    # NC clips are sampled at 30 fps; align scene fps so glb keys land on
    # integer frames (the .anim exporter truncates key frames to ints).
    if context.scene.render.fps != 30:
        context.scene.render.fps = 30
        context.scene.render.fps_base = 1.0
        notes.append("Scene FPS set to 30 to match NC animation sampling")

    gltf, binary = read_glb_chunks(filepath)
    missing_root_index, missing_root = find_missing_root_node(gltf)
    bind_worlds = _read_ibm_bind_worlds(gltf, binary)
    if not bind_worlds:
        raise NcImportError("glb has no skinned joints (no inverse bind matrices)")

    _log(f"=== Import glb: {filepath}")
    pre_objects = set(bpy.data.objects)
    pre_actions = set(bpy.data.actions)
    result = bpy.ops.import_scene.gltf(filepath=filepath)
    if result != {'FINISHED'}:
        raise NcImportError(f"glTF import returned {result}")
    new_objects = [o for o in bpy.data.objects if o not in pre_objects]
    imported_actions = [a for a in bpy.data.actions if a not in pre_actions]

    old_armature, body_objects = _find_target_armature(new_objects)
    bone_names = [b.name for b in old_armature.data.bones]
    _log(f"armature '{old_armature.name}': {len(bone_names)} bones, "
         f"{len(body_objects)} lod0 body meshes, {len(imported_actions)} actions")

    if missing_root and missing_root in old_armature.data.bones:
        _log(f"root node '{missing_root}' already imported as a bone")
        missing_root_index, missing_root = None, None
    if missing_root is None:
        notes.append("No missing root node detected; skeleton imported as-is")

    # --- rigid alignment: imported rests -> glb bind worlds --------------
    current_heads = {b.name: b.matrix_local.translation.copy() for b in old_armature.data.bones}
    bind_heads = {name: m.translation.copy() for name, m in bind_worlds.items()
                  if name in current_heads}
    if len(bind_heads) < max(3, len(current_heads) // 2):
        raise NcImportError(
            f"Only {len(bind_heads)} of {len(current_heads)} bones have glb bind "
            "matrices; cannot derive the alignment rotation"
        )
    alignment, fit_error = fit_alignment_rotation(current_heads, bind_heads)
    _log(f"alignment fit over {len(bind_heads)} joints: max head error {fit_error:.6f}")
    if alignment is None or fit_error > 1e-3:
        raise NcImportError(
            f"No axis-aligned rotation maps the imported rest pose onto the glb "
            f"bind pose (best error {fit_error:.6f})"
        )
    alignment_inverse = alignment.inverted()

    # Scene hygiene: stop the importer's last-assigned action from evaluating.
    old_anim_data = old_armature.animation_data
    if old_anim_data:
        old_anim_data.action = None
        for track in old_anim_data.nla_tracks:
            track.mute = True
    context.view_layer.update()

    # --- root rest, derived from the glb node TRS ------------------------
    # The imported bone rests equal C @ node_bind_world for a constant axis
    # rotation the importer applied; the FINAL rests are the IBM bind worlds.
    # Fit the node-world -> bind-world conversion over the joints and apply it
    # to the missing root node's static world. (The armature OBJECT transform
    # cannot be used here: Blender leaves it at the last evaluated frame.)
    root_rest = None
    node_conversion = None
    if missing_root is not None:
        joint_indices = _glb_all_joints(gltf)
        node_worlds = _glb_node_worlds(gltf, joint_indices | {missing_root_index})
        glb_nodes = gltf.get("nodes", [])
        node_heads = {}
        for joint in joint_indices:
            name = glb_nodes[joint].get("name")
            if name in bind_heads:
                node_heads[name] = node_worlds[joint].translation.copy()
        node_conversion, node_fit_error = fit_alignment_rotation(node_heads, bind_heads)
        _log(f"node-world -> bind-world fit over {len(node_heads)} joints: "
             f"max error {node_fit_error:.6f}")
        if node_conversion is None or node_fit_error > 1e-3:
            raise NcImportError(
                f"glb node hierarchy does not coincide with the bind pose "
                f"(best fit error {node_fit_error:.6f}); cannot place the root bone"
            )
        # The glTF importer's object-transform conversion is the fixed Y-up ->
        # Z-up rotation; the node-derived root rest is only valid when the glb
        # node hierarchy uses the same convention (true for nc-extractor
        # files). Otherwise fall back to a rest equal to the alignment
        # rotation (origin-anchored), which is always self-consistent.
        yup_to_zup = Matrix.Rotation(math.pi / 2.0, 4, 'X')
        conversion_diff = max(
            abs(node_conversion[r][c] - yup_to_zup[r][c])
            for r in range(4) for c in range(4)
        )
        if conversion_diff <= 1e-3:
            root_rest = node_conversion @ node_worlds[missing_root_index]
        else:
            root_rest = alignment.copy()
            notes.append(
                "glb node convention differs from the glTF importer's Y-up "
                "conversion; root bone rest anchored at the origin instead of "
                "the root node TRS"
            )
        if abs(root_rest.to_3x3().determinant() - 1.0) > 1e-3:
            notes.append("Root node transform is not a pure rotation; "
                         "root rest may be skewed")
        _log(f"root rest translation: {tuple(round(v, 5) for v in root_rest.translation)}")

    # --- per-action root motion (object F-Curves -> root bone deltas) ----
    # The new root bone's world transform is keyed to equal the armature
    # OBJECT's animated world O(t) (= the glb root node's motion), i.e.
    # delta_root(t) = R^-1 @ O(t). To keep every other bone's world identical
    # to the original import, the bones that used to be parentless (Bip001
    # Pelvis) get their basis keys left-multiplied by the constant
    # K = rest_old^-1 @ M^-1 @ R @ rest_old (identity when R == M).
    rotation_mode = old_armature.rotation_mode
    parent_world = old_armature.parent.matrix_world.copy() if old_armature.parent else Matrix.Identity(4)
    parent_inverse = old_armature.matrix_parent_inverse.copy()

    root_deltas_by_action = {}
    child_tracks_by_action = {}
    pose_slot_by_action = {}
    if missing_root:
        # Defaults for object channels an action does not animate: the static
        # local basis implied by the glb node TRS (the armature object's own
        # properties are unreliable, see above).
        static_basis = (parent_world @ parent_inverse).inverted() @ root_rest
        static_loc, static_quat, static_scale = static_basis.decompose()
        euler_mode = rotation_mode if rotation_mode in {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"} else "XYZ"
        static_euler = static_quat.to_euler(euler_mode)
        obj_defaults = {
            ("location", 0): static_loc[0], ("location", 1): static_loc[1], ("location", 2): static_loc[2],
            ("rotation_quaternion", 0): static_quat[0], ("rotation_quaternion", 1): static_quat[1],
            ("rotation_quaternion", 2): static_quat[2], ("rotation_quaternion", 3): static_quat[3],
            ("rotation_euler", 0): static_euler[0], ("rotation_euler", 1): static_euler[1],
            ("rotation_euler", 2): static_euler[2],
            ("scale", 0): static_scale[0], ("scale", 1): static_scale[1], ("scale", 2): static_scale[2],
        }
        rest_inverse = root_rest.inverted()

        old_root_bones = [b for b in old_armature.data.bones if b.parent is None]
        child_corrections = {}
        for bone in old_root_bones:
            correction = (
                bone.matrix_local.inverted() @ alignment_inverse
                @ root_rest @ bone.matrix_local
            )
            if not _matrix_is_identity(correction):
                child_corrections[bone.name] = correction
        if child_corrections:
            _log(f"old root bones needing basis correction: {sorted(child_corrections)}")

        def _bone_basis_at(bone_fcurves, frame):
            def value(channel, index, default):
                fc = bone_fcurves.get((channel, index))
                return fc.evaluate(frame) if fc is not None else default
            loc = Vector((value("location", 0, 0.0), value("location", 1, 0.0), value("location", 2, 0.0)))
            quat = Quaternion((
                value("rotation_quaternion", 0, 1.0), value("rotation_quaternion", 1, 0.0),
                value("rotation_quaternion", 2, 0.0), value("rotation_quaternion", 3, 0.0),
            ))
            if quat.magnitude > 1e-8:
                quat.normalize()
            else:
                quat = Quaternion((1.0, 0.0, 0.0, 0.0))
            scale = Vector((value("scale", 0, 1.0), value("scale", 1, 1.0), value("scale", 2, 1.0)))
            return Matrix.Translation(loc) @ quat.to_matrix().to_4x4() @ Matrix.Diagonal((*scale, 1.0))

        for action in imported_actions:
            pose_slot, pose_fcurves = _pose_slot_and_fcurves(action)
            if not pose_fcurves:
                continue
            pose_slot_by_action[action] = pose_slot
            fcurve_map = {
                (fc.data_path, fc.array_index): fc
                for fc in pose_fcurves
                if fc.data_path in OBJECT_TRANSFORM_PATHS
            }
            frame_start, frame_end = _action_frame_span(action)
            deltas = {}
            for frame in range(frame_start, frame_end + 1):
                basis = object_basis_matrix_from_fcurves(obj_defaults, rotation_mode, fcurve_map, frame)
                object_world = parent_world @ parent_inverse @ basis
                deltas[frame] = rest_inverse @ object_world
            root_deltas_by_action[action] = deltas

            child_tracks = {}
            for bone_name, correction in child_corrections.items():
                prefix = f'pose.bones["{bone_name}"].'
                bone_fcurves = {}
                for fc in pose_fcurves:
                    data_path = fc.data_path
                    if data_path.startswith(prefix):
                        bone_fcurves[(data_path[len(prefix):], fc.array_index)] = fc
                key_frames = sorted({
                    float(kf.co[0])
                    for fc in bone_fcurves.values()
                    for kf in fc.keyframe_points
                })
                if not key_frames:
                    key_frames = [float(frame_start)]
                child_tracks[bone_name] = {
                    frame: correction @ _bone_basis_at(bone_fcurves, frame)
                    for frame in key_frames
                }
            child_tracks_by_action[action] = child_tracks
        _log(f"computed root-motion deltas for {len(root_deltas_by_action)} actions")

    # --- donor materials / flags / metadata ------------------------------
    _log(f"=== Import donor geom: {donor_geom_path}")
    collection, body_infos, outline_info = _import_donor(context, donor_geom_path)
    missing_keys = [k for k in _GEOM_METADATA_KEYS if k not in collection]
    if missing_keys:
        raise NcImportError(f"Donor collection missing metadata keys: {missing_keys}")
    if build_outline and outline_info is None:
        notes.append(f"Donor geom has no {_OUTLINE_MATERIAL} material; outline skipped")

    # --- build export armature -------------------------------------------
    new_armature = _build_new_armature(context, old_armature, missing_root, root_rest, alignment)
    new_bone_names = [b.name for b in new_armature.data.bones]
    _log(f"new armature: {len(new_bone_names)} bones"
         + (f", root '{new_bone_names[0]}'" if missing_root else ""))

    # --- rehome root motion into the actions, strip object curves --------
    # Object-level curves are only removed when their motion has been rehomed
    # onto the new root bone; otherwise they are left for the .anim exporter's
    # object-motion composition to handle.
    for action in imported_actions:
        if action in root_deltas_by_action:
            frame_start, frame_end = _action_frame_span(action)
            tracks = {missing_root: root_deltas_by_action[action]}
            tracks.update(child_tracks_by_action.get(action, {}))
            write_pose_matrix_keys(
                action, new_armature, pose_slot_by_action.get(action), tracks,
            )
            removed = remove_object_transform_fcurves(action)
            # Bake onto the integer frame grid: the .anim format cannot store
            # the fractional key times Unity's keyframe reduction left in the
            # glb, and truncating them drifts the animation mid-clip.
            resampled = resample_pose_fcurves(action, frame_start, frame_end)
            _log(f"  {action.name}: rehomed root motion ({len(tracks)} bone track(s)), "
                 f"removed {removed} object F-Curve(s), resampled {resampled} pose F-Curve(s)")

    # --- rebuild NLA tracks on the new armature --------------------------
    from .blender_compat import create_nla_track
    anim_data = new_armature.animation_data or new_armature.animation_data_create()
    for action in imported_actions:
        pose_slot, pose_fcurves = _pose_slot_and_fcurves(action)
        if not pose_fcurves:
            continue
        anim_data.action = action
        if pose_slot is not None and hasattr(anim_data, "action_slot"):
            try:
                anim_data.action_slot = pose_slot
            except Exception:
                pass
        start, _end = _action_frame_span(action)
        create_nla_track(new_armature, action, action.name, max(start, 1),
                         scale=1.0, blend_type='COMBINE')
    anim_data.action = None

    # --- bake meshes to file-consistent space and rebind ------------------
    for obj in body_objects:
        _bake_mesh_to_world(context, obj, alignment)

    min_z = min(v.co.z for obj in body_objects for v in obj.data.vertices)
    max_z = max(v.co.z for obj in body_objects for v in obj.data.vertices)
    _log(f"baked mesh Z range: [{min_z:.4f}, {max_z:.4f}] (expect grounded ~0)")
    if not (-0.25 < min_z < 0.25):
        notes.append(f"Baked meshes are not grounded (min Z {min_z:.3f}); "
                     "check the model's bind pose")

    for index, obj in enumerate(body_objects):
        info = body_infos[min(index, len(body_infos) - 1)]

        if not obj.data.uv_layers:
            raise NcImportError(f"{obj.name}: mesh has no UV layers")
        if obj.data.uv_layers[0].name != "uv1":
            obj.data.uv_layers[0].name = "uv1"

        obj.data.materials.clear()
        obj.data.materials.append(info["material"])
        for key, value in info["flags"].items():
            obj.data[key] = value

        unknown_groups = {vg.name for vg in obj.vertex_groups} - set(new_bone_names)
        if unknown_groups:
            raise NcImportError(
                f"{obj.name}: vertex groups missing from skeleton: "
                f"{sorted(unknown_groups)[:8]}"
            )

        obj.parent = new_armature
        obj.matrix_parent_inverse = Matrix.Identity(4)
        modifier = obj.modifiers.new(name="Armature", type='ARMATURE')
        modifier.object = new_armature

        for coll in list(obj.users_collection):
            coll.objects.unlink(obj)
        collection.objects.link(obj)
        set_flags = [i for i in range(8) if info["flags"][f"DSTS_flag_{i}"]]
        _log(f"  {obj.name}: material={info['material'].name} flags_set={set_flags} "
             f"verts={len(obj.data.vertices)}")

    # --- outline ----------------------------------------------------------
    if build_outline and outline_info is not None:
        outline = _build_outline_mesh(context, body_objects, new_armature, outline_info, collection)
        _log(f"  outline: material={outline_info['material'].name} "
             f"verts={len(outline.data.vertices)} (white vertex color)")

    # --- clean up the rest of the glb import ------------------------------
    keep = set(body_objects) | {new_armature}
    for obj in new_objects:
        if obj in keep:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass
    _purge_orphans()

    # --- final placement + bookkeeping -------------------------------------
    for coll in list(new_armature.users_collection):
        if coll is not collection:
            coll.objects.unlink(new_armature)
    if new_armature.name not in collection.objects:
        collection.objects.link(new_armature)

    collection["DSTS_material_donor_geom"] = bpy.path.abspath(donor_geom_path)
    collection["DSTS_source_nc_glb"] = bpy.path.abspath(filepath)

    # release the fake users taken to protect donor materials during surgery,
    # then purge again so unassigned donor materials (e.g. eyes) do not linger
    for info in body_infos + ([outline_info] if outline_info else []):
        if info["material"]:
            info["material"].use_fake_user = False
    _purge_orphans()

    # sanity: exporter preconditions
    for obj in collection.objects:
        if obj.type in {'MESH', 'ARMATURE'} and not _matrix_is_identity(obj.matrix_basis):
            raise NcImportError(f"{obj.name}: non-identity object transform after import")
    posed = [pb.name for pb in new_armature.pose.bones if not _matrix_is_identity(pb.matrix_basis)]
    if posed:
        raise NcImportError(f"pose bones not at rest after import: {posed[:8]}")

    _log("=== NC import complete")
    return collection, new_armature, notes
