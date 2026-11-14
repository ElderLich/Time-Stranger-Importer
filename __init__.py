"""
Time Stranger Importer - Digimon Time Stranger Blender Tools.
Combines geom import/export with animation import/export support.
"""

import bpy
import base64
import os
import re
import math
from pathlib import Path
from bpy_extras.io_utils import ImportHelper, ExportHelper
from bpy.props import StringProperty, EnumProperty, BoolProperty, FloatProperty
from bpy.types import Operator, Panel
from mathutils import Matrix, Vector
from .src.blender_compat import create_action_fcurve, create_nla_track, iter_action_fcurves, scene_fps
from .src.animation import single_strip, animation_path, validate_strip

bl_info = {
    "name": "Time Stranger Importer",
    "author": "Senator John, Nymic_Razor, Pherakki (Animation), Zeak",
    "version": (1, 4, 5),
    "blender": (5, 0, 0),
    "category": "Import-Export",
    "description": "Import/Export .geom and .anim files for Digimon Time Stranger",
    "location": "File > Import/Export",
}

_ACTION_BONE_RE = re.compile(r'pose\.bones\["([^"]+)"\]')
_IDENTITY_4X4 = Matrix.Identity(4)
_GEOM_METADATA_KEYS = ("unknown_0x10", "unknown_0x30", "unknown_0x34", "CLUT")
_DEFAULT_EXTRACTED_ROOT = r"D:\Digimon Modding\Time Stranger Extracted"


def _matrix_is_identity(matrix, epsilon=1e-5):
    for row in range(4):
        for col in range(4):
            if abs(matrix[row][col] - _IDENTITY_4X4[row][col]) > epsilon:
                return False
    return True


def _imported_geom_collections():
    return [col for col in bpy.data.collections if all(key in col for key in _GEOM_METADATA_KEYS)]


def _exportable_geom_collections():
    return [
        col for col in bpy.data.collections
        if all(key in col for key in _GEOM_METADATA_KEYS)
        or bool(col.get("DSTS_material_donor_geom"))
        or bool(col.get("DSTS_source_geom"))
    ]


def _active_geom_collection(context):
    active = getattr(context, "object", None)
    if active:
        for collection in active.users_collection:
            if all(key in collection for key in _GEOM_METADATA_KEYS):
                return collection
        for collection in active.users_collection:
            if (
                bool(collection.get("DSTS_material_donor_geom"))
                or bool(collection.get("DSTS_source_geom"))
                or bool(_chr_stem_from_text(collection.name))
            ):
                return collection

    geom = bpy.data.collections.get("Geom")
    if geom and all(key in geom for key in _GEOM_METADATA_KEYS):
        return geom

    collections = _imported_geom_collections()
    return collections[0] if collections else geom


def _geom_collection_items(self, context):
    collections = list(_imported_geom_collections())
    active_collection = _active_geom_collection(context)
    if active_collection and active_collection not in collections:
        collections.insert(0, active_collection)
    items = [(col.name, col.name, "") for col in collections]
    geom = bpy.data.collections.get("Geom")
    if geom and not any(name == geom.name for name, _label, _desc in items):
        items.insert(0, (geom.name, geom.name, "Geom collection without full DSTS metadata"))
    return items or [("__NONE__", "No Geom collection found", "Import a .geom first")]


def _find_workflow_armature(context, collection=None):
    active = getattr(context, "object", None)
    if active and active.type == "ARMATURE":
        return active

    selected = [obj for obj in getattr(context, "selected_objects", []) if obj.type == "ARMATURE"]
    if selected:
        return selected[0]

    if collection:
        armatures = [obj for obj in collection.objects if obj.type == "ARMATURE"]
        if armatures:
            return armatures[0]

    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    return armatures[0] if len(armatures) == 1 else None


def _find_geom_workflow_armature(context, collection=None):
    if collection:
        collection_armatures = [obj for obj in collection.objects if obj.type == "ARMATURE"]
        active = getattr(context, "object", None)
        if active in collection_armatures:
            return active

        selected = [
            obj for obj in getattr(context, "selected_objects", [])
            if obj in collection_armatures
        ]
        if selected:
            return selected[0]

        if collection_armatures:
            return collection_armatures[0]

    return _find_workflow_armature(context, collection)


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


def _normalize_chr_stem(value):
    text = str(value or "").strip().lower()
    match = re.search(r"(\d+)", text)
    if not match:
        return text or "chr000", 0
    number = int(match.group(1))
    return f"chr{number:03d}", number


def _chr_stem_from_text(value):
    match = re.search(r"chr[\s_-]*0*(\d+)", str(value or ""), re.IGNORECASE)
    if not match:
        return ""
    return f"chr{int(match.group(1)):03d}"


def _target_chr_from_context(context, collection=None, fallback=""):
    active = getattr(context, "object", None)
    if active:
        for active_collection in active.users_collection:
            stem = _chr_stem_from_text(active_collection.name)
            if stem:
                return stem

    if collection:
        stem = _chr_stem_from_text(collection.name)
        if stem:
            return stem
        stored = str(collection.get("DSTS_target_chr", "") or "")
        if stored:
            return _normalize_chr_stem(stored)[0]

    if fallback:
        return _normalize_chr_stem(fallback)[0]
    return ""


def _scene_extracted_root(context):
    return bpy.path.abspath(getattr(context.scene, "dsts_extracted_root", "") or _DEFAULT_EXTRACTED_ROOT)


def _resolve_donor_geom_path(extracted_root, source_chr, explicit_path=""):
    explicit = bpy.path.abspath(str(explicit_path or ""))
    if explicit and os.path.isfile(explicit):
        return explicit

    source_stem, _source_number = _normalize_chr_stem(source_chr)
    root = bpy.path.abspath(str(extracted_root or _DEFAULT_EXTRACTED_ROOT))
    if not os.path.isdir(root):
        raise RuntimeError(f"Extracted root does not exist: {root}")

    direct_candidates = [
        os.path.join(root, f"{source_stem}.geom"),
        os.path.join(root, "app_0.dx11", f"{source_stem}.geom"),
        os.path.join(root, "app_0", f"{source_stem}.geom"),
    ]
    for candidate in direct_candidates:
        if os.path.isfile(candidate):
            return candidate

    for dirpath, _dirnames, filenames in os.walk(root):
        if f"{source_stem}.geom" in filenames:
            return os.path.join(dirpath, f"{source_stem}.geom")

    raise RuntimeError(f"Could not find {source_stem}.geom under {root}")


