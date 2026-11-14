"""Blender editing adapter for the maintained Formats SPAK/SPRK codec.

Only the verified common fields are editable. The viewport objects are transform
guides, not a substitute for the native particle/material/owner evaluator.
"""

import base64
from dataclasses import replace
import hashlib
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, IntVectorProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ImportHelper, ExportHelper


_COMPONENTS = ('translation', 'rotation', 'scale')
_STAGES = ('primary', 'secondary')
_SOURCE = 'DSTS_spark_source'
_TRANSFORM_CHANNELS = {
    **{1 + axis: f'Primary translation {name}' for axis, name in enumerate('XYZ')},
    **{4 + axis: f'Primary rotation {name} (degrees)' for axis, name in enumerate('XYZ')},
    **{7 + axis: f'Primary scale {name}' for axis, name in enumerate('XYZ')},
    **{28 + axis: f'Secondary translation {name}' for axis, name in enumerate('XYZ')},
    **{31 + axis: f'Secondary rotation {name} (degrees)' for axis, name in enumerate('XYZ')},
    **{34 + axis: f'Secondary scale {name}' for axis, name in enumerate('XYZ')},
}


def effect_collection(obj):
    if obj is None:
        return None
    return next((col for col in obj.users_collection if _SOURCE in col), None)


def _preview_update(settings, context):
    obj = settings.id_data
    if not isinstance(obj, bpy.types.Object):
        return
    # Translation guide uses the explicitly labelled minimum primary range.
    # No parent/socket/secondary/animation composition is guessed here.
    x, y, z = settings.primary_translation_low
    obj.location = (x, -z, y)


class DSTS_PG_SparkKey(PropertyGroup):
    frame: IntProperty(name='Frame', min=-32768, max=32767)
    value: FloatProperty(name='Value')
    interpolation: EnumProperty(name='Interpolation', items=(('0', 'Constant', ''), ('1', 'Linear', '')))


class DSTS_PG_SparkChannel(PropertyGroup):
    channel_id: IntProperty(options={'HIDDEN'})
    keys: CollectionProperty(type=DSTS_PG_SparkKey)
    active_key: IntProperty(default=0)


class DSTS_PG_SparkNode(PropertyGroup):
    editable: BoolProperty(default=False, options={'HIDDEN'})
    delay_range: IntVectorProperty(name='Delay range (frames)', size=2, min=-32768, max=32767)
    lifetime_range: IntVectorProperty(name='Lifetime range (frames; -1 infinite)', size=2, min=-32768, max=32767)
    channels: CollectionProperty(type=DSTS_PG_SparkChannel)
    active_channel: IntProperty(default=0)


for _stage in _STAGES:
    for _component in _COMPONENTS:
        for _bound in ('low', 'high'):
            _name = f'{_stage}_{_component}_{_bound}'
            DSTS_PG_SparkNode.__annotations__[_name] = FloatVectorProperty(
                name=f'{_stage.title()} {_component} {_bound}', size=3,
                update=_preview_update if _name == 'primary_translation_low' else None)


def import_effect(context, filepath):
    from .spark import SparkDocument
    from .scene_state import snapshot, rollback

    path = Path(filepath).resolve()
    raw = path.read_bytes()
    doc = SparkDocument.from_bytes(raw)
    before = snapshot()
    try:
        collection = bpy.data.collections.new(f'Effect · {path.stem}')
        context.scene.collection.children.link(collection)
        collection[_SOURCE] = base64.b64encode(raw).decode('ascii')
        collection['DSTS_spark_sha256'] = hashlib.sha256(raw).hexdigest()
        collection['DSTS_spark_path'] = str(path)
        collection['DSTS_spark_fps'] = doc.fps
        collection['DSTS_spark_preview'] = (
            'Primary translation minimum guides only; native particles, materials, '
            'owner/socket inheritance, secondary transforms and curves are not evaluated.')
        for node in doc.nodes.values():
            obj = bpy.data.objects.new(f'{node.id}: {node.name or "Node"}', None)
            collection.objects.link(obj)
            obj.empty_display_type = 'PLAIN_AXES'
            obj.empty_display_size = .25
            obj.hide_render = True
            obj.lock_location = (True,) * 3
            obj.lock_rotation = (True,) * 3
            obj.lock_scale = (True,) * 3
            obj['DSTS_spark_node_id'] = str(node.id)
            obj['DSTS_spark_kind'] = node.kind
            obj['DSTS_spark_children'] = ', '.join(map(str, node.children))
            obj['DSTS_spark_animation'] = str(node.animation_index)
            settings = obj.dsts_spark_node
            settings.editable = node.editable
            if node.editable:
                for stage in _STAGES:
                    for component in _COMPONENTS:
                        key = f'{stage}_{component}'
                        value = getattr(node, key)
                        setattr(settings, key + '_low', value.low)
                        setattr(settings, key + '_high', value.high)
                settings.delay_range = node.delay_range
                settings.lifetime_range = node.lifetime_range
                if node.animation_index is not None:
                    for source_channel in doc.channels(node.animation_index):
                        if source_channel.id not in _TRANSFORM_CHANNELS or not source_channel.editable:
                            continue
                        channel = settings.channels.add()
                        channel.name = _TRANSFORM_CHANNELS[source_channel.id]
                        channel.channel_id = source_channel.id
                        for source_key in source_channel.keys:
                            key = channel.keys.add()
                            key.frame, key.value = source_key.frame, source_key.value
                            key.interpolation = str(source_key.interpolation)
        return collection
    except Exception:
        rollback(before)
        raise


