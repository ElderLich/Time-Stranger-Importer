import bpy
import os
import re
import base64
import glob
import math
import zlib
from .. import data
from pathlib import Path
from mathutils import Matrix
from .backend import load_format_modules
from .blender_compat import create_action_fcurve, create_nla_track
from .anim.InterfaceHundredLine import AnimFileHundredLine
from .anim.BinaryHundredLine import AnimFileBinary as AnimFileBinaryHundredLine


_IDENTITY_4X4 = Matrix.Identity(4)


def _matrix_is_identity(matrix, epsilon=1e-5):
    for row in range(4):
        for col in range(4):
            if abs(matrix[row][col] - _IDENTITY_4X4[row][col]) > epsilon:
                return False
    return True


def _format_vec(values):
    return ", ".join(f"{float(value):.6g}" for value in values)


def _object_transform_summary(obj):
    loc, rot, scale = obj.matrix_basis.decompose()
    rot_deg = [math.degrees(value) for value in rot.to_euler()]
    return (
        f"{obj.name} ({obj.type}) loc=({_format_vec(loc)}) "
        f"rot=({_format_vec(rot_deg)}) scale=({_format_vec(scale)})"
    )


def _pose_bone_summary(pose_bone):
    loc, rot, scale = pose_bone.matrix_basis.decompose()
    rot_deg = [math.degrees(value) for value in rot.to_euler()]
    return (
        f"{pose_bone.name} loc=({_format_vec(loc)}) "
        f"rot=({_format_vec(rot_deg)}) scale=({_format_vec(scale)})"
    )


def _ensure_export_transforms_are_baked(collection):
    transformed_objects = [
        obj for obj in collection.objects
        if obj.type in {"ARMATURE", "MESH"} and not _matrix_is_identity(obj.matrix_basis)
    ]
    if transformed_objects:
        details = "\n".join(f"- {_object_transform_summary(obj)}" for obj in transformed_objects[:8])
        if len(transformed_objects) > 8:
            details += f"\n- ... and {len(transformed_objects) - 8} more object(s)"
        raise RuntimeError(
            "Export cancelled: mesh/armature object transforms are not applied. "
            "Blender can display the model correctly while the .geom exporter writes "
            "unrotated local mesh and rest-pose armature data.\n\n"
            f"{details}\n\n"
            "Apply or bake the object transforms before export. For ordinary meshes, "
            "use Object Mode > Ctrl+A > Rotation & Scale. For animated armatures, use "
            "Smart Apply Scale before applying transforms and then check animation playback. "
            "Do not edit src/utils.py for this; flop/unflop already handle the DSTS axis conversion."
        )

    posed_bones = []
    for obj in collection.objects:
        if obj.type != "ARMATURE" or not obj.pose:
            continue
        for pose_bone in obj.pose.bones:
            if not _matrix_is_identity(pose_bone.matrix_basis):
                posed_bones.append((obj, pose_bone))

    if posed_bones:
        details = "\n".join(
            f"- {armature.name}: {_pose_bone_summary(pose_bone)}"
            for armature, pose_bone in posed_bones[:12]
        )
        if len(posed_bones) > 12:
            details += f"\n- ... and {len(posed_bones) - 12} more posed bone(s)"
        raise RuntimeError(
            "Export cancelled: the export armature has pose-mode transforms. "
            "The .geom exporter writes the armature rest pose and local mesh data, "
            "so a model that only looks correct because of a current pose will export in the wrong orientation.\n\n"
            f"{details}\n\n"
            "To export the visible pose, select the armature, enter Pose Mode, and use "
            "Pose > Apply > Apply Pose as Rest Pose, then verify the mesh and animations. "
            "To export the original rest pose instead, clear the pose with Alt+G, Alt+R, and Alt+S. "
            "Ctrl+A and Smart Apply Scale only fix object transforms; they do not bake pose bones."
        )


def _texture_search_dirs(geom_path, images_folder=None):
    dirs = []
    seen = set()

    def add(path):
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen and path.is_dir():
            seen.add(key)
            dirs.append(path)

    if images_folder:
        add(Path(images_folder))
    geom_dir = Path(geom_path).parent
    for depth, base in enumerate((geom_dir, *geom_dir.parents[:3])):
        add(base / "images")
        if depth == 0:
            add(base)
        for shared in ("patch", "patch.dx11", "app_0", "app_0.dx11"):
            add(base / shared / "images")
    return dirs