def _copy_geom_metadata_to_collection(collection, geom, source_path="", texture_dirs=None):
    collection["unknown_0x10"] = geom.unknown_0x10
    collection["unknown_0x30"] = geom.unknown_0x30
    collection["unknown_0x34"] = geom.unknown_0x34
    collection["CLUT"] = base64.b64encode(bytes(geom.clut)).decode("ascii")
    if source_path:
        collection["DSTS_source_geom"] = bpy.path.abspath(str(source_path))
    if texture_dirs:
        collection["DSTS_texture_search_dirs"] = "\n".join(str(path) for path in texture_dirs)


def _set_material_vertex_layout_from_mesh(mat, mesh_data):
    if not mat or not mat.use_nodes or mesh_data is None:
        return False

    from . import data

    from .src.material import find_data_group
    group_node = find_data_group(mat)
    if group_node is None:
        return False

    data_node = next(
        (
            node for node in group_node.node_tree.nodes
            if type(node) == data.material_nodes.ShaderDataNode
        ),
        None,
    )
    if data_node is None:
        return False

    data_node.bytes_per_vertex = int(mesh_data.bytes_per_vertex)
    while len(data_node.attributes) > 0:
        data_node.attributes.remove(len(data_node.attributes) - 1)
    for attr in mesh_data.mesh_attributes:
        item = data_node.attributes.add()
        item.count = int(attr.count)
        item.offset = int(attr.offset)
        item.atype = attr.atype
        item.dtype = attr.dtype
    return True


def _ensure_collection_export_metadata(collection):
    if not collection or all(key in collection for key in _GEOM_METADATA_KEYS):
        return False

    donor_path = collection.get("DSTS_material_donor_geom", "") or collection.get("DSTS_source_geom", "")
    donor_path = bpy.path.abspath(str(donor_path or ""))
    if not donor_path or not os.path.isfile(donor_path):
        return False

    from .src.backend import load_format_modules
    dsts_formats, _material, _mesh, _skeleton, _utils = load_format_modules()
    donor_geom = dsts_formats.Geom.from_file(donor_path)
    _copy_geom_metadata_to_collection(collection, donor_geom, donor_path)
    return True


def _geom_meshes(collection):
    if not collection:
        return []
    return [obj for obj in collection.objects if obj.type == "MESH"]


def _target_meshes(context, collection, apply_to_all=True):
    if apply_to_all:
        return _geom_meshes(collection)
    return [obj for obj in context.selected_objects if obj.type == "MESH"]


def _iter_texture_nodes_from_tree(node_tree, seen=None):
    if not node_tree:
        return
    seen = seen or set()
    if node_tree.name in seen:
        return
    seen.add(node_tree.name)
    for node in node_tree.nodes:
        if isinstance(node, bpy.types.ShaderNodeTexImage):
            yield node
        elif node.type == "GROUP" and node.node_tree:
            yield from _iter_texture_nodes_from_tree(node.node_tree, seen)


def _iter_material_texture_nodes(mat):
    if not mat or not mat.use_nodes:
        return []
    return list(_iter_texture_nodes_from_tree(mat.node_tree))


def _texture_uniform_from_node(node):
    label = str(getattr(node, "label", "") or "")
    if label.startswith("DSTS-"):
        return label[5:]
    return str(node.get("DSTS_uniform", "") or "")


def _texture_stem_from_node(node):
    image = getattr(node, "image", None)
    if image:
        return _strip_texture_extension(Path(image.name).name)
    filepath = str(node.get("DSTS_texture_file", "") or "")
    return _strip_texture_extension(Path(filepath).name) if filepath else ""


def _strip_texture_extension(stem):
    stem = str(stem or "")
    stem = re.sub(r"\.[0-9]{3}$", "", stem)
    while os.path.splitext(stem)[1].lower() in {".img", ".dds", ".png"}:
        stem = os.path.splitext(stem)[0]
        stem = re.sub(r"\.[0-9]{3}$", "", stem)
    return stem


def _default_texture_stem_for_uniform(target_stem, uniform):
    suffix_by_uniform = {
        "DiffuseColor": "a01",
        "OverlayColorSampler": "a01",
        "OverlayColorSampler2": "a01",
        "OverlayColorSampler3": "a01",
        "Bumpiness": "a01n",
        "OverlayNormalSampler": "a01n",
        "OverlayNormalSampler2": "a01n",
        "OverlayNormalSampler3": "a01n",
        "OverlayMaskSampler": "a01m",
        "OverlayMaskSampler2": "a01m",
        "OverlayMaskSampler3": "a01m",
        "BackBufferSampler": "a01s",
        "IrradianceMapStrength": "a01l",
        "LightPixelProj": "a01em",
    }
    suffix = suffix_by_uniform.get(uniform)
    return target_stem + suffix if suffix else ""


def _normalize_body_atlas(value):
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"a\d{2}", text) else ""


def _apply_preferred_body_atlas(target_stem, stem, preferred_atlas):
    preferred_atlas = _normalize_body_atlas(preferred_atlas)
    if not preferred_atlas:
        return ""
    stem = _strip_texture_extension(stem)
    if not stem.lower().startswith(target_stem.lower()):
        return ""
    suffix = stem[len(target_stem):]
    match = re.match(r"a\d{2}(.*)$", suffix, re.IGNORECASE)
    if not match:
        return ""
    return target_stem + preferred_atlas + match.group(1)


def _texture_stems_for_uniform(target_stem, uniform, current_stem, preferred_atlas=""):
    current_stem = _strip_texture_extension(current_stem)
    stems = []
    match = re.match(r"chr\d+(.+)", str(current_stem or ""), re.IGNORECASE)
    if match:
        remapped = target_stem + match.group(1)
        preferred = _apply_preferred_body_atlas(target_stem, remapped, preferred_atlas)
        if preferred:
            stems.append(preferred)
        if remapped not in stems:
            stems.append(remapped)
    elif current_stem:
        stems.append(current_stem)

    fallback = _default_texture_stem_for_uniform(target_stem, uniform)
    preferred_fallback = _apply_preferred_body_atlas(target_stem, fallback, preferred_atlas)
    if preferred_fallback and preferred_fallback not in stems:
        stems.append(preferred_fallback)
    if fallback and fallback not in stems:
        stems.append(fallback)
    return stems


