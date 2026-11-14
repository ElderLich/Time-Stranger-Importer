"""PC Cyber Sleuth import, using credited MIT readers and Blender 5 actions.

CS native export and game shader parity are not implemented. Imported assets
carry a game marker so Time Stranger exporters can reject them explicitly.
"""
import json
import math
from pathlib import Path
import tempfile

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix
import numpy as np

from ..external.cyber_sleuth.CollatedData.FromReadWrites import (
    generate_intermediate_format_from_files, add_anims)
from ..external.cyber_sleuth.FileInterfaces.AnimInterface import AnimInterface
from ..external.cyber_sleuth.FileInterfaces.SkelInterface import SkelInterface
from ..external.cyber_sleuth.FileReaders.GeomReader.ShaderUniforms import shader_textures
from ..external.cyber_sleuth.Utilities.StringHashing import int_to_BE_hex
from .blender_compat import create_action_fcurve, create_nla_track, scene_fps


GAME = 'cyber_sleuth'
LIMITS = ('Cyber Sleuth preview: native shader programs, animated shader uniforms, '
          'effects, collisions and native export are not implemented.')


def _json(value):
    return json.dumps(value, default=lambda v: v.tolist() if hasattr(v, 'tolist') else str(v))


def is_cyber_sleuth(obj):
    while obj is not None:
        if obj.get('asset_game') == GAME:
            return True
        obj = getattr(obj, 'parent', None)
    return False


def reject_export(obj=None):
    raise ValueError('Cyber Sleuth native export is not implemented. Save a .blend for editing; '
                     'Time Stranger GEOM/ANIM files are incompatible with Cyber Sleuth.')


def load_model(filepath, import_animations=True):
    path = Path(filepath).resolve()
    if path.suffix.lower() not in {'.geom', '.name', '.skel', '.anim'}:
        raise ValueError('Select a Cyber Sleuth PC .geom, .name, .skel or base .anim file')
    base = path.with_suffix('')
    missing = [str(base.with_suffix(ext)) for ext in ('.name', '.skel', '.geom', '.anim')
               if not base.with_suffix(ext).is_file()]
    if missing:
        raise ValueError('Missing Cyber Sleuth companions: ' + ', '.join(missing))
    if base.with_suffix('.geom').read_bytes()[:4] != b'd\0\0\0':
        raise ValueError('Cyber Sleuth PC requires GEOM version 100; use the Time Stranger menu for GEOM 316')
    model = generate_intermediate_format_from_files(str(base), 'PC', False)
    warnings = []
    if import_animations:
        skeleton = SkelInterface.from_file(str(base.with_suffix('.skel')))
        for candidate in sorted(base.parent.glob(base.name + '_*.anim')):
            if candidate.with_suffix('.skel').exists():
                continue  # A separate model/camera, not a clip for this skeleton.
            try:
                animation = AnimInterface.from_file(str(candidate), skeleton)
                add_anims(model, {candidate.stem: animation})
            except Exception as error:
                warnings.append(f'{candidate.name} was not imported: {type(error).__name__}: {error}')
    bones = model.skeleton.bone_names
    if len(set(bones)) != len(bones) or any(len(b.encode('utf-8')) > 63 for b in bones):
        raise ValueError('Duplicate or overlong bone names cannot be represented losslessly in Blender')
    if len(bones) != len(model.skeleton.inverse_bind_pose_matrices):
        raise ValueError('NAME/SKEL/GEOM bone counts disagree')
    model.import_warnings = warnings
    return base, model


def _armature(collection, root, model):
    skeleton = model.skeleton
    data = bpy.data.armatures.new(root.name + ' Rig')
    obj = bpy.data.objects.new(data.name, data)
    collection.objects.link(obj)
    obj.parent = root
    obj['asset_game'] = GAME
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for index, name in enumerate(skeleton.bone_names):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = (0, 0, 0), (0, .2, 0)
        bone.matrix = Matrix(np.linalg.inv(skeleton.inverse_bind_pose_matrices[index]).tolist())
    for child, parent in skeleton.bone_relations:
        if parent != -1:
            data.edit_bones[skeleton.bone_names[child]].parent = data.edit_bones[skeleton.bone_names[parent]]
    bpy.ops.object.mode_set(mode='OBJECT')
    for bone in obj.pose.bones:
        bone.rotation_mode = 'QUATERNION'
    return obj


