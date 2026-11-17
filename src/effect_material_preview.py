"""Preview color and coverage for reviewed native rigid-effect shaders."""
import hashlib
import json
import struct
from collections import Counter
from pathlib import Path

from .blender_compat import create_action_fcurve
from .material import find_data_group
from .native_rest_scale import is_rest_track


# Primary vertex/pixel binaries verified together. The vertex-colored pairs
# pass COLOR0 through to a pixel shader that multiplies the entire sampled RGBA.
_PLAIN = '9dc95a33e619cf8ac2631e1c8e9b590dcc8f5cb7ea57a3fbb8c26a513cfd14cc'
_DISTORTED = 'ec2e6db19c6196603c90173b913a0ba36c51d5c6f0898b2732c402e87d2474f4'
_SCRATCH = 'ff6a87116b83b65069a382a2e1db43faac7bcd72fa458fd0ee04c6e65337afdc'
_PAIRS = {
    '436b22d8010c2b6bc254feb89e608eb4a748bc018c05b82ccfa6ca7b0c214ff7': {
        '5f48b8e8ec60295f07420c0bbef121d478af8c692b65633a5d2db8ae1723861c',
        '674d19cc7296ebf08ed388eeafd70c04a72886aa63c79f07ed0fcd9c3bae3204'},
    '8d84a140350b188376b0709293de721322ee3b1597026c2c55e0ecf59eb73aa8': {_PLAIN},
    'b3f7df6413b2ecdd160b5700ed4e04a3a20c752e9fc055f5e76e85571caa659c': {_PLAIN},
    '07a3da279ec964fd4743e6dd1c1c24033078bd7d6f8c788fef4f52b1d2351c4f': {_DISTORTED},
    'd9c53bae7f4b165bacd168586c8ef7ff3fecb151ef4592501ccdbf8d57754624': {_DISTORTED},
    '705078a7a550769faecea8f5ca9fd878db61fbd40d51c608f15d7060e3754942': {
        '7a72420d274da01c9c42d758b1fe99b4dee4ef505ba801789e580e47e8c41509'},
    'd6fadb2419a915763b5da8d2b18ac827bdb69cc7e523171a8de358499274f34d': {
        '8805bb5317be6f5ea43a1cb6a711e39abcacc6bb8e932459eb10ea7cf3e1e490'},
    'f2fc55f78d2d0fbdaff41569f5ade9402b50edd493005ad4411980dbe40aebba': {_SCRATCH},
    '84295cc82fcee7bbab5ad90fe0a9c056764e9d897977bb16284f9d5e5a7ba7d4': {_SCRATCH},
    '6183843ff16747d847f2ef742c8bc90037c56f1159e4c97e03a1e538d6874bc2': {
        'fd5331b3e495841f1a4a328393b83f1475028588077cc6f999b806a770224de7'},
    '6e3fee444bdd3a3800261fdf8b77503333aff90a5cb0948e32440321969dcd7c': {
        '5fea4de09142d2c29d103add61f2f884a3be322a176e46454e5d6f9ec17270e3'},
}
_VERTEX_COLOR = {_DISTORTED, '7a72420d274da01c9c42d758b1fe99b4dee4ef505ba801789e580e47e8c41509',
                 '8805bb5317be6f5ea43a1cb6a711e39abcacc6bb8e932459eb10ea7cf3e1e490'}
_EMISSIVE = {_SCRATCH, '674d19cc7296ebf08ed388eeafd70c04a72886aa63c79f07ed0fcd9c3bae3204',
             '5fea4de09142d2c29d103add61f2f884a3be322a176e46454e5d6f9ec17270e3'}