def _find_texture_path(images_folder, stem):
    if not images_folder or not stem:
        return ""
    direct = os.path.join(images_folder, stem)
    if os.path.isfile(direct):
        return direct
    stem = _strip_texture_extension(stem)
    for ext in (".img", ".dds", ".png", ".IMG", ".DDS", ".PNG"):
        candidate = os.path.join(images_folder, stem + ext)
        if os.path.isfile(candidate):
            return candidate
    return ""


def _load_texture_image(path):
    from .src.material import get_image
    return get_image(path)


def _set_texture_colorspace(image, uniform):
    if not image:
        return
    colorspace = "sRGB" if uniform in {
        "DiffuseColor",
        "OverlayMaskSampler",
        "LightPixelProj",
        "OverlayNormalSampler",
        "OverlayColorSampler3",
    } else "Non-Color"
    try:
        image.colorspace_settings.name = colorspace
    except TypeError:
        pass


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


# Per-mesh flag byte: bit2 selects contiguous skeleton-palette upload and belongs
# to the target geometry. Preserve it when borrowing material/render metadata.
# The other defaults mirror the exporter when a donor attribute is absent.
_DSTS_MESH_FLAG_DEFAULTS = (True, False, False, False, False, False, False, False)


def _copy_mesh_flags_from_donor(mesh_data, donor_mesh):
    """Borrow donor render flags while retaining the target's palette mode.

    Stores them as the same ``DSTS_flag_N`` custom properties the exporter reads
    (src/mesh.py). A new target defaults to indexed gathering: its bones need
    not form the donor's contiguous range. Returns True if anything was written.
    """
    if mesh_data is None or donor_mesh is None:
        return False
    for i in range(8):
        value = (bool(mesh_data.get("DSTS_flag_2", False)) if i == 2 else
                 bool(getattr(donor_mesh, f"flag_{i}", _DSTS_MESH_FLAG_DEFAULTS[i])))
        mesh_data[f"DSTS_flag_{i}"] = value
    return True


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