def import_geom(context, filepath, images_folder=None, *, include_scene_records=False):
    from .scene_state import snapshot, rollback
    import struct
    raw = Path(filepath).read_bytes()
    if len(raw) < 0xB0 or struct.unpack_from('<I', raw)[0] != 316:
        raise ValueError('Expected a complete Time Stranger GEOM v316 header')
    for offset in (0x48, 0x50, 0x58, 0x60, 0x68, 0x78, 0x80, 0x90, 0x98):
        pointer = struct.unpack_from('<Q', raw, offset)[0]
        if pointer > len(raw):
            raise ValueError(f'GEOM section pointer at {offset:#x} exceeds the file size')
    before = snapshot()
    active = context.view_layer.objects.active
    selected = list(context.selected_objects)
    try:
        return _import_geom(context, filepath, images_folder, include_scene_records=include_scene_records)
    except Exception:
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        rollback(before)
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
        raise


def _import_geom(context, filepath, images_folder=None, *, include_scene_records=False):
    dsts_formats, material, mesh, skeleton, utils = load_format_modules()

    error_state_old = dsts_formats.get_throw_errors()
    error_list_old = dsts_formats.get_error_list()
    dsts_formats.set_throw_errors(False)
    dsts_formats.set_error_list([])

    try:
        geom = dsts_formats.Geom.from_file(filepath)

        new_collection = bpy.data.collections.new(name="Geom")
        context.scene.collection.children.link(new_collection)

        new_collection["unknown_0x10"] = geom.unknown_0x10
        new_collection["unknown_0x30"] = geom.unknown_0x30
        new_collection["unknown_0x34"] = geom.unknown_0x34
        new_collection["CLUT"] = base64.b64encode(bytes(geom.clut)).decode("ascii")
        if hasattr(geom, 'bind_pose_snapshot') and geom.bind_pose_snapshot:
            new_collection['DSTS_bind_pose_snapshot'] = base64.b64encode(geom.bind_pose_snapshot).decode('ascii')

        skeleton_result = skeleton.import_skeleton(geom.skeleton, new_collection, utils.unflop)
        if isinstance(skeleton_result, tuple):
            armature_obj, bone_name_map = skeleton_result
        else:
            armature_obj = skeleton_result
            bone_name_map = {}

        from .native_camera import preserve_camera_source
        is_camera = preserve_camera_source(new_collection, armature_obj, filepath, allow_mixed=True)
        if geom.lights or (include_scene_records and geom.cameras):
            from .native_scene import preserve_scene_records
            preserve_scene_records(context, new_collection, armature_obj, filepath, geom,
                                   preview=include_scene_records)

        texture_search_dirs = [str(p) for p in _texture_search_dirs(Path(filepath), images_folder)]
        new_collection["DSTS_texture_search_dirs"] = "\n".join(texture_search_dirs)

        blender_materials = {}
        for mat_data in geom.materials:
            source_name_hash = int(mat_data.name_hash)
            mat = bpy.data.materials.new(name=mat_data.name)
            mat_data.name = mat.name
            material.resolve_material(new_collection, mat, mat_data, texture_search_dirs)
            mat['DSTS_source_name_hash'] = str(source_name_hash)
            blender_materials[mat_data.name] = mat

        for mesh_obj in geom.meshes:
            mesh.import_mesh_object(
                mesh_obj,
                armature_obj,
                blender_materials,
                new_collection,
                bone_name_map=bone_name_map,
            )
            mat = blender_materials[mesh_obj.material.name]
            attr_list = [
                {"count": attr.count, "offset": attr.offset, "atype": attr.atype, "dtype": attr.dtype}
                for attr in mesh_obj.mesh_attributes
            ]
            g_node = material.find_data_group(mat)
            data_node = next(n for n in g_node.node_tree.nodes if type(n) == data.material_nodes.ShaderDataNode)
            if len(data_node.attributes) == 0:
                data_node.bytes_per_vertex = mesh_obj.bytes_per_vertex
                for attr in attr_list:
                    at = data_node.attributes.add()
                    at.count, at.offset, at.atype, at.dtype = attr.values()

        error_list = dsts_formats.get_error_list()
        error_list = [e for e in error_list if not (is_camera and "camera data" in e.lower())]
        if error_list:
            new_collection['DSTS_import_warnings'] = '\n'.join(error_list)
            if bpy.app.background:
                print('[DSTS] Import warnings:', '\n'.join(error_list))
            else:
                bpy.ops.wm.show_errors_window('INVOKE_DEFAULT', errors="\n\n".join(error_list))

        import_animations_for_geom(filepath, armature_obj)
        if armature_obj is not None:
            from .texture_animation import configure_preview
            notes = configure_preview(new_collection, armature_obj, filepath)
            if notes:
                new_collection['DSTS_texture_preview_notes'] = '\n'.join(notes)
        return new_collection

    finally:
        dsts_formats.set_throw_errors(error_state_old)
        dsts_formats.set_error_list(error_list_old)