def _image(texture, cache, warnings):
    if not texture.name or texture.name in {'.', '..'} or any(c in texture.name for c in '\\/:'):
        raise ValueError('Texture references must be local basenames')
    path = Path(texture.filepath)
    if path in cache:
        return cache[path]
    if not path.is_file():
        warnings.append(f'Missing texture: {path}')
        cache[path] = None
        return None
    raw = path.read_bytes()
    if raw[:4] != b'DDS ':
        raise ValueError(f'Unsupported Cyber Sleuth IMG encoding (expected DDS): {path.name}')
    # A temporary suffix avoids Blender extension guessing, without sidecars in
    # the extracted assets or installed game. Pack before the temporary is removed.
    with tempfile.TemporaryDirectory(prefix='dscs_texture_') as folder:
        temporary = Path(folder) / (texture.name + '.dds')
        temporary.write_bytes(raw)
        image = bpy.data.images.load(str(temporary), check_existing=False)
        image.name = texture.name
        image.pack()
    image.filepath_raw = str(path.resolve())
    image['asset_game'] = GAME
    image['dscs_source'] = str(path.resolve())
    cache[path] = image
    return image


def _materials(model, warnings):
    materials, cache = [], {}
    for source in model.materials:
        mat = bpy.data.materials.new(source.name)
        mat['asset_game'] = GAME
        mat['dscs_shader'] = source.shader_hex
        mat['dscs_uniforms'] = _json(source.shader_uniforms)
        mat['dscs_render_state'] = _json(source.unknown_data)
        mat['dscs_preview_note'] = 'Approximate textured preview; native shader program is not executed.'
        mat.use_nodes = True
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        bsdf = nodes.get('Principled BSDF')
        bsdf.inputs['Roughness'].default_value = .65
        samplers = {}
        for name in shader_textures:
            if name not in source.shader_uniforms:
                continue
            index = source.shader_uniforms[name][0]
            if not 0 <= index < len(model.textures):
                raise ValueError(f'{source.name}: invalid texture index {index}')
            node = nodes.new('ShaderNodeTexImage')
            node.name = node.label = name
            node.location = (-650, -180 * len(samplers))
            node.image = _image(model.textures[index], cache, warnings)
            samplers[name] = node
        color = source.shader_uniforms.get('DiffuseColor', (1, 1, 1, 1))
        bsdf.inputs['Base Color'].default_value = color
        alpha = float(source.shader_uniforms.get('DiffuseAlpha', (color[3],))[0])
        bsdf.inputs['Alpha'].default_value = alpha
        if 'ColorSampler' in samplers:
            texture = samplers['ColorSampler']
            multiply = nodes.new('ShaderNodeMixRGB')
            multiply.blend_type = 'MULTIPLY'
            multiply.inputs[0].default_value = 1
            multiply.inputs[2].default_value = color
            links.new(texture.outputs['Color'], multiply.inputs[1])
            links.new(multiply.outputs[0], bsdf.inputs['Base Color'])
            multiply_alpha = nodes.new('ShaderNodeMath')
            multiply_alpha.operation = 'MULTIPLY'
            multiply_alpha.inputs[1].default_value = alpha
            links.new(texture.outputs['Alpha'], multiply_alpha.inputs[0])
            links.new(multiply_alpha.outputs[0], bsdf.inputs['Alpha'])
        vertex_color = nodes.new('ShaderNodeVertexColor')
        vertex_color.layer_name = 'Color'
        vertex_color.label = 'Imported vertex color'
        for socket, output, operation in ((bsdf.inputs['Base Color'], vertex_color.outputs['Color'], 'color'),
                                           (bsdf.inputs['Alpha'], vertex_color.outputs['Alpha'], 'alpha')):
            previous = socket.links[0].from_socket if socket.is_linked else None
            if operation == 'color':
                multiply = nodes.new('ShaderNodeMixRGB')
                multiply.blend_type = 'MULTIPLY'
                multiply.inputs[0].default_value = 1
                multiply.inputs[1].default_value = color
                value_input, vertex_input = multiply.inputs[1], multiply.inputs[2]
            else:
                multiply = nodes.new('ShaderNodeMath')
                multiply.operation = 'MULTIPLY'
                multiply.inputs[0].default_value = alpha
                value_input, vertex_input = multiply.inputs[0], multiply.inputs[1]
            if previous:
                links.new(previous, value_input)
            links.new(output, vertex_input)
            links.new(multiply.outputs[0], socket)
        if hasattr(mat, 'surface_render_method'):
            mat.surface_render_method = 'DITHERED'
        if 'Fat' in source.shader_uniforms and 'ColorSampler' not in samplers:
            # The expansion/outline pass must not cover the textured surface.
            # This is a visible Blender approximation, not native shader parity:
            # source Fat displacement and runtime raster state remain unimplemented.
            geometry = nodes.new('ShaderNodeNewGeometry')
            light_path = nodes.new('ShaderNodeLightPath')
            discard = nodes.new('ShaderNodeMath')
            discard.operation = 'MAXIMUM'
            links.new(geometry.outputs['Backfacing'], discard.inputs[0])
            links.new(light_path.outputs['Is Shadow Ray'], discard.inputs[1])
            transparent = nodes.new('ShaderNodeBsdfTransparent')
            mix = nodes.new('ShaderNodeMixShader')
            links.new(discard.outputs[0], mix.inputs[0])
            links.new(bsdf.outputs[0], mix.inputs[1])
            links.new(transparent.outputs[0], mix.inputs[2])
            links.new(mix.outputs[0], nodes.get('Material Output').inputs['Surface'])
            mat['dscs_preview_note'] = 'Fat shader approximation: back faces hidden; native vertex expansion is not implemented.'
            warnings.append(f'{source.name}: Fat shader uses approximate backface culling; native expansion is not implemented.')
        materials.append(mat)
    return materials


