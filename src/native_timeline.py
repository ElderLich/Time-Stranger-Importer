"""Native effect BSON timeline controls backed by the shared Formats codec."""

import base64
import hashlib
import json
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ImportHelper, ExportHelper
from mathutils import Matrix

from .effect_bson import EffectDocument

_SOURCE = 'DSTS_bson_source'
_PREFIX = 'DSTS event '


def controller(context):
    active = context.object
    if active and _SOURCE in active:
        return active
    candidates = [obj for obj in context.scene.objects if _SOURCE in obj]
    return candidates[0] if len(candidates) == 1 else None


def _scene(obj):
    scenes = [scene for scene in bpy.data.scenes if obj.name in scene.objects]
    if len(scenes) != 1:
        raise ValueError('Native timeline controller must belong to exactly one scene')
    return scenes[0]


def _source(obj):
    raw = base64.b64decode(obj[_SOURCE], validate=True)
    if hashlib.sha256(raw).hexdigest() != obj['DSTS_bson_sha256']:
        raise ValueError('Stored BSON source hash mismatch; reimport the effect')
    return EffectDocument.from_bytes(raw)


def _value(field):
    if field.value_type in {'integer', 'boolean'}:
        return field.value_integer
    if field.value_type == 'float':
        return field.value_float
    return field.value_string


def _markers(obj):
    return {f'{_PREFIX}{index:03d}: {field.name}': field for index, field in enumerate(obj.dsts_effect_fields)
            if field.units == 'frames (30 Hz)' and field.value_integer >= 0 and
            json.loads(field.path_json)[-1] not in {'se_fadeout'}}


def refresh_markers(obj):
    scene = _scene(obj)
    if scene.render.fps / scene.render.fps_base != 30:
        raise ValueError('Native effect timing uses 30 Hz; restore this scene to 30 fps before editing events')
    values = _markers(obj)
    if any(field.value_integer >= 1048574 for field in values.values()):
        raise ValueError('The native event exceeds Blender timeline limits; its value is preserved but cannot be previewed')
    for marker in list(scene.timeline_markers):
        if marker.name.startswith(_PREFIX):
            scene.timeline_markers.remove(marker)
    for name, field in values.items():
        scene.timeline_markers.new(name, frame=field.value_integer + 1)
    obj['DSTS_bson_marker_snapshot'] = json.dumps({name: field.value_integer + 1 for name, field in values.items()})
    scene.frame_start = 1
    scene.frame_end = max([1] + [field.value_integer + 1 for field in values.values()])
    obj['DSTS_bson_preview_error'] = ''


def _field_update(field, context):
    obj = field.id_data
    if obj.get('DSTS_bson_ready'):
        try:
            scene = _scene(obj)
            actual = {marker.name: marker.frame for marker in scene.timeline_markers if marker.name.startswith(_PREFIX)}
            snapshot = json.loads(obj.get('DSTS_bson_marker_snapshot', '{}'))
            if actual != snapshot:
                raise ValueError('Timeline markers have pending edits; apply them or refresh the timeline before export')
            refresh_markers(obj)
            obj['DSTS_bson_preview_error'] = ''
        except ValueError as exc:
            obj['DSTS_bson_preview_error'] = str(exc)


class DSTS_PG_EffectField(PropertyGroup):
    path_json: StringProperty(options={'HIDDEN'})
    value_type: StringProperty(options={'HIDDEN'})
    units: StringProperty(options={'HIDDEN'})
    value_integer: IntProperty(name='Value', update=_field_update)
    value_float: FloatProperty(name='Value', update=_field_update)
    value_string: StringProperty(name='Value', update=_field_update)
    resource_path: StringProperty(options={'HIDDEN'})
    resource_hash: StringProperty(options={'HIDDEN'})
    resource_nlst_hash: StringProperty(options={'HIDDEN'})