def import_animations_for_geom(geom_filepath, armature_obj):
    if not armature_obj:
        return

    geom_dir = os.path.dirname(geom_filepath)
    geom_stem = os.path.splitext(os.path.basename(geom_filepath))[0]

    base_anim_path = os.path.join(geom_dir, geom_stem + ".anim")
    related_anims = glob.glob(os.path.join(geom_dir, geom_stem + "_*.anim"))

    anim_paths = []
    if os.path.isfile(base_anim_path):
        anim_paths.append(base_anim_path)
    anim_paths.extend(related_anims)

    if not anim_paths:
        return

    bone_names = [b.name for b in armature_obj.data.bones]
    if not armature_obj.animation_data:
        armature_obj.animation_data_create()

    for anim_path in anim_paths:
        try:
            anim_name = os.path.splitext(os.path.basename(anim_path))[0]
            ab = AnimFileBinaryHundredLine()
            ab.read(anim_path)
            anim_data = AnimFileHundredLine.from_binary(ab)
            _import_animation_to_nla(armature_obj, anim_name, anim_data, bone_names)
        except Exception as e:
            print(f"[DSTS] Warning: Failed to import animation {anim_path}: {e}")


def _import_animation_to_nla(armature_obj, anim_name, anim_data, bone_names):
    from .animation import build_action
    from .blender_compat import scene_fps
    action = build_action(armature_obj, anim_name, anim_data, bone_names)
    scale = scene_fps(bpy.context.scene) / anim_data.playback_rate if anim_data.playback_rate > 0 else 1.0
    create_nla_track(armature_obj, action, anim_name, 1, scale=scale, blend_type='COMBINE')