def _meshes(collection, armature, model, materials):
    result = []
    for index, source in enumerate(model.meshes):
        data = bpy.data.meshes.new(f'{collection.name} Mesh {index}')
        data.from_pydata([v['Position'] for v in source.vertices], [], [p.indices for p in source.polygons])
        obj = bpy.data.objects.new(data.name, data)
        collection.objects.link(obj)
        obj.parent = armature
        obj['asset_game'] = GAME
        obj['dscs_mesh_hash'] = source.name_hash
        if not 0 <= source.material_id < len(materials):
            raise ValueError('Mesh material index is invalid')
        data.materials.append(materials[source.material_id])
        for uv_name in ('UV', 'UV2', 'UV3'):
            if source.vertices and uv_name in source.vertices[0]:
                uv = data.uv_layers.new(name=uv_name + 'Map')
                for loop in data.loops:
                    uv.data[loop.index].uv = source.vertices[loop.vertex_index][uv_name]
        color = data.color_attributes.new(name='Color', type='FLOAT_COLOR', domain='CORNER')
        for loop in data.loops:
            color.data[loop.index].color = source.vertices[loop.vertex_index].get('Colour', (1, 1, 1, 1))
        for group in source.vertex_groups:
            vg = obj.vertex_groups.new(name=model.skeleton.bone_names[group.bone_idx])
            for vertex, weight in zip(group.vertex_indices, group.weights):
                if weight:
                    vg.add([vertex], float(weight), 'REPLACE')
        modifier = obj.modifiers.new('Cyber Sleuth Rig', 'ARMATURE')
        modifier.object = armature
        if source.vertices and 'Normal' in source.vertices[0]:
            for polygon in data.polygons:
                polygon.use_smooth = True
            data.normals_split_custom_set([source.vertices[loop.vertex_index]['Normal'] for loop in data.loops])
        data.update()
        result.append(obj)
    return result


