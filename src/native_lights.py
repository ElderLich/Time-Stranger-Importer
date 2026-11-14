"""Existing native GEOM light fields, with an explicitly approximate point preview."""
import base64
import hashlib
import json
import math
from pathlib import Path
import struct

import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Quaternion, Vector

from .backend import load_format_modules
from .spark import atomic_write
from .source_edit import publish_document

_SOURCE = 'DSTS_light_source'
_BASIS = Matrix.Rotation(math.pi / 2, 4, 'X')


def collection_for(obj):
    return next((col for col in obj.users_collection if _SOURCE in col), None) if obj else None


def _read(raw):
    backend = load_format_modules()[0]
    old_throw, old_errors = backend.get_throw_errors(), backend.get_error_list()
    try:
        # Preserve shipped semantic discrepancies; structural validation and
        # unsupported controllers still fail in the maintained reader.
        backend.set_throw_errors(False)
        backend.set_error_list([])
        geom = backend.Geom.from_bytes(raw)
    finally:
        backend.set_throw_errors(old_throw)
        backend.set_error_list(old_errors)
    if not geom.lights:
        raise ValueError('This GEOM has no native light records')
    offset = struct.unpack_from('<Q', raw, 0x58)[0]
    if offset < 0xB0 or offset + 240 * len(geom.lights) > len(raw):
        raise ValueError('Invalid native light record bounds')
    return geom, offset


def _snapshot(collection):
    raw = base64.b64decode(collection[_SOURCE], validate=True)
    if hashlib.sha256(raw).hexdigest() != collection['DSTS_light_sha256']:
        raise ValueError('Stored light source changed; reimport the GEOM')
    return raw


def _matrix_values(matrix):
    return [float(value) for row in matrix for value in row]


def _preview_state(data):
    state = {}
    for prop in data.bl_rna.properties:
        if (prop.is_readonly or prop.identifier in {'name', 'use_fake_user', 'tag'} or
                prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'ENUM', 'STRING'}):
            continue
        value = getattr(data, prop.identifier)
        state[prop.identifier] = list(value) if getattr(prop, 'is_array', False) else value
    return state


def _preview_changed(obj):
    expected = json.loads(obj['DSTS_light_preview_snapshot'])
    actual = _preview_state(obj.data)
    # Compare the saved properties across Blender versions; newly introduced
    # RNA properties are not mistaken for edits to an older .blend.
    return any(key not in actual or actual[key] != value for key, value in expected.items())


def _refresh(obj):
    if obj.type != 'LIGHT':
        return
    if 'DSTS_light_preview_snapshot' in obj and _preview_changed(obj):
        raise ValueError('Restore the Blender light settings; edit Native Light fields for export')
    fields = obj.dsts_native_light
    color, intensity, radius = tuple(fields.color), fields.intensity, fields.range
    if not all(math.isfinite(v) and v >= 0 for v in (*color, intensity, radius)):
        raise ValueError('Native light fields must be finite and nonnegative')
    # Relative appearance only: native gain has no established watt mapping.
    peak = max(1.0, *color)
    obj.data.color = tuple(v / peak for v in color)
    obj.data.energy = intensity * peak if radius > 0 else 0.0
    obj.data.use_custom_distance = True
    obj.data.cutoff_distance = max(0.0001, radius * obj['DSTS_light_range_scale'])
    obj.data.shadow_soft_size = 0.0
    obj.data.use_nodes = False
    obj['DSTS_light_preview_snapshot'] = json.dumps(_preview_state(obj.data))


def _update(fields, context):
    obj = fields.id_data
    if obj.get('DSTS_light_ready'):
        try:
            _refresh(obj)
            obj['DSTS_light_preview_error'] = ''
        except (ValueError, OverflowError) as exc:
            obj['DSTS_light_preview_error'] = str(exc)


class DSTS_PG_NativeLight(PropertyGroup):
    color: FloatVectorProperty(name='Native RGB', size=3, default=(1, 1, 1), update=_update)
    intensity: FloatProperty(name='Native Intensity', default=1, update=_update)
    range: FloatProperty(name='Native Range', default=1, update=_update)


