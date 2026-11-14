"""Own native camera/light records alongside their model and animation rig."""
import base64
import hashlib
import json
import math
from pathlib import Path
import struct

import bpy
from bpy.types import Operator, Panel
from mathutils import Matrix

from .backend import load_format_modules
from .native_camera import LENS_FIELDS, patch_camera_lenses, rest_signature

_LENS_DRIVERS = {'lens': ('fov_degrees', '18 / tan(value * pi / 360)'),
                 'clip_start': ('near', 'value'), 'clip_end': ('far', 'value')}
_DERIVED_CAMERA = {'lens', 'clip_start', 'clip_end', 'angle', 'angle_x', 'angle_y'}


def _camera_data(collection, index, record):
    # Native 2CBEE0 has clip.w=-view.z; 2CDFC0 composes the bound bone.
    # Both native and Blender perspective cameras therefore look along -Z.
    if (record.projection_flag != 0 or not all(math.isfinite(v) for v in
            (record.fov_degrees, record.near, record.far)) or
            not 0 < record.fov_degrees < 179 or not 0 < record.near < record.far):
        return None
    data = bpy.data.cameras.new(f'Native Camera {index}')
    values = {'lens': 18/math.tan(math.radians(record.fov_degrees)/2),
              'clip_start': record.near, 'clip_end': record.far}
    if any(not data.bl_rna.properties[key].hard_min <= value <= data.bl_rna.properties[key].hard_max
           for key, value in values.items()):
        bpy.data.cameras.remove(data)
        return None
    data.type, data.sensor_fit, data.sensor_height = 'PERSP', 'VERTICAL', 36
    for path, (field, expression) in _LENS_DRIVERS.items():
        curve = data.driver_add(path)
        for modifier in list(curve.modifiers):
            curve.modifiers.remove(modifier)
        curve.keyframe_points.clear()
        driver = curve.driver
        driver.type, driver.expression = 'SCRIPTED', expression
        variable = driver.variables.new()
        variable.name, variable.type = 'value', 'SINGLE_PROP'
        target = variable.targets[0]
        target.id_type, target.id = 'COLLECTION', collection
        target.data_path = f'["DSTS_camera_{index}_{field}"]'
    from .native_lights import _preview_state
    data['DSTS_camera_preview_state'] = json.dumps({key: value for key, value in _preview_state(data).items()
                                                 if key not in _DERIVED_CAMERA})
    data['DSTS_camera_dof_state'] = json.dumps(_preview_state(data.dof))
    data['DSTS_camera_stereo_state'] = json.dumps(_preview_state(data.stereo))
    return data


def _validate_camera_data(collection, obj):
    from .native_lights import _preview_state
    data, index = obj.data, obj['DSTS_camera_index']
    fov, near, far = (collection[f'DSTS_camera_{index}_{name}'] for name in ('fov_degrees', 'near', 'far'))
    if not (all(math.isfinite(value) for value in (fov, near, far)) and 0 < fov < 179 and 0 < near < far):
        raise ValueError('Camera preview needs valid native vertical FOV and clipping planes')
    for path, value in (('lens', 18/math.tan(math.radians(fov)/2)), ('clip_start', near), ('clip_end', far)):
        bounds = data.bl_rna.properties[path]
        if not bounds.hard_min <= value <= bounds.hard_max:
            raise ValueError('Native lens value is outside Blender preview limits; import without scene previews to preserve/edit it')
    expected = json.loads(data['DSTS_camera_preview_state'])
    actual = _preview_state(data)
    if any(key not in actual or actual[key] != value for key, value in expected.items()):
        raise ValueError('Edit Native Scene Records lens fields; Blender camera settings are preview-only')
    for key, settings in (('dof', data.dof), ('stereo', data.stereo)):
        expected = json.loads(data[f'DSTS_camera_{key}_state'])
        actual = _preview_state(settings)
        if any(name not in actual or actual[name] != value for name, value in expected.items()):
            raise ValueError('Native camera depth-of-field and stereo editing are not implemented')
    if data.dof.focus_object is not None:
        raise ValueError('Native camera focus-object editing is not implemented')
    ad = data.animation_data
    if ad is None or ad.action or ad.nla_tracks or len(ad.drivers) != len(_LENS_DRIVERS):
        raise ValueError('Native camera preview drivers were changed; use the owning native fields')
    for curve in ad.drivers:
        if curve.data_path not in _LENS_DRIVERS or curve.array_index != 0 or curve.mute or curve.keyframe_points or curve.modifiers:
            raise ValueError(f'Native camera preview driver channels were modified ({curve.data_path}, index={curve.array_index}, mute={curve.mute}, keys={len(curve.keyframe_points)}, modifiers={len(curve.modifiers)})')
        field, expression = _LENS_DRIVERS[curve.data_path]
        driver = curve.driver
        if driver.type != 'SCRIPTED' or driver.expression != expression or driver.use_self or len(driver.variables) != 1:
            raise ValueError('Native camera preview driver expression was modified')
        var = driver.variables[0]
        target = var.targets[0]
        if (var.name != 'value' or var.type != 'SINGLE_PROP' or target.id != collection or
                target.data_path != f'["DSTS_camera_{index}_{field}"]'):
            raise ValueError('Native camera preview driver target was modified')