def build_basic_hundred_line_anim_from_action(armature_obj, action, playback_rate=30.0, slot=None, frame_range=None):
    from .src.animation import build_from_action
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
    from .src.nc_glb import (
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
    include_scene_records: BoolProperty(
        name="Camera and Light Previews", default=False,
        description="Import owned native camera bindings and light controls with the model; records are preserved even when previews are disabled",
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

    def execute(self, context):
        try:
            from .src.geom import import_geom
            images_folder = bpy.path.abspath(self.images_path) if self.use_custom_images_path and self.images_path else None
            imported_collection = import_geom(context, self.filepath, images_folder,
                                              include_scene_records=self.include_scene_records)
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
            from .src.spark import atomic_write
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


class DSTS_OT_NcGlbImport(Operator, ImportHelper):
    """Import a Digimon New Century model.glb as an export-ready DSTS scene"""
    bl_idname = "import_scene.dsts_nc_glb"
    bl_label = "Digimon New Century Model (.glb)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".glb"
    filter_glob: StringProperty(default="*.glb", options={'HIDDEN'})

    donor_chr: StringProperty(
        name="Donor Chr",
        description="Existing Digimon whose materials, mesh flags and geom metadata to borrow, for example chr050",
        default="chr050",
    )
    donor_geom_path: StringProperty(
        name="Donor Geom",
        description="Optional direct donor .geom path (overrides Donor Chr lookup)",
        subtype='FILE_PATH',
        default="",
    )
    extracted_root: StringProperty(
        name="Extracted Assets Root",
        description="Root folder containing extracted Time Stranger assets (used to find the donor .geom)",
        subtype='DIR_PATH',
        default=_DEFAULT_EXTRACTED_ROOT,
    )
    build_outline: BoolProperty(
        name="Build Outline Mesh",
        description="Join a copy of the body meshes into an 'outline' mesh with the donor's MTR_line01 material and a white vertex color (required by the DSTS outline shader)",
        default=True,
    )
    import_related_skills: BoolProperty(
        name="Import Related Skill Scenes", default=False,
        description="Discover captured cast/approach phases beside this model and import their source animations, available cameras and audio into separate scenes",
    )
    related_camera: BoolProperty(name="Related Cameras", default=True)
    related_audio: BoolProperty(name="Related Audio", default=True)
    related_effect_references: BoolProperty(name="Related Effect Timeline References", default=True,
        description="Source effect records for inspection; Unity particles/shaders are not yet translated")

    def invoke(self, context, event):
        self.extracted_root = _scene_extracted_root(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "donor_chr")
        layout.prop(self, "donor_geom_path")
        layout.prop(self, "extracted_root")
        layout.prop(self, "build_outline")
        layout.prop(self, "import_related_skills")
        if self.import_related_skills:
            layout.prop(self, "related_camera")
            layout.prop(self, "related_audio")
            layout.prop(self, "related_effect_references")
            layout.label(text="Cast/approach phases; cinematic is selected separately.")

    def execute(self, context):
        from .src.nc_glb import import_nc_model
        from .src.scene_state import snapshot, rollback

        before, old_scene, old_scenes = snapshot(), context.window.scene, set(bpy.data.scenes)
        old_fps = (old_scene.render.fps, old_scene.render.fps_base)
        old_model = old_scene.dsts_nc_model_path
        old_active, old_selected = context.view_layer.objects.active, list(context.selected_objects)
        try:
            if self.import_related_skills:
                from .src.nc_skill_ui import catalog, primary_phases, import_related_skills
                primary_phases(catalog(self.filepath))
            donor_path = _resolve_donor_geom_path(self.extracted_root, self.donor_chr, self.donor_geom_path)
            collection, armature, notes = import_nc_model(
                context, self.filepath, donor_path, build_outline=self.build_outline
            )
            context.scene.dsts_nc_model_path = str(Path(self.filepath).resolve())
            if self.import_related_skills:
                scenes = import_related_skills(context, self.filepath, import_camera=self.related_camera,
                    import_audio=self.related_audio, import_effect_references=self.related_effect_references)
                notes.append(f"Imported {len(scenes)} independent source skill scenes; effect records are not rendered")
        except Exception as exc:
            context.window.scene = old_scene
            for created in list(bpy.data.scenes):
                if created not in old_scenes:
                    bpy.data.scenes.remove(created)
            rollback(before)
            old_scene.render.fps, old_scene.render.fps_base = old_fps
            old_scene.dsts_nc_model_path = old_model
            for obj in old_selected:
                obj.select_set(True)
            context.view_layer.objects.active = old_active
            print(f"[DSTS] NC glb import failed: {exc}")
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        info_note_prefixes = (
            "Scene FPS set to 30",
            "No missing root node detected",
            "Retried glb import with Blender original-bind guessing disabled",
        )
        for note in notes:
            level = {'INFO'} if note.startswith(info_note_prefixes) else {'WARNING'}
            self.report(level, note)
        collection["DSTS_material_donor_chr"] = _normalize_chr_stem(self.donor_chr)[0]
        self.report(
            {'INFO'},
            f"Imported NC model into '{collection.name}': {len(armature.data.bones)} bones, "
            f"{len([o for o in collection.objects if o.type == 'MESH'])} meshes",
        )
        return {'FINISHED'}


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
        if not track.mute and not track.lock and track.strips
    ]
    return tuple(track_list) if track_list else (("NONE", "No Active Tracks", "", "ERROR", 0),)


def _build_anim_action_from_data(armature_obj, anim_name, anim_data, bone_names):
    from .src.animation import build_action
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
        new_track, _ = create_nla_track(armature_obj, action, anim_name, start_frame,
                                        scale=scale, blend_type='COMBINE')
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
    from .src.anim import AnimFileHundredLine
    from .src.anim.BinaryHundredLine import AnimFileBinary as AnimFileBinaryHundredLine
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
    from .src.anim import AnimFileHundredLine
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


# NC clip/action aliases -> TS animation code. TS-coded target tracks such as
# chr549_bg01 are accepted; other chr-prefixed tracks are ignored.
_NC_MOVESET_MAP = (
    ("ba01", ("attack_move", "attack1_move_to", "ba01")),
    ("ba02", ("attack", "attack_move_to", "attack1_move_to", "ba02")),
    ("bs01", ("skill1", "skill1_move_to", "skill1_move_back", "bs01")),
    ("bs02", ("skill2_move_to", "skill2_display", "skill2_move_back", "bs02")),
    ("bd01", ("hurt", "bd01")),
    ("bd02", ("bd02", "hurt")),
    ("bd03", ("bd03", "stand", "idle", "bn01")),
    ("bg01", ("guard", "bg01", "stand", "idle", "bn01")),
    ("bn01", ("idle", "bn01")),
    ("br01", ("move2", "move", "br01")),
    ("bv01", ("win", "win2", "bv01")),
    ("fn01_01", ("fn01_01", "field_idle", "idle", "bn01")),
    ("fw01_01", ("fw01_01", "field_walk", "move2", "move", "br01")),
    ("fr01_01b", ("fr01_01b", "field_run", "move2", "move", "br01")),
    ("fe01", ("fe01", "idle", "bn01")),
    ("fe02", ("fe02", "idle", "bn01")),
    ("fe03", ("fe03", "stand", "idle", "bn01")),
    ("fe04", ("fe04", "idle", "bn01")),
    ("fq01", ("fq01", "idle", "bn01")),
    ("fq02", ("fq02", "idle", "bn01")),
    ("bn02", ("stand", "tongyong", "sleep", "sleeping", "bn02")),
    ("e001", ("evolution2", "evolution", "e001")),
)
_NC_MOVESET_CODE_ORDER = tuple(code for code, _aliases in _NC_MOVESET_MAP)
_NC_MOVESET_SEQUENCE_MAP = {
    "bs02": (
        ("skill2_move_to",),
        ("skill2_display",),
        ("skill2_move_back",),
    ),
}
_TS_MOVESET_CODES = frozenset(
    _NC_MOVESET_CODE_ORDER
    + ("bf01", "bg02", "e001")
)
_SKIP_TS_MOVESET_CODES = frozenset(("lod_2",))
_SEQUENCE_OBJECT_TRANSFORM_PATHS = frozenset((
    "location",
    "rotation_quaternion",
    "rotation_euler",
    "rotation_axis_angle",
    "scale",
    "delta_location",
    "delta_rotation_euler",
    "delta_rotation_quaternion",
    "delta_scale",
))


def _track_name_moveset_parts(track_name):
    stem = os.path.splitext(str(track_name or "").strip())[0].lower()
    if not stem:
        return "", ""
    if stem in _SKIP_TS_MOVESET_CODES or stem.endswith("_lod_2"):
        return "", ""
    if re.fullmatch(r"chr\d+loc(?:_.*)?", stem):
        return "", ""
    prefix = ""
    match = re.fullmatch(r"chr\d+_(.+)", stem)
    if match:
        prefix = stem[:stem.find("_")]
        stem = match.group(1)
    if stem in _TS_MOVESET_CODES and stem not in _SKIP_TS_MOVESET_CODES:
        return prefix, stem
    ride_match = re.fullmatch(r"r\d+_(fn01|fw01)", stem)
    if ride_match:
        return prefix, stem
    return "", ""


def _track_name_to_moveset_code(track_name):
    _prefix, code = _track_name_moveset_parts(track_name)
    return code


def _candidate_track_names_for_code(ts_code, target_stem=""):
    names = []
    for code, aliases in _NC_MOVESET_MAP:
        if code == ts_code:
            names.extend(aliases)
            break
    names.append(ts_code)
    if target_stem:
        names.append(f"{target_stem}_{ts_code}")
    return tuple(dict.fromkeys(name for name in names if name))


def _find_track_by_names(tracks, names):
    return next((tracks[name.lower()] for name in names if name.lower() in tracks), None)


def _export_action_as_anim(armature_obj, action, strip, filepath):
    validate_strip(strip)
    playback_rate = scene_fps(bpy.context.scene) / strip.scale if strip and strip.scale > 0 else 30.0
    anim_file = build_basic_hundred_line_anim_from_action(armature_obj, action, playback_rate=playback_rate, slot=strip.action_slot, frame_range=(strip.action_frame_start, strip.action_frame_end))
    anim_file.to_file(filepath)


def _build_sequence_action_from_tracks(armature_obj, name, tracks):
    import math
    from .src.animation import _write_curve, legacy_native_locations, location_to_native_matrix
    location_bones = {pb.path_from_id() + '.location': pb.bone for pb in armature_obj.pose.bones}
    playback_rate = scene_fps(bpy.context.scene)
    sources, keys = [], set()
    for track in tracks:
        strip = single_strip(track)
        curves = {(fc.data_path, fc.array_index): fc
                  for fc in iter_action_fcurves(strip.action, armature_obj, strip.action_slot)
                  if fc.data_path.startswith('pose.bones') or fc.data_path in _SEQUENCE_OBJECT_TRANSFORM_PATHS
                  or fc.data_path.startswith('["DSTS_float_')}
        keys.update(curves)
        legacy_locations = legacy_native_locations(strip.action)
        if legacy_locations:
            for path in location_bones:
                if any((path, component) in curves for component in range(3)):
                    keys.update((path, component) for component in range(3))
        sources.append((strip, curves, legacy_locations))
    if not keys:
        raise RuntimeError(f"No supported transform curves found for sequence {name}")
    samples = {key: [] for key in keys}
    current_frame = 1
    for strip, curves, legacy_locations in sources:
        start, end = strip.action_frame_start, strip.action_frame_end
        source_rate = scene_fps(bpy.context.scene) / strip.scale
        count = max(1, math.ceil((end - start) * playback_rate / source_rate) + 1)
        for path, component in keys:
            curve = curves.get((path, component))
            default = 1.0 if path.endswith('scale') or (path.endswith('rotation_quaternion') and component == 0) else 0.0
            for i in range(count):
                source_frame = min(end, start + i * source_rate / playback_rate)
                value = curve.evaluate(source_frame) if curve else default
                if legacy_locations and path in location_bones:
                    native_value = Vector([curves[(path, i)].evaluate(source_frame)
                                           if (path, i) in curves else 0.0 for i in range(3)])
                    value = (location_to_native_matrix(location_bones[path]).inverted() @ native_value)[component]
                samples[(path, component)].append((current_frame + i, value))
        current_frame += count - 1
    ad = armature_obj.animation_data or armature_obj.animation_data_create()
    previous_action, previous_slot = ad.action, getattr(ad, 'action_slot', None)
    action = bpy.data.actions.new(name)
    try:
        for (path, component), values in samples.items():
            _write_curve(action, armature_obj, path, component, values, 'Sequence')
        return action, playback_rate
    except Exception:
        ad.action = previous_action
        if previous_slot is not None:
            ad.action_slot = previous_slot
        if action.users == 0:
            bpy.data.actions.remove(action)
        raise
    finally:
        ad.action = previous_action
        if previous_slot is not None:
            ad.action_slot = previous_slot


def _try_export_sequence(armature_obj, tracks, ts_code, output_code, target_stem, directory):
    sequences = _NC_MOVESET_SEQUENCE_MAP.get(ts_code)
    if not sequences:
        return None
    sequence_tracks = []
    for aliases in sequences:
        track = _find_track_by_names(tracks, aliases)
        if track is None:
            return None
        sequence_tracks.append(track)

    action, playback_rate = _build_sequence_action_from_tracks(
        armature_obj,
        f"{target_stem}_{output_code}_sequence_export",
        sequence_tracks,
    )
    try:
        filepath = os.path.join(directory, f"{target_stem}_{output_code}.anim")
        anim_file = build_basic_hundred_line_anim_from_action(armature_obj, action, playback_rate=playback_rate)
        anim_file.to_file(filepath)
        return "+".join(track.name for track in sequence_tracks)
    finally:
        anim_data = armature_obj.animation_data
        if anim_data and anim_data.action == action:
            anim_data.action = None
        if action.users == 0:
            bpy.data.actions.remove(action)


class DSTS_OT_ExportNcMoveset(Operator):
    """Export NC animation clips as target-stem TS .anim files"""
    bl_idname = "export_scene.dsts_nc_moveset"
    bl_label = "Digimon Time Stranger Export NC Moveset"
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(name="Output Directory", subtype='DIR_PATH')
    armature_name: EnumProperty(items=fetch_armatures, name="Armature")
    target_chr: StringProperty(
        name="Target Chr",
        description="Target character stem used for the exported file names, for example chr110",
        default="chr110",
    )
    def invoke(self, context, event):
        collection = _active_geom_collection(context)
        stem = _target_chr_from_context(context, collection, "")
        if stem:
            self.target_chr = stem
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "armature_name")
        layout.prop(self, "target_chr")

    def execute(self, context):
        if self.armature_name == "NONE":
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}
        armature_obj = bpy.data.objects.get(self.armature_name)
        if not armature_obj or armature_obj.type != "ARMATURE":
            self.report({'ERROR'}, f"Armature '{self.armature_name}' not found")
            return {'CANCELLED'}
        if not armature_obj.animation_data or not armature_obj.animation_data.nla_tracks:
            self.report({'ERROR'}, "Armature has no NLA tracks (import an NC model first)")
            return {'CANCELLED'}

        target_stem, target_num = _normalize_chr_stem(self.target_chr)

        tracks = {track.name.lower(): track for track in armature_obj.animation_data.nla_tracks if track.strips}
        tracks_by_code = {}
        for track in armature_obj.animation_data.nla_tracks:
            if not track.strips:
                continue
            prefix, code = _track_name_moveset_parts(track.name)
            if prefix and prefix != target_stem:
                continue
            if code and code not in tracks_by_code:
                tracks_by_code[code] = track

        exported = []
        missing = []

        base_path = os.path.join(self.directory, f"{target_stem}.anim")
        try:
            base_anim = _build_rest_pose_anim_file(armature_obj, playback_rate=30.0)
            base_anim.to_file(base_path)
            exported.append("base<-rest")
        except Exception as exc:
            print(f"[DSTS] Failed to export base rest animation {target_stem}.anim: {exc}")
            missing.append("base")

        export_codes = list(_NC_MOVESET_CODE_ORDER)
        for code in tracks_by_code:
            if code not in export_codes:
                export_codes.append(code)

        for ts_code in export_codes:
            output_code = ts_code
            ride_match = re.fullmatch(r"r\d+_(fn01|fw01)", ts_code)
            if ride_match and target_num:
                output_code = f"r{target_num:03d}_{ride_match.group(1)}"

            try:
                sequence_source = _try_export_sequence(
                    armature_obj,
                    tracks,
                    ts_code,
                    output_code,
                    target_stem,
                    self.directory,
                )
            except Exception as exc:
                print(f"[DSTS] Failed to export sequence {output_code}: {exc}")
                sequence_source = None
            if sequence_source:
                exported.append(f"{output_code}<-{sequence_source}")
                continue

            candidates = _candidate_track_names_for_code(ts_code, target_stem=target_stem)
            track = tracks_by_code.get(ts_code)
            if track is None:
                track = _find_track_by_names(tracks, candidates)
            if track is None:
                missing.append(output_code)
                continue

            filepath = os.path.join(self.directory, f"{target_stem}_{output_code}.anim")
            try:
                strip = single_strip(track)
                _export_action_as_anim(armature_obj, strip.action, strip, filepath)
                exported.append(f"{output_code}<-{track.name}")
            except Exception as exc:
                print(f"[DSTS] Failed to export {output_code} from {track.name}: {exc}")
                missing.append(output_code)

        if not exported:
            self.report({'ERROR'}, "No NC clips matched the moveset mapping")
            return {'CANCELLED'}
        message = f"Exported {len(exported)} moveset anim(s): {', '.join(exported)}"
        if missing:
            message += f"; no source clip for: {', '.join(missing)}"
            self.report({'WARNING'}, message)
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}


