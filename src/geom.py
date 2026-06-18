import bpy
import os
import re
import base64
import glob
from .. import data
from pathlib import Path
from .backend import load_format_modules
from .blender_compat import create_action_fcurve, create_nla_track
from .anim.InterfaceHundredLine import AnimFileHundredLine
from .anim.BinaryHundredLine import AnimFileBinary as AnimFileBinaryHundredLine


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
    return dirs


def import_geom(context, filepath, images_folder=None):
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

        skeleton_result = skeleton.import_skeleton(geom.skeleton, new_collection, utils.unflop)
        if isinstance(skeleton_result, tuple):
            armature_obj, bone_name_map = skeleton_result
        else:
            armature_obj = skeleton_result
            bone_name_map = {}

        texture_search_dirs = [str(p) for p in _texture_search_dirs(Path(filepath), images_folder)]
        new_collection["DSTS_texture_search_dirs"] = "\n".join(texture_search_dirs)

        blender_materials = {}
        for mat_data in geom.materials:
            mat = bpy.data.materials.new(name=mat_data.name)
            mat_data.name = mat.name
            material.resolve_material(new_collection, mat, mat_data, texture_search_dirs)
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
            g_node = next(n for n in mat.node_tree.nodes if n.type == "GROUP" and n.node_tree.name.startswith(f"DSTS_Data-{mat.name}"))
            data_node = next(n for n in g_node.node_tree.nodes if type(n) == data.material_nodes.ShaderDataNode)
            if len(data_node.attributes) == 0:
                data_node.bytes_per_vertex = mesh_obj.bytes_per_vertex
                for attr in attr_list:
                    at = data_node.attributes.add()
                    at.count, at.offset, at.atype, at.dtype = attr.values()

        error_list = dsts_formats.get_error_list()
        error_list = [e for e in error_list if "float channels" not in e.lower() and "ibpm does not match" not in e.lower()]
        if error_list:
            bpy.ops.wm.show_errors_window('INVOKE_DEFAULT', errors="\n\n".join(error_list))

        import_animations_for_geom(filepath, armature_obj)
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


def export_geom(collection):
    dsts_formats, material, mesh, skeleton, _utils = load_format_modules()

    missing_keys = [k for k in ("unknown_0x10", "unknown_0x30", "unknown_0x34", "CLUT") if k not in collection]
    if missing_keys:
        raise RuntimeError(
            f"Collection is missing DSTS import metadata: {', '.join(missing_keys)}. "
            "Only natively imported .geom collections can be exported."
        )

    geom = dsts_formats.Geom()
    geom.unknown_0x10 = collection["unknown_0x10"]
    geom.unknown_0x30 = collection["unknown_0x30"]
    geom.unknown_0x34 = collection["unknown_0x34"]
    geom.clut = base64.b64decode(bytes(collection["CLUT"], "ascii"))

    armatures = [o for o in collection.objects if o.type == "ARMATURE"]
    if not armatures:
        raise ValueError("No armature found in collection")
    geom.skeleton = skeleton.export_skeleton(armatures[0])

    name_to_mat = {}
    for mat in {o.data.materials[0] for o in collection.objects if o.type == "MESH"}:
        mat_data = material.export_material(mat)
        geom.materials.append(mat_data)
        name_to_mat[mat_data.name] = mat_data

    for obj in [o for o in collection.objects if o.type == "MESH"]:
        mesh_out = mesh.export_mesh_object(obj, geom.skeleton)
        mat_name = obj.data.materials[0].name
        mesh_out.material = name_to_mat[mat_name]
        geom.meshes.append(mesh_out)

    for obj in [*geom.skeleton.bones] + [*geom.materials] + [*geom.meshes]:
        obj.name = re.sub(r"(\.[0-9]{3})?$", "", obj.name)

    return geom


def export_textures(collection, output_dir, as_png=False):
    import numpy as np

    images_dir = os.path.join(output_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    exported = []
    processed_images = set()

    for obj in collection.objects:
        if obj.type != "MESH" or not obj.data.materials:
            continue
        for mat in obj.data.materials:
            if not mat or not mat.use_nodes:
                continue
            for node in mat.node_tree.nodes:
                if node.type == "GROUP" and node.node_tree and node.node_tree.name.startswith("DSTS_Data-"):
                    for g_node in node.node_tree.nodes:
                        if isinstance(g_node, bpy.types.ShaderNodeTexImage) and g_node.image:
                            img = g_node.image
                            if img.name in processed_images:
                                continue
                            processed_images.add(img.name)
                            tex_name = os.path.splitext(img.name)[0]
                            try:
                                if as_png:
                                    output_path = os.path.join(images_dir, tex_name + ".png")
                                    export_image_as_png(img, output_path)
                                else:
                                    output_path = os.path.join(images_dir, tex_name + ".img")
                                    export_image_as_dds(img, output_path)
                                exported.append(tex_name)
                            except Exception as e:
                                print(f"[DSTS] Warning: Failed to export texture {tex_name}: {e}")

    return exported


def export_image_as_dds(image, output_path):
    import numpy as np

    if image.packed_file:
        data = image.packed_file.data
        if data[:4] == b'DDS ':
            with open(output_path, 'wb') as f:
                f.write(data)
            return

    if image.filepath:
        src_path = bpy.path.abspath(image.filepath)
        if os.path.isfile(src_path):
            with open(src_path, 'rb') as f:
                header = f.read(4)
            if header == b'DDS ':
                import shutil
                shutil.copy2(src_path, output_path)
                return

    width = image.size[0]
    height = image.size[1]
    if width == 0 or height == 0:
        raise ValueError(f"Image {image.name} has invalid dimensions")

    pixels = np.array(image.pixels[:], dtype=np.float32).reshape((height, width, 4))
    pixels = np.flipud(pixels)
    pixels_8bit = (pixels * 255).astype(np.uint8)
    pixels_bgra = pixels_8bit.copy()
    pixels_bgra[:, :, 0] = pixels_8bit[:, :, 2]
    pixels_bgra[:, :, 2] = pixels_8bit[:, :, 0]

    dds_header = bytearray(128)
    dds_header[0:4] = b'DDS '
    dds_header[4:8] = (124).to_bytes(4, 'little')
    dds_header[8:12] = (0x1 | 0x2 | 0x4 | 0x1000 | 0x8).to_bytes(4, 'little')
    dds_header[12:16] = height.to_bytes(4, 'little')
    dds_header[16:20] = width.to_bytes(4, 'little')
    dds_header[20:24] = (width * 4).to_bytes(4, 'little')
    dds_header[28:32] = (1).to_bytes(4, 'little')
    dds_header[76:80] = (32).to_bytes(4, 'little')
    dds_header[80:84] = (0x1 | 0x40).to_bytes(4, 'little')
    dds_header[88:92] = (32).to_bytes(4, 'little')
    dds_header[92:96] = (0x00FF0000).to_bytes(4, 'little')
    dds_header[96:100] = (0x0000FF00).to_bytes(4, 'little')
    dds_header[100:104] = (0x000000FF).to_bytes(4, 'little')
    dds_header[104:108] = (0xFF000000).to_bytes(4, 'little')
    dds_header[108:112] = (0x1000).to_bytes(4, 'little')

    with open(output_path, 'wb') as f:
        f.write(dds_header)
        f.write(pixels_bgra.tobytes())


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