def import_lights(context, filepath):
    from .scene_state import snapshot, rollback
    path = Path(filepath).resolve()
    raw = path.read_bytes()
    geom, _ = _read(raw)
    before = snapshot()
    collection = bpy.data.collections.new(f'Native Lights · {path.stem}')
    context.scene.collection.children.link(collection)
    try:
        collection[_SOURCE] = base64.b64encode(raw).decode('ascii')
        collection['DSTS_light_sha256'] = hashlib.sha256(raw).hexdigest()
        collection['DSTS_light_path'] = str(path)
        cache = {}

        def world(bone):
            if bone in cache:
                return cache[bone]
            transform = bone.transform
            q = transform.quaternion
            local = Matrix.LocRotScale(Vector(transform.position[:3]),
                Quaternion((q[3], *q[:3])), Vector(transform.scale[:3]))
            result = world(bone.parent) @ local if bone.parent else local
            cache[bone] = result
            return result

        for index, source in enumerate(geom.lights):
            matches = [bone for bone in geom.skeleton.bones if bone.name_hash == source.name_hash]
            native_world = world(matches[0]) if len(matches) == 1 else None
            point = (source.native_type == 0 and native_world is not None and
                all(math.isfinite(value) and value >= 0 for value in (*source.color_rgb, source.intensity, source.range)))
            data = bpy.data.lights.new(f'Native Point {index}', 'POINT') if point else None
            obj = bpy.data.objects.new(f'Light {index} · type {source.native_type}', data)
            collection.objects.link(obj)
            if native_world is not None:
                # Only position is previewed; changing the bound skeleton is a
                # different export contract and is rejected by this editor.
                obj.location = (_BASIS @ native_world).to_translation()
            obj['DSTS_light_index'] = index
            obj['DSTS_light_type'] = source.native_type
            obj['DSTS_light_slot'] = source.native_slot
            obj['DSTS_light_bone_hash'] = f'{source.name_hash:08x}'
            obj['DSTS_light_ready'] = False
            obj['DSTS_light_range_scale'] = native_world.col[0].to_3d().length if native_world else 1.0
            obj['DSTS_light_matrix'] = json.dumps(_matrix_values(obj.matrix_basis))
            obj['DSTS_light_preview_kind'] = 'point' if point else 'guide'
            obj['DSTS_light_animated_binding'] = any(channel.name_hash == source.name_hash for channel in geom.skeleton.float_channels)
            if source.supports_optical_editing:
                obj.dsts_native_light.color = source.color_rgb
                obj.dsts_native_light.intensity = source.intensity
                if source.native_type == 0:
                    obj.dsts_native_light.range = source.range
            else:
                obj['DSTS_light_note'] = 'Optical fields for this native type are not decoded; record preserved.'
            obj.lock_location = obj.lock_rotation = obj.lock_scale = (True,) * 3
            obj['DSTS_light_ready'] = True
            _update(obj.dsts_native_light, context)
        context.view_layer.objects.active = collection.objects[0]
        collection.objects[0].select_set(True)
        return collection
    except Exception:
        rollback(before)
        raise


class LightDocument:
    def __init__(self, raw):
        self.raw = raw

    def write(self, path, **kwargs):
        atomic_write(path, self.raw, **kwargs)
        return hashlib.sha256(self.raw).hexdigest()


