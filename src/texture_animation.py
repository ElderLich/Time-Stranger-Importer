"""Verified native UV transforms, driven by the existing material float channels.

Only reviewed vertex/pixel shader bytecode pairs are supported. This describes
UV arithmetic, not full native material or render-pass equivalence. See docs.
"""
import hashlib
import json
import math
from pathlib import Path

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from .blender_compat import action_slot_for, create_action_fcurve, iter_action_fcurves

TAG = 'DSTS_uv_role'
META = 'DSTS_uv_preview'
OWNER = 'DSTS_uv_owner'
CLIP = 'DSTS_uv_clip'
PARAMETER_NAMES = {59: 'ScrollSpeedSet1', 68: 'OffsetSet1', 71: 'OffsetSet2',
                   94: 'RotationSet1', 103: 'ScaleSet1', 106: 'ScaleSet2'}

# Pair fingerprints prevent a custom replacement shader with a familiar name
# from silently inheriting the wrong preview semantics. Values are native IDs.
# tuple: label, texture ID -> (UV set, ordered operations), required float widths
CONTRACTS = {
    ('bf0583989160a1fe8a244f99df8b8e351162e2d66ede4adb435ed7979632f6fa',
     '4df2f0c7bcbf3dab07c03eb560310e9fe37eeb324c847150215081bf364154cc'):
        ('Offset / color', {24: (1, (('offset', 68),))}, {68: 2}),
    ('aef2fceda12e9e1586ad86cc96d852a8c2beac2f75e48426bbfc46e2fc507a37',
     'eac8a40de6916c187a53790dbc7a91880976b2133db0d99b65fdbde3f858cbf9'):
        ('Offset / color and fixed second UV emissive',
         {24: (1, (('offset', 68),)), 303: (2, ())}, {68: 2}),
    ('730cdddeecd541580032a6356aaee7670691fc24873b54150770284845165491',
     'feb942452cc870fd70b7a97900f5b22af04881cf1a432a39d1349a9956235423'):
        ('Offset then scale / two UV sets',
         {24: (1, (('offset', 68), ('scale', 103))),
          42: (2, (('offset', 71), ('scale', 106)))}, {68: 2, 71: 2, 103: 2, 106: 2}),
    ('8e39f275b1d2108fb6624b28f5c36d5acd200641df4c44fb1258c22129425f55',
     '9dc95a33e619cf8ac2631e1c8e9b590dcc8f5cb7ea57a3fbb8c26a513cfd14cc'):
        ('Rotation in radians then offset',
         {24: (1, (('rotation', 94), ('offset', 68)))}, {94: 1, 68: 2}),
    ('b59fcaac7ccc12ce6882f65ae9fa560396057d80a538d225264219494049b7e3',
     '6c82d8be0b4017da285b7eb352aead378cb5ba2b2dff77774aadfe80b2085f80'):
        ('Unwrapped time / scrolling color and fixed third UV emissive',
         {24: (1, (('scroll', 59),)), 303: (3, ())}, {58: 1, 59: 2}),
}


def _group(mat):
    from .material import find_data_group
    group = find_data_group(mat)
    if group is None:
        raise ValueError('Material has no native Time Stranger metadata')
    return group.node_tree


def _uniforms(tree):
    result = {}
    for node in tree.nodes:
        if node.bl_idname == 'DSTS_ShaderFloatUniform':
            index = node.get('DSTS_parameter_id')
            if index is not None:
                if int(index) in result:
                    raise ValueError('Duplicate native float uniform IDs are ambiguous')
                result[int(index)] = node
    return result


def _shader(tree):
    nodes = [node for node in tree.nodes if node.bl_idname == 'DSTS_ShaderData']
    if len(nodes) != 1 or len(nodes[0].shader_strings) != 14:
        raise ValueError('Native material must retain all fourteen shader slots')
    return nodes[0].shader_strings[0].value


def _bindings(owner):
    result = json.loads(owner.get('DSTS_float_bindings', '[]'))
    if not isinstance(result, list):
        raise ValueError('Invalid native float binding table')
    return result


def binding_index(owner, mat, parameter, component):
    name_hash = int(mat['DSTS_source_name_hash'])
    matches = [i for i, row in enumerate(_bindings(owner)) if row['name_hash'] == name_hash
               and row['array_index'] == (parameter << 4) | component and row['flags'] == 8]
    if len(matches) > 1:
        raise ValueError('Multiple native float channels target the same material component')
    return matches[0] if matches else None


def _targets(owner, mat, required):
    return [[int(parameter), component, binding_index(owner, mat, int(parameter), component)]
            for parameter, width in required.items() for component in range(width) if int(parameter) != 58]