def export_geom(collection):
    from .cyber_sleuth import is_cyber_sleuth, reject_export
    if is_cyber_sleuth(collection) or any(is_cyber_sleuth(obj) for obj in collection.all_objects):
        reject_export(collection)
    dsts_formats, material, mesh, skeleton, _utils = load_format_modules()

    missing_keys = [k for k in ("unknown_0x10", "unknown_0x30", "unknown_0x34", "CLUT") if k not in collection]
    if missing_keys:
        raise RuntimeError(
            f"Collection is missing DSTS import metadata: {', '.join(missing_keys)}. "
            "Only natively imported .geom collections can be exported."
        )

    _ensure_export_transforms_are_baked(collection)

    geom = dsts_formats.Geom()
    if 'DSTS_bind_pose_snapshot' in collection:
        if not hasattr(geom, 'bind_pose_snapshot'):
            raise ValueError('This model has preserved native bind matrices; install the updated geometry backend')
        geom.bind_pose_snapshot = base64.b64decode(collection['DSTS_bind_pose_snapshot'], validate=True)
    geom.unknown_0x10 = collection["unknown_0x10"]
    geom.unknown_0x30 = collection["unknown_0x30"]
    geom.unknown_0x34 = collection["unknown_0x34"]
    geom.clut = base64.b64decode(bytes(collection["CLUT"], "ascii"))

    armatures = [o for o in collection.objects if o.type == "ARMATURE"]
    if len(armatures) != 1:
        raise ValueError("A Geom collection must contain exactly one armature")
    geom.skeleton = skeleton.export_skeleton(armatures[0])

    if 'DSTS_camera_source' in collection and 'DSTS_scene_source' not in collection:
        from .native_camera import PreservedCameraGeom
        if collection.children or any(obj != armatures[0] for obj in collection.objects):
            raise ValueError('Native camera GEOM export supports only its original rig; remove added objects/collections')
        return PreservedCameraGeom(collection, armatures[0], geom.skeleton)

    name_to_mat = {}
    meshes = [o for o in collection.objects if o.type == "MESH"]
    materials = []
    for obj in meshes:
        if not obj.data.materials or obj.data.materials[0] is None:
            raise ValueError(f"Mesh '{obj.name}' has no export material")
        mat = obj.data.materials[0]
        if mat not in materials:
            materials.append(mat)
    material_hash_map = {}
    for mat in materials:
        mat_data = material.export_material(mat)
        mat_data.name = re.sub(r"(\.[0-9]{3})?$", "", mat_data.name)
        if mat_data.name in name_to_mat:
            raise ValueError(f"Materials resolve to the same native name '{mat_data.name}'; rename split materials before export")
        geom.materials.append(mat_data)
        name_to_mat[mat_data.name] = mat_data
        if 'DSTS_source_name_hash' in mat:
            material_hash_map.setdefault(int(mat['DSTS_source_name_hash']), set()).add(
                zlib.crc32(mat_data.name.encode('utf-8')) ^ 0xFFFFFFFF)

    for obj in [o for o in collection.objects if o.type == "MESH"]:
        mesh_out = mesh.export_mesh_object(obj, geom.skeleton)
        mat_name = re.sub(r"(\.[0-9]{3})?$", "", obj.data.materials[0].name)
        mesh_out.material = name_to_mat[mat_name]
        geom.meshes.append(mesh_out)

    for channel in getattr(geom.skeleton, 'float_channels', []):
        if channel.flags == 8:
            if channel.name_hash not in material_hash_map:
                raise ValueError('A native material animation target was removed; preserve its material before export')
            targets = material_hash_map[channel.name_hash]
            if len(targets) != 1:
                raise ValueError('A native animated material was split into multiple targets; explicit float-channel remapping is required')
            channel.name_hash = next(iter(targets))

    for obj in [*geom.skeleton.bones] + [*geom.materials] + [*geom.meshes]:
        obj.name = re.sub(r"(\.[0-9]{3})?$", "", obj.name)

    if 'DSTS_scene_source' in collection:
        from .native_scene import restore_scene_records
        preserved = restore_scene_records(collection, armatures[0], geom)
        if preserved is not None:
            return preserved

    return geom