def document_from_collection(collection):
    raw = _snapshot(collection)
    geom, offset = _read(raw)
    objects = list(collection.objects)
    if (collection.children or len(objects) != len(geom.lights) or
            sorted(obj.get('DSTS_light_index', -1) for obj in objects) != list(range(len(geom.lights)))):
        raise ValueError('Adding, deleting or duplicating native lights is not supported yet')
    output = bytearray(raw)
    for obj in objects:
        index = obj['DSTS_light_index']
        source = geom.lights[index]
        if (obj.parent or obj.constraints or obj.animation_data or
                _matrix_values(obj.matrix_basis) != json.loads(obj['DSTS_light_matrix'])):
            raise ValueError('Native light placement/animation edits require the bound rig; edit optical fields only')
        if (obj['DSTS_light_type'] != source.native_type or obj['DSTS_light_slot'] != source.native_slot or
                obj['DSTS_light_bone_hash'] != f'{source.name_hash:08x}'):
            raise ValueError('Changing native light types, slots or bone bindings is not supported')
        expected_type = 'LIGHT' if obj['DSTS_light_preview_kind'] == 'point' else 'EMPTY'
        if obj.type != expected_type:
            raise ValueError('Native light preview object type changed')
        if obj.type == 'LIGHT':
            if obj.data.animation_data or _preview_changed(obj):
                raise ValueError('Edit Native Light fields; Blender light settings/animation are preview-only')
        fields = obj.dsts_native_light
        if source.supports_optical_editing:
            if tuple(fields.color) != tuple(source.color_rgb):
                source.color_rgb = tuple(fields.color)
            if fields.intensity != source.intensity:
                source.intensity = fields.intensity
            if source.native_type == 0 and fields.range != source.range:
                source.range = fields.range
            elif source.native_type != 0 and fields.range != 1:
                raise ValueError('Range editing is established only for native point lights')
        elif (tuple(fields.color), fields.intensity, fields.range) != ((1, 1, 1), 1, 1):
            raise ValueError('Optical edits for this native light type are not supported')
        output[offset + 240*index:offset + 240*(index+1)] = source.raw
    # Check the patched container; retain every non-light source byte exactly.
    _read(bytes(output))
    return LightDocument(bytes(output))


def export_lights(collection, filepath, *, overwrite=False):
    return publish_document(document_from_collection(collection), collection, filepath,
        source_path=collection['DSTS_light_path'], source_hash=collection['DSTS_light_sha256'], overwrite=overwrite)


class DSTS_OT_NativeLightsImport(Operator, ImportHelper):
    bl_idname = 'import_scene.dsts_native_lights'
    bl_label = 'Native Light Records (.geom)'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.geom'
    filter_glob: StringProperty(default='*.geom', options={'HIDDEN'})

    def execute(self, context):
        try:
            collection = import_lights(context, self.filepath)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Imported {len(collection.objects)} light records; point preview uses approximate photometry')
        return {'FINISHED'}


class DSTS_OT_NativeLightsExport(Operator, ExportHelper):
    bl_idname = 'export_scene.dsts_native_lights'
    bl_label = 'Export Native Light Edits'
    filename_ext = '.geom'
    filter_glob: StringProperty(default='*.geom', options={'HIDDEN'})
    overwrite: BoolProperty(name='Replace Existing File', default=False)

    def execute(self, context):
        try:
            collection = collection_for(context.object)
            if collection is None:
                raise ValueError('Select an imported native light record')
            export_lights(collection, self.filepath, overwrite=self.overwrite)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Exported native light fields; all other GEOM bytes retained')
        return {'FINISHED'}


class DSTS_PT_NativeLight(Panel):
    bl_label = 'Native Light'
    bl_idname = 'DSTS_PT_native_light'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'DSTS'

    @classmethod
    def poll(cls, context):
        return collection_for(context.object) is not None

    def draw(self, context):
        obj, layout = context.object, self.layout
        kind = obj['DSTS_light_type']
        layout.label(text=f'Native type {kind} · slot {obj["DSTS_light_slot"]}')
        if kind in {0, 2, 3}:
            layout.prop(obj.dsts_native_light, 'color')
            layout.prop(obj.dsts_native_light, 'intensity')
            if kind == 0:
                layout.prop(obj.dsts_native_light, 'range')
        else:
            layout.label(text='Opaque native type: inspection only', icon='INFO')
        if obj['DSTS_light_preview_kind'] == 'point':
            layout.label(text='Approximate point preview; native values retained')
        else:
            layout.label(text='Inspection guide; no optical preview')
        if obj['DSTS_light_animated_binding']:
            layout.label(text='Source animation may override static fields', icon='INFO')
        if obj.get('DSTS_light_preview_error'):
            layout.label(text=obj['DSTS_light_preview_error'], icon='ERROR')
        layout.operator('export_scene.dsts_native_lights')


CLASSES = (DSTS_PG_NativeLight, DSTS_OT_NativeLightsImport, DSTS_OT_NativeLightsExport, DSTS_PT_NativeLight)


def register_properties():
    bpy.types.Object.dsts_native_light = PointerProperty(type=DSTS_PG_NativeLight)


def unregister_properties():
    if hasattr(bpy.types.Object, 'dsts_native_light'):
        del bpy.types.Object.dsts_native_light