def _animations(armature, base, model, scene):
    base_animation = model.animations[base.name]
    for index, (quat, location, scale) in enumerate(model.skeleton.rest_pose_delta):
        for channel, value in ((base_animation.rotations[index], np.roll(quat, 1)),
                               (base_animation.locations[index], location[:3]),
                               (base_animation.scales[index], scale[:3])):
            if not channel.frames:
                channel.frames.append(0)
                channel.values.append(value)
    actions = []
    for name, source in model.animations.items():
        if not math.isfinite(source.playback_rate) or source.playback_rate <= 0:
            raise ValueError(f'{name}: invalid animation playback rate')
        action = bpy.data.actions.new(name)
        action['asset_game'] = GAME
        action['dscs_fps'] = source.playback_rate
        action['dscs_duration_seconds'] = source.duration_seconds
        action['dscs_user_channels'] = _json(source.uv_data)
        action['dscs_user_channel_note'] = 'Native scalar channels preserved for inspection; material playback is not implemented.'
        action.use_fake_user = True
        for index, bone_name in enumerate(model.skeleton.bone_names):
            bone = armature.pose.bones[bone_name]
            for attr, channel, count in (('rotation_quaternion', source.rotations[index], 4),
                                         ('location', source.locations[index], 3),
                                         ('scale', source.scales[index], 3)):
                for component in range(count):
                    if not channel.frames:
                        continue
                    fc = create_action_fcurve(action, armature, bone.path_from_id(attr), component, bone_name)
                    fc.keyframe_points.add(len(channel.frames))
                    fc.keyframe_points.foreach_set('co', [number for frame, value in zip(channel.frames, channel.values)
                                                         for number in (frame + 1, value[component])])
                    for key in fc.keyframe_points:
                        key.interpolation = 'LINEAR'
                    fc.update()
        action.use_frame_range = True
        action.frame_start = 1
        action.frame_end = max(1, source.total_frames, source.num_frames + 1)
        track, strip = create_nla_track(armature, action, 'Base' if name == base.name else name,
                                        1, scale=scene_fps(scene) / source.playback_rate)
        track.mute = name != base.name
        action['dscs_base'] = name == base.name
        actions.append(action)
    return actions


def _scene_records(collection, armature, root, model, warnings):
    # CS light enum/attenuation units have not been verified against its runtime.
    # Keep inspectable attached records without falsely rendering them as points.
    for index, light in enumerate(model.light_sources):
        obj = bpy.data.objects.new(f'{root.name} Light record {index}', None)
        collection.objects.link(obj)
        obj.parent = armature
        obj['asset_game'] = GAME
        obj['dscs_light_record'] = _json(vars(light))
        obj['dscs_preview_note'] = 'Light record only: Cyber Sleuth optical type and units not verified.'
        if light.bone_name in armature.pose.bones:
            constraint = obj.constraints.new('COPY_TRANSFORMS')
            constraint.target, constraint.subtarget = armature, light.bone_name
    if model.light_sources:
        warnings.append(f'{len(model.light_sources)} light record(s) imported for inspection; light rendering is not implemented.')
    for index, source in enumerate(model.cameras):
        if source.projection not in (0, 1):
            raise ValueError(f'Unsupported Cyber Sleuth camera projection {source.projection}')
        if not (math.isfinite(source.fov) and 0 < source.fov < 179 and
                0 < source.zNear < source.zFar and source.orthographic_scale > 0):
            raise ValueError('Invalid Cyber Sleuth camera optical parameters')
        camera = bpy.data.cameras.new(f'{root.name} Camera {index}')
        camera.type = 'PERSP' if source.projection == 0 else 'ORTHO'
        camera.lens_unit = 'FOV'
        camera.sensor_fit = 'VERTICAL'
        camera.sensor_height = 24
        camera.lens = camera.sensor_height / (2 * math.tan(math.radians(source.fov) / 2))
        camera.ortho_scale = source.orthographic_scale
        camera.clip_start, camera.clip_end = source.zNear, source.zFar
        obj = bpy.data.objects.new(camera.name, camera)
        collection.objects.link(obj)
        obj.parent = armature
        obj['asset_game'] = GAME
        obj['dscs_camera_record'] = _json(vars(source))
        bone_name = model.bone_name_hashes.get(int_to_BE_hex(source.bone_name_hash))
        if not bone_name:
            raise ValueError('Cyber Sleuth camera attachment bone cannot be resolved')
        constraint = obj.constraints.new('COPY_TRANSFORMS')
        constraint.target, constraint.subtarget = armature, bone_name
        obj['dscs_preview_note'] = ('Approximate vertical-FOV preview with native bone motion. '
                                    'In-game optical/shot parity is not verified.')
    if model.cameras:
        warnings.append(f'{len(model.cameras)} animated camera preview(s) imported; in-game shot parity is not verified.')