def import_timeline(context, filepath):
    path = Path(filepath).resolve()
    raw = path.read_bytes()
    doc = EffectDocument.from_bytes(raw)
    fields, sets = doc.fields, doc.sets
    if not fields:
        raise ValueError('This BSON has no supported effect duration/hit/sound/drawable fields')
    from .scene_state import snapshot, rollback
    before, old_scene = snapshot(), context.window.scene
    scene = bpy.data.scenes.new(f'Native Effect · {path.stem}')
    try:
        context.window.scene = scene
        scene.render.fps, scene.render.fps_base = 30, 1
        obj = bpy.data.objects.new(f'Effect timeline · {path.stem}', None)
        scene.collection.objects.link(obj)
        obj.hide_render = True
        obj.empty_display_type = 'PLAIN_AXES'
        obj.lock_location = obj.lock_rotation = obj.lock_scale = (True,) * 3
        obj[_SOURCE] = base64.b64encode(raw).decode('ascii')
        obj['DSTS_bson_path'] = str(path)
        obj['DSTS_bson_sha256'] = doc.source_sha256
        obj['DSTS_bson_ready'] = False
        obj['DSTS_bson_sets'] = json.dumps([dict(name=item.name, id=item.id, drawables=item.drawables,
            sound=item.sound_name, owner=item.attachment_owner, position_socket=item.position_socket,
            rotation_socket=item.rotation_socket, scale_socket=item.scale_socket) for item in sets])
        for source in fields:
            field = obj.dsts_effect_fields.add()
            owner = source.path[0] + ' · ' if source.path[0].startswith('set') else ''
            field.name = owner + source.label
            field.path_json = json.dumps(source.path)
            field.value_type, field.units = source.value_type, source.units
            if source.value_type in {'integer', 'boolean'}:
                field.value_integer = source.value
            elif source.value_type == 'float':
                field.value_float = source.value
            else:
                field.value_string = source.value
        obj['DSTS_bson_ready'] = True
        refresh_markers(obj)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        scene.frame_set(1)
        return obj
    except Exception:
        context.window.scene = old_scene
        bpy.data.scenes.remove(scene)
        rollback(before)
        raise


def document_from_controller(obj, *, check_markers=True):
    doc = _source(obj)
    originals = doc.fields
    fields = list(obj.dsts_effect_fields)
    if [tuple(json.loads(field.path_json)) for field in fields] != [field.path for field in originals]:
        raise ValueError('Native event creation, deletion and reordering are not supported yet')
    if obj.parent or obj.constraints or obj.animation_data or obj.matrix_basis != Matrix.Identity(4):
        raise ValueError('Edit native timeline fields and markers; object transforms/constraints are not native events')
    for field, source in zip(fields, originals):
        if (field.value_type, field.units) != (source.value_type, source.units):
            raise ValueError('Changing native field types is not supported')
        value = _value(field)
        if value == source.value:
            continue
        if field.units == 'cue ID':
            raise ValueError('Changing sound identity requires validated sound-bank mapping; existing cue timing and cleanup are editable')
        if field.units == 'native basename':
            path = Path(field.resource_path)
            if path.suffix.lower() != '.geom' or path.stem != value:
                raise ValueError('Use Select Drawable Geometry to validate a replacement resource')
            if (hashlib.sha256(path.read_bytes()).hexdigest() != field.resource_hash or
                    hashlib.sha256(path.with_suffix('.nlst').read_bytes()).hexdigest() != field.resource_nlst_hash):
                raise ValueError('Selected drawable geometry/name list changed; select it again')
        doc.set_field(source.path, value)
    if check_markers:
        scene = _scene(obj)
        expected = _markers(obj)
        actual = {marker.name: marker for marker in scene.timeline_markers if marker.name.startswith(_PREFIX)}
        if set(actual) != set(expected) or any(actual[name].frame != field.value_integer + 1 for name, field in expected.items()):
            raise ValueError('Timeline markers changed; use Apply Marker Timing or Refresh Timeline before export')
        if scene.render.fps / scene.render.fps_base != 30:
            raise ValueError('Native event scenes must use 30 fps')
    return doc