def _source(collection):
    raw = base64.b64decode(collection['DSTS_scene_source'], validate=True)
    if hashlib.sha256(raw).hexdigest() != collection['DSTS_scene_sha256']:
        raise ValueError('Stored native scene source changed; reimport the model')
    backend = load_format_modules()[0]
    old_throw, old_errors = backend.get_throw_errors(), backend.get_error_list()
    try:
        backend.set_throw_errors(False)
        backend.set_error_list([])
        return raw, backend.Geom.from_bytes(raw)
    finally:
        backend.set_throw_errors(old_throw)
        backend.set_error_list(old_errors)


def _bone(collection, name_hash):
    _, source = _source(collection)
    indices = [i for i, bone in enumerate(source.skeleton.bones) if bone.name_hash == name_hash]
    rig = collection['DSTS_scene_armature']
    matches = [bone for bone in rig.data.bones if bone.get('DSTS_source_bone_index') in indices]
    return matches[0].name if len(indices) == len(matches) == 1 else ''


def _bind(collection, obj, name_hash, kind):
    bone = _bone(collection, name_hash)
    obj['DSTS_scene_bone'] = bone
    obj['DSTS_scene_constraint'] = kind if bone else ''
    if not bone:
        obj['DSTS_scene_binding_note'] = 'Missing or ambiguous native bone; static inspection only'
        return
    constraint = obj.constraints.new(kind)
    constraint.name = 'Native scene bone binding'
    constraint.target = collection['DSTS_scene_armature']
    constraint.subtarget = bone
    constraint.target_space = constraint.owner_space = 'WORLD'
    constraint.head_tail = 0


def validate_preview_binding(child, obj):
    owner = child.get('DSTS_scene_owner')
    if owner is None or child.name not in owner.children:
        raise ValueError('Native scene preview was detached from its owning GEOM collection')
    rig = owner.get('DSTS_scene_armature')
    if rig is None or rig.name not in owner.objects:
        raise ValueError('Native scene preview lost its owning rig')
    hash_key = 'DSTS_light_bone_hash' if child.get('DSTS_scene_role') == 'lights' else 'DSTS_camera_hash'
    expected_bone = _bone(owner, int(obj[hash_key], 16))
    expected_kind = ('COPY_LOCATION' if child.get('DSTS_scene_role') == 'lights' else 'COPY_TRANSFORMS') if expected_bone else ''
    kind, bone = obj.get('DSTS_scene_constraint', ''), obj.get('DSTS_scene_bone', '')
    if (kind, bone) != (expected_kind, expected_bone):
        raise ValueError('Native scene binding metadata no longer matches the source record')
    if not kind:
        if obj.constraints:
            raise ValueError('Unbound native scene guides cannot acquire constraints')
        return
    if len(obj.constraints) != 1:
        raise ValueError('The native scene bone binding was removed or additional constraints were added')
    con = obj.constraints[0]
    if (con.type != kind or con.target != rig or con.subtarget != bone or
            con.target_space != 'WORLD' or con.owner_space != 'WORLD' or
            con.influence != 1 or con.mute or con.head_tail != 0):
        raise ValueError('Changing native scene preview bone bindings is not supported')
    if kind == 'COPY_LOCATION' and (con.use_offset or not all((con.use_x, con.use_y, con.use_z)) or
                                    any((con.invert_x, con.invert_y, con.invert_z))):
        raise ValueError('Changing native light location-copy settings is not supported')
    if kind == 'COPY_TRANSFORMS' and (con.mix_mode != 'REPLACE' or con.remove_target_shear):
        raise ValueError('Changing native camera transform-copy settings is not supported')