def export_textures(collection, output_dir, as_png=False):
    from .material import find_data_group, texture_stem

    images_dir = os.path.join(output_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    exported = []
    processed_images = set()
    stems = {}
    failures = []

    for obj in collection.objects:
        if obj.type != "MESH" or not obj.data.materials:
            continue
        for mat in obj.data.materials:
            if not mat or not mat.use_nodes:
                continue
            node = find_data_group(mat)
            if node is not None:
                for g_node in node.node_tree.nodes:
                    if isinstance(g_node, bpy.types.ShaderNodeTexImage) and g_node.image:
                        img = g_node.image
                        if img.name in processed_images:
                            continue
                        processed_images.add(img.name)
                        try:
                            tex_name = texture_stem(img)
                            if tex_name in stems and stems[tex_name] != img:
                                raise ValueError(f'Different images resolve to {tex_name!r}; give them distinct texture names')
                            stems[tex_name] = img
                            if as_png:
                                output_path = os.path.join(images_dir, tex_name + ".png")
                                export_image_as_png(img, output_path)
                            else:
                                output_path = os.path.join(images_dir, tex_name + ".img")
                                export_image_as_dds(img, output_path)
                            exported.append(tex_name)
                        except Exception as e:
                            failures.append(f'{img.name}: {e}')

    if failures:
        raise RuntimeError('Texture export incomplete:\n' + '\n'.join(failures))

    return exported


def export_image_as_dds(image, output_path):
    import numpy as np

    if image.packed_file and not image.is_dirty:
        data = image.packed_file.data
        if data[:4] == b'DDS ':
            with open(output_path, 'wb') as f:
                f.write(data)
            return

    if image.filepath and not image.is_dirty:
        src_path = bpy.path.abspath(image.filepath)
        if os.path.isfile(src_path):
            with open(src_path, 'rb') as f:
                header = f.read(4)
            if header == b'DDS ':
                import shutil
                if os.path.normcase(os.path.abspath(src_path)) != os.path.normcase(os.path.abspath(output_path)):
                    shutil.copy2(src_path, output_path)
                return

    width = image.size[0]
    height = image.size[1]
    if width == 0 or height == 0:
        raise ValueError(f"Image {image.name} has invalid dimensions")

    pixels = np.array(image.pixels[:], dtype=np.float32).reshape((height, width, 4))
    pixels = np.flipud(pixels)
    # Blender exposes byte-backed image.pixels in the image's stored colour
    # space. Float-backed images expose scene-linear values. Decode only byte
    # sRGB buffers before linear mip filtering, otherwise loaded PNGs receive a
    # second gamma conversion (64 becomes 137 in an 8-bit DDS).
    if image.colorspace_settings.name == 'sRGB' and not image.is_float:
        rgb = np.maximum(pixels[:, :, :3], 0)
        pixels[:, :, :3] = np.where(rgb <= 0.04045, rgb / 12.92,
                                    np.power((rgb + 0.055) / 1.055, 2.4))
    mip_data = []
    while True:
        encoded = pixels.copy()
        # Filter scene-linear pixels, then encode colour RGB for the DDS.
        if image.colorspace_settings.name == 'sRGB':
            rgb = np.maximum(encoded[:, :, :3], 0)
            encoded[:, :, :3] = np.where(rgb <= 0.0031308, rgb * 12.92,
                                         1.055 * np.power(rgb, 1 / 2.4) - 0.055)
        encoded = np.rint(np.clip(encoded, 0, 1) * 255).astype(np.uint8)
        mip_data.append(encoded[:, :, [2, 1, 0, 3]].tobytes())
        h, w = pixels.shape[:2]
        if h == 1 and w == 1:
            break
        next_h, next_w = max(1, h // 2), max(1, w // 2)
        # Sample paired source texels; replicate the sole row/column at 1px.
        y = np.minimum(np.arange(next_h) * 2, h - 1)
        x = np.minimum(np.arange(next_w) * 2, w - 1)
        pixels = (pixels[y[:, None], x] + pixels[np.minimum(y + 1, h - 1)[:, None], x]
                  + pixels[y[:, None], np.minimum(x + 1, w - 1)]
                  + pixels[np.minimum(y + 1, h - 1)[:, None], np.minimum(x + 1, w - 1)]) * .25

    dds_header = bytearray(128)
    dds_header[0:4] = b'DDS '
    dds_header[4:8] = (124).to_bytes(4, 'little')
    dds_header[8:12] = (0x1 | 0x2 | 0x4 | 0x1000 | 0x8 | 0x20000).to_bytes(4, 'little')
    dds_header[12:16] = height.to_bytes(4, 'little')
    dds_header[16:20] = width.to_bytes(4, 'little')
    dds_header[20:24] = (width * 4).to_bytes(4, 'little')
    dds_header[28:32] = len(mip_data).to_bytes(4, 'little')
    dds_header[76:80] = (32).to_bytes(4, 'little')
    dds_header[80:84] = (0x1 | 0x40).to_bytes(4, 'little')
    dds_header[88:92] = (32).to_bytes(4, 'little')
    dds_header[92:96] = (0x00FF0000).to_bytes(4, 'little')
    dds_header[96:100] = (0x0000FF00).to_bytes(4, 'little')
    dds_header[100:104] = (0x000000FF).to_bytes(4, 'little')
    dds_header[104:108] = (0xFF000000).to_bytes(4, 'little')
    dds_header[108:112] = (0x1000 | (0x400008 if len(mip_data) > 1 else 0)).to_bytes(4, 'little')

    with open(output_path, 'wb') as f:
        f.write(dds_header)
        for level in mip_data:
            f.write(level)


def export_image_as_png(image, output_path):
    original_path = image.filepath_raw
    original_format = image.file_format
    try:
        image.filepath_raw = output_path
        image.file_format = 'PNG'
        image.save()
    finally:
        image.filepath_raw = original_path
        image.file_format = original_format