def export_all_collection(collection, filepath, export_textures_as_png=False):
    from .src.geom import export_geom, export_textures
    from .src.nlst import write_nlst

    output_dir = os.path.dirname(filepath)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    _ensure_collection_export_metadata(collection)
    geom = export_geom(collection)
    geom.to_file(filepath)
    export_note = getattr(geom, "export_note", None)

    nlst_path = os.path.splitext(filepath)[0] + ".nlst"
    from .src.spark import atomic_write
    atomic_write(nlst_path, write_nlst(geom).encode("utf-8"), overwrite=True)

    exported_textures = export_textures(collection, output_dir, as_png=export_textures_as_png)

    armatures = [obj for obj in collection.objects if obj.type == "ARMATURE"]
    exported_anims = 0
    if armatures:
        armature_obj = armatures[0]
        if armature_obj.animation_data and armature_obj.animation_data.nla_tracks:
            for nla_track in armature_obj.animation_data.nla_tracks:
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


# =============================================================================
# VIEWPORT WORKFLOW OPERATORS
# =============================================================================

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


class DSTS_OT_BorrowMaterialSetup(Operator):
    """Borrow donor DSTS material setup from an existing .geom"""
    bl_idname = "object.dsts_borrow_material_setup"
    bl_label = "Borrow Material Setup"
    bl_options = {'REGISTER', 'UNDO'}

    collection_name: EnumProperty(
        name="Collection",
        description="Geom collection whose meshes should receive borrowed materials",
        items=_geom_collection_items,
    )

    source_chr: StringProperty(
        name="Donor Chr",
        description="Existing Digimon stem, for example chr050",
        default="chr050",
    )

    source_geom_path: StringProperty(
        name="Donor Geom",
        description="Optional direct donor .geom path",
        subtype='FILE_PATH',
        default="",
    )

    extracted_root: StringProperty(
        name="Extracted Assets Root",
        description="Root folder containing extracted Time Stranger assets",
        subtype='DIR_PATH',
        default=_DEFAULT_EXTRACTED_ROOT,
    )

    apply_to_all_geom_meshes: BoolProperty(
        name="Apply To All Geom Meshes",
        description="Assign borrowed materials to every mesh in the selected Geom collection",
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
        layout.prop(self, "apply_to_all_geom_meshes")

    def execute(self, context):
        if self.collection_name == "__NONE__":
            self.report({'ERROR'}, "No Geom collection found")
            return {'CANCELLED'}

        collection = bpy.data.collections.get(self.collection_name)
        if not collection:
            self.report({'ERROR'}, f"Collection '{self.collection_name}' not found")
            return {'CANCELLED'}

        targets = _target_meshes(context, collection, self.apply_to_all_geom_meshes)
        if not targets:
            self.report({'ERROR'}, "No target mesh objects found")
            return {'CANCELLED'}

        try:
            donor_path = _resolve_donor_geom_path(self.extracted_root, self.source_chr, self.source_geom_path)
            from .src.backend import load_format_modules
            from .src.geom import _texture_search_dirs
            _dsts_formats, material, _mesh, _skeleton, _utils = load_format_modules()
            donor_geom = _dsts_formats.Geom.from_file(donor_path)
            texture_dirs = [str(path) for path in _texture_search_dirs(Path(donor_path), None)]
            _copy_geom_metadata_to_collection(collection, donor_geom, donor_path, texture_dirs)
        except Exception as exc:
            print(f"[DSTS] Borrow material setup failed: {exc}")
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        borrowed_by_name = {}
        borrowed_list = []
        for mat_data in donor_geom.materials:
            original_name = mat_data.name
            mat = bpy.data.materials.new(name=original_name)
            material.resolve_material(collection, mat, mat_data, texture_dirs)
            mat["DSTS_material_donor_geom"] = donor_path
            mat["DSTS_material_donor_chr"] = _normalize_chr_stem(self.source_chr)[0]
            borrowed_by_name[mat.name] = mat
            borrowed_by_name[original_name] = mat
            borrowed_list.append(mat)

        if not borrowed_list:
            self.report({'ERROR'}, "Donor .geom has no materials")
            return {'CANCELLED'}

        layouts_copied = 0
        for index, donor_mesh in enumerate(donor_geom.meshes):
            mat = None
            if donor_mesh.material:
                mat = borrowed_by_name.get(donor_mesh.material.name)
            if mat is None:
                mat = borrowed_list[min(index, len(borrowed_list) - 1)]
            if _set_material_vertex_layout_from_mesh(mat, donor_mesh):
                layouts_copied += 1

        applied = 0
        flags_copied = 0
        for index, obj in enumerate(targets):
            donor_mesh = donor_geom.meshes[min(index, len(donor_geom.meshes) - 1)] if donor_geom.meshes else None
            mat = None
            if donor_mesh and donor_mesh.material:
                mat = borrowed_by_name.get(donor_mesh.material.name)
            if mat is None:
                mat = borrowed_list[min(index, len(borrowed_list) - 1)]
            if donor_mesh:
                _set_material_vertex_layout_from_mesh(mat, donor_mesh)
                # Also take the donor mesh's flag byte (isRendered/isWireframe/...)
                # so a custom mesh exports the donor's flags instead of the
                # hardcoded True,FalseÃ—7 defaults â€” this is the "get the flags of
                # another model" step.
                if _copy_mesh_flags_from_donor(obj.data, donor_mesh):
                    flags_copied += 1
            obj.data.materials.clear()
            obj.data.materials.append(mat)
            obj.data["DSTS_material_donor_geom"] = donor_path
            obj.data["DSTS_material_donor_chr"] = _normalize_chr_stem(self.source_chr)[0]
            applied += 1

        collection["DSTS_material_donor_chr"] = _normalize_chr_stem(self.source_chr)[0]
        collection["DSTS_material_donor_geom"] = donor_path
        collection["DSTS_extracted_root"] = bpy.path.abspath(self.extracted_root)
        try:
            context.scene.dsts_extracted_root = bpy.path.abspath(self.extracted_root)
        except Exception:
            pass

        self.report({'INFO'}, f"Borrowed {len(borrowed_list)} material(s) from {os.path.basename(donor_path)}; copied {layouts_copied} layout(s); copied flags to {flags_copied} mesh(es); applied to {applied} mesh(es)")
        return {'FINISHED'}


class DSTS_OT_RetargetMaterialTextures(Operator):
    """Retarget DSTS material texture nodes to a chosen texture family"""
    bl_idname = "object.dsts_retarget_material_textures"
    bl_label = "Retarget Material Textures"
    bl_options = {'REGISTER', 'UNDO'}

    collection_name: EnumProperty(
        name="Collection",
        description="Geom collection whose material texture nodes should be retargeted",
        items=_geom_collection_items,
    )

    target_chr: StringProperty(
        name="Target Chr",
        description="Target character stem/id used for texture names",
        default="chr110",
    )

    images_path: StringProperty(
        name="Images Folder",
        description="Folder containing target textures",
        subtype='DIR_PATH',
        default="",
    )

    apply_to_all_geom_meshes: BoolProperty(
        name="Retarget All Geom Meshes",
        description="Retarget materials used by every mesh in the selected Geom collection",
        default=True,
    )

    def invoke(self, context, event):
        collection = _active_geom_collection(context)
        if collection:
            self.collection_name = collection.name
            self.target_chr = _target_chr_from_context(context, collection, self.target_chr) or self.target_chr
            self.images_path = collection.get("DSTS_images_folder", self.images_path)
        if not self.images_path:
            self.images_path = getattr(context.scene, "dsts_target_images_folder", "")
        return context.window_manager.invoke_props_dialog(self, width=650)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "collection_name")
        layout.prop(self, "target_chr")
        layout.prop(self, "images_path")
        layout.prop(self, "apply_to_all_geom_meshes")

    def execute(self, context):
        if self.collection_name == "__NONE__":
            self.report({'ERROR'}, "No Geom collection found")
            return {'CANCELLED'}

        collection = bpy.data.collections.get(self.collection_name)
        if not collection:
            self.report({'ERROR'}, f"Collection '{self.collection_name}' not found")
            return {'CANCELLED'}

        target_stem, _target_number = _normalize_chr_stem(_target_chr_from_context(context, collection, self.target_chr) or self.target_chr)
        images_folder = bpy.path.abspath(self.images_path)
        if not images_folder or not os.path.isdir(images_folder):
            self.report({'ERROR'}, "Choose the patch images folder containing target textures")
            return {'CANCELLED'}

        materials = []
        preferred_atlas_by_material = {}
        for obj in _target_meshes(context, collection, self.apply_to_all_geom_meshes):
            preferred_atlas = _normalize_body_atlas(
                obj.get("DSTS_nc_atlas", "") or obj.data.get("DSTS_nc_atlas", "")
            )
            for mat in getattr(obj.data, "materials", []):
                if mat and mat not in materials:
                    materials.append(mat)
                if mat and preferred_atlas and mat not in preferred_atlas_by_material:
                    preferred_atlas_by_material[mat] = preferred_atlas

        changed = 0
        missing = set()
        fallback_used = set()
        from .src.material import find_data_group, make_data_group_single_user
        for mat in materials:
            if find_data_group(mat) is None:
                continue
            make_data_group_single_user(mat)
            preferred_atlas = _normalize_body_atlas(
                preferred_atlas_by_material.get(mat, "") or mat.get("DSTS_nc_atlas", "")
            )
            for node in _iter_material_texture_nodes(mat):
                uniform = _texture_uniform_from_node(node)
                candidate_stems = _texture_stems_for_uniform(
                    target_stem,
                    uniform,
                    _texture_stem_from_node(node),
                    preferred_atlas=preferred_atlas,
                )
                if not candidate_stems:
                    continue
                new_stem = candidate_stems[0]
                path = ""
                for candidate_stem in candidate_stems:
                    path = _find_texture_path(images_folder, candidate_stem)
                    if path:
                        new_stem = candidate_stem
                        break
                if not path:
                    missing.add(new_stem)
                    continue
                if new_stem != candidate_stems[0]:
                    fallback_used.add(f"{candidate_stems[0]} -> {new_stem}")
                node.image = _load_texture_image(path)
                node["DSTS_texture_stem"] = new_stem
                node["DSTS_texture_file"] = path
                if uniform:
                    node.label = "DSTS-" + uniform
                _set_texture_colorspace(node.image, uniform)
                changed += 1
            mat["DSTS_target_chr"] = target_stem
            mat["DSTS_images_folder"] = images_folder

        collection["DSTS_target_chr"] = target_stem
        collection["DSTS_images_folder"] = images_folder
        try:
            context.scene.dsts_target_images_folder = images_folder
            context.scene.dsts_workflow_target_chr = target_stem
        except Exception:
            pass

        if missing:
            print(f"[DSTS] Retarget textures missing optional files: {', '.join(sorted(missing))}")
        if fallback_used:
            print(f"[DSTS] Retarget textures used fallback stems: {', '.join(sorted(fallback_used))}")
        if not changed:
            self.report({'WARNING'}, "No DSTS texture nodes were changed; borrow/import material setup first")
            return {'CANCELLED'}
        if missing or fallback_used:
            details = []
            if fallback_used:
                preview = ", ".join(sorted(fallback_used)[:4])
                suffix = "..." if len(fallback_used) > 4 else ""
                details.append(f"fallback {len(fallback_used)}: {preview}{suffix}")
            if missing:
                preview = ", ".join(sorted(missing)[:6])
                suffix = "..." if len(missing) > 6 else ""
                details.append(f"missing {len(missing)}: {preview}{suffix}")
            self.report({'WARNING'}, f"Retargeted {changed} texture node(s) to {target_stem}; {'; '.join(details)}")
        else:
            self.report({'INFO'}, f"Retargeted {changed} texture node(s) to {target_stem} in {len(materials)} material(s)")
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

