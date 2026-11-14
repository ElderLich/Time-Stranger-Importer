"""Real native UV binding -> Blender preview -> GEOM/ANIM editing regressions."""
import argparse
import importlib.util
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--addon-root', type=Path, default=ROOT,
                    help='Load only this source or installed add-on directory')
parser.add_argument('--report', type=Path,
                    help='Write the JSON report and render evidence outside the add-on directory')
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
ADDON_ROOT = args.addon_root.resolve()
REPORT = (args.report or ROOT / 'local/texture-animation' / f'blender-{bpy.app.version[0]}.{bpy.app.version[1]}.json').resolve()
REPORT.parent.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', ADDON_ROOT / '__init__.py',
                                            submodule_search_locations=[str(ADDON_ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
assert Path(addon.__file__).resolve() == ADDON_ROOT / '__init__.py'
addon.register()
from Time_Stranger_Importer.src import texture_animation as uv
from Time_Stranger_Importer.src import geom, material, animation
from Time_Stranger_Importer.src.anim.BinaryHundredLine import AnimFileBinary
from Time_Stranger_Importer.src.anim.InterfaceHundredLine import AnimFileHundredLine
from Time_Stranger_Importer.src.backend import load_format_modules
from Time_Stranger_Importer.src.blender_compat import iter_action_fcurves

for cls in uv.CLASSES:
    if not getattr(bpy.types, cls.__name__, None):
        bpy.utils.register_class(cls)

FIXTURES = Path(r'D:/Digimon Modding/Time Stranger Extracted/app_0.dx11')
backend = load_format_modules()[0]
checks = []


def import_fixture(name):
    collection = geom.import_geom(bpy.context, str(FIXTURES / (name + '.geom')))
    owner = next(obj for obj in collection.objects if obj.type == 'ARMATURE')
    notes = uv.configure_preview(collection, owner, FIXTURES / (name + '.geom'))
    print('UV_CONFIGURE', name, f'{len(notes)} unsupported material families retained')
    configured = [mat for obj in collection.objects if obj.type == 'MESH' for mat in obj.data.materials if uv.META in mat]
    return collection, owner, list(dict.fromkeys(configured))


def select_material(collection, mat):
    obj = next(obj for obj in collection.objects if obj.type == 'MESH' and mat in list(obj.data.materials))
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.active_material_index = list(obj.data.materials).index(mat)
    return obj


def preview_value(mat, parameter, component):
    tree = material.find_data_group(mat).node_tree
    role = f'uniform:{parameter}:{component}'
    node = next(n for n in tree.nodes if n.get(uv.TAG) == role)
    return node.outputs[0].default_value


def exported_float(owner, action, index):
    metadata = json.loads(action['DSTS_animation_metadata'])
    output = animation.build_from_action(owner, action, metadata['playback_rate'])
    with tempfile.TemporaryDirectory(prefix='dsts_uv_anim_') as folder:
        path = Path(folder) / 'material.anim'
        output.to_file(str(path))
        binary = AnimFileBinary()
        binary.read(str(path))
        decoded = AnimFileHundredLine.from_binary(binary)
    assert decoded.float_channel_count == len(uv._bindings(owner))
    return decoded


def actor_state(owner):
    return {'pose': [tuple(value for row in bone.matrix for value in row) for bone in owner.pose.bones],
            'tracks': [(track.name, track.mute, track.is_solo,
                        [(strip.name, strip.blend_type, strip.influence, strip.frame_start,
                          strip.frame_end, strip.scale, strip.action.name) for strip in track.strips])
                       for track in owner.animation_data.nla_tracks],
            'action': owner.animation_data.action}


def render_uv(mat, parameter, coordinates=(.25, .4), *, artifact=None):
    """Read the actual Blender shader evaluation through a diagnostic output.

    The original native material and its export metadata stay untouched.
    Offsetting the output keeps negative native UV coordinates measurable.
    """
    scene = bpy.context.scene
    hidden = [(obj, obj.hide_render) for obj in scene.objects]
    old_camera = scene.camera
    for obj, _ in hidden:
        obj.hide_render = True
    tree = uv._group(mat).copy()
    tree.name = 'UV arithmetic render probe'
    texture = next(n for n in tree.nodes if n.type == 'TEX_IMAGE' and n.get('DSTS_parameter_id') == parameter)
    incoming = texture.outputs['Color'] if artifact else texture.inputs['Vector'].links[0].from_socket
    tree.interface.new_socket(name='Diagnostic UV', in_out='OUTPUT', socket_type='NodeSocketColor')
    output = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT')
    if artifact:
        tree.links.new(incoming, output.inputs['Diagnostic UV'])
    else:
        add = tree.nodes.new('ShaderNodeVectorMath')
        add.operation = 'ADD'
        add.inputs[1].default_value = (4, 4, 0)
        tree.links.new(incoming, add.inputs[0])
        tree.links.new(add.outputs[0], output.inputs['Diagnostic UV'])
    diagnostic = bpy.data.materials.new('UV diagnostic')
    diagnostic.use_nodes = True
    diagnostic.node_tree.nodes.clear()
    group = diagnostic.node_tree.nodes.new('ShaderNodeGroup')
    group.node_tree = tree
    emission = diagnostic.node_tree.nodes.new('ShaderNodeEmission')
    output = diagnostic.node_tree.nodes.new('ShaderNodeOutputMaterial')
    diagnostic.node_tree.links.new(group.outputs['Diagnostic UV'], emission.inputs['Color'])
    diagnostic.node_tree.links.new(emission.outputs[0], output.inputs['Surface'])
    mesh = bpy.data.meshes.new('UV diagnostic plane')
    mesh.from_pydata([(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)], [], [(0,1,2,3)])
    for name in ('uv1', 'uv2', 'uv3'):
        layer = mesh.uv_layers.new(name=name)
        for item, corner in zip(layer.data, ((0,0), (1,0), (1,1), (0,1))):
            item.uv = corner if artifact else coordinates
    plane = bpy.data.objects.new('UV diagnostic plane', mesh)
    scene.collection.objects.link(plane)
    plane.data.materials.append(diagnostic)
    data = bpy.data.cameras.new('UV diagnostic camera')
    data.type, data.ortho_scale = 'ORTHO', 2 if artifact else 1
    camera = bpy.data.objects.new('UV diagnostic camera', data)
    scene.collection.objects.link(camera)
    camera.location = (0,0,3)
    scene.camera = camera
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.render.resolution_x = scene.render.resolution_y = 256 if artifact else 8
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG' if artifact else 'OPEN_EXR'
    scene.render.image_settings.color_depth = '8' if artifact else '32'
    try:
        if artifact:
            scene.render.filepath = str(artifact)
            bpy.ops.render.render(write_still=True)
            return hashlib.sha256(artifact.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='dsts_uv_render_') as folder:
            scene.render.filepath = str(Path(folder) / 'probe.exr')
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(scene.render.filepath, check_existing=False)
            pixel = tuple(image.pixels[(4 * 8 + 4) * 4:(4 * 8 + 4) * 4 + 3])
            bpy.data.images.remove(image)
        return (pixel[0]-4, pixel[1]-4)
    finally:
        scene.camera = old_camera
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.objects.remove(plane, do_unlink=True)
        bpy.data.materials.remove(diagnostic)
        bpy.data.node_groups.remove(tree)
        for obj, value in hidden:
            obj.hide_render = value


def close_vector(actual, expected):
    assert max(abs(a-b) for a,b in zip(actual, expected)) < .0002, (actual, expected)


def main():
    collection, owner, mats = import_fixture('chr000')
    assert len(mats) == 1, 'Reviewed source shader was not configured'
    mat = mats[0]
    select_material(collection, mat)
    action = uv._actions(owner)[0]
    metadata = json.loads(action['DSTS_animation_metadata'])
    index = uv.binding_index(owner, mat, 68, 0)
    assert index == 0
    assert bpy.ops.dsts.uv_key(parameter=68, component=0, clip='0', native_frame=0, value=0.0) == {'FINISHED'}
    before = actor_state(owner)
    try:
        uv.key_component(owner, mat, 68, 0, action, 30, .75)
        raise AssertionError('Clip duration extended without explicit opt-in')
    except ValueError as error:
        assert 'Extend native clip' in str(error)
    assert actor_state(owner) == before
    assert bpy.ops.dsts.uv_key(parameter=68, component=0, clip='0', native_frame=30, value=.75, extend_clip=True) == {'FINISHED'}
    before = actor_state(owner)
    assert bpy.ops.dsts.uv_solo(clip='0') == {'FINISHED'}
    assert actor_state(owner) == before
    # The strip maps native frames to scene frames at its actual playback rate.
    strip = next(strip for track in owner.animation_data.nla_tracks for strip in track.strips if strip.action == action)
    frame = strip.frame_start + 15 * strip.scale
    bpy.context.scene.frame_set(int(frame), subframe=frame-int(frame))
    bpy.context.view_layer.update()
    assert abs(preview_value(mat, 68, 0) - .375) < 2e-5, preview_value(mat, 68, 0)
    before = actor_state(owner)
    assert bpy.ops.dsts.uv_defaults() == {'FINISHED'}
    assert actor_state(owner) == before
    assert abs(preview_value(mat, 68, 0)) < 2e-5
    assert bpy.ops.dsts.uv_solo(clip='0') == {'FINISHED'}
    assert actor_state(owner) == before
    assert abs(preview_value(mat, 68, 0) - .375) < 2e-5
    # Preview drivers must not overwrite the static GEOM uniform defaults.
    assert list(next(u.value for u in material.export_material(mat).uniforms if u.parameter_id == 68)) == [0, 0]
    decoded = exported_float(owner, action, index)
    assert abs(decoded.float_channels[index][30] - .75) < 2e-5
    assert abs(decoded.playback_rate - metadata['playback_rate']) < 1e-4
    checks.append('existing native UV channels: public keys, mixed FPS preview, native export, enable/disable preserves actor')
    before = actor_state(owner)
    before_paths = [(f.data_path, f.array_index) for f in iter_action_fcurves(action, owner)]
    original_create = uv.create_action_fcurve
    def fail_after_creating(*args, **kwargs):
        original_create(*args, **kwargs)
        raise RuntimeError('Injected key creation failure')
    uv.create_action_fcurve = fail_after_creating
    try:
        uv.key_component(owner, mat, 68, 1, action, 5, .9)
        raise AssertionError('Injected key failure was ignored')
    except RuntimeError as error:
        assert 'Injected' in str(error)
    finally:
        uv.create_action_fcurve = original_create
    assert actor_state(owner) == before
    assert [(f.data_path, f.array_index) for f in iter_action_fcurves(action, owner)] == before_paths
    checks.append('out-of-duration keys reject by default; failed key edits restore curve, action and NLA state')
    masked_action = next(a for a in uv._actions(owner) if a.name == 'chr000_ba01')
    before = actor_state(owner)
    try:
        uv.key_component(owner, mat, 68, 0, masked_action, 0, .9)
        raise AssertionError('Masked UV channel accepted an unverified semantic edit')
    except ValueError as error:
        assert 'masked UV blending' in str(error)
    before_clip = owner.get(uv.CLIP)
    try:
        bpy.ops.dsts.uv_solo(clip=str(uv._actions(owner).index(masked_action)))
        raise AssertionError('Masked UV clip preview was accepted')
    except RuntimeError as error:
        assert 'masked UV blending' in str(error)
    assert actor_state(owner) == before and owner.get(uv.CLIP) == before_clip
    checks.append('nondefault native float masks reject UV editing and preview without altering raw channels')
    # An unknown shader is explicitly rejected without changing source nodes.
    data_node = next(n for n in uv._group(mat).nodes if n.bl_idname == 'DSTS_ShaderData')
    original = data_node.shader_strings[0].value
    data_node.shader_strings[0].value = '0' * 84
    try:
        uv.configure_preview(collection, owner, FIXTURES / 'chr000.geom', True, mat)
        raise AssertionError('Unknown shader preview was accepted')
    except ValueError:
        pass
    data_node.shader_strings[0].value = original
    uv.validate_material(mat)
    checks.append('unknown shader rejected; unchanged configured preview retained')
    # A real rotation donor has a proven radian transform and accepts an appended
    # material channel through the shared native Skeleton and ANIM bindings.
    rot_collection, rot_owner, rot_mats = import_fixture('ef_e_com_110')
    assert rot_mats
    rot_mat = rot_mats[0]
    select_material(rot_collection, rot_mat)
    rot_index = uv.binding_index(rot_owner, rot_mat, 94, 0)
    if rot_index is None:
        # An interrupted multi-clip binding edit must restore every source
        # record and previously appended curve, not leave mismatched counts.
        if len(uv._actions(rot_owner)) > 1:
            original_create = uv.create_action_fcurve
            call_count = [0]
            before_bindings = rot_owner['DSTS_float_bindings']
            before_metadata = [(a, a['DSTS_animation_metadata']) for a in uv._actions(rot_owner)]
            before_curves = {a.name: [(f.data_path,f.array_index) for f in iter_action_fcurves(a, rot_owner)]
                             for a,_ in before_metadata}
            def failing_create(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 2:
                    raise RuntimeError('Injected channel creation failure')
                return original_create(*args, **kwargs)
            uv.create_action_fcurve = failing_create
            try:
                uv.add_binding(rot_owner, rot_mat, 94, 0)
                raise AssertionError('Injected failure was ignored')
            except RuntimeError as error:
                assert 'Injected' in str(error)
            finally:
                uv.create_action_fcurve = original_create
            assert rot_owner['DSTS_float_bindings'] == before_bindings
            assert all(a['DSTS_animation_metadata'] == value for a,value in before_metadata)
            assert before_curves == {a.name: [(f.data_path,f.array_index) for f in iter_action_fcurves(a, rot_owner)]
                                     for a,_ in before_metadata}
            uv.validate_material(rot_mat)
            checks.append('injected multi-clip binding failure restores native records, curves and preview')
        assert bpy.ops.dsts.uv_add_binding(parameter=94, component=0) == {'FINISHED'}
        rot_index = uv.binding_index(rot_owner, rot_mat, 94, 0)
    rot_action = uv._actions(rot_owner)[0]
    uv.key_component(rot_owner, rot_mat, 94, 0, rot_action, 0, 0)
    uv.key_component(rot_owner, rot_mat, 94, 0, rot_action, 15, 1.57079632679, extend_clip=True)
    rot_native = exported_float(rot_owner, rot_action, rot_index)
    assert abs(rot_native.float_channels[rot_index][15] - 1.57079632679) < 2e-5
    native_geom = geom.export_geom(rot_collection)
    assert len(native_geom.skeleton.float_channels) == len(uv._bindings(rot_owner))
    binding = native_geom.skeleton.float_channels[rot_index]
    assert binding.array_index == 94 << 4 and binding.flags == 8
    with tempfile.TemporaryDirectory(prefix='dsts_uv_geom_') as folder:
        path = Path(folder) / 'edited.geom'
        native_geom.to_file(str(path))
        # This existing effect has a nondecomposable native bind pose, retained
        # by the source-snapshot path. Use the normal import warning policy.
        old = backend.get_throw_errors()
        try:
            backend.set_throw_errors(False)
            reopened = backend.Geom.from_file(str(path))
        finally:
            backend.set_throw_errors(old)
        assert reopened.skeleton.float_channels[rot_index].array_index == 94 << 4
    checks.append('rotation: new native binding, all clip counts, GEOM/ANIM serialization')
    scale_collection, scale_owner, scale_mats = import_fixture('ef_e_m430_010_01')
    assert len(scale_mats) == 2
    scale_mat = scale_mats[0]
    scale_uniforms = uv._uniforms(uv._group(scale_mat))
    for parameter, values in ((68, (.125,.25)), (103, (-2,0))):
        for item, value in zip(scale_uniforms[parameter].values, values):
            item.value = value
    bpy.context.scene.frame_set(7)
    close_vector(render_uv(scale_mat, 24), (-.75, 0))
    native_scale = material.export_material(scale_mat)
    assert list(next(row.value for row in native_scale.uniforms if row.parameter_id == 103)) == [-2,0]
    checks.append('native offset-before-scale shader render and export preserve negative/zero scale')
    scroll_collection, scroll_owner, scroll_mats = import_fixture('atchiddentreasure00')
    assert len(scroll_mats) == 1
    scroll_mat = scroll_mats[0]
    assert abs(uv._uniforms(uv._group(scroll_mat))[59].values[1].value + 1) < 1e-6
    scroll_owner['DSTS_uv_clock_start'] = 1.0
    scroll_owner['DSTS_uv_clock_rate'] = 1.0
    scroll_owner.update_tag()
    bpy.context.scene.render.fps, bpy.context.scene.render.fps_base = 24, 1
    bpy.context.scene.frame_set(31)  # 1.25s, beyond the one-frame source clip.
    clock = next(n for n in uv._group(scroll_mat).nodes if n.get(uv.TAG) == 'unwrapped-seconds')
    assert abs(clock.outputs[0].default_value-1.25) < 1e-6, clock.outputs[0].default_value
    close_vector(render_uv(scroll_mat, 24), (.25, 1.15))  # frac(-1.25)=.75
    close_vector(render_uv(scroll_mat, 303), (.25, .4))  # Third UV remains fixed.
    scroll_owner['DSTS_uv_clock_start'] = 1 - 86400 * 24
    scroll_owner.update_tag()
    bpy.context.scene.frame_set(30)
    bpy.context.scene.frame_set(31)
    assert abs(clock.outputs[0].default_value-1.25) < 1e-6
    scroll_owner['DSTS_uv_clock_start'] = 1
    scroll_owner.update_tag()
    bpy.context.scene.render.fps, bpy.context.scene.render.fps_base = 30, 1.001
    bpy.context.scene.frame_set(61)
    assert abs(clock.outputs[0].default_value-2.002) < 1e-5, (clock.outputs[0].default_value, bpy.context.scene.render.fps, bpy.context.scene.render.fps_base, scroll_owner['DSTS_uv_clock_start'])
    bpy.context.scene.render.fps, bpy.context.scene.render.fps_base = 24, 1
    select_material(scroll_collection, scroll_mat)
    assert bpy.ops.dsts.uv_add_binding(parameter=59, component=0) == {'FINISHED'}
    scroll_action = uv._actions(scroll_owner)[0]
    uv.key_component(scroll_owner, scroll_mat, 59, 0, scroll_action, 0, -.25)
    uv.key_component(scroll_owner, scroll_mat, 59, 0, scroll_action, 30, 0, extend_clip=True)
    scroll_index = uv.binding_index(scroll_owner, scroll_mat, 59, 0)
    scroll_native = exported_float(scroll_owner, scroll_action, scroll_index)
    assert abs(scroll_native.float_channels[scroll_index][0]+.25) < 1e-6
    assert abs(scroll_native.float_channels[scroll_index][30]) < 1e-6
    retimed_strip = uv._preview_strip(scroll_owner, scroll_action)
    retimed_strip.scale *= 2
    assert bpy.ops.dsts.uv_solo(clip='0') == {'FINISHED'}
    bpy.context.scene.frame_set(25)
    assert abs(clock.outputs[0].default_value-1.0) < 1e-6
    assert scroll_owner['DSTS_uv_clock_rate'] == 1.0
    assert bpy.ops.dsts.uv_defaults() == {'FINISHED'}
    bpy.context.scene.frame_set(62)
    assert abs(clock.outputs[0].default_value) < 1e-6
    checks.append('native scroll renders negative speed with fractional wrap; fixed emissive UV and unwrapped seconds')
    clock_scene = bpy.context.scene
    moved_scene = bpy.data.scenes.new('Moved scrolling actor scene')
    clock_scene.collection.children.unlink(scroll_collection)
    moved_scene.collection.children.link(scroll_collection)
    bpy.context.window.scene = moved_scene
    moved_scene.render.fps = 12
    scroll_owner['DSTS_uv_clock_start'] = 1.0
    scroll_owner['DSTS_uv_clock_rate'] = 1.0
    scroll_owner['DSTS_uv_clock_enabled'] = True
    scroll_owner.update_tag()
    moved_scene.frame_set(25)
    assert abs(clock.outputs[0].default_value-2.0) < 1e-6, clock.outputs[0].default_value
    clock_scene.collection.children.link(scroll_collection)
    try:
        uv.configure_preview(scroll_collection, scroll_owner, FIXTURES / 'atchiddentreasure00.geom', True, scroll_mat)
        raise AssertionError('A shared actor used an ambiguous scene clock')
    except ValueError as error:
        assert 'exactly one scene' in str(error)
    moved_scene.collection.children.unlink(scroll_collection)
    bpy.context.window.scene = clock_scene
    clock_scene.frame_set(25)
    assert abs(clock.outputs[0].default_value-1.0) < 1e-6
    uv.validate_material(scroll_mat)
    bpy.data.scenes.remove(moved_scene)
    checks.append('moved scrolling assets use their current scene FPS; ambiguous linked scene clocks reject')
    select_material(rot_collection, rot_mat)
    for item, value in zip(uv._uniforms(uv._group(rot_mat))[68].values, (.125,.25)):
        item.value = value
    assert bpy.ops.dsts.uv_solo(clip='0') == {'FINISHED'}
    rot_strip = uv._preview_strip(rot_owner, rot_action)
    rot_frame = rot_strip.frame_start + 15 * rot_strip.scale
    bpy.context.scene.frame_set(int(rot_frame), subframe=rot_frame-int(rot_frame))
    close_vector(render_uv(rot_mat, 24), (-.275, .5))
    checks.append('native radian rotation render uses origin pivot then offset')
    posed_collection, posed_owner, posed_mats = import_fixture('chr074')
    assert len(posed_mats) == 1
    posed_mat = posed_mats[0]
    select_material(posed_collection, posed_mat)
    assert uv.binding_index(posed_owner, posed_mat, 68, 0) is None
    original_create = uv.create_action_fcurve
    calls = [0]
    before_bindings = posed_owner['DSTS_float_bindings']
    before_metadata = [(a, a['DSTS_animation_metadata']) for a in uv._actions(posed_owner)]
    before_curves = {a.name: [(f.data_path,f.array_index) for f in iter_action_fcurves(a, posed_owner)]
                     for a,_ in before_metadata}
    def failing_create(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 2:
            original_create(*args, **kwargs)
            raise RuntimeError('Injected channel creation failure')
        return original_create(*args, **kwargs)
    uv.create_action_fcurve = failing_create
    try:
        uv.add_binding(posed_owner, posed_mat, 68, 0)
        raise AssertionError('Injected failure was ignored')
    except RuntimeError as error:
        assert 'Injected' in str(error)
    finally:
        uv.create_action_fcurve = original_create
    assert calls[0] == 2
    assert posed_owner['DSTS_float_bindings'] == before_bindings
    assert all(a['DSTS_animation_metadata'] == value for a,value in before_metadata)
    assert before_curves == {a.name: [(f.data_path,f.array_index) for f in iter_action_fcurves(a, posed_owner)]
                             for a,_ in before_metadata}
    uv.validate_material(posed_mat)
    checks.append('injected multi-clip binding failure restores all records, curves and preview')
    for track in posed_owner.animation_data.nla_tracks:
        track.mute = not (track.name == 'chr074' or track.name == 'chr074_bs01')
    poses = []
    for sample_frame in (4, 16, 28):
        bpy.context.scene.frame_set(sample_frame)
        before = actor_state(posed_owner)
        poses.append(before['pose'])
        assert bpy.ops.dsts.uv_solo(clip='0') == {'FINISHED'}
        bpy.context.view_layer.update()
        assert actor_state(posed_owner) == before
        assert bpy.ops.dsts.uv_defaults() == {'FINISHED'}
        bpy.context.view_layer.update()
        assert actor_state(posed_owner) == before
    assert poses[0] != poses[1], 'Actor fixture did not exercise animated pose'
    checks.append('animated actor base+motion pose and every NLA setting unchanged across UV preview toggles')
    changed_node = next(node for node in uv._group(posed_mat).nodes if node.get(uv.TAG) == '24:offset')
    changed_node.operation = 'SUBTRACT'
    try:
        material.export_material(posed_mat)
        raise AssertionError('Unsupported preview edit was silently exported')
    except ValueError as error:
        assert 'changed' in str(error)
    changed_node.operation = 'ADD'
    uv.validate_material(posed_mat)
    changed_node.mute = True
    try:
        material.export_material(posed_mat)
        raise AssertionError('Muted native UV arithmetic was silently exported')
    except ValueError as error:
        assert 'changed' in str(error)
    changed_node.mute = False
    clock_curve = next(curve for curve in uv._group(scroll_mat).animation_data.drivers
                       if curve.data_path == clock.outputs[0].path_from_id('default_value'))
    clock_curve.mute = True
    try:
        material.export_material(scroll_mat)
        raise AssertionError('Muted native clock driver was silently exported')
    except ValueError as error:
        assert 'changed' in str(error)
    clock_curve.mute = False
    uv.validate_material(posed_mat)
    uv.validate_material(scroll_mat)
    checks.append('unsupported preview graph edits explicitly reject native export')
    # Each imported actor belongs to its own scene. Scrubbing one must not
    # update material values belonging only to the other scene.
    primary = bpy.context.scene
    secondary = bpy.data.scenes.new('Independent UV preview scene')
    primary.collection.children.unlink(posed_collection)
    secondary.collection.children.link(posed_collection)
    bpy.context.window.scene = secondary
    secondary.render.fps = 24
    second_action = uv._actions(posed_owner)[0]
    uv.key_component(posed_owner, posed_mat, 68, 1, second_action, 0, .2)
    uv.key_component(posed_owner, posed_mat, 68, 1, second_action, 30, .8, extend_clip=True)
    posed_owner[uv.CLIP] = second_action
    secondary.frame_set(7)
    independent_value = preview_value(posed_mat, 68, 1)
    bpy.context.window.scene = primary
    primary.frame_set(19)
    assert preview_value(posed_mat, 68, 1) == independent_value
    checks.append('frame handler leaves independent scenes and their material preview values untouched')
    bpy.context.scene.frame_set(1)
    before_image = render_uv(mat, 24, artifact=REPORT.parent / 'texture-before.png')
    bpy.context.scene.frame_set(25)
    after_image = render_uv(mat, 24, artifact=REPORT.parent / 'texture-after.png')
    assert before_image != after_image
    checks.append('native ColorSampler texture visibly changes under edited UV animation in Cycles render')
    names = (owner.name, mat.name, rot_owner.name, rot_mat.name)
    with tempfile.TemporaryDirectory(prefix='dsts_uv_blend_') as folder:
        path = Path(folder) / 'texture-animation.blend'
        bpy.ops.file.pack_all()
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        uv.unregister_handlers()
        assert uv.update_previews not in bpy.app.handlers.frame_change_pre
        uv.register_handlers()
        uv.register_handlers()
        assert list(bpy.app.handlers.frame_change_pre).count(uv.update_previews) == 1
        bpy.ops.wm.open_mainfile(filepath=str(path))
        owner, mat = bpy.data.objects[names[0]], bpy.data.materials[names[1]]
        uv.validate_material(mat)
        uv.validate_material(bpy.data.materials[names[3]])
        bpy.context.scene.frame_set(int(frame), subframe=frame-int(frame))
        bpy.context.view_layer.update()
        assert abs(preview_value(mat, 68, 0) - .375) < 2e-5
    checks.append('saved .blend retains native bindings and functional UV drivers')
    for name, module in sys.modules.items():
        if name.startswith('Time_Stranger_Importer.') and getattr(module, '__file__', None):
            assert Path(module.__file__).resolve().is_relative_to(ADDON_ROOT), (name, module.__file__)
    result = {'blender': bpy.app.version_string, 'addon_root': str(ADDON_ROOT),
              'addon_version': list(addon.bl_info['version']), 'module_path': str(Path(uv.__file__).resolve()),
              'checks': checks}
    REPORT.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