def _preview(mat, tree, owner, bindings, rest_action, pixel, uniforms):
    def node(kind, label, x, y):
        result = tree.nodes.new(kind)
        result.label = result.name = label
        result.location = (x, y)
        return result

    link = tree.links.new
    name_hash = int(mat['DSTS_source_name_hash'])

    def drive(socket, parameter, component=0):
        uniform = uniforms[parameter]
        initial = uniform.values[component].value
        socket.default_value = initial
        index = bindings.get((name_hash, parameter << 4 | component))
        if index is None:
            target, path = tree, uniform.path_from_id('values') + f'[{component}].value'
        else:
            key = f'DSTS_float_{index}'
            owner[key] = initial
            path = f'["{key}"]'
            # Without an authored base, Blender keeps the last evaluated value
            # after muting an NLA strip. The GEOM default must then take effect.
            curve = create_action_fcurve(rest_action, owner, path, group_name='Native material rest values')
            if not curve.keyframe_points:
                curve.keyframe_points.insert(1, initial)
            target = owner
        driver = socket.driver_add('default_value').driver
        driver.type = 'AVERAGE'
        variable = driver.variables.new()
        variable.type = 'SINGLE_PROP'
        variable.targets[0].id_type = target.id_type
        variable.targets[0].id = target
        variable.targets[0].data_path = path

    def vector_parameter(parameter, label, y):
        combine = node('ShaderNodeCombineXYZ', label, 0, y)
        for component in range(3):
            drive(combine.inputs[component], parameter, component)
        return combine.outputs[0]

    def multiply(first, second, label, x, y, vector=False):
        kind = 'ShaderNodeVectorMath' if vector else 'ShaderNodeMath'
        product = node(kind, label, x, y)
        product.operation = 'MULTIPLY'
        link(first, product.inputs[0])
        link(second, product.inputs[1])
        return product.outputs[0]

    texture = uniforms[24]
    tint = vector_parameter(25, 'Diffuse RGB', 800)
    alpha = node('ShaderNodeValue', 'Diffuse alpha', 0, 570)
    drive(alpha.outputs[0], 25, 3)
    rgb = multiply(texture.outputs['Color'], tint, 'Tinted texture', 220, 800, vector=True)
    opacity = multiply(texture.outputs['Alpha'], alpha.outputs[0], 'Texture opacity', 220, 570)
    if pixel in _VERTEX_COLOR:
        painted = node('ShaderNodeVertexColor', 'Native vertex color', 220, 340)
        painted.layer_name = 'Color'
        rgb = multiply(rgb, painted.outputs['Color'], 'Vertex tint', 440, 800, vector=True)
        opacity = multiply(opacity, painted.outputs['Alpha'], 'Vertex opacity', 440, 570)
    if pixel in _EMISSIVE:
        glow = node('ShaderNodeVectorMath', 'Native emission', 220, 1080)
        glow.operation = 'SCALE'
        link(vector_parameter(301, 'Emissive RGB', 1080), glow.inputs[0])
        drive(glow.inputs['Scale'], 302)
        total = node('ShaderNodeVectorMath', 'Surface plus emission', 660, 800)
        total.operation = 'ADD'
        link(rgb, total.inputs[0])
        link(glow.outputs[0], total.inputs[1])
        rgb = total.outputs[0]
    if pixel == _SCRATCH:
        # Native discard is alpha < AlphaTestRef, including equality survives.
        rejected = node('ShaderNodeMath', 'Below alpha threshold', 440, 340)
        rejected.operation = 'LESS_THAN'
        link(opacity, rejected.inputs[0])
        drive(rejected.inputs[1], 80)
        coverage = node('ShaderNodeMath', 'Cutout coverage', 660, 340)
        coverage.operation = 'SUBTRACT'
        coverage.inputs[0].default_value = 1
        link(rejected.outputs[0], coverage.inputs[1])
        opacity = coverage.outputs[0]

    visible = node('ShaderNodeValue', 'Material visibility', 660, 140)
    drive(visible.outputs[0], 401)
    opacity = multiply(opacity, visible.outputs[0], 'Visible opacity', 880, 440)
    # This family uses an inward-facing backdrop. Material Preview and Cycles
    # must agree about the visible side. Explicit native cull disable wins.
    mat.use_backface_culling = bool(_setting(uniforms, 129, (1,))[0])
    if mat.use_backface_culling:
        geometry = node('ShaderNodeNewGeometry', 'Effect face orientation', 660, -100)
        front = node('ShaderNodeMath', 'Front face', 880, 140)
        front.operation = 'SUBTRACT'
        front.inputs[0].default_value = 1
        link(geometry.outputs['Backfacing'], front.inputs[1])
        opacity = multiply(opacity, front.outputs[0], 'Facing opacity', 1100, 440)
    light = node('ShaderNodeEmission', 'Unlit effect color', 1100, 800)
    link(rgb, light.inputs['Color'])
    transparent = node('ShaderNodeBsdfTransparent', 'Transparent effect surface', 1100, 230)
    additive = _setting(uniforms, 127)[0] and _setting(uniforms, 125)[:2] == (0x302, 1)
    if additive:
        surface = node('ShaderNodeAddShader', 'Source alpha additive', 1320, 700)
        link(opacity, light.inputs['Strength'])
        link(transparent.outputs[0], surface.inputs[0])
        link(light.outputs[0], surface.inputs[1])
    else:
        surface = node('ShaderNodeMixShader', 'Alpha coverage', 1320, 700)
        link(opacity, surface.inputs[0])
        link(transparent.outputs[0], surface.inputs[1])
        link(light.outputs[0], surface.inputs[2])
    output = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    output.location = (1550, 700)
    link(surface.outputs[0], output.inputs['Shader'])
    mat.diffuse_color = [min(1, max(0, v.value)) for v in uniforms[25].values]
    mat['DSTS_effect_material_preview'] = 'Native color and coverage'