def _node(tree, kind, role):
    node = tree.nodes.new(kind)
    node[TAG] = role
    node.name = 'Native UV / ' + role
    node.label = node.name
    return node


def _variable(driver, name, target, path):
    variable = driver.variables.new()
    variable.name, variable.type = name, 'SINGLE_PROP'
    variable.targets[0].id_type = target.id_type
    variable.targets[0].id = target
    variable.targets[0].data_path = path


def _value(tree, owner, mat, parameter, component, uniform):
    node = _node(tree, 'ShaderNodeValue', f'uniform:{parameter}:{component}')
    node.outputs[0].default_value = uniform.values[component].value
    index = binding_index(owner, mat, parameter, component)
    if index is None:
        driver = node.outputs[0].driver_add('default_value').driver
        driver.type = 'SCRIPTED'
        _variable(driver, 'value', tree, uniform.path_from_id('values') + f'[{component}].value')
        driver.expression = 'value'
    # Bound values are sampled from the selected maintained Action by the
    # frame handler. Evaluating them separately leaves additive actor NLA,
    # pose channels, masks and constraints entirely untouched.
    return node.outputs[0]


def _clock(tree, owner, scene):
    scene = _clock_scene(owner)
    for key, default in (('DSTS_uv_clock_start', 1.0), ('DSTS_uv_clock_rate', 1.0), ('DSTS_uv_clock_enabled', True)):
        if key not in owner:
            owner[key] = default
    owner.update_tag()
    node = _node(tree, 'ShaderNodeValue', 'unwrapped-seconds')
    driver = node.outputs[0].driver_add('default_value').driver
    driver.type = 'SCRIPTED'
    _variable(driver, 'start', owner, '["DSTS_uv_clock_start"]')
    _variable(driver, 'rate', owner, '["DSTS_uv_clock_rate"]')
    _variable(driver, 'fps', scene, 'render.fps')
    _variable(driver, 'base', scene, 'render.fps_base')
    _variable(driver, 'enabled', owner, '["DSTS_uv_clock_enabled"]')
    # Native 2EA410 copies the first active animator's unwrapped +108 seconds,
    # wraps at 86400 seconds and then supplies Time58. It does not clamp/loop
    # with the skeletal clip. The preview start/rate are explicit scene controls.
    driver.expression = 'fmod((frame-start)*base/fps*rate,86400.0) if enabled else 0.0'
    return node.outputs[0]


def graph_signature(tree, owner):
    nodes = []
    for node in tree.nodes:
        if TAG not in node:
            continue
        fields = {key: getattr(node, key) for key in ('operation', 'uv_map', 'rotation_type', 'invert', 'mute', 'use_clamp')
                  if hasattr(node, key)}
        constants = []
        for index, socket in enumerate(node.inputs):
            if not socket.is_linked and hasattr(socket, 'default_value'):
                value = socket.default_value
                constants.append((index, float(value) if isinstance(value, (float, int)) else list(value)))
        nodes.append((node[TAG], node.bl_idname, fields, constants))
    def endpoint(node):
        return node.get(TAG, 'native:' + node.name)
    links = sorted((endpoint(link.from_node), link.from_socket.identifier,
                    endpoint(link.to_node), link.to_socket.identifier)
                   for link in tree.links if TAG in link.from_node or TAG in link.to_node)
    drivers = []
    if tree.animation_data:
        for curve in tree.animation_data.drivers:
            if any(curve.data_path == node.outputs[0].path_from_id('default_value')
                   for node in tree.nodes if TAG in node and node.type == 'VALUE'):
                if curve.modifiers:
                    raise ValueError('Native UV preview drivers cannot use FCurve modifiers')
                variables = []
                for variable in curve.driver.variables:
                    target = variable.targets[0]
                    target_role = 'owner' if target.id == owner else 'tree' if target.id == tree else target.id_type
                    variables.append((variable.name, variable.type, target_role, target.data_path))
                drivers.append((curve.data_path, curve.mute, curve.driver.type, curve.driver.use_self,
                                curve.driver.expression, variables))
    return json.dumps((sorted(nodes), links, sorted(drivers)), sort_keys=True)