def preserve_scene_records(context, collection, armature, filepath, geom, *, preview):
    raw = Path(filepath).read_bytes()
    collection['DSTS_scene_source'] = base64.b64encode(raw).decode('ascii')
    collection['DSTS_scene_sha256'] = hashlib.sha256(raw).hexdigest()
    collection['DSTS_scene_armature'] = armature
    collection['DSTS_scene_rest'] = rest_signature(armature)
    collection['DSTS_scene_preview'] = preview
    collection['DSTS_scene_path'] = str(Path(filepath).resolve())
    collection['DSTS_scene_nlst'] = Path(filepath).with_suffix('.nlst').read_text(encoding='utf-8-sig') if Path(filepath).with_suffix('.nlst').exists() else ''
    expected = []
    if preview and geom.lights:
        from .native_lights import import_lights
        child = import_lights(context, filepath)
        context.scene.collection.children.unlink(child)
        collection.children.link(child)
        child['DSTS_scene_owner'] = collection
        child['DSTS_scene_role'] = 'lights'
        for obj in child.objects:
            record = geom.lights[obj['DSTS_light_index']]
            _bind(collection, obj, record.name_hash, 'COPY_LOCATION')
        expected.append('lights')
    if preview and geom.cameras:
        child = bpy.data.collections.new('Native Camera Bindings')
        collection.children.link(child)
        child['DSTS_scene_owner'] = collection
        child['DSTS_scene_role'] = 'cameras'
        for index, record in enumerate(geom.cameras):
            data = _camera_data(collection, index, record) if _bone(collection, record.name_hash) else None
            obj = bpy.data.objects.new(f'Native camera {index}', data)
            child.objects.link(obj)
            obj.empty_display_type = 'ARROWS'
            obj['DSTS_camera_index'] = index
            obj['DSTS_camera_hash'] = f'{record.name_hash:08x}'
            obj['DSTS_scene_note'] = 'Perspective -Z camera with native vertical FOV; runtime framing parity unverified' if data else 'Unsupported projection/lens/bone binding retained as a guide'
            obj['DSTS_camera_preview_kind'] = 'perspective' if data else 'guide'
            obj.lock_location = obj.lock_rotation = obj.lock_scale = (True,) * 3
            _bind(collection, obj, record.name_hash, 'COPY_TRANSFORMS')
        expected.append('cameras')
    collection['DSTS_scene_children'] = json.dumps(expected)


def _edited_raw(collection, armature):
    raw, source = _source(collection)
    if collection.get('DSTS_scene_armature') != armature:
        raise ValueError('Native scene records must stay with their original owning rig')
    if getattr(collection, 'animation_data', None):
        raise ValueError('Native scene optical fields are static; animate native rig float channels instead of collection properties')
    children = list(collection.children)
    roles = [child.get('DSTS_scene_role', '') for child in children]
    if sorted(roles) != sorted(json.loads(collection['DSTS_scene_children'])):
        raise ValueError('Native scene preview collections were added, deleted or duplicated')
    for child in children:
        if child.get('DSTS_scene_owner') != collection:
            raise ValueError('Native scene preview belongs to a different model')
        if child['DSTS_scene_role'] == 'lights':
            from .native_lights import document_from_collection
            if child.get('DSTS_light_sha256') != collection['DSTS_scene_sha256']:
                raise ValueError('Native light preview source does not match the owning model')
            edited = document_from_collection(child).raw
            at = struct.unpack_from('<Q', raw, 0x58)[0]
            out = bytearray(raw)
            out[at:at+240*len(source.lights)] = edited[at:at+240*len(source.lights)]
            raw = bytes(out)
        else:
            if child.children or sorted(obj.get('DSTS_camera_index', -1) for obj in child.objects) != list(range(len(source.cameras))):
                raise ValueError('Camera binding creation/deletion/duplication is not supported')
            for obj in child.objects:
                index = obj['DSTS_camera_index']
                expected_type = 'CAMERA' if obj['DSTS_camera_preview_kind'] == 'perspective' else 'EMPTY'
                if (obj.type != expected_type or obj['DSTS_camera_hash'] != f'{source.cameras[index].name_hash:08x}' or
                        obj.parent or obj.animation_data or obj.matrix_basis != Matrix.Identity(4)):
                    raise ValueError('Edit native camera optics and the owning rig; camera guide transforms are not native edits')
                validate_preview_binding(child, obj)
                if obj.type == 'CAMERA':
                    _validate_camera_data(collection, obj)
    changes = {}
    for index, record in enumerate(source.cameras):
        fields = {}
        for name in LENS_FIELDS:
            key = f'DSTS_camera_{index}_{name}'
            if key not in collection:
                raise ValueError('Native camera lens metadata was removed')
            value, original = collection[key], getattr(record, name)
            if value != original and not (isinstance(value, float) and math.isnan(value) and math.isnan(original)):
                fields[name] = collection[key]
        if fields:
            changes[index] = fields
    if changes:
        raw = patch_camera_lenses(raw, changes)
    return raw, source