class DSTS_OT_CinematicImport(Operator, ImportHelper):
    """Open captured camera motion and audio in a separate preview scene"""
    bl_idname = "import_scene.dsts_cinematic"
    bl_label = "New Century Cinematic Preview"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.cinematic.json;*.json", options={'HIDDEN'})
    import_actor: BoolProperty(name="Import Source Actor", default=True)
    import_camera: BoolProperty(name="Import Captured Camera", default=True)
    import_audio: BoolProperty(name="Import Audio", default=True)
    import_effect_references: BoolProperty(name="Import Effect References", default=True)
    reset_actor: BoolProperty(name="Reset to Source Idle", default=True)
    show_source_helpers: BoolProperty(name="Show Source Helper Hierarchy", default=False)

    def execute(self, context):
        from .src.cinematic import import_preview
        try:
            scene = import_preview(context, self.filepath, self.import_actor,
                import_camera=self.import_camera, import_audio=self.import_audio,
                import_effect_references=self.import_effect_references,
                reset_actor=self.reset_actor, show_source_helpers=self.show_source_helpers)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Created {scene.name}; effects remain source references")
        return {'FINISHED'}


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
        from .src.model_package import package_native_model
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
        layout.operator(DSTS_OT_CinematicImport.bl_idname, text="Cinematic Preview (.json)")