def apply_marker_timing(obj):
    scene = _scene(obj)
    if scene.render.fps / scene.render.fps_base != 30:
        raise ValueError('Native event scenes must use 30 fps')
    expected = _markers(obj)
    actual = {marker.name: marker.frame - 1 for marker in scene.timeline_markers if marker.name.startswith(_PREFIX)}
    if set(actual) != set(expected):
        raise ValueError('Event creation/deletion through markers is not supported; refresh the timeline')
    staged = document_from_controller(obj, check_markers=False)
    snapshot = json.loads(obj.get('DSTS_bson_marker_snapshot', '{}'))
    changes = {name: frame for name, frame in actual.items() if frame + 1 != snapshot.get(name)}
    for name, frame in changes.items():
        if expected[name].value_integer + 1 != snapshot.get(name) and expected[name].value_integer != frame:
            raise ValueError('The same event was edited in both the field and timeline; refresh to keep fields or restore the field before applying markers')
        staged.set_field(tuple(json.loads(expected[name].path_json)), frame)
    obj['DSTS_bson_ready'] = False
    try:
        for name, frame in changes.items():
            expected[name].value_integer = frame
    finally:
        obj['DSTS_bson_ready'] = True
    refresh_markers(obj)


def export_timeline(obj, filepath, *, overwrite=False):
    from .source_edit import publish_document
    return publish_document(document_from_controller(obj), obj, filepath,
        source_path=obj['DSTS_bson_path'], source_hash=obj['DSTS_bson_sha256'], overwrite=overwrite)


class DSTS_OT_EffectTimelineImport(Operator, ImportHelper):
    bl_idname = 'import_scene.dsts_effect_timeline'
    bl_label = 'Native Effect Timeline (.bson)'
    bl_description = 'Import native effect duration, hit and sound timing into an editable 30 fps timeline; particle and audio playback remain unsupported'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.bson'
    filter_glob: StringProperty(default='*.bson', options={'HIDDEN'})

    def execute(self, context):
        try:
            obj = import_timeline(context, self.filepath)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Imported {len(obj.dsts_effect_fields)} editable/inspectable fields; scene markers use 30 Hz')
        return {'FINISHED'}


class DSTS_OT_EffectTimelineExport(Operator, ExportHelper):
    bl_idname = 'export_scene.dsts_effect_timeline'
    bl_label = 'Export Native Effect Timeline'
    bl_description = 'Validate and export edited native effect fields while preserving the remaining BSON data'
    filename_ext = '.bson'
    filter_glob: StringProperty(default='*.bson', options={'HIDDEN'})
    overwrite: BoolProperty(name='Replace Existing File', default=False)

    def execute(self, context):
        try:
            obj = controller(context)
            if obj is None:
                raise ValueError('Select an imported native effect timeline controller')
            export_timeline(obj, self.filepath, overwrite=self.overwrite)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Exported native timeline; full dependencies and in-game behavior still require validation')
        return {'FINISHED'}


