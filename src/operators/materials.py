"""Borrow native material data and retarget character textures."""

import os
from pathlib import Path
import re

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator

from .context import (
    _DEFAULT_EXTRACTED_ROOT,
    _active_geom_collection,
    _copy_geom_metadata_to_collection,
    _geom_collection_items,
    _normalize_chr_stem,
    _resolve_donor_geom_path,
    _scene_extracted_root,
    _target_chr_from_context,
    _target_meshes,
)


def _set_material_vertex_layout_from_mesh(mat, mesh_data):
    if not mat or not mat.use_nodes or mesh_data is None:
        return False

    from ... import data

    from ..material import find_data_group
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
    from ..material import get_image
    return get_image(path)


def _set_texture_colorspace(image, uniform):
    if not image:
        return
    colorspace = "sRGB" if uniform in {
        "DiffuseColor",
        "ColorSampler",
        "OverlayMaskSampler",
        "LightPixelProj",
        "OverlayNormalSampler",
        "OverlayColorSampler3",
    } else "Non-Color"
    try:
        image.colorspace_settings.name = colorspace
    except TypeError:
        pass


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
            from ..backend import load_format_modules
            from ..geom import _texture_search_dirs
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

        layouts_copied = set()
        for index, donor_mesh in enumerate(donor_geom.meshes):
            mat = None
            if donor_mesh.material:
                mat = borrowed_by_name.get(donor_mesh.material.name)
            if mat is None:
                mat = borrowed_list[min(index, len(borrowed_list) - 1)]
            if mat not in layouts_copied and _set_material_vertex_layout_from_mesh(mat, donor_mesh):
                layouts_copied.add(mat)

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
                from ..mesh import preserve_draw_layout
                preserve_draw_layout(obj.data, mat, donor_mesh)
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

        self.report({'INFO'}, f"Borrowed {len(borrowed_list)} material(s) from {os.path.basename(donor_path)}; copied {len(layouts_copied)} material layout(s); copied flags to {flags_copied} mesh(es); applied to {applied} mesh(es)")
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
        from ..material import find_data_group, make_data_group_single_user
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