class DSTS_MT_CyberSleuthImport(bpy.types.Menu):
    bl_idname = 'DSTS_MT_cyber_sleuth_import'
    bl_label = 'Digimon Cyber Sleuth'

    def draw(self, context):
        from .src.cyber_sleuth import draw_import_menu
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


# =============================================================================
# REGISTRATION
# =============================================================================

CLASSES = (
    DSTS_OT_CinematicImport,
    DSTS_OT_PackageNativeModel,
    DSTS_OT_GeomImport,
    DSTS_OT_GeomExport,
    DSTS_OT_NcGlbImport,
    DSTS_OT_AnimImport,
    DSTS_OT_AnimExport,
    DSTS_OT_AnimExportAll,
    DSTS_OT_ExportNcMoveset,
    DSTS_OT_ExportAll,
    DSTS_OT_PrepareSelectedAsGeom,
    DSTS_OT_ApplyPoseAsRest,
    DSTS_OT_ClearExportPose,
    DSTS_OT_ApplyMeshObjectTransforms,
    DSTS_OT_ApplyArmatureObjectTransforms,
    DSTS_OT_CleanActionObjectTransforms,
    DSTS_OT_RenamePrimaryUvsToUv1,
    DSTS_OT_BorrowMaterialSetup,
    DSTS_OT_RetargetMaterialTextures,
    DSTS_OT_ImportDonorAnimations,
    DSTS_OT_SetupBaseAnimationNla,
    DSTS_PT_WorkflowPanel,
    DSTS_OT_ShowErrors,
    DSTS_MT_ImportSubmenu,
    DSTS_MT_NewCenturyImport,
    DSTS_MT_CyberSleuthImport,
    DSTS_MT_ExportSubmenu,
)