class PreservedSceneGeom:
    def __init__(self, raw, geom, collection):
        self._raw, self.skeleton = raw, geom.skeleton
        self.meshes, self.materials = [], []
        self._nlst = collection['DSTS_scene_nlst']
        self.export_note = 'Native camera/light records exported with their original rig. Export edited motion separately as ANIM.'

    def to_file(self, filepath):
        from .spark import atomic_write
        atomic_write(filepath, self._raw, overwrite=True)

    def nlst_text(self):
        return self._nlst


def restore_scene_records(collection, armature, geom):
    if any(obj.type not in {'ARMATURE', 'MESH'} for obj in collection.objects):
        raise ValueError('Place native camera/light previews in their owned collections; additional scene objects cannot be serialized here')
    raw, source = _edited_raw(collection, armature)
    backend = load_format_modules()[0]
    hashes = [bone.name_hash for bone in geom.skeleton.bones]
    # A renamed/deleted target would silently orphan both records and floats.
    for record in [*source.lights, *source.cameras]:
        original_count = sum(bone.name_hash == record.name_hash for bone in source.skeleton.bones)
        if hashes.count(record.name_hash) != original_count:
            raise ValueError('Camera/light target bones were renamed, removed or duplicated; explicit rebinding is not implemented')
    lights_at, cameras_at = struct.unpack_from('<QQ', raw, 0x58)
    for index in range(len(source.lights)):
        record = backend.LightRecord()
        record.raw = raw[lights_at+240*index:lights_at+240*(index+1)]
        geom.lights.append(record)
    for index in range(len(source.cameras)):
        record = backend.CameraRecord()
        record.raw = raw[cameras_at+56*index:cameras_at+56*(index+1)]
        geom.cameras.append(record)
    if not source.meshes and not source.materials:
        if geom.meshes or geom.materials or rest_signature(armature) != collection['DSTS_scene_rest']:
            raise ValueError('A native camera/light-only GEOM retains its original rest rig; edit motion as ANIM')
        return PreservedSceneGeom(raw, geom, collection)
    return None


class DSTS_OT_UseNativeCamera(Operator):
    bl_idname = 'dsts.use_native_camera'
    bl_label = 'Use Native Preview Camera'
    bl_description = 'Set the selected imported native perspective camera as this scene\'s active preview camera'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        if obj is None or obj.type != 'CAMERA' or 'DSTS_camera_index' not in obj:
            self.report({'ERROR'}, 'Select an imported native perspective camera')
            return {'CANCELLED'}
        context.scene.camera = obj
        return {'FINISHED'}


class DSTS_PT_NativeScene(Panel):
    bl_idname = 'DSTS_PT_native_scene'
    bl_label = 'Native Scene Records'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'DSTS'

    @classmethod
    def owner(cls, context):
        if not context.object:
            return None
        for collection in context.object.users_collection:
            if 'DSTS_scene_source' in collection:
                return collection
            if collection.get('DSTS_scene_owner'):
                return collection['DSTS_scene_owner']
        return None

    @classmethod
    def poll(cls, context):
        return cls.owner(context) is not None

    def draw(self, context):
        owner, layout = self.owner(context), self.layout
        layout.label(text=f'Owned by {owner.name}')
        layout.label(text='Export this GEOM to include model and optical edits.')
        if context.object.type == 'CAMERA':
            layout.operator('dsts.use_native_camera')
        for key in owner.keys():
            if key.startswith('DSTS_camera_') and key.endswith(tuple(LENS_FIELDS)):
                layout.prop(owner, '["'+key+'"]', text=key.removeprefix('DSTS_camera_').replace('_', ' ').title())
        layout.label(text='Perspective cameras: native -Z / vertical FOV.', icon='INFO')
        layout.label(text='Game framing and animated optics remain unverified.')
        layout.label(text='Light optics are static; ANIM may override them.')


CLASSES = (DSTS_OT_UseNativeCamera, DSTS_PT_NativeScene)
