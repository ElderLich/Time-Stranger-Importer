"""Import New Century models and cinematics and export native movesets."""

import math
import os
from pathlib import Path
import re

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper
from mathutils import Vector

from ..animation import single_strip
from ..blender_compat import iter_action_fcurves, scene_fps

from .animation import (
    _build_rest_pose_anim_file,
    _export_action_as_anim,
    build_basic_hundred_line_anim_from_action,
    fetch_armatures,
)
from .context import (
    _DEFAULT_EXTRACTED_ROOT,
    _active_geom_collection,
    _normalize_chr_stem,
    _resolve_donor_geom_path,
    _scene_extracted_root,
    _target_chr_from_context,
)


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
        from ..nc_glb import import_nc_model
        from ..scene_state import snapshot, rollback

        before, old_scene, old_scenes = snapshot(), context.window.scene, set(bpy.data.scenes)
        old_fps = (old_scene.render.fps, old_scene.render.fps_base)
        old_model = old_scene.dsts_nc_model_path
        old_active, old_selected = context.view_layer.objects.active, list(context.selected_objects)
        try:
            if self.import_related_skills:
                from ..nc_skill_ui import catalog, primary_phases, import_related_skills
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


def _build_sequence_action_from_tracks(armature_obj, name, tracks):
    import math
    from ..animation import _write_curve, legacy_native_locations, location_to_native_matrix
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
        from ..cinematic import import_preview
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


class DSTS_OT_NativeSkillSequence(Operator, ImportHelper):
    """Build a native actor motion and optional PC/EM cameras from an ordered JSON phase recipe"""
    bl_idname = 'export_scene.dsts_native_skill_sequence'
    bl_label = 'Build Native Skill Sequence'
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})
    output_directory: StringProperty(name='New Output Folder', subtype='DIR_PATH')

    def execute(self, context):
        from ..nc_skill_sequence import export_sequence
        try:
            if not self.output_directory:
                raise ValueError('Select a new output folder')
            result = export_sequence(bpy.path.abspath(self.filepath), bpy.path.abspath(self.output_directory))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Built {result['stem']} with {result['frames']} frames")
        return {'FINISHED'}
