"""Find the active collection, armature, donor and character paths."""

import base64
import os
import re

import bpy


_GEOM_METADATA_KEYS = ("unknown_0x10", "unknown_0x30", "unknown_0x34", "CLUT")
_DEFAULT_EXTRACTED_ROOT = r"D:\Digimon Modding\Time Stranger Extracted"


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


def _ensure_collection_export_metadata(collection):
    if not collection or all(key in collection for key in _GEOM_METADATA_KEYS):
        return False

    donor_path = collection.get("DSTS_material_donor_geom", "") or collection.get("DSTS_source_geom", "")
    donor_path = bpy.path.abspath(str(donor_path or ""))
    if not donor_path or not os.path.isfile(donor_path):
        return False

    from ..backend import load_format_modules
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