def _setting(uniforms, parameter, default=(0, 0, 0, 0)):
    setting = uniforms.get(parameter)
    if setting is None:
        return default
    return struct.unpack('<4I', bytes(v.value for v in setting.values))


def _shader_hashes(name, folders):
    if not name or name in ('.', '..') or any(c in name for c in '/\\:'):
        return None, None
    hashes = []
    for stage in ('vp', 'fp'):
        basename = f'{name}_{stage}.shad'
        for folder in folders:
            path = folder / basename
            if path.is_file():
                try:
                    hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
                except OSError:
                    return None, None
                break
        else:
            return None, None
    return tuple(hashes)


def _unsupported_state(uniforms, pixel):
    if _setting(uniforms, 129, (1,))[0] and _setting(uniforms, 128, (0x405,))[0] != 0x405:
        return 'unreviewed face culling mode'
    if _setting(uniforms, 127)[0]:
        blend = _setting(uniforms, 125)
        if (blend[:2] not in ((0x302, 1), (0x302, 0x303)) or any(blend[2:]) or
                _setting(uniforms, 126, (0x8006,))[0] != 0x8006):
            return 'unreviewed blend factors or equation'
    if _setting(uniforms, 124)[0] and pixel != _SCRATCH:
        return 'unreviewed alpha test'
    return ''


def configure_effect_materials(collection, owner, source):
    """Configure newly imported rigs, leaving unrelated shader families alone."""
    if not owner or not owner.get('DSTS_rigid_scene') or not owner.animation_data:
        return
    ad = owner.animation_data
    rest_action = next((t.strips[0].action for t in ad.nla_tracks if is_rest_track(t)), None)
    if not rest_action:
        return
    channels = [(row['name_hash'], row['array_index']) if row['flags'] == 8 else None
                for row in json.loads(owner.get('DSTS_float_bindings', '[]'))]
    bindings = {key: i for i, key in enumerate(channels) if key is not None}
    ambiguous = {key[0] for key, count in Counter(channels).items() if key is not None and count > 1}
    materials = dict.fromkeys(m for obj in collection.objects if obj.type == 'MESH' for m in obj.data.materials)
    hashes, notes = {}, []
    folders = dict.fromkeys([source.parent / 'shaders', *(
        Path(folder).parent / 'shaders' for folder in collection.get('DSTS_texture_search_dirs', '').splitlines()
        if Path(folder).name.lower() == 'images')])
    previous, slot = ad.action, ad.action_slot
    try:
        for mat in materials:
            if mat.get('DSTS_effect_material_preview'):
                continue
            tree = find_data_group(mat).node_tree
            parameters = [(int(n['DSTS_parameter_id']), n) for n in tree.nodes if 'DSTS_parameter_id' in n]
            uniforms = dict(parameters)
            if 401 not in uniforms:
                continue
            if len(uniforms) != len(parameters) or int(mat['DSTS_source_name_hash']) in ambiguous:
                notes.append(f'{mat.name}: ambiguous native material bindings')
                continue
            data = next(n for n in tree.nodes if n.bl_idname == 'DSTS_ShaderData')
            name = data.shader_strings[0].value
            if name not in hashes:
                hashes[name] = _shader_hashes(name, folders)
            vertex, pixel = hashes[name]
            if pixel not in _PAIRS.get(vertex, ()):
                notes.append(f'{mat.name}: primary shader pair unavailable or unreviewed')
                continue
            required = {25: 4, 401: 1}
            if pixel in _EMISSIVE:
                required.update({301: 3, 302: 1})
            if pixel == _SCRATCH:
                required[80] = 1
            if (24 not in uniforms or uniforms[24].type != 'TEX_IMAGE' or
                    any(p not in uniforms or uniforms[p].bl_idname != 'DSTS_ShaderFloatUniform' or
                        len(uniforms[p].values) != width for p, width in required.items()) or
                    any(uniforms[p].bl_idname != 'DSTS_ShaderSetting' or len(uniforms[p].values) != 16
                        for p in (124, 125, 126, 127, 128, 129) if p in uniforms)):
                notes.append(f'{mat.name}: primary shader lacks the expected native uniform bindings')
                continue
            unsupported = _unsupported_state(uniforms, pixel)
            if unsupported:
                notes.append(f'{mat.name}: {unsupported}')
                continue
            _preview(mat, tree, owner, bindings, rest_action, pixel, uniforms)
    finally:
        ad.action = previous
        if previous and slot:
            ad.action_slot = slot
    if notes:
        collection['DSTS_effect_material_preview_notes'] = 'Using the generic material preview for:\n' + '\n'.join(notes)
    elif 'DSTS_effect_material_preview_notes' in collection:
        del collection['DSTS_effect_material_preview_notes']