def import_model(context, filepath, import_animations=True, import_cameras=False):
    if context.object and context.object.mode != 'OBJECT':
        raise ValueError('Switch to Object Mode before importing Cyber Sleuth assets')
    base, model = load_model(filepath, import_animations)
    before = {name: set(getattr(bpy.data, name)) for name in
              ('objects', 'collections', 'meshes', 'armatures', 'materials', 'images', 'actions', 'cameras')}
    warnings = list(model.import_warnings)
    selected, active = list(context.selected_objects), context.view_layer.objects.active
    try:
        bpy.ops.object.select_all(action='DESELECT')
        collection = bpy.data.collections.new(base.name + ' [Cyber Sleuth]')
        context.scene.collection.children.link(collection)
        collection['asset_game'] = GAME
        root = bpy.data.objects.new(base.name + ' [Cyber Sleuth]', None)
        collection.objects.link(root)
        root['asset_game'] = GAME
        root['dscs_source'] = str(base)
        root['dscs_limitations'] = LIMITS
        root.rotation_euler.x = math.pi / 2
        armature = _armature(collection, root, model)
        materials = _materials(model, warnings)
        meshes = _meshes(collection, armature, model, materials)
        actions = _animations(armature, base, model, context.scene)
        _scene_records(collection, armature, root, model, warnings)
        related = []
        if import_cameras and not model.cameras:
            for path in sorted(base.parent.glob('cam_' + base.name + '_*.geom')):
                camera_root, camera_warnings = import_model(context, str(path), import_animations, False)
                related.append(camera_root.name)
                warnings.extend(camera_warnings)
        root['dscs_related_cameras'] = _json(related)
        root['dscs_warnings'] = _json(warnings)
        root['dscs_summary'] = _json({'meshes': len(meshes), 'bones': len(armature.data.bones),
                                     'materials': len(materials), 'actions': len(actions),
                                     'cameras': len(model.cameras), 'lights': len(model.light_sources)})
        context.view_layer.objects.active = armature
        armature.select_set(True)
        context.scene.frame_set(1)
        return root, warnings
    except Exception:
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for name, previous in before.items():
            datablocks = getattr(bpy.data, name)
            for block in set(datablocks) - previous:
                datablocks.remove(block, do_unlink=True)
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
        raise


class DSCS_OT_import_model(bpy.types.Operator, ImportHelper):
    bl_idname = 'import_scene.cyber_sleuth_model'
    bl_label = 'Cyber Sleuth PC model / scene'
    bl_description = 'Import a Cyber Sleuth PC model or scene with its native companion files, textures and optional matching motions and camera rigs'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.geom'
    filter_glob: StringProperty(default='*.geom;*.name;*.skel', options={'HIDDEN'})
    import_animations: BoolProperty(name='Matching motion clips', default=True,
                                   description='Import compatible modelname_*.anim companions; base pose is always included')
    import_cameras: BoolProperty(name='Matching camera rigs', default=True,
                                description='Import cam_modelname_*.geom companions as separate animated camera rigs')

    def draw(self, context):
        self.layout.prop(self, 'import_animations')
        self.layout.prop(self, 'import_cameras')
        self.layout.label(text='Requires NAME, SKEL, GEOM and base ANIM companions.')
        self.layout.label(text='Textures: images/*.img (DDS), packed into the .blend.')
        self.layout.label(text='Approximate materials; native export is unavailable.', icon='INFO')

    def execute(self, context):
        try:
            root, warnings = import_model(context, self.filepath, self.import_animations, self.import_cameras)
        except Exception as error:
            self.report({'ERROR'}, f'Cyber Sleuth import failed: {error}')
            return {'CANCELLED'}
        self.report({'WARNING'} if warnings else {'INFO'},
                    f'Imported {root.name}. ' + (warnings[0] if warnings else 'Textured preview; native shader parity is not implemented.'))
        return {'FINISHED'}


class DSCS_OT_export_unsupported(bpy.types.Operator):
    bl_idname = 'export_scene.cyber_sleuth_unsupported'
    bl_label = 'Cyber Sleuth export (not implemented)'
    bl_description = 'Explain the current native Cyber Sleuth export limitation; this operation does not write a game asset'

    def execute(self, context):
        self.report({'ERROR'}, 'Cyber Sleuth native export is not implemented. Save a .blend for editing.')
        return {'CANCELLED'}