def document_from_collection(collection):
    from .spark import SparkDocument

    raw = base64.b64decode(collection[_SOURCE], validate=True)
    if hashlib.sha256(raw).hexdigest() != collection['DSTS_spark_sha256']:
        raise ValueError('Stored effect source hash mismatch; reimport the original SPRK')
    doc = SparkDocument.from_bytes(raw)
    source_doc = SparkDocument.from_bytes(raw)
    edited_channels = {}
    objects = list(collection.objects)
    ids = [int(obj['DSTS_spark_node_id']) for obj in objects if 'DSTS_spark_node_id' in obj]
    if len(ids) != len(set(ids)) or set(ids) != set(doc.nodes):
        raise ValueError('Effect nodes were deleted, duplicated or moved; topology edits are not supported yet')
    for obj in objects:
        if 'DSTS_spark_node_id' not in obj:
            raise ValueError('Additional objects cannot be serialized into this effect graph')
        node = doc.nodes[int(obj['DSTS_spark_node_id'])]
        if obj.get('DSTS_spark_kind') != node.kind:
            raise ValueError('Changing an effect node type is not supported yet')
        if obj.get('DSTS_spark_children') != ', '.join(map(str, node.children)) or obj.get('DSTS_spark_animation') != str(node.animation_index):
            raise ValueError('Changing effect graph references is not supported by this editor yet')
        settings = obj.dsts_spark_node
        expected = settings.primary_translation_low if node.editable else (0, 0, 0)
        position = (expected[0], -expected[2], expected[1])
        if (obj.parent is not None or tuple(obj.location) != position or obj.rotation_mode != 'XYZ' or
                any(abs(value) > 1e-7 for value in obj.rotation_euler) or
                tuple(obj.rotation_quaternion) != (1, 0, 0, 0) or obj.rotation_axis_angle[0] != 0 or
                tuple(obj.delta_rotation_quaternion) != (1, 0, 0, 0) or
                any(abs(value) > 1e-7 for value in obj.delta_location) or
                any(abs(value) > 1e-7 for value in obj.delta_rotation_euler) or
                any(abs(value - 1) > 1e-7 for value in (*obj.scale, *obj.delta_scale)) or
                obj.constraints or obj.animation_data):
            raise ValueError('Edit the Native Effect Node properties; moving, parenting or keying guide objects is not supported')
        if not node.editable:
            continue
        for stage in _STAGES:
            for component in _COMPONENTS:
                key = f'{stage}_{component}'
                value = getattr(node, key)
                low, high = tuple(getattr(settings, key + '_low')), tuple(getattr(settings, key + '_high'))
                if low != value.low or high != value.high:
                    doc.set_transform(node.id, stage=stage, component=component, low=low, high=high)
        delay, lifetime = tuple(settings.delay_range), tuple(settings.lifetime_range)
        if delay != node.delay_range or lifetime != node.lifetime_range:
            doc.set_timing(node.id, delay_range=delay, lifetime_range=lifetime)
        expected_channels = {channel.id: channel for channel in source_doc.channels(node.animation_index)
                             if channel.id in _TRANSFORM_CHANNELS and channel.editable} if node.animation_index is not None else {}
        if sorted(channel.channel_id for channel in settings.channels) != sorted(expected_channels):
            raise ValueError('Adding/removing native animation channels is not supported yet')
        for channel in settings.channels:
            original = expected_channels[channel.channel_id]
            if len(channel.keys) != len(original.keys):
                raise ValueError('Adding/removing native effect keys is not supported yet')
            keys = tuple(replace(before, frame=after.frame, value=after.value,
                                 interpolation=int(after.interpolation))
                         for before, after in zip(original.keys, channel.keys))
            if keys != original.keys:
                identity = (node.animation_index, channel.channel_id)
                if identity in edited_channels and edited_channels[identity] != keys:
                    raise ValueError('Nodes sharing an animation contain conflicting edits; keep the shared channel edits identical')
                edited_channels[identity] = keys
    for (animation, channel), keys in edited_channels.items():
        doc.set_channel_keys(animation, channel, keys)
    return doc


