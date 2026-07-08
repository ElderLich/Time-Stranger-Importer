"""
Time Stranger Importer - Digimon Time Stranger Blender Tools.
Combines geom import/export with animation import/export support.
"""

import bpy
import base64
import os
import re
from pathlib import Path
from bpy_extras.io_utils import ImportHelper, ExportHelper
from bpy.props import StringProperty, EnumProperty, BoolProperty, FloatProperty
from bpy.types import Operator, Panel
from mathutils import Matrix
from .src.blender_compat import create_action_fcurve, create_nla_track, iter_action_fcurves

bl_info = {
    "name": "Time Stranger Importer",
    "author": "Senator John, Nymic_Razor, Pherakki (Animation), Zeak",
    "version": (1, 2, 26),
    "blender": (4, 5, 0),
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

    # Standard DSTS import parents the armature ("root") to a +90° "chr###"
    # orientation empty and gives it a -90° LOCAL rotation, so its WORLD transform
    # is identity (upright). transform_apply() here would BAKE that -90° into every
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
    # world transform — a real user rotation/scale that belongs in the bone data.
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

    group_node = next(
        (
            node for node in mat.node_tree.nodes
            if node.type == "GROUP"
            and node.node_tree
            and node.node_tree.name.startswith(f"DSTS_Data-{mat.name}")
        ),
        None,
    )
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

    Mirrors Romstar's ``add_rest_pose_to_base_anim``: the base action is NOT a
    truly empty/keyframeless action — it carries a rest-pose key for every bone
    so the exported ``.anim`` supplies the bind pose the engine attaches meshes
    to. Keying identity basis (loc 0, rot identity, scale 1) encodes the rest
    pose regardless of how the armature is currently posed. Returns the bone count.
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


# Per-mesh flag byte (TS .geom): bit0 isRendered, bit1 isWireframe, bits 2-7
# unknown. Export defaults to flag_0=True and the rest False, so a custom mesh
# that never copied a donor's flags silently ships the wrong byte. This mirrors
# those export defaults when a donor attribute is somehow absent.
_DSTS_MESH_FLAG_DEFAULTS = (True, False, False, False, False, False, False, False)


def _copy_mesh_flags_from_donor(mesh_data, donor_mesh):
    """Copy the donor mesh's flag byte (flag_0..flag_7) onto a target mesh datablock.

    Stores them as the same ``DSTS_flag_N`` custom properties the exporter reads
    (src/mesh.py), so a borrowed mesh exports the donor's flags instead of the
    hardcoded defaults. Returns True if anything was written.
    """
    if mesh_data is None or donor_mesh is None:
        return False
    for i in range(8):
        value = bool(getattr(donor_mesh, f"flag_{i}", _DSTS_MESH_FLAG_DEFAULTS[i]))
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

    # Root-motion fix: NC glb imports (and hand-made actions) can carry the
    # rig's world motion on the ARMATURE OBJECT's transform channels, which the
    # per-bone loop above silently drops. Compose it onto the root bone track.
    _compose_object_motion_onto_root(armature_obj, action, anim_file, bone_names, action_frame_start)

    return anim_file


def _compose_object_motion_onto_root(armature_obj, action, anim_file, bone_names, action_frame_start):
    """Fold object-level animation of ``action`` into the root bone's tracks.

    TS .anim files know nothing about object transforms: they store per-bone
    deltas vs the geom rest (``local = rest_local @ T@R@S``). When the armature
    object itself is animated (e.g. glTF imports fold an un-skinned root node's
    motion onto the object), the equivalent root-bone delta is::

        delta'(f) = rest_root^-1 @ O(f) @ rest_root @ T@R@S(delta(f))

    where O(f) is the object's local basis matrix (identity object rest is
    assumed - the .geom exporter enforces it). No-op when the action has no
    non-identity object transform curves. Returns True when composed.
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
    _pose_slot, slot_fcurves = _pose_slot_and_fcurves(action)
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
    rest_root_inv = rest_root.inverted()

    # Existing root deltas (from the per-bone loop) evaluated via the action's
    # own pose F-Curves so interpolation matches Blender.
    base_path = f'pose.bones["{root_bone.name}"]'
    pose_fcurves = {}
    for fc in slot_fcurves:
        data_path = str(getattr(fc, "data_path", ""))
        if data_path.startswith(base_path):
            pose_fcurves[(data_path, int(getattr(fc, "array_index", 0)))] = fc

    def _pose_value(channel, index, frame, default):
        fc = pose_fcurves.get((f"{base_path}.{channel}", index))
        return fc.evaluate(frame) if fc is not None else default

    frames = set(anim_file.rotations[root_idx]) | set(anim_file.positions[root_idx]) | set(anim_file.scales[root_idx])
    for fc in object_fcurves.values():
        for keyframe in getattr(fc, "keyframe_points", []):
            frame_anim = int(keyframe.co[0]) - action_frame_start
            if frame_anim >= 0:
                frames.add(frame_anim)
    if not frames:
        frames = {0}

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

        old_loc = Vector((
            _pose_value("location", 0, frame_abs, 0.0),
            _pose_value("location", 1, frame_abs, 0.0),
            _pose_value("location", 2, frame_abs, 0.0),
        ))
        old_quat = Quaternion((
            _pose_value("rotation_quaternion", 0, frame_abs, 1.0),
            _pose_value("rotation_quaternion", 1, frame_abs, 0.0),
            _pose_value("rotation_quaternion", 2, frame_abs, 0.0),
            _pose_value("rotation_quaternion", 3, frame_abs, 0.0),
        ))
        if old_quat.magnitude > 1e-8:
            old_quat.normalize()
        else:
            old_quat = Quaternion((1.0, 0.0, 0.0, 0.0))
        old_scale = Vector((
            _pose_value("scale", 0, frame_abs, 1.0),
            _pose_value("scale", 1, frame_abs, 1.0),
            _pose_value("scale", 2, frame_abs, 1.0),
        ))
        old_delta = (
            Matrix.Translation(old_loc)
            @ old_quat.to_matrix().to_4x4()
            @ Matrix.Diagonal((*old_scale, 1.0))
        )

        new_delta = rest_root_inv @ object_world @ rest_root @ old_delta
        loc, quat, scale = new_delta.decompose()
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
        description="Export textures as PNG files instead of .dds (DDS) format",
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

        with open(os.path.splitext(self.filepath)[0] + ".nlst", "wb") as f:
            f.write(write_nlst(geom).encode("utf-8"))

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

    def execute(self, context):
        from .src.nc_glb import import_nc_model

        try:
            donor_path = _resolve_donor_geom_path(self.extracted_root, self.donor_chr, self.donor_geom_path)
            collection, armature, notes = import_nc_model(
                context, self.filepath, donor_path, build_outline=self.build_outline
            )
        except Exception as exc:
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


def _build_anim_action_and_nla(armature_obj, anim_name, anim_data, bone_names, replace_existing=False):
    """Build a pose action from parsed .anim data and push it as an NLA track.

    Single source of truth shared by the .anim import operator and the one-click
    "Import Donor Animations" button. Retargets by bone order (anim bone_idx →
    armature.data.bones[bone_idx]). When replace_existing is True, an NLA track of
    the same name (and its now-orphaned action) is removed FIRST so re-running does
    not stack duplicate tracks or leave the new action auto-suffixed ".001".
    """
    if replace_existing and armature_obj.animation_data:
        for track in list(armature_obj.animation_data.nla_tracks):
            if track.name == anim_name:
                strip_actions = [s.action for s in track.strips if s.action]
                armature_obj.animation_data.nla_tracks.remove(track)
                for old_action in strip_actions:
                    if old_action.users == 0:
                        bpy.data.actions.remove(old_action)

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
    # (Any pre-existing same-named track was already removed at the top when
    # replace_existing is set, so the new action keeps its clean name.)
    create_nla_track(armature_obj, action, anim_name, start_frame, scale=scale, blend_type='COMBINE')
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
        # stem like chr0500 — the char after the stem must be "_" or ".".
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


def _build_rest_pose_anim_file(armature_obj, playback_rate=30.0):
    """Build the stem.anim rest-pose file from the current export armature."""
    action = bpy.data.actions.new(f"{armature_obj.name}_rest_export")
    try:
        if _seed_rest_pose_into_action(armature_obj, action, frame=1.0) == 0:
            raise RuntimeError("Armature has no pose bones to export as a base rest pose")
        return build_basic_hundred_line_anim_from_action(
            armature_obj,
            action,
            playback_rate=playback_rate,
        )
    finally:
        if action.users == 0:
            bpy.data.actions.remove(action)


# NC clip name -> TS animation code. Per TS slot conventions:
# bn01 idle, bn02 secondary idle, br01 run/move, bd01 hurt, bv01 victory,
# ba01/ba02 attacks, bs01/bs02 skills, e001 evolution.
_NC_MOVESET_MAP = (
    ("bn01", ("idle",)),
    ("bn02", ("stand", "tongyong")),
    ("br01", ("move2", "move")),
    ("bd01", ("hurt",)),
    ("bv01", ("win", "win2")),
    ("ba01", ("attack_move", "attack1_move_to")),
    ("ba02", ("attack", "attack_move_to", "attack1_move_to")),
    ("bs01", ("skill1", "skill1_move_to", "skill1_move_back")),
    ("bs02", ("skill2_move_to", "skill2_display")),
    ("e001", ("evolution2", "evolution")),
)


class DSTS_OT_ExportNcMoveset(Operator):
    """Export the NC animation set as TS-named .anim files (idle -> bn01, move2 -> br01, ...)"""
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

        tracks = {track.name: track for track in armature_obj.animation_data.nla_tracks if track.strips}
        target_stem, _num = _normalize_chr_stem(self.target_chr)

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

        for ts_code, candidates in _NC_MOVESET_MAP:
            track = next((tracks[name] for name in candidates if name in tracks), None)
            if track is None:
                missing.append(ts_code)
                continue
            strip = track.strips[0]
            action = strip.action
            if not action:
                missing.append(ts_code)
                continue
            playback_rate = 24.0 / strip.scale if strip.scale > 0 else 30.0
            anim_file = build_basic_hundred_line_anim_from_action(armature_obj, action, playback_rate=playback_rate)
            filepath = os.path.join(self.directory, f"{target_stem}_{ts_code}.anim")
            try:
                anim_file.to_file(filepath)
                exported.append(f"{ts_code}<-{track.name}")
            except Exception as exc:
                print(f"[DSTS] Failed to export {ts_code} from {track.name}: {exc}")
                missing.append(ts_code)

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
        items = [(col.name, col.name, "") for col in _exportable_geom_collections()]
        return items or [("__NONE__", "No imported Geom collection found", "")]

    collection_name: EnumProperty(
        name="Collection",
        description="Select collection to export",
        items=collection_items,
    )
    export_textures_as_png: BoolProperty(
        name="Export Textures as PNG",
        description="Export textures as PNG files instead of .dds (DDS) format",
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
                # hardcoded True,False×7 defaults — this is the "get the flags of
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
        for mat in materials:
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
        # tracks (borrowing materials/flags does NOT bring animations — this does).
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

        # Mesh flags for the active mesh — the TS .geom per-mesh flag byte. These
        # are populated by import or by Borrow Material Setup; shown here so the
        # user can SEE and tweak the flags they copied from a donor model.
        active = getattr(context, "object", None)
        if active and active.type == 'MESH':
            box = layout.box()
            box.label(text="Mesh Flags (active mesh)")
            mesh_data = active.data
            flag_labels = ("isRendered", "isWireframe", "flag 2", "flag 3",
                           "flag 4", "flag 5", "flag 6", "flag 7")
            if "DSTS_flag_0" in mesh_data.keys():
                for i, label_text in enumerate(flag_labels):
                    key = f"DSTS_flag_{i}"
                    if key in mesh_data.keys():
                        box.prop(mesh_data, f'["{key}"]', text=label_text)
            else:
                box.label(text="No flags yet — Import or Borrow Material Setup", icon='INFO')

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

class DSTS_MT_ImportSubmenu(bpy.types.Menu):
    """Digimon Time Stranger Import Submenu"""
    bl_idname = "DSTS_MT_import_submenu"
    bl_label = "Digimon Time Stranger (.geom, .anim)"

    def draw(self, context):
        layout = self.layout
        layout.operator(DSTS_OT_GeomImport.bl_idname, text="Geometry (.geom)")
        layout.operator(DSTS_OT_AnimImport.bl_idname, text="Animation (.anim)")
        layout.separator()
        layout.operator(DSTS_OT_NcGlbImport.bl_idname, text="New Century Model (.glb)")


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
    DSTS_MT_ExportSubmenu,
)


def register():
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
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    from .data import material_nodes
    material_nodes.register()


def unregister():
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
    from .data import material_nodes
    material_nodes.unregister()


if __name__ == "__main__":
    register()