def selected_rig(context):
    obj = context.object
    while obj is not None:
        if obj.type == 'ARMATURE' and obj.get('asset_game') == GAME:
            return obj
        for child in obj.children:
            if child.type == 'ARMATURE' and child.get('asset_game') == GAME:
                return child
        obj = obj.parent
    return None


def activate_clip(rig, index, scene):
    tracks = rig.animation_data.nla_tracks
    if not 0 <= index < len(tracks):
        raise ValueError('Selected Cyber Sleuth action no longer exists')
    for at, track in enumerate(tracks):
        track.mute = at != index and not track.strips[0].action.get('dscs_base', False)
    strip = tracks[index].strips[0]
    scene.frame_start = max(0, int(strip.frame_start))
    scene.frame_end = max(scene.frame_start + 1, math.ceil(strip.frame_end))
    scene.frame_set(scene.frame_start)


class DSCS_OT_preview_clip(bpy.types.Operator):
    bl_idname = 'dscs.preview_clip'
    bl_label = 'Preview Cyber Sleuth clip'
    bl_description = 'Activate an imported Cyber Sleuth animation clip on its owning rig and set the timeline preview range'
    bl_options = {'REGISTER', 'UNDO'}
    index: IntProperty()

    def execute(self, context):
        rig = selected_rig(context)
        if rig is None or rig.animation_data is None:
            self.report({'ERROR'}, 'Select an imported Cyber Sleuth rig or mesh')
            return {'CANCELLED'}
        try:
            activate_clip(rig, self.index, context.scene)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSCS_OT_use_camera(bpy.types.Operator):
    bl_idname = 'dscs.use_camera'
    bl_label = 'Use Cyber Sleuth camera preview'
    bl_description = 'Use this imported Cyber Sleuth camera for the scene and activate its first captured motion'
    bl_options = {'REGISTER', 'UNDO'}
    object_name: StringProperty()

    def execute(self, context):
        camera = bpy.data.objects.get(self.object_name)
        if camera is None or camera.type != 'CAMERA' or not is_cyber_sleuth(camera):
            self.report({'ERROR'}, 'Cyber Sleuth camera is unavailable')
            return {'CANCELLED'}
        context.scene.camera = camera
        rig = camera.parent
        activate_clip(rig, 0, context.scene)
        if context.space_data and context.space_data.type == 'VIEW_3D':
            context.space_data.region_3d.view_perspective = 'CAMERA'
        return {'FINISHED'}


class DSCS_PT_assets(bpy.types.Panel):
    bl_label = 'Cyber Sleuth assets'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Cyber Sleuth'

    def draw(self, context):
        layout = self.layout
        layout.operator(DSCS_OT_import_model.bl_idname, icon='IMPORT')
        layout.label(text='PC model and scene preview', icon='INFO')
        layout.label(text='Materials are approximate.')
        layout.label(text='Native export / effects are unavailable.')
        rig = selected_rig(context)
        if rig is None:
            layout.label(text='Select a Cyber Sleuth mesh or rig.')
            return
        root = rig.parent
        layout.label(text=root.name)
        if rig.animation_data:
            layout.label(text='Choose clip, then play the timeline:')
            for index, track in enumerate(rig.animation_data.nla_tracks):
                op = layout.operator(DSCS_OT_preview_clip.bl_idname, text=track.name,
                                     icon='PLAY', depress=not track.mute)
                op.index = index
        cameras = [child for child in rig.children if child.type == 'CAMERA']
        for name in json.loads(root.get('dscs_related_cameras', '[]')):
            related_root = bpy.data.objects.get(name)
            if related_root:
                cameras.extend(child for owner in related_root.children for child in owner.children if child.type == 'CAMERA')
        for camera in cameras:
            layout.operator(DSCS_OT_use_camera.bl_idname, text=camera.name, icon='CAMERA_DATA').object_name = camera.name
        warnings = json.loads(root.get('dscs_warnings', '[]'))
        if warnings:
            layout.label(text=f'{len(warnings)} import note(s): see root custom properties.', icon='ERROR')


def draw_import_menu(self, context):
    self.layout.operator(DSCS_OT_import_model.bl_idname, text='Cyber Sleuth PC (.geom/.name/.skel)')


CLASSES = (DSCS_OT_import_model, DSCS_OT_export_unsupported, DSCS_OT_preview_clip,
           DSCS_OT_use_camera, DSCS_PT_assets)