def export_effect(collection, filepath, *, overwrite=False):
    destination = Path(filepath).resolve()
    source = Path(collection['DSTS_spark_path']).resolve()
    expected = collection['DSTS_spark_sha256'] if destination == source else None
    document_from_collection(collection).write(destination, overwrite=overwrite, expected_sha256=expected)


class DSTS_OT_SparkImport(Operator, ImportHelper):
    """Import a native effect graph and edit verified common fields"""
    bl_idname = 'import_scene.dsts_spark'
    bl_label = 'Native Effect Graph (.sprk)'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.sprk'
    filter_glob: StringProperty(default='*.sprk', options={'HIDDEN'})

    def execute(self, context):
        try:
            col = import_effect(context, self.filepath)
            objects = list(col.objects)
            if objects:
                bpy.ops.object.select_all(action='DESELECT')
                objects[0].select_set(True)
                context.view_layer.objects.active = objects[0]
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Imported {len(col.objects)} effect nodes; viewport shows translation guides')
        return {'FINISHED'}


class DSTS_OT_SparkExport(Operator, ExportHelper):
    """Export verified common-field edits while preserving unknown bytes"""
    bl_idname = 'export_scene.dsts_spark'
    bl_label = 'Export Native Effect Graph'
    filename_ext = '.sprk'
    filter_glob: StringProperty(default='*.sprk', options={'HIDDEN'})
    collection_name: StringProperty(options={'HIDDEN'})
    overwrite: BoolProperty(name='Replace Existing File', default=False,
        description='Allow atomic replacement after complete validation')

    def invoke(self, context, event):
        col = effect_collection(context.object)
        if col:
            self.collection_name = col.name
            self.filepath = Path(col.get('DSTS_spark_path', 'edited.sprk')).stem + '_edited.sprk'
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            col = bpy.data.collections.get(self.collection_name) or effect_collection(context.object)
            if col is None:
                raise ValueError('Select a node belonging to an imported native effect graph')
            export_effect(col, self.filepath, overwrite=self.overwrite)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Exported effect graph; native rendering and game behavior require validation')
        return {'FINISHED'}


class DSTS_UL_SparkChannels(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon='FCURVE')


class DSTS_UL_SparkKeys(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'frame', text='')
        row.prop(item, 'value', text='')
        row.prop(item, 'interpolation', text='')


class DSTS_PT_SparkNode(Panel):
    bl_idname = 'DSTS_PT_spark_node'
    bl_label = 'Native Effect Node'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'DSTS'

    @classmethod
    def poll(cls, context):
        return effect_collection(context.object) is not None

    def draw(self, context):
        obj, layout = context.object, self.layout
        settings = obj.dsts_spark_node
        layout.label(text=f'Node {obj.get("DSTS_spark_node_id")} · type {obj.get("DSTS_spark_kind")}')
        layout.label(text=f'Children: {obj.get("DSTS_spark_children") or "none"}')
        layout.label(text=f'Animation: {obj.get("DSTS_spark_animation")}')
        layout.label(text='Translation guides; particles are not rendered.', icon='INFO')
        if settings.editable:
            for stage in _STAGES:
                box = layout.box()
                box.label(text=f'{stage.title()} native ranges (XYZ, degrees)')
                for component in _COMPONENTS:
                    box.prop(settings, f'{stage}_{component}_low', text=f'{component.title()} minimum')
                    box.prop(settings, f'{stage}_{component}_high', text=f'{component.title()} maximum')
            layout.prop(settings, 'delay_range')
            layout.prop(settings, 'lifetime_range')
            if settings.channels:
                layout.label(text='Native transform keys (fixed count)')
                layout.template_list('DSTS_UL_SparkChannels', '', settings, 'channels', settings, 'active_channel', rows=3)
                if 0 <= settings.active_channel < len(settings.channels):
                    channel = settings.channels[settings.active_channel]
                    box = layout.box()
                    box.label(text=channel.name)
                    box.template_list('DSTS_UL_SparkKeys', '', channel, 'keys', channel, 'active_key', rows=5)
                    box.label(text='Curves are not evaluated by translation guides.')
        else:
            layout.label(text='This node type is preserved read-only.', icon='LOCKED')
        layout.operator(DSTS_OT_SparkExport.bl_idname, icon='EXPORT')


CLASSES = (DSTS_PG_SparkKey, DSTS_PG_SparkChannel, DSTS_PG_SparkNode,
           DSTS_OT_SparkImport, DSTS_OT_SparkExport, DSTS_UL_SparkChannels,
           DSTS_UL_SparkKeys, DSTS_PT_SparkNode)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.dsts_spark_node = bpy.props.PointerProperty(type=DSTS_PG_SparkNode)


def unregister():
    del bpy.types.Object.dsts_spark_node
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
