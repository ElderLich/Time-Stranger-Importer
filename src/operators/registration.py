"""Register Blender operators, menus, scene properties and handlers."""

import bpy
from bpy.props import BoolProperty, StringProperty

from .animation import (
    DSTS_OT_AnimExport,
    DSTS_OT_AnimExportAll,
    DSTS_OT_AnimImport,
    DSTS_OT_ImportDonorAnimations,
)
from .context import _DEFAULT_EXTRACTED_ROOT
from .geometry import (
    DSTS_OT_ExportAll,
    DSTS_OT_GeomExport,
    DSTS_OT_GeomImport,
    DSTS_OT_PackageNativeModel,
)
from .materials import DSTS_OT_BorrowMaterialSetup, DSTS_OT_RetargetMaterialTextures
from .new_century import (
    DSTS_OT_CinematicImport,
    DSTS_OT_ExportNcMoveset,
    DSTS_OT_NativeSkillSequence,
    DSTS_OT_NcGlbImport,
)
from .nla import DSTS_OT_CleanActionObjectTransforms, DSTS_OT_SetupBaseAnimationNla
from .rig import (
    DSTS_OT_ApplyArmatureObjectTransforms,
    DSTS_OT_ApplyMeshObjectTransforms,
    DSTS_OT_ApplyPoseAsRest,
    DSTS_OT_ClearExportPose,
    DSTS_OT_PrepareSelectedAsGeom,
    DSTS_OT_RenamePrimaryUvsToUv1,
)
from .ui import (
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
    DSTS_OT_NativeSkillSequence,
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
    from .. import native_effects, nc_skill_ui, nc_native_skill_ui, native_timeline, native_collision, native_lights, native_scene, cyber_sleuth, texture_animation
    native_effects.register()
    nc_skill_ui.register()
    nc_native_skill_ui.register()
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
    from ...data import material_nodes
    material_nodes.register()
    texture_animation.register_handlers()


def unregister():
    from .. import texture_animation
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
    from ...data import material_nodes
    material_nodes.unregister()
    from .. import native_effects, nc_skill_ui, nc_native_skill_ui, native_timeline, native_collision, native_lights, native_scene, cyber_sleuth
    for cls in reversed(cyber_sleuth.CLASSES):
        bpy.utils.unregister_class(cls)
    for cls in reversed(native_scene.CLASSES):
        bpy.utils.unregister_class(cls)
    native_lights.unregister_properties()
    for cls in reversed(native_lights.CLASSES):
        bpy.utils.unregister_class(cls)
    native_collision.unregister()
    native_timeline.unregister()
    nc_native_skill_ui.unregister()
    nc_skill_ui.unregister()
    native_effects.unregister()