def _configure_material(collection, owner, mat, shader_folder):
    from .material import make_data_group_single_user
    tree = _group(mat)
    if mat.get(OWNER) is not None and mat.get(OWNER) != owner:
        raise ValueError('Make a material copy before binding its preview to a different armature')
    name = _shader(tree)
    if not name or any(c in name for c in '/\\:'):
        raise ValueError('Invalid native shader basename')
    paths = [shader_folder / (name + '_' + stage + '.shad') for stage in ('vp', 'fp')]
    if not all(path.is_file() for path in paths):
        raise ValueError('Matching native vertex/pixel .shad files are required to verify UV behavior')
    hashes = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in paths)
    if hashes not in CONTRACTS:
        raise ValueError('This shader pair has no verified UV preview contract yet')
    label, textures, required = CONTRACTS[hashes]
    if 58 in required:
        _clock_scene(owner)
    uniforms = _uniforms(tree)
    for parameter, width in required.items():
        if parameter not in uniforms or len(uniforms[parameter].values) != width:
            raise ValueError(f'Missing or malformed native UV uniform {parameter}')
        if not all(math.isfinite(item.value) for item in uniforms[parameter].values):
            raise ValueError('Native UV values must be finite')
        for component in range(width):
            binding_index(owner, mat, parameter, component)
    texture_nodes = {int(node['DSTS_parameter_id']): node for node in tree.nodes
                     if node.type == 'TEX_IMAGE' and 'DSTS_parameter_id' in node}
    for parameter, (uv_set, _) in textures.items():
        if parameter not in texture_nodes:
            raise ValueError(f'This UV family requires texture sampler {parameter}')
        socket = texture_nodes[parameter].inputs['Vector']
        if socket.is_linked and TAG not in socket.links[0].from_node:
            raise ValueError('An existing custom UV input would be replaced; remove it explicitly before configuring')
        for obj in collection.all_objects:
            if obj.type == 'MESH' and mat in list(obj.data.materials) and f'uv{uv_set}' not in obj.data.uv_layers:
                raise ValueError(f'Mesh {obj.name} lacks native UV set {uv_set}')
    tree = make_data_group_single_user(mat).node_tree
    uniforms = _uniforms(tree)
    for node in list(tree.nodes):
        if TAG in node:
            if node.type == 'VALUE':
                node.outputs[0].driver_remove('default_value')
            tree.nodes.remove(node)
    textures_in_tree = {int(node['DSTS_parameter_id']): node for node in tree.nodes
                        if node.type == 'TEX_IMAGE' and 'DSTS_parameter_id' in node}
    values = {(parameter, component): _value(tree, owner, mat, parameter, component, uniforms[parameter])
              for parameter, width in required.items() if parameter != 58 for component in range(width)}
    clock = _clock(tree, owner, bpy.context.scene) if 58 in required else None
    for sampler, (uv_set, operations) in textures.items():
        uv = _node(tree, 'ShaderNodeUVMap', f'{sampler}:uv{uv_set}')
        uv.uv_map = f'uv{uv_set}'
        vector = uv.outputs[0]
        for operation, parameter in operations:
            if operation == 'rotation':
                node = _node(tree, 'ShaderNodeVectorRotate', f'{sampler}:rotation')
                node.rotation_type = 'AXIS_ANGLE'
                node.inputs['Axis'].default_value = (0, 0, 1)
                tree.links.new(vector, node.inputs['Vector'])
                tree.links.new(values[(parameter, 0)], node.inputs['Angle'])
            else:
                combine = _node(tree, 'ShaderNodeCombineXYZ', f'{sampler}:{operation}:xy')
                combine.inputs['Z'].default_value = 1 if operation == 'scale' else 0
                for component in range(2):
                    value = values[(parameter, component)]
                    if operation == 'scroll':
                        multiply = _node(tree, 'ShaderNodeMath', f'{sampler}:scroll:{component}:time')
                        multiply.operation = 'MULTIPLY'
                        tree.links.new(value, multiply.inputs[0])
                        tree.links.new(clock, multiply.inputs[1])
                        fraction = _node(tree, 'ShaderNodeMath', f'{sampler}:scroll:{component}:fraction')
                        fraction.operation = 'FRACT'
                        tree.links.new(multiply.outputs[0], fraction.inputs[0])
                        value = fraction.outputs[0]
                    tree.links.new(value, combine.inputs[component])
                node = _node(tree, 'ShaderNodeVectorMath', f'{sampler}:{operation}')
                node.operation = 'MULTIPLY' if operation == 'scale' else 'ADD'
                tree.links.new(vector, node.inputs[0])
                tree.links.new(combine.outputs[0], node.inputs[1])
            vector = node.outputs[0]
        tree.links.new(vector, textures_in_tree[sampler].inputs['Vector'])
    for index, node in enumerate(node for node in tree.nodes if TAG in node):
        node.location = (-1500 + (index % 5) * 220, -650 - (index // 5) * 180)
    mat[OWNER] = owner
    mat[META] = json.dumps({'version': 1, 'shader': name, 'hashes': hashes, 'label': label,
                           'folder': str(shader_folder), 'required': required,
                           'targets': _targets(owner, mat, required),
                           'signature': graph_signature(tree, owner)})
    if 'DSTS_uv_error' in mat:
        del mat['DSTS_uv_error']
    update_previews(bpy.context.scene)
    return label


def configure_preview(collection, armature, source_path=None, strict=False, material=None):
    """Configure reviewed families; ordinary import keeps unknown families intact.

    source_path is the imported GEOM, or an explicit shader directory. Explicit
    UI requests use strict=True and select one material.
    """
    if source_path:
        source = Path(source_path)
        if not strict:
            armature['DSTS_texture_source'] = str(source.resolve())
    else:
        source = Path(armature.get('DSTS_texture_source', ''))
    folder = source if source.is_dir() else source.parent / 'shaders'
    materials = [material] if material else list(dict.fromkeys(mat for obj in collection.all_objects
                            if obj.type == 'MESH' for mat in obj.data.materials if mat))
    notes = []
    for mat in materials:
        try:
            _configure_material(collection, armature, mat, folder)
        except (ValueError, OSError) as error:
            if strict:
                raise
            mat['DSTS_uv_error'] = str(error)
            notes.append(f'{mat.name}: {error}')
    if strict and source_path:
        armature['DSTS_texture_source'] = str(source.resolve())
    return notes


def validate_material(mat):
    if META not in mat:
        return
    metadata = json.loads(mat[META])
    owner = mat.get(OWNER)
    if owner is None or owner.type != 'ARMATURE':
        raise ValueError('Native UV preview lost its armature; configure it again')
    if mat.get('DSTS_uv_playback_error'):
        raise ValueError('Native UV preview: ' + mat['DSTS_uv_playback_error'])
    tree = _group(mat)
    if _shader(tree) != metadata['shader'] or graph_signature(tree, owner) != metadata['signature']:
        raise ValueError('Native UV preview graph/bindings changed; configure the verified preview again before export')
    if _targets(owner, mat, metadata['required']) != metadata['targets']:
        raise ValueError('Native material channel targets changed; configure the verified preview again before export')
    for parameter, width in metadata['required'].items():
        parameter = int(parameter)
        node = _uniforms(tree).get(parameter)
        if node is None or len(node.values) != width or not all(math.isfinite(v.value) for v in node.values):
            raise ValueError('Malformed native UV uniform')
        for component in range(width):
            binding_index(owner, mat, parameter, component)


def validate_export(owner):
    for mat in bpy.data.materials:
        if mat.get(OWNER) == owner:
            validate_material(mat)
    if owner.get('DSTS_uv_binding_count') is not None and len(_bindings(owner)) != owner['DSTS_uv_binding_count']:
        raise ValueError('Native UV binding table was changed outside the editor')


def _actions(owner):
    result = []
    ad = owner.animation_data
    if ad:
        if ad.action:
            result.append(ad.action)
        for track in ad.nla_tracks:
            result.extend(strip.action for strip in track.strips if strip.action)
    return list(dict.fromkeys(result))


def _restore_action(owner, action, slot):
    owner.animation_data.action = action
    if action is not None and slot is not None:
        owner.animation_data.action_slot = slot


def _preview_strip(owner, action):
    strips = [strip for track in owner.animation_data.nla_tracks
              for strip in track.strips if strip.action == action]
    if len(strips) != 1:
        raise ValueError('UV clip preview requires exactly one matching native NLA strip')
    from .animation import validate_strip
    validate_strip(strips[0])
    if strips[0].scale <= 0:
        raise ValueError('UV clip preview requires a positive strip scale')
    return strips[0]


def _clock_scene(owner):
    scenes = [scene for scene in bpy.data.scenes if scene.objects.get(owner.name) == owner]
    if len(scenes) != 1:
        raise ValueError('Native clock preview requires an armature in exactly one scene; make linked actors independent')
    return scenes[0]


def _refresh_clock_scene(tree, owner):
    scene = _clock_scene(owner)
    clock = next(node for node in tree.nodes if node.get(TAG) == 'unwrapped-seconds')
    path = clock.outputs[0].path_from_id('default_value')
    curve = next(curve for curve in tree.animation_data.drivers if curve.data_path == path)
    for variable in curve.driver.variables:
        if variable.name in ('fps', 'base') and variable.targets[0].id != scene:
            variable.targets[0].id = scene
            tree.update_tag()


def _validate_clip_masks(action, indices):
    metadata = json.loads(action.get('DSTS_animation_metadata', '{}'))
    masks = metadata.get('float_masks', [])
    for index in indices:
        if index is not None and index < len(masks) and masks[index] != 255:
            raise ValueError(f'Native float channel {index} has mask {masks[index]}; masked UV blending is not yet supported')


@persistent
def update_previews(scene, *_):
    """Sample original material FCurves without changing the actor's animation."""
    for mat in bpy.data.materials:
        if META not in mat or mat.get(OWNER) is None:
            continue
        try:
            owner = mat[OWNER]
            if scene.objects.get(owner.name) != owner:
                continue
            tree = _group(mat)
            uniforms = _uniforms(tree)
            metadata = json.loads(mat[META])
            if '58' in metadata['required']:
                _refresh_clock_scene(tree, owner)
            action = owner.get(CLIP)
            curves, frame = {}, 0.0
            if action is not None:
                _validate_clip_masks(action, (row[2] for row in metadata['targets']))
                strip = _preview_strip(owner, action)
                frame = min(strip.action_frame_end, max(strip.action_frame_start,
                            strip.action_frame_start + (scene.frame_current_final - strip.frame_start) / strip.scale))
                curves = {curve.data_path: curve for curve in iter_action_fcurves(action, owner)}
            for parameter, component, index in metadata['targets']:
                if index is None:
                    continue
                curve = curves.get(f'["DSTS_float_{index}"]')
                value = curve.evaluate(frame) if curve else uniforms[parameter].values[component].value
                if not math.isfinite(value):
                    raise ValueError('Selected native UV clip evaluates to a nonfinite value')
                node = next(n for n in tree.nodes if n.get(TAG) == f'uniform:{parameter}:{component}')
                node.outputs[0].default_value = value
            if 'DSTS_uv_playback_error' in mat:
                del mat['DSTS_uv_playback_error']
        except (ValueError, KeyError, AttributeError, StopIteration) as error:
            mat['DSTS_uv_playback_error'] = str(error)


@persistent
def _load_previews(*_):
    update_previews(bpy.context.scene)


def register_handlers():
    for handlers, callback in ((bpy.app.handlers.frame_change_pre, update_previews),
                               (bpy.app.handlers.load_post, _load_previews)):
        if callback not in handlers:
            handlers.append(callback)


def unregister_handlers():
    for handlers, callback in ((bpy.app.handlers.frame_change_pre, update_previews),
                               (bpy.app.handlers.load_post, _load_previews)):
        if callback in handlers:
            handlers.remove(callback)


def _remove_curve(action, curve):
    if getattr(action, 'layers', None):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    if curve in list(bag.fcurves):
                        bag.fcurves.remove(curve)
                        return
    else:
        action.fcurves.remove(curve)


def add_binding(owner, mat, parameter, component):
    validate_material(mat)
    if mat.get(OWNER) != owner:
        raise ValueError('Select the material on its configured native armature')
    metadata = json.loads(mat[META])
    width = metadata['required'].get(str(parameter), 0)
    if parameter == 58 or not 0 <= component < width:
        raise ValueError('Only verified UV components can receive animation channels; Time is engine controlled')
    if binding_index(owner, mat, parameter, component) is not None:
        raise ValueError('This UV component already has a native animation channel')
    bindings = _bindings(owner)
    actions = _actions(owner)
    if not actions:
        raise ValueError('Import the native base animation before adding a material channel')
    changes = []
    for action in actions:
        data = json.loads(action.get('DSTS_animation_metadata', '{}'))
        if data.get('float_count') != len(bindings):
            raise ValueError('All imported native clips must match the existing float binding count')
        for other in bpy.data.objects:
            if other != owner and action in _actions(other):
                raise ValueError('Make shared actions single user before adding a native binding')
        action_slot_for(action, owner)  # Reject ambiguous slots before mutations.
        if len(data.get('float_masks', [])) > len(bindings):
            raise ValueError('Native clip has more float masks than declared bindings')
        changes.append((action, data, action['DSTS_animation_metadata']))
    value = float(_uniforms(_group(mat))[parameter].values[component].value)
    index = len(bindings)
    prop = f'DSTS_float_{index}'
    path = f'["{prop}"]'
    if prop in owner or any(f.data_path == path for action in actions for f in iter_action_fcurves(action, owner)):
        raise ValueError('An undeclared channel already occupies the new native binding index')
    tree = _group(mat)
    nodes = [n for n in tree.nodes if n.get(TAG) == f'uniform:{parameter}:{component}']
    if len(nodes) != 1:
        raise ValueError('Native UV preview component is missing or ambiguous')
    old_action, old_slot = owner.animation_data.action, getattr(owner.animation_data, 'action_slot', None)
    old_bindings = owner.get('DSTS_float_bindings')
    old_count = owner.get('DSTS_uv_binding_count')
    old_metadata = mat[META]
    strip_slots = []
    bindings.append({'name_hash': int(mat['DSTS_source_name_hash']),
                     'array_index': (parameter << 4) | component, 'flags': 8})
    try:
        owner[prop] = value
        for action, data, _ in changes:
            start = action.frame_range[0]
            curve = create_action_fcurve(action, owner, path, 0, 'Native UV')
            curve.keyframe_points.insert(start, value).interpolation = 'LINEAR'
            data['float_count'] = len(bindings)
            data['float_masks'] = list(data.get('float_masks', [])) + [255] * (len(bindings) - len(data.get('float_masks', [])))
            action['DSTS_animation_metadata'] = json.dumps(data)
            for track in owner.animation_data.nla_tracks:
                for strip in track.strips:
                    if strip.action == action and hasattr(strip, 'action_slot'):
                        strip_slots.append((strip, strip.action_slot))
                        strip.action_slot = action_slot_for(action, owner)
        owner['DSTS_float_bindings'] = json.dumps(bindings)
        owner['DSTS_uv_binding_count'] = len(bindings)
        # Rewire the new component in place. Existing preview nodes, unrelated
        # material settings and imported animation channels remain intact.
        nodes[0].outputs[0].driver_remove('default_value')
        metadata['targets'] = _targets(owner, mat, metadata['required'])
        metadata['signature'] = graph_signature(tree, owner)
        mat[META] = json.dumps(metadata)
    except Exception:
        # Creation can raise after Blender has inserted the FCurve. Scan the
        # previously absent path in every participating action, including the
        # failing invocation that did not return a curve object.
        for action in actions:
            for curve in list(iter_action_fcurves(action, owner)):
                if curve.data_path == path:
                    _remove_curve(action, curve)
        for action, _, old in changes:
            action['DSTS_animation_metadata'] = old
        for strip, slot in strip_slots:
            strip.action_slot = slot
        del owner[prop]
        if old_bindings is None:
            del owner['DSTS_float_bindings']
        else:
            owner['DSTS_float_bindings'] = old_bindings
        if old_count is None:
            if 'DSTS_uv_binding_count' in owner:
                del owner['DSTS_uv_binding_count']
        else:
            owner['DSTS_uv_binding_count'] = old_count
        # The original unbound component had one native-default driver.
        if not any(f.data_path == nodes[0].outputs[0].path_from_id('default_value')
                   for f in tree.animation_data.drivers):
            driver = nodes[0].outputs[0].driver_add('default_value').driver
            driver.type = 'SCRIPTED'
            _variable(driver, 'value', tree, _uniforms(tree)[parameter].path_from_id('values') + f'[{component}].value')
            driver.expression = 'value'
        mat[META] = old_metadata
        raise
    finally:
        _restore_action(owner, old_action, old_slot)
    update_previews(bpy.context.scene)
    return index


def key_component(owner, mat, parameter, component, action, native_frame, value, *, extend_clip=False):
    validate_material(mat)
    index = binding_index(owner, mat, parameter, component)
    if index is None:
        raise ValueError('Add a native animation channel for this component first')
    if action not in _actions(owner) or native_frame < 0 or not math.isfinite(value):
        raise ValueError('Choose an owned native clip, nonnegative frame and finite value')
    data = json.loads(action.get('DSTS_animation_metadata', '{}'))
    if data.get('float_count') != len(_bindings(owner)):
        raise ValueError('Clip float channel count does not match the native skeleton')
    _validate_clip_masks(action, (index,))
    frame = float(action.frame_range[0]) + native_frame
    if frame > action.frame_range[1] and not extend_clip:
        raise ValueError('Frame exceeds this native clip; explicitly enable Extend native clip duration')
    ad = owner.animation_data
    old_action, old_slot = ad.action, getattr(ad, 'action_slot', None)
    path = f'["DSTS_float_{index}"]'
    matches = [f for f in iter_action_fcurves(action, owner) if f.data_path == path]
    if len(matches) > 1:
        raise ValueError('Native material component has ambiguous animation curves')
    previous_curve = matches[0] if matches else None
    old_range = (action.use_frame_range, action.frame_start, action.frame_end)
    point_fields = ('co', 'interpolation', 'easing', 'handle_left_type', 'handle_right_type',
                    'handle_left', 'handle_right', 'amplitude', 'back', 'period')
    points = [{key: tuple(getattr(p, key)) if key in ('co', 'handle_left', 'handle_right') else getattr(p, key)
               for key in point_fields} for p in previous_curve.keyframe_points] if previous_curve else []
    strips = [(strip, strip.action_frame_end, strip.scale, getattr(strip, 'action_slot', None))
              for track in ad.nla_tracks for strip in track.strips if strip.action == action]
    try:
        curve = create_action_fcurve(action, owner, path, 0, 'Native UV')
        curve.keyframe_points.insert(frame, value).interpolation = 'LINEAR'
        curve.update()
        if frame > action.frame_range[1]:
            action.use_frame_range = True
            action.frame_end = frame
        for strip, _, scale, _ in strips:
            if hasattr(strip, 'action_slot'):
                strip.action_slot = action_slot_for(action, owner)
            if extend_clip:
                strip.action_frame_end = max(strip.action_frame_end, action.frame_end)
                strip.scale = scale
    except Exception:
        current = next((f for f in iter_action_fcurves(action, owner) if f.data_path == path), None)
        if current and previous_curve is None:
            _remove_curve(action, current)
        elif current:
            current.keyframe_points.clear()
            current.keyframe_points.add(len(points))
            for point, fields in zip(current.keyframe_points, points):
                for key, original in fields.items():
                    setattr(point, key, original)
            current.update()
        action.use_frame_range, action.frame_start, action.frame_end = old_range
        for strip, end, scale, slot in strips:
            strip.action_frame_end, strip.scale = end, scale
            if hasattr(strip, 'action_slot'):
                strip.action_slot = slot
        raise
    finally:
        _restore_action(owner, old_action, old_slot)
    update_previews(bpy.context.scene)


def _selection(context):
    obj = context.object
    mat = obj.active_material if obj and obj.type == 'MESH' else None
    owner = obj
    while owner and owner.type != 'ARMATURE':
        owner = owner.parent
    return owner, mat


class _UVSelection:
    @classmethod
    def poll(cls, context):
        owner, mat = _selection(context)
        return owner is not None and mat is not None and 'DSTS_source_name_hash' in mat


_ACTION_ITEMS = []
def _action_items(self, context):
    global _ACTION_ITEMS
    owner, _ = _selection(context)
    _ACTION_ITEMS = [(str(i), action.name, 'Native animation clip') for i, action in enumerate(_actions(owner))] if owner else []
    return _ACTION_ITEMS


class DSTS_OT_uv_configure(_UVSelection, bpy.types.Operator):
    bl_idname = 'dsts.uv_configure'
    bl_label = 'Configure verified UV preview'
    bl_description = 'Connect a reviewed native shader pair to its editable UV uniforms and animation channels'
    bl_options = {'REGISTER', 'UNDO'}
    shader_folder: StringProperty(name='Native shaders directory', subtype='DIR_PATH')

    def invoke(self, context, event):
        owner, mat = _selection(context)
        if owner and owner.get('DSTS_texture_source'):
            source = Path(owner['DSTS_texture_source'])
            self.shader_folder = str(source if source.is_dir() else source.parent / 'shaders')
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):
        owner, mat = _selection(context)
        if owner is None or mat is None:
            self.report({'ERROR'}, 'Select a native Time Stranger mesh material')
            return {'CANCELLED'}
        try:
            configure_preview(owner.users_collection[0], owner, self.shader_folder, True, mat)
        except (ValueError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_uv_add_binding(_UVSelection, bpy.types.Operator):
    bl_idname = 'dsts.uv_add_binding'
    bl_label = 'Animate native UV component'
    bl_description = 'Append this UV component to the native skeleton bindings and every imported animation clip'
    bl_options = {'REGISTER', 'UNDO'}
    parameter: IntProperty()
    component: IntProperty()

    def execute(self, context):
        owner, mat = _selection(context)
        try:
            add_binding(owner, mat, self.parameter, self.component)
        except (ValueError, KeyError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_uv_key(_UVSelection, bpy.types.Operator):
    bl_idname = 'dsts.uv_key'
    bl_label = 'Edit native UV key'
    bl_description = 'Insert or replace a native UV value at a zero-based frame in the selected animation clip'
    bl_options = {'REGISTER', 'UNDO'}
    parameter: IntProperty()
    component: IntProperty()
    clip: EnumProperty(name='Native clip', items=_action_items)
    native_frame: IntProperty(name='Native frame (zero based)', min=0)
    value: FloatProperty(name='Native value')
    extend_clip: BoolProperty(name='Extend native clip duration', default=False,
                             description='Allow keys past the clip end; this also extends its actor NLA strips')

    def invoke(self, context, event):
        owner, mat = _selection(context)
        if owner and mat:
            actions = _actions(owner)
            if owner.get(CLIP) in actions:
                self.clip = str(actions.index(owner[CLIP]))
            node = next((n for n in _group(mat).nodes if n.get(TAG) == f'uniform:{self.parameter}:{self.component}'), None)
            if node:
                self.value = node.outputs[0].default_value
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        owner, mat = _selection(context)
        try:
            action = _actions(owner)[int(self.clip)]
            key_component(owner, mat, self.parameter, self.component, action, self.native_frame, self.value,
                          extend_clip=self.extend_clip)
        except (ValueError, KeyError, IndexError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_uv_solo(_UVSelection, bpy.types.Operator):
    bl_idname = 'dsts.uv_solo'
    bl_label = 'Preview native UV clip only'
    bl_description = 'Sample one native clip for texture preview without changing actor pose or NLA track playback'
    bl_options = {'REGISTER', 'UNDO'}
    clip: EnumProperty(name='Clip', items=_action_items)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        owner, mat = _selection(context)
        try:
            action = _actions(owner)[int(self.clip)]
            strip = _preview_strip(owner, action)
            for candidate in bpy.data.materials:
                if candidate.get(OWNER) == owner:
                    metadata = json.loads(candidate[META])
                    _validate_clip_masks(action, (row[2] for row in metadata['targets']))
            data = json.loads(action.get('DSTS_animation_metadata', '{}'))
            rate = data.get('playback_rate', 0)
            if not math.isfinite(rate) or rate <= 0 or strip.scale <= 0:
                raise ValueError('Clip must retain a positive native playback rate and strip scale')
            owner[CLIP] = action
            owner['DSTS_uv_clock_enabled'] = True
            owner['DSTS_uv_clock_start'] = float(strip.frame_start)
            # NLA scale changes exported ANIM playback FPS, not the engine's
            # separate animator-rate field that advances unwrapped seconds.
            owner['DSTS_uv_clock_rate'] = 1.0
            owner.update_tag()
            update_previews(context.scene)
        except (ValueError, IndexError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_uv_defaults(_UVSelection, bpy.types.Operator):
    bl_idname = 'dsts.uv_defaults'
    bl_label = 'Use static native UV defaults'
    bl_description = 'Stop UV clip and clock preview and restore GEOM uniform values without changing actor animation'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        owner, _ = _selection(context)
        if CLIP in owner:
            del owner[CLIP]
        owner['DSTS_uv_clock_enabled'] = False
        owner.update_tag()
        update_previews(context.scene)
        return {'FINISHED'}


class DSTS_PT_texture_animation(bpy.types.Panel):
    bl_label = 'Native Texture Animation'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'DSTS'

    def draw(self, context):
        owner, mat = _selection(context)
        layout = self.layout
        if owner is None or mat is None:
            layout.label(text='Select an imported native mesh material.')
            return
        layout.operator(DSTS_OT_uv_configure.bl_idname)
        if META not in mat:
            layout.label(text='Only verified shader pairs support UV preview.', icon='INFO')
            return
        metadata = json.loads(mat[META])
        layout.label(text=metadata['label'])
        layout.label(text='UV transforms only; full shader parity is pending.')
        layout.label(text='Actor animation is unchanged by UV clip preview.')
        layout.operator(DSTS_OT_uv_solo.bl_idname, icon='PLAY')
        layout.operator(DSTS_OT_uv_defaults.bl_idname, icon='PAUSE')
        if owner.get(CLIP):
            layout.label(text='UV clip: ' + owner[CLIP].name)
        if mat.get('DSTS_uv_playback_error'):
            layout.label(text=mat['DSTS_uv_playback_error'], icon='ERROR')
        uniforms = _uniforms(_group(mat))
        for parameter, width in metadata['required'].items():
            parameter = int(parameter)
            if parameter == 58:
                layout.label(text='Unwrapped animator clock (seconds):')
                layout.prop(owner, '["DSTS_uv_clock_enabled"]', text='Preview native time')
                layout.prop(owner, '["DSTS_uv_clock_start"]', text='Start at scene frame')
                layout.prop(owner, '["DSTS_uv_clock_rate"]', text='Animator rate')
                continue
            node = uniforms.get(parameter)
            if node is None:
                layout.label(text='Missing native UV uniform', icon='ERROR')
                continue
            for component in range(width):
                row = layout.row(align=True)
                label = PARAMETER_NAMES[parameter] + ((' U' if component == 0 else ' V') if width == 2 else ' (radians)')
                row.prop(node.values[component], 'value', text=label + ' default')
                try:
                    bound = binding_index(owner, mat, parameter, component) is not None
                except ValueError:
                    layout.label(text='Ambiguous native binding', icon='ERROR')
                    continue
                op = row.operator(DSTS_OT_uv_key.bl_idname if bound else DSTS_OT_uv_add_binding.bl_idname,
                                  text='Key' if bound else 'Animate', icon='KEY_HLT' if bound else 'ADD')
                op.parameter, op.component = parameter, component


CLASSES = (DSTS_OT_uv_configure, DSTS_OT_uv_add_binding, DSTS_OT_uv_key,
           DSTS_OT_uv_solo, DSTS_OT_uv_defaults, DSTS_PT_texture_animation)
