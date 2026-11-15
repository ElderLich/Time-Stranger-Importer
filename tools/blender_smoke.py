"""Run in Blender; optionally select an installed --addon-root after ``--``."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile

import bpy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--addon-root', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--workspace-root', type=Path, default=Path(__file__).resolve().parents[2])
parser.add_argument('--nc-export', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
ROOT = args.addon_root.resolve()
WORKSPACE = args.workspace_root.resolve()
spec = importlib.util.spec_from_file_location("Time_Stranger_Importer", ROOT / "__init__.py",
                                            submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.blender_compat import create_action_fcurve, create_nla_track, iter_action_fcurves
from Time_Stranger_Importer.src.animation import build_action, build_from_action, source_rate_from_strip
from Time_Stranger_Importer.src.anim import AnimFileHundredLine
from Time_Stranger_Importer.src.anim.BinaryHundredLine import AnimFileBinary
from Time_Stranger_Importer.src.backend import load_format_modules

print("RUNTIME", bpy.app.version_string, sys.version)
load_format_modules()
backend = load_format_modules()[0]
borrow_target = bpy.data.meshes.new('Borrowed render flags')
borrow_donor = backend.Mesh()
borrow_donor.flag_2 = True
borrow_donor.flag_3 = True
addon._copy_mesh_flags_from_donor(borrow_target, borrow_donor)
assert not borrow_target['DSTS_flag_2'] and borrow_target['DSTS_flag_3']
borrow_target['DSTS_flag_2'] = True
borrow_donor.flag_2 = False
addon._copy_mesh_flags_from_donor(borrow_target, borrow_donor)
assert borrow_target['DSTS_flag_2'], 'Borrowing materials must retain the target geometry palette mode'
print('PASS borrowed render flags preserve target palette ownership')
arm = bpy.data.objects.new("Test Rig", bpy.data.armatures.new("Test Rig"))
bpy.context.scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
arm.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
bone = arm.data.edit_bones.new('Root "escaped"')
bone.tail.y = 1
bpy.ops.object.mode_set(mode='OBJECT')
base_pose = addon._build_rest_pose_anim_file(arm)
assert base_pose.bone_count == 1 and base_pose.frame_count == 1
assert not base_pose.positions and not base_pose.rotations and not base_pose.scales
print('PASS native base animation inherits GEOM bind pose without identity overrides')

source = AnimFileHundredLine()
source.bone_count = 1
source.frame_count = 31
source.float_channel_count = 1
source.bone_blend_factors = [128]
source.float_channel_blend_factors = [64]
source.positions = {0: {0: [0, 0, 0], 30: [3, 6, 9]}}
source.rotations = {0: {0: [0, 0, 0, 1], 30: [0, 0, -1, 0]}}
source.scales = {0: {0: [1, 1, 1]}}
source.float_channels = {0: {0: 43.0, 30: 60.0}}
action = build_action(arm, "Roundtrip", source)
bpy.context.scene.render.fps = 60
track, strip = create_nla_track(arm, action, action.name, 1, scale=2)
assert math.isclose(source_rate_from_strip(strip, bpy.context.scene), 30)
result = build_from_action(arm, action, 30, strip.action_slot)
assert result.frame_count == 31
assert result.bone_blend_factors == [128]
assert result.float_channel_blend_factors == [64]
assert all(abs(a-b)<1e-5 for a,b in zip(result.positions[0][15], [1.5,3,4.5]))
assert abs(result.float_channels[0][15] - 51.5) < 1e-5
assert abs(sum(v*v for v in result.rotations[0][15])-1) < 1e-5
with tempfile.TemporaryDirectory(prefix="dsts_test_") as folder:
    path = str(Path(folder) / "roundtrip.anim")
    result.to_file(path)
    binary = AnimFileBinary()
    binary.read(path)
    restored = AnimFileHundredLine.from_binary(binary)
    assert restored.frame_count == 31
    assert restored.float_channel_count == 1
    assert abs(restored.float_channels[0][15] - 51.5) < .01

# Asynchronous component keys must evaluate other components, not reset them.
sparse = bpy.data.actions.new("Sparse")
prefix = arm.pose.bones[0].path_from_id() + ".location"
for component, points in ((0, [(1, 0), (3, 2)]), (1, [(2, 4)])):
    fc = create_action_fcurve(sparse, arm, prefix, component)
    for frame, value in points:
        fc.keyframe_points.insert(frame, value).interpolation = 'LINEAR'
sparse_result = build_from_action(arm, sparse)
assert all(abs(a-b)<1e-6 for a,b in zip(sparse_result.positions[0][1], [1.0, 0.0, -4.0]))

# Owners in a layered action must remain isolated.
other = bpy.data.objects.new("Unrelated", None)
bpy.context.scene.collection.objects.link(other)
fc = create_action_fcurve(sparse, other, "location", 0)
fc.keyframe_points.insert(1, 999)
assert all(f.data_path != "location" for f in iter_action_fcurves(sparse, arm))
second_track, second_strip = create_nla_track(arm, sparse, 'Second phase', 1, scale=1)
arm.animation_data.action = action
arm.animation_data.action_slot = strip.action_slot
previous_action, previous_slot = arm.animation_data.action, arm.animation_data.action_slot
sequence, sequence_rate = addon._build_sequence_action_from_tracks(arm, 'Mixed-rate sequence', [track, second_track])
assert arm.animation_data.action == previous_action
assert arm.animation_data.action_slot == previous_slot
assert sequence_rate == 60
sequence_result = build_from_action(arm, sequence, sequence_rate)
assert sequence_result.frame_count == 63, sequence_result.frame_count
assert abs(sequence_result.positions[0][30][0] - 1.5) < 1e-5
assert all(abs(a-b)<1e-6 for a,b in zip(sequence_result.positions[0][60], [0.0, 0.0, -4.0]))
assert math.isclose((sequence_result.frame_count - 1) / sequence_rate, 1 + 2 / 60)
print('PASS mixed-rate sequence samples and missing-component reset')
from Time_Stranger_Importer.src.animation import single_strip
invalid_track = arm.animation_data.nla_tracks.new()
invalid_track.mute = True
invalid_track.strips.new('One', 1, action)
invalid_track.strips.new('Two', 40, action)
try:
    single_strip(invalid_track)
    raise AssertionError('Multi-strip export must not silently discard a phase')
except ValueError:
    pass
arm.animation_data.nla_tracks.remove(invalid_track)
replace_action = addon._build_anim_action_and_nla(arm, 'Replace me', source, [arm.data.bones[0].name])
before_actions = set(bpy.data.actions)
before_tracks = set(arm.animation_data.nla_tracks)
bad = AnimFileHundredLine()
bad.bone_count = 2
try:
    addon._build_anim_action_and_nla(arm, 'Replace me', bad, [arm.data.bones[0].name], replace_existing=True)
    raise AssertionError('Incompatible replacement should fail')
except ValueError:
    pass
assert set(bpy.data.actions) == before_actions
assert set(arm.animation_data.nla_tracks) == before_tracks
assert next(t for t in arm.animation_data.nla_tracks if t.name == 'Replace me').strips[0].action == replace_action
print('PASS failed replacement preserves existing animation')
root_only = bpy.data.actions.new('Object-only root motion')
fc = create_action_fcurve(root_only, arm, 'location', 0)
fc.keyframe_points.insert(1, 0).interpolation = 'LINEAR'
fc.keyframe_points.insert(31, 30).interpolation = 'LINEAR'
trimmed = addon.build_basic_hundred_line_anim_from_action(arm, root_only, frame_range=(11, 21))
assert trimmed.frame_count == 11
assert len(trimmed.positions[0]) == 11
assert abs(trimmed.positions[0][0][0] - 10) < 1e-5
assert abs(trimmed.positions[0][10][0] - 20) < 1e-5
arm.animation_data.action = None
arm.location = (0, 0, 0)
print('PASS object-only root motion respects trimmed export interval')

# glTF actions can retain an empty first object slot after root-motion bake.
# Binding the pose slot after strips.new() must expand beyond Blender's 0..1
# placeholder interval instead of exporting only two frames.
layered = bpy.data.actions.new('Empty object slot before real pose')
empty = create_action_fcurve(layered, other, 'location', 0)
fc = create_action_fcurve(layered, arm, arm.pose.bones[0].path_from_id() + '.location', 0)
fc.keyframe_points.insert(0, 0)
fc.keyframe_points.insert(50, 1)
track, strip = create_nla_track(arm, layered, 'Full selected slot', 1)
assert strip.action_frame_start == 0 and strip.action_frame_end == 50
assert strip.frame_end == 51
print('PASS layered NLA strip keeps selected slot full duration')

from Time_Stranger_Importer.src.geom import export_image_as_dds
import struct
with tempfile.TemporaryDirectory(prefix='dsts_texture_') as folder:
    pixels = bpy.data.images.new('Edited texture', width=4, height=4)
    pixels.colorspace_settings.name = 'Non-Color'
    pixels.pixels[:] = [1.5, -.25, .5, 1] * 16
    output = Path(folder) / 'texture.img'
    export_image_as_dds(pixels, str(output))
    raw = output.read_bytes()
    assert raw[:4] == b'DDS '
    assert struct.unpack_from('<I', raw, 28)[0] == 3
    assert len(raw) == 128 + (16 + 4 + 1) * 4
    assert raw[128:132] == bytes((128, 0, 255, 255))
print('PASS DDS channel clipping and complete mip chain')

# Byte PNGs expose encoded samples; float buffers expose linear samples.
# Exercise a real loaded file so a double sRGB encode cannot pass unnoticed.
import struct
import zlib
with tempfile.TemporaryDirectory(prefix='dsts_srgb_') as folder:
    def chunk(kind, content):
        return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', zlib.crc32(kind + content))
    png = Path(folder) / 'source.png'
    source_rgba = bytes([64, 128, 194, 230]) * 4
    png.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 6, 0, 0, 0))
                   + chunk(b'IDAT', zlib.compress(b'\0' + source_rgba[:8] + b'\0' + source_rgba[8:])) + chunk(b'IEND', b''))
    loaded = bpy.data.images.load(str(png))
    assert not loaded.is_float and loaded.colorspace_settings.name == 'sRGB'
    output = Path(folder) / 'source.img'
    export_image_as_dds(loaded, str(output))
    assert output.read_bytes()[128:144] == bytes([194, 128, 64, 230]) * 4
    linear = bpy.data.images.new('Linear source', width=1, height=1, float_buffer=True)
    linear.colorspace_settings.name = 'sRGB'
    linear.pixels[:] = [.2158605, .2158605, .2158605, 1]
    export_image_as_dds(linear, str(output))
    assert output.read_bytes()[128:132] == bytes([128, 128, 128, 255])
print('PASS loaded PNG and float-buffer sRGB conversion')

# Compare actual evaluated Blender poses to native component-wise motion
# blending, including rotated root/child rest bases and both location modes.
from mathutils import Matrix, Vector, Euler, Quaternion
from Time_Stranger_Importer.src.skeleton import export_skeleton
pose_rig = bpy.data.objects.new('Native coordinate test', bpy.data.armatures.new('Native coordinate test'))
bpy.context.scene.collection.objects.link(pose_rig)
bpy.context.view_layer.objects.active = pose_rig
pose_rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
root = pose_rig.data.edit_bones.new('Rotated root')
root.tail.y = 1
root.matrix = Matrix.LocRotScale(Vector((.2,.3,.4)), Euler((.3,.1,.5)).to_quaternion(), Vector((1,1,1)))
child = pose_rig.data.edit_bones.new('Rotated child')
child.tail.y = 1
child.parent = root
child.matrix = root.matrix @ Matrix.LocRotScale(Vector((.3,.7,.2)), Euler((-.2,.6,.1)).to_quaternion(), Vector((1,1,1)))
bpy.ops.object.mode_set(mode='OBJECT')
pose_action = bpy.data.actions.new('Parent-space translation regression')
for pb in pose_rig.pose.bones:
    pb.rotation_mode = 'QUATERNION'
    for component, value in enumerate((.1,.2,.3)):
        create_action_fcurve(pose_action, pose_rig, pb.path_from_id()+'.location', component).keyframe_points.insert(1,value)
    rotation = Euler((.2,-.4,.1)).to_quaternion()
    for component, value in enumerate(rotation):
        create_action_fcurve(pose_action, pose_rig, pb.path_from_id()+'.rotation_quaternion', component).keyframe_points.insert(1,value)
native_rest = export_skeleton(pose_rig)
native_axes = Matrix.Rotation(math.radians(-90),4,'X')
for local_location in (True, False):
    for bone in pose_rig.data.bones:
        bone.use_local_location = local_location
    pose_rig.animation_data.action = pose_action
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    expected = {pb.name: native_axes @ pb.matrix for pb in pose_rig.pose.bones}
    exported = build_from_action(pose_rig, pose_action)
    worlds = {}
    for i, bone in enumerate(native_rest.bones):
        rest = bone.transform
        position = Vector(rest.position[:3]) + Vector(exported.positions[i][0])
        rq = Quaternion((rest.quaternion[3],*rest.quaternion[:3]))
        q = exported.rotations[i][0]
        world = Matrix.LocRotScale(position,rq @ Quaternion((q[3],*q[:3])),Vector(rest.scale[:3]))
        if bone.parent:
            world = worlds[bone.parent.name] @ world
        worlds[bone.name] = world
        assert max(abs(world[r][c]-expected[bone.name][r][c]) for r in range(4) for c in range(4)) < 1e-5
    imported = build_action(pose_rig, 'Native coordinate reimport', exported)
    pose_rig.animation_data.action = imported
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    for pb in pose_rig.pose.bones:
        world = native_axes @ pb.matrix
        assert max(abs(world[r][c]-expected[pb.name][r][c]) for r in range(4) for c in range(4)) < 1e-5
print('PASS native root axes and rotated local/parent-space bone translation in both directions')
pose_rig.rotation_mode = 'XYZ'
for path, values in [('location',(.2,.4,.6)),('rotation_euler',(.1,.2,.4))]:
    for component, value in enumerate(values):
        create_action_fcurve(pose_action,pose_rig,path,component).keyframe_points.insert(1,value)
pose_rig.animation_data.action = pose_action
bpy.context.scene.frame_set(1)
bpy.context.view_layer.update()
composed = addon.build_basic_hundred_line_anim_from_action(pose_rig,pose_action)
worlds = {}
for i, bone in enumerate(native_rest.bones):
    rest=bone.transform
    p=Vector(rest.position[:3])+Vector(composed.positions[i][0])
    rq=Quaternion((rest.quaternion[3],*rest.quaternion[:3]))
    q=composed.rotations[i][0]
    world=Matrix.LocRotScale(p,rq@Quaternion((q[3],*q[:3])),Vector(composed.scales.get(i,{}).get(0,[1,1,1])))
    if bone.parent:world=worlds[bone.parent.name]@world
    worlds[bone.name]=world
    expected=native_axes@pose_rig.matrix_world@pose_rig.pose.bones[bone.name].matrix
    assert max(abs(world[r][c]-expected[r][c]) for r in range(4) for c in range(4))<1e-5
print('PASS object rotation/translation composes onto a rotated native root')
legacy = bpy.data.actions.new('Legacy native-vector action')
legacy['DSTS_animation_metadata'] = '{}'
for component,value in enumerate((1,2,3)):
    create_action_fcurve(legacy,arm,arm.pose.bones[0].path_from_id()+'.location',component).keyframe_points.insert(1,value)
assert build_from_action(arm,legacy).positions[0][0] == [1,2,3]
legacy_track,_ = create_nla_track(arm,legacy,'Legacy native-vector track',1)
legacy_sequence,rate = addon._build_sequence_action_from_tracks(arm,'Legacy normalized sequence',[legacy_track])
legacy_output = build_from_action(arm,legacy_sequence,rate).positions[0][0]
assert max(abs(a-b) for a,b in zip(legacy_output,(1,2,3)))<1e-6
print('PASS legacy native-vector action export and sequence migration')

from Time_Stranger_Importer.src.geom import import_geom, export_geom
from Time_Stranger_Importer.src.mesh import export_mesh_object
from mathutils import Matrix
import numpy as np
native_path = Path(r"D:\Digimon Modding\Time Stranger Extracted\app_0.dx11\chr050.geom")
if native_path.exists():
    native = import_geom(bpy.context, str(native_path))
    for obj in native.objects:
        if obj.type == 'ARMATURE':
            obj.animation_data.action = None
            for pb in obj.pose.bones:
                pb.matrix_basis = Matrix.Identity(4)
    encoded = export_geom(native)
    assert encoded.bind_pose_snapshot_matches, 'Untouched native rest pose should retain source bind matrices'
    with tempfile.TemporaryDirectory(prefix="dsts_geom_") as folder:
        path = str(Path(folder) / "roundtrip.geom")
        encoded.to_file(path)
        reread = load_format_modules()[0].Geom.from_file(path)
        assert len(reread.meshes) == len(encoded.meshes)
        assert len(reread.skeleton.bones) == len(encoded.skeleton.bones)
        source_raw, output_raw = native_path.read_bytes(), Path(path).read_bytes()
        source_ibpm = struct.unpack_from('<Q', source_raw, 0x68)[0]
        output_ibpm = struct.unpack_from('<Q', output_raw, 0x68)[0]
        matrix_bytes = len(encoded.skeleton.bones) * 48
        assert source_raw[source_ibpm:source_ibpm + matrix_bytes] == output_raw[output_ibpm:output_ibpm + matrix_bytes]
    donor = next(o for o in native.objects if o.type == 'MESH')
    from Time_Stranger_Importer.src.material import export_material
    material = donor.data.materials[0]
    group = next(n.node_tree for n in material.node_tree.nodes if n.type == 'GROUP' and n.node_tree.name.startswith('DSTS_Data-'))
    texture_node = next(n for n in group.nodes if n.type == 'TEX_IMAGE' and n.get('DSTS_parameter_id') == 24)
    label = texture_node.label
    texture_node.label = 'DSTS-Label changed after native schema correction'
    exported = export_material(material)
    assert any(u.parameter_id == 24 and u.uniform_type == 'texture' for u in exported.uniforms)
    # A saved pre-schema .blend has only the old, misleading label.
    del texture_node['DSTS_parameter_id']
    texture_node.label = 'DSTS-DiffuseColor'
    exported = export_material(material)
    assert any(u.parameter_id == 24 and u.uniform_type == 'texture' for u in exported.uniforms)
    texture_node['DSTS_parameter_id'] = 24
    texture_node.label = label
    print('PASS numeric shader IDs and legacy blend material migration')
    seam_mesh = bpy.data.meshes.new("Seam regression")
    seam_mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0)], [], [(0,1,2),(0,2,3)])
    seam_mesh.materials.append(donor.data.materials[0])
    uv = seam_mesh.uv_layers.new(name="uv1")
    for item, value in zip(uv.data, [(0,0),(1,0),(1,1),(.5,0),(.5,1),(0,1)]):
        item.uv = value
    seam = bpy.data.objects.new("Seam regression", seam_mesh)
    bpy.context.scene.collection.objects.link(seam)
    # A high-weight non-bone group must not displace actual skin weights.
    seam.vertex_groups.new(name="Selection only").add([0,1,2,3], 1, 'REPLACE')
    seam.vertex_groups.new(name=encoded.skeleton.bones[0].name).add([0,1,2,3], .25, 'REPLACE')
    out = export_mesh_object(seam, encoded.skeleton)
    assert len(out.vertices) == 6, len(out.vertices)
    assert len(out.indices) == 6
    print("PASS native chr050 geometry import/export and UV seam / non-bone weight regression")

float_path = WORKSPACE / 'Time Stranger/local/leviamon-native/dsts-loader/patch/n147_p0492.geom'
if float_path.exists():
    backend = load_format_modules()[0]
    assert hasattr(backend, 'FloatChannel'), 'Native material animation needs the updated backend'
    original_geom = backend.Geom.from_file(str(float_path))
    original_binding = original_geom.skeleton.float_channels[0]
    # Import twice to exercise Blender's .001 material suffix and source hash retention.
    import_geom(bpy.context, str(float_path))
    animated_material = import_geom(bpy.context, str(float_path))
    rig = next(o for o in animated_material.objects if o.type == 'ARMATURE')
    clip = next(t for t in rig.animation_data.nla_tracks if t.name == float_path.stem).strips[0]
    restored_anim = build_from_action(rig, clip.action, 30, clip.action_slot)
    original_anim = addon._read_anim_file(str(float_path.with_suffix('.anim')))
    assert restored_anim.float_channel_count == original_anim.float_channel_count == 1
    assert restored_anim.frame_count == original_anim.frame_count
    assert max(abs(restored_anim.float_channels[0][f] - v)
               for f, v in original_anim.float_channels[0].items()) < 1e-5
    rig.animation_data.action = None
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    output = export_geom(animated_material)
    binding = output.skeleton.float_channels[0]
    assert (binding.name_hash, binding.array_index, binding.flags) == (
        original_binding.name_hash, original_binding.array_index, original_binding.flags)
    model = next(o for o in animated_material.objects if o.type == 'MESH')
    model.data.materials[0].name = 'Renamed native animated material'
    output = export_geom(animated_material)
    binding = output.skeleton.float_channels[0]
    assert binding.name_hash == (zlib.crc32(model.data.materials[0].name.encode()) ^ 0xFFFFFFFF)
    from Time_Stranger_Importer.src.material import copy_native_material
    duplicate_model = model.copy()
    duplicate_model.data = model.data.copy()
    duplicate_material = copy_native_material(model.data.materials[0], 'Split animated material')
    duplicate_model.data.materials[0] = duplicate_material
    animated_material.objects.link(duplicate_model)
    try:
        export_geom(animated_material)
        raise AssertionError('Split material animation must not silently target the last material')
    except ValueError as error:
        assert 'float-channel remapping' in str(error)
    duplicate_material.name = model.data.materials[0].name + '.001'
    try:
        export_geom(animated_material)
        raise AssertionError('Native material name collision must not silently rebind meshes')
    except ValueError as error:
        assert 'same native name' in str(error)
    bpy.data.objects.remove(duplicate_model, do_unlink=True)
    with tempfile.TemporaryDirectory(prefix='dsts_material_animation_') as folder:
        destination = Path(folder) / 'animated.geom'
        output.to_file(str(destination))
        reread = backend.Geom.from_file(str(destination))
        assert reread.skeleton.float_channels[0].name_hash == reread.materials[0].name_hash
        assert reread.skeleton.float_channels[0].array_index == (302 << 4)
    print('PASS native material float binding, duplicate import, rename and ANIM curve roundtrip')
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    moved = rig.data.edit_bones[0].matrix.copy()
    moved.translation.x += .125
    rig.data.edit_bones[0].matrix = moved
    bpy.ops.object.mode_set(mode='OBJECT')
    assert not export_geom(animated_material).bind_pose_snapshot_matches
    print('PASS changed rest pose invalidates preserved native bind matrices')

flagged_path = native_path.with_name('chr800.geom')
if flagged_path.exists():
    from Time_Stranger_Importer.src.skeleton import import_skeleton, export_skeleton
    from Time_Stranger_Importer.src.utils import unflop
    flagged = load_format_modules()[0].Geom.from_file(str(flagged_path))
    rig, _ = import_skeleton(flagged.skeleton, coordinate_remap=unflop)
    skeleton_copy = export_skeleton(rig)
    def parent_records(raw):
        count = struct.unpack_from('<H', raw, 14)[0]
        bones = struct.unpack_from('<H', raw, 16)[0]
        parent_table = struct.unpack_from('<I', raw, 28)[0] + 28
        return raw[60:60 + 4 * count], raw[parent_table:parent_table + 2 * bones]
    assert parent_records(skeleton_copy.to_bytes()) == parent_records(flagged.skeleton.to_bytes())
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    child = next(b for b in rig.data.edit_bones if b.parent)
    child.parent = None
    bpy.ops.object.mode_set(mode='OBJECT')
    try:
        export_skeleton(rig).to_bytes()
        raise AssertionError('Changing a flagged native hierarchy must fail explicitly')
    except RuntimeError as error:
        assert 'flagged' in str(error).lower()
    print('PASS flagged native hierarchy preservation and changed-topology rejection')

camera_path = Path(r"D:\Digimon Modding\Time Stranger Extracted\app_0.dx11\cam_chr039_bs01_pc.geom")
if camera_path.exists():
    camera = import_geom(bpy.context, str(camera_path))
    camera_arm = next(o for o in camera.objects if o.type == 'ARMATURE')
    print("CAMERA_RIG", [b.name for b in camera_arm.data.bones])
    camera_arm.animation_data.action = None
    for pb in camera_arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    camera_export = export_geom(camera)
    with tempfile.TemporaryDirectory(prefix="dsts_camera_") as folder:
        destination = Path(folder) / camera_path.name
        camera_export.to_file(str(destination))
        assert destination.read_bytes() == camera_path.read_bytes()
    assert camera_export.nlst_text().strip() == camera_path.with_suffix('.nlst').read_text().strip()
    from Time_Stranger_Importer.src.native_camera import patch_camera_lenses
    raw = camera_path.read_bytes()
    offset = struct.unpack_from('<Q', raw, 0x60)[0]
    edited = patch_camera_lenses(raw, {0: {'fov_degrees': 45, 'near': .31, 'far': 1000}})
    allowed = {offset + field + byte for field in (4, 12, 16) for byte in range(4)}
    assert all(a == b or i in allowed for i, (a,b) in enumerate(zip(raw, edited)))
    assert struct.unpack_from('<f', edited, offset + 4)[0] == 45
    try:
        patch_camera_lenses(raw, {0: {'near': 3000}})
        raise AssertionError('Invalid camera clipping range must fail')
    except ValueError:
        pass
    extra = bpy.data.objects.new('Unsupported camera edit', bpy.data.meshes.new('Camera edit mesh'))
    camera.objects.link(extra)
    try:
        export_geom(camera)
        raise AssertionError('Camera export must reject added mesh')
    except ValueError:
        pass
    bpy.data.objects.remove(extra, do_unlink=True)
    extra = bpy.data.objects.new('Unsupported extra rig', bpy.data.armatures.new('Extra rig'))
    camera.objects.link(extra)
    try:
        export_geom(camera)
        raise AssertionError('Camera export must reject added armature')
    except ValueError:
        pass
    bpy.data.objects.remove(extra, do_unlink=True)
    print("PASS Lucemon CM native camera rig import and byte-preserved GEOM/NLST export")

from Time_Stranger_Importer.src.cinematic import import_preview, camera_transform, UNITY_TO_BLENDER
from mathutils import Vector, Quaternion
matrix = camera_transform([1, 2, 3], [0, 0, 0, 1])
assert (matrix.translation - Vector((-1, -3, 2))).length < 1e-6
assert ((matrix.to_3x3() @ Vector((0, 0, -1))) - Vector((0, -1, 0))).length < 1e-6
assert abs(matrix.to_3x3().determinant() - 1) < 1e-6
manifest = WORKSPACE / 'Time Stranger/local/leviamon-x/skill2.cinematic.json'
if manifest.exists():
    scene = import_preview(bpy.context, str(manifest))
    assert scene.camera is not None
    assert len(scene.camera.animation_data.action.layers) > 0
    manifest_data = json.loads(manifest.read_text())
    if manifest_data.get('audio'):
        sound_strips = list(scene.sequence_editor.strips)
        assert len(sound_strips) == len(manifest_data['audio'])
        assert sound_strips[0].frame_start == manifest_data['audio'][0]['frame'] + 1
    assert scene.frame_end == manifest_data['duration_frames'] + 1
    scene.frame_set(min(90, scene.frame_end))
    assert all(math.isfinite(v) for v in scene.camera.matrix_world.translation)
    print('PASS real Leviamon X cinematic actor/camera preview', scene.frame_end)

if args.nc_export:
    from Time_Stranger_Importer.src.nc_glb import import_nc_model
    glb = Path(r'D:\Digimon Modding\Digimon Library\New Century\Digimon_Library\LeviamonX_x147\model\model.glb')
    unused = bpy.data.meshes.new('User unlinked mesh - must survive')
    scene = bpy.data.scenes.new('NC export test')
    bpy.context.window.scene = scene
    collection, rig, notes = import_nc_model(bpy.context, str(glb), str(native_path))
    assert unused.name in bpy.data.meshes
    from Time_Stranger_Importer.src.material import find_data_group
    body_materials = {o.data.materials[0] for o in collection.objects
                      if o.type == 'MESH' and o.get('DSTS_nc_atlas')}
    assert len(body_materials) >= 2
    native_groups = [find_data_group(m).node_tree for m in body_materials]
    assert len(set(native_groups)) == len(native_groups), 'Body atlas materials must own independent native groups'
    geometry = export_geom(collection)
    assert all(not mesh.flag_2 for mesh in geometry.meshes), 'New NC meshes require indexed bone gathering'
    bone_indices = {bone: index for index, bone in enumerate(geometry.skeleton.bones)}
    palettes = [[bone_indices[bone] for bone in mesh.matrix_palette] for mesh in geometry.meshes]
    assert any(indices != list(range(indices[0], indices[0]+len(indices))) for indices in palettes if indices)
    track = next(t for t in rig.animation_data.nla_tracks if t.name == 'skill2_display')
    assert abs(track.strips[0].action_frame_end - track.strips[0].action_frame_start - 260) < .01
    idle = next(t for t in rig.animation_data.nla_tracks if t.name == 'idle')
    assert abs(idle.strips[0].action_frame_end - idle.strips[0].action_frame_start - 50) < .01
    with tempfile.TemporaryDirectory(prefix='dsts_nc_export_') as folder:
        geometry.to_file(str(Path(folder) / 'leviamon.geom'))
        addon._export_action_as_anim(rig, track.strips[0].action, track.strips[0], str(Path(folder) / 'leviamon.anim'))
        binary = AnimFileBinary()
        binary.read(str(Path(folder) / 'leviamon.anim'))
        assert binary.bone_count == len(rig.data.bones)
        assert abs(binary.playback_rate - 30) < 1e-5
        tracks = {t.name.lower(): t for t in rig.animation_data.nla_tracks}
        if all(k in tracks for k in ('skill2_move_to', 'skill2_display', 'skill2_move_back')):
            result = addon._try_export_sequence(rig, tracks, 'bs02', 'bs02', 'chr999', folder)
            assert result
    print('PASS Leviamon X complete model conversion, geometry, cinematic animation and sequence export')

addon.unregister()
addon.register()
addon.unregister()
print("PASS animation roundtrip, float channels, masks, fps, escaped bones, sparse keys, slots, registration")
