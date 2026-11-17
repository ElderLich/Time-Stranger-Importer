"""Blender entry point for the Time Stranger Importer add-on."""

bl_info = {
    "name": "Time Stranger Importer",
    "author": "Senator John (ElderLich)",
    "version": (1, 5, 8),
    "blender": (5, 0, 0),
    "category": "Import-Export",
    "description": "Import/Export .geom and .anim files for Digimon Time Stranger",
    "location": "File > Import/Export",
}

# Existing Blender scripts import these operators and helpers directly from the add-on.
from .src.animation import animation_path, single_strip, validate_strip
from .src.blender_compat import create_action_fcurve, create_nla_track, iter_action_fcurves, scene_fps
from .src.operators.context import (
    _DEFAULT_EXTRACTED_ROOT,
    _GEOM_METADATA_KEYS,
    _active_geom_collection,
    _chr_stem_from_text,
    _copy_geom_metadata_to_collection,
    _ensure_collection_export_metadata,
    _exportable_geom_collections,
    _find_geom_workflow_armature,
    _find_workflow_armature,
    _geom_collection_items,
    _geom_meshes,
    _imported_geom_collections,
    _normalize_chr_stem,
    _resolve_donor_geom_path,
    _scene_extracted_root,
    _target_chr_from_context,
    _target_meshes,
)
from .src.operators.rig import (
    DSTS_OT_ApplyArmatureObjectTransforms,
    DSTS_OT_ApplyMeshObjectTransforms,
    DSTS_OT_ApplyPoseAsRest,
    DSTS_OT_ClearExportPose,
    DSTS_OT_PrepareSelectedAsGeom,
    DSTS_OT_RenamePrimaryUvsToUv1,
    _IDENTITY_4X4,
    _apply_armature_object_transforms,
    _apply_mesh_object_transforms,
    _apply_pose_as_rest,
    _clear_armature_pose,
    _ensure_object_mode,
    _link_selected_to_collection,
    _matrix_is_identity,
    _objects_with_unapplied_transforms,
    _posed_bones,
    _readiness_report,
    _rename_primary_uvs_to_uv1,
    _write_readiness_text,
)
from .src.operators.materials import (
    DSTS_OT_BorrowMaterialSetup,
    DSTS_OT_RetargetMaterialTextures,
    _DSTS_MESH_FLAG_DEFAULTS,
    _apply_preferred_body_atlas,
    _copy_mesh_flags_from_donor,
    _default_texture_stem_for_uniform,
    _find_texture_path,
    _iter_material_texture_nodes,
    _iter_texture_nodes_from_tree,
    _load_texture_image,
    _normalize_body_atlas,
    _set_material_vertex_layout_from_mesh,
    _set_texture_colorspace,
    _strip_texture_extension,
    _texture_stem_from_node,
    _texture_stems_for_uniform,
    _texture_uniform_from_node,
)
from .src.operators.nla import (
    DSTS_OT_CleanActionObjectTransforms,
    DSTS_OT_SetupBaseAnimationNla,
    _ACTION_BONE_RE,
    _action_has_object_transform_curves,
    _action_has_pose_curves,
    _clean_action_object_transforms,
    _clear_fcurve_to_constant,
    _configure_workflow_nla_track,
    _find_action_by_name,
    _find_base_action,
    _move_nla_track_to_index,
    _nla_track_index,
    _remove_nla_tracks_named,
    _seed_rest_pose_into_action,
)
from .src.operators.animation import (
    DSTS_OT_AnimExport,
    DSTS_OT_AnimExportAll,
    DSTS_OT_AnimImport,
    DSTS_OT_ImportDonorAnimations,
    _build_anim_action_and_nla,
    _build_anim_action_from_data,
    _build_rest_pose_anim_file,
    _compose_object_motion_onto_root,
    _export_action_as_anim,
    _read_anim_file,
    build_basic_hundred_line_anim_from_action,
    fetch_armatures,
    fetch_nla_tracks,
)
from .src.operators.geometry import (
    DSTS_OT_ExportAll,
    DSTS_OT_GeomExport,
    DSTS_OT_GeomImport,
    DSTS_OT_PackageNativeModel,
    export_all_collection,
)
from .src.operators.new_century import (
    DSTS_OT_CinematicImport,
    DSTS_OT_ExportNcMoveset,
    DSTS_OT_NativeSkillSequence,
    DSTS_OT_NcGlbImport,
    _NC_MOVESET_CODE_ORDER,
    _NC_MOVESET_MAP,
    _NC_MOVESET_SEQUENCE_MAP,
    _SEQUENCE_OBJECT_TRANSFORM_PATHS,
    _SKIP_TS_MOVESET_CODES,
    _TS_MOVESET_CODES,
    _build_sequence_action_from_tracks,
    _candidate_track_names_for_code,
    _find_track_by_names,
    _track_name_moveset_parts,
    _track_name_to_moveset_code,
    _try_export_sequence,
)
from .src.operators.ui import (
    DSTS_MT_CyberSleuthImport,
    DSTS_MT_ExportSubmenu,
    DSTS_MT_ImportSubmenu,
    DSTS_MT_NewCenturyImport,
    DSTS_OT_ShowErrors,
    DSTS_PT_WorkflowPanel,
    _order_digimon_import_menu,
    menu_func_export,
    menu_func_import,
)
from .src.operators.registration import CLASSES, register, unregister


if __name__ == "__main__":
    register()