class DSTS_OT_EffectTimelineRefresh(Operator):
    bl_idname = 'dsts.effect_timeline_refresh'
    bl_label = 'Refresh Timeline'
    bl_description = 'Rebuild timeline markers from the native field values, discarding unapplied marker moves'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            refresh_markers(controller(context))
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_EffectTimelineApply(Operator):
    bl_idname = 'dsts.effect_timeline_apply'
    bl_label = 'Apply Marker Timing'
    bl_description = 'Validate moved timeline markers and apply their times to the corresponding native effect fields'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            apply_marker_timing(controller(context))
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_EffectDrawableSelect(Operator, ImportHelper):
    bl_idname = 'dsts.effect_drawable_select'
    bl_label = 'Select Drawable Geometry'
    bl_description = 'Validate a GEOM and its matching NLST, then use its native resource name for the selected effect drawable'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.geom'
    filter_glob: StringProperty(default='*.geom', options={'HIDDEN'})

    def execute(self, context):
        try:
            obj = controller(context)
            field = obj.dsts_effect_fields[obj.dsts_effect_field_index]
            if field.units != 'native basename':
                raise ValueError('Select a drawable resource field first')
            path = Path(self.filepath).resolve()
            raw, names = path.read_bytes(), path.with_suffix('.nlst').read_bytes()
            from .backend import load_format_modules
            from .model_package import validate_matrix_alignment, validate_runtime_skinning
            from .nlst import matches_geom
            backend, *_ = load_format_modules()
            old_throw, old_errors = backend.get_throw_errors(), backend.get_error_list()
            try:
                backend.set_throw_errors(True)
                backend.set_error_list([])
                geom = backend.Geom.from_bytes(raw)
            finally:
                backend.set_throw_errors(old_throw)
                backend.set_error_list(old_errors)
            validate_matrix_alignment(raw, len(geom.skeleton.bones))
            validate_runtime_skinning(raw, geom)
            if not matches_geom(geom, names.decode('utf-8-sig')):
                raise ValueError('Selected GEOM and NLST do not describe the same resource')
            _source(obj).set_field(tuple(json.loads(field.path_json)), path.stem)
            field.resource_path = str(path)
            field.resource_hash = hashlib.sha256(raw).hexdigest()
            field.resource_nlst_hash = hashlib.sha256(names).hexdigest()
            field.value_string = path.stem
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Validated geometry reference; complete effect dependency packaging remains required')
        return {'FINISHED'}


class DSTS_UL_EffectFields(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name)


class DSTS_PT_EffectTimeline(Panel):
    bl_idname = 'DSTS_PT_effect_timeline'
    bl_label = 'Native Effect Timeline'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'DSTS'

    @classmethod
    def poll(cls, context):
        return controller(context) is not None

    def draw(self, context):
        obj, layout = controller(context), self.layout
        layout.label(text='Native event clock: 30 Hz')
        layout.label(text='Event timing preview; particles/audio are not played.', icon='INFO')
        layout.template_list('DSTS_UL_EffectFields', '', obj, 'dsts_effect_fields', obj, 'dsts_effect_field_index', rows=8)
        if 0 <= obj.dsts_effect_field_index < len(obj.dsts_effect_fields):
            field = obj.dsts_effect_fields[obj.dsts_effect_field_index]
            box = layout.box()
            box.label(text=field.name)
            if field.value_type == 'string':
                box.label(text=field.value_string or '(empty)')
                if field.units == 'native basename':
                    box.operator(DSTS_OT_EffectDrawableSelect.bl_idname)
                else:
                    box.label(text='Cue identity requires validated bank mapping.')
            else:
                box.prop(field, 'value_float' if field.value_type == 'float' else 'value_integer', text=field.units or '0 / 1')
        row = layout.row(align=True)
        row.operator(DSTS_OT_EffectTimelineApply.bl_idname)
        row.operator(DSTS_OT_EffectTimelineRefresh.bl_idname)
        if obj.get('DSTS_bson_preview_error'):
            layout.label(text=obj['DSTS_bson_preview_error'], icon='ERROR')
        layout.operator(DSTS_OT_EffectTimelineExport.bl_idname, icon='EXPORT')


CLASSES = (DSTS_PG_EffectField, DSTS_OT_EffectTimelineImport, DSTS_OT_EffectTimelineExport,
           DSTS_OT_EffectTimelineRefresh, DSTS_OT_EffectTimelineApply, DSTS_OT_EffectDrawableSelect,
           DSTS_UL_EffectFields, DSTS_PT_EffectTimeline)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.dsts_effect_fields = CollectionProperty(type=DSTS_PG_EffectField)
    bpy.types.Object.dsts_effect_field_index = IntProperty(default=0)


def unregister():
    del bpy.types.Object.dsts_effect_fields
    del bpy.types.Object.dsts_effect_field_index
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