def register():
    from .src import native_effects, nc_skill_ui, native_timeline, native_collision, native_lights, native_scene, cyber_sleuth, texture_animation
    native_effects.register()
    nc_skill_ui.register()
    native_timeline.register()
    native_collision.register()
    for cls in native_lights.CLASSES:
        bpy.utils.register_class(cls)
    native_lights.register_properties()
    for cls in native_scene.CLASSES:
        bpy.utils.register_class(cls)
    for cls in cyber_sleuth.CLASSES:
        bpy.utils.register_class(cls)
    for cls in texture_animation.CLASSES:
        bpy.utils.register_class(cls)
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.dsts_extracted_root = StringProperty(
        name="DSTS Extracted Assets Root",
        description="Root folder containing extracted Time Stranger assets",
        subtype='DIR_PATH',
        default=_DEFAULT_EXTRACTED_ROOT,
    )
    bpy.types.Scene.dsts_target_images_folder = StringProperty(
        name="DSTS Target Images Folder",
        description="Last patch images folder used for material texture retargeting",
        subtype='DIR_PATH',
        default="",
    )
    bpy.types.Scene.dsts_workflow_donor_chr = StringProperty(
        name="DSTS Donor Chr",
        description="Donor character stem used for material setup, for example chr050",
        default="chr050",
    )
    bpy.types.Scene.dsts_workflow_donor_geom = StringProperty(
        name="DSTS Donor Geom",
        description="Optional direct donor .geom path",
        subtype='FILE_PATH',
        default="",
    )
    bpy.types.Scene.dsts_workflow_target_chr = StringProperty(
        name="DSTS Target Chr",
        description="Target character stem used for texture and animation names",
        default="chr050",
    )
    bpy.types.Scene.dsts_workflow_apply_all = BoolProperty(
        name="DSTS Apply All Meshes",
        description="Apply workflow operations to all meshes in the Geom collection",
        default=True,
    )
    bpy.types.Scene.dsts_workflow_replace_actions = BoolProperty(
        name="DSTS Replace Action Tracks",
        description="Replace existing generated DSTS NLA tracks",
        default=False,
    )
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    _order_digimon_import_menu()
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    from .data import material_nodes
    material_nodes.register()
    texture_animation.register_handlers()


def unregister():
    from .src import texture_animation
    texture_animation.unregister_handlers()
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    for attr in (
        "dsts_extracted_root",
        "dsts_target_images_folder",
        "dsts_workflow_donor_chr",
        "dsts_workflow_donor_geom",
        "dsts_workflow_target_chr",
        "dsts_workflow_apply_all",
        "dsts_workflow_replace_actions",
    ):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    for cls in reversed(texture_animation.CLASSES):
        bpy.utils.unregister_class(cls)
    from .data import material_nodes
    material_nodes.unregister()
    from .src import native_effects, nc_skill_ui, native_timeline, native_collision, native_lights, native_scene, cyber_sleuth
    for cls in reversed(cyber_sleuth.CLASSES):
        bpy.utils.unregister_class(cls)
    for cls in reversed(native_scene.CLASSES):
        bpy.utils.unregister_class(cls)
    native_lights.unregister_properties()
    for cls in reversed(native_lights.CLASSES):
        bpy.utils.unregister_class(cls)
    native_collision.unregister()
    native_timeline.unregister()
    nc_skill_ui.unregister()
    native_effects.unregister()


if __name__ == "__main__":
    register()
