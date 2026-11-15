"""Blender regression for native authored rest scales and chr189 issue #4.

External fixture: --source path/to/chr189.geom --output an/isolated/directory.
Imports all related native clips, checks native skinning, full GEOM readback,
source animation roundtrip, unsupported edits and source material identity.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--addon-root', type=Path, default=root)
parser.add_argument('--render', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
args.source = args.source.resolve()
args.output = args.output.resolve()
if args.output.exists():
    raise ValueError('Use a new isolated output directory')
args.output.mkdir(parents=True)
root = args.addon_root.resolve()
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root / '__init__.py',
                                             submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.geom import import_geom, export_geom
from Time_Stranger_Importer.src.backend import load_format_modules
from Time_Stranger_Importer.src.animation import build_from_action
from Time_Stranger_Importer.src.blender_compat import iter_action_fcurves
from Time_Stranger_Importer.src.native_rest_scale import MARKER, is_rest_track, partition_diagnostics

scene = bpy.context.scene
scene.render.fps = 30
formats = load_format_modules()[0]
formats.set_throw_errors(False)
source_raw = args.source.read_bytes()
native = formats.Geom.from_bytes(source_raw)
native_bones = list(native.skeleton.bones)
names = [bone.name for bone in native_bones]
ibm_offset = struct.unpack_from('<Q', source_raw, 0x68)[0]
ibms = [Matrix((*[struct.unpack_from('<4f', source_raw, ibm_offset + i * 48 + r * 16)
                  for r in range(3)], (0, 0, 0, 1))) for i in range(len(names))]
collection = import_geom(bpy.context, str(args.source))
rig = next(obj for obj in collection.objects if obj.type == 'ARMATURE')
profile = json.loads(rig[MARKER])
assert len(profile['scales']) == 14 and not profile['unhandled_mismatches']
assert len(collection['DSTS_backend_diagnostics'].splitlines()) == 14
assert len(collection['DSTS_handled_bind_rest_notes'].splitlines()) == 14
assert not collection.get('DSTS_import_warnings')
assert partition_diagnostics(['unknown corruption'], profile) == ([], ['unknown corruption'])
tracks = rig.animation_data.nla_tracks
base = next(track for track in tracks if is_rest_track(track))
assert len([track for track in tracks if is_rest_track(track)]) == 1
motion = next(track for track in tracks if track.name == args.source.stem + '_bs01')
clip = addon._read_anim_file(str(args.source.with_name(args.source.stem + '_bs01.anim')))
face_indices = [names.index(name) for name in profile['scales']]
active_frame = next(frame for index in face_indices for frame, value in clip.scales.get(index, {}).items()
                    if value[0] > 9)
axis = Matrix.Rotation(math.pi / 2, 4, 'X')


def sample(channels, index, frame, default, rotation=False):
    values = channels.get(index, {})
    keys = sorted(values)
    if not keys:
        return default
    lo = max((key for key in keys if key <= frame), default=keys[0])
    hi = min((key for key in keys if key >= frame), default=keys[-1])
    fraction = (frame - lo) / (hi - lo) if hi != lo else 0
    if rotation:
        a, b = values[lo], values[hi]
        return Quaternion((a[3], *a[:3])).slerp(Quaternion((b[3], *b[:3])), fraction)
    return Vector(values[lo]).lerp(Vector(values[hi]), fraction)


def native_worlds(frame=None):
    result = {}
    def build(index):
        if index in result:
            return result[index]
        bone = native_bones[index]
        t = bone.transform
        p, q, s = Vector(t.position[:3]), Quaternion((t.quaternion[3], *t.quaternion[:3])), Vector(t.scale[:3])
        if frame is not None:
            p += sample(clip.positions, index, frame, Vector((0, 0, 0)))
            q = q @ sample(clip.rotations, index, frame, Quaternion(), True)
            scale_delta = sample(clip.scales, index, frame, Vector((1, 1, 1)))
            s = Vector([s[i] * scale_delta[i] for i in range(3)])
        local = Matrix.LocRotScale(p, q, s)
        result[index] = build(names.index(bone.parent.name)) @ local if bone.parent else local
        return result[index]
    for index in range(len(names)):
        build(index)
    return result


def compare_skin(frame=None):
    motion.mute = frame is None
    rig.update_tag()
    scene.frame_set(1 if frame is None else frame + 1)
    expected = native_worlds(frame)
    maximum = 0
    for index in face_indices:
        bone = rig.pose.bones[names[index]]
        actual = bone.matrix @ bone.bone.matrix_local.inverted()
        wanted = axis @ expected[index] @ ibms[index] @ axis.inverted()
        maximum = max(maximum, max(abs(actual[r][c] - wanted[r][c]) for r in range(4) for c in range(4)))
    assert maximum < 3e-4, (frame, maximum)
    return maximum


comparisons = {'rest': compare_skin()}
for frame in sorted({0, active_frame, clip.frame_count // 2, clip.frame_count - 1}):
    comparisons[str(frame)] = compare_skin(frame)
comparisons['muted_after_playback'] = compare_skin()
assert abs(rig.pose.bones['J_close_l'].scale.x - .1) < 1e-6
motion.mute = False
base_clip = next(track for track in tracks if track.name == args.source.stem)
base_clip.mute = False
scene.frame_set(active_frame + 1)
assert abs(rig.pose.bones['J_close_l'].scale.x - 1) < 1e-5
base_clip.mute = True
motion.mute = True
rig.animation_data.action = motion.strips[0].action
scene.frame_set(active_frame + 1)
assert abs(rig.pose.bones['J_close_l'].scale.x - 1) < 1e-5
rig.animation_data.action = None
compare_skin()

# Source clips retain their native delta scale; correction is never serialized.
roundtrip = build_from_action(rig, motion.strips[0].action, playback_rate=clip.playback_rate)
anim_path = args.output / 'chr189_bs01.anim'
roundtrip.to_file(str(anim_path))
readback_anim = addon._read_anim_file(str(anim_path))
scale_error = 0
for index in face_indices:
    for frame in range(clip.frame_count):
        expected = sample(clip.scales, index, frame, Vector((1, 1, 1)))
        actual = sample(readback_anim.scales, index, frame, Vector((1, 1, 1)))
        scale_error = max(scale_error, max(abs(a - b) for a, b in zip(expected, actual)))
assert scale_error < .01
try:
    build_from_action(rig, base.strips[0].action)
    raise AssertionError('Internal rest layer was exported')
except ValueError as exc:
    assert 'not a native animation' in str(exc)

exported = export_geom(collection)
assert exported.bind_pose_snapshot_matches
assert exported.bind_pose_snapshot == native.bind_pose_snapshot
geom_path = args.output / 'chr189.geom'
exported.to_file(str(geom_path))
raw = geom_path.read_bytes()
at = struct.unpack_from('<Q', raw, 0x68)[0]
assert raw[at:at + 48 * len(names)] == source_raw[ibm_offset:ibm_offset + 48 * len(names)]
readback = formats.Geom.from_file(str(geom_path))
assert len(readback.materials) == len(native.materials)
assert len(readback.meshes) == len(native.meshes)
native_assignments = [list(native.materials).index(mesh.material) for mesh in native.meshes]
readback_assignments = [list(readback.materials).index(mesh.material) for mesh in readback.meshes]
assert native_assignments == readback_assignments
for a, b in zip(native_bones, readback.skeleton.bones):
    for prop in ('position', 'quaternion', 'scale'):
        assert list(getattr(a.transform, prop)) == list(getattr(b.transform, prop))

# Imported duplicate material records remain distinct; copied instances fail.
meshes = [obj for obj in collection.objects if obj.type == 'MESH']
obj = meshes[1]
original_material = obj.data.materials[0]
copy = meshes[0].data.materials[0].copy()
obj.data.materials[0] = copy
try:
    export_geom(collection)
    raise AssertionError('Copied duplicate material was accepted')
except ValueError as exc:
    assert 'same native name' in str(exc)
finally:
    obj.data.materials[0] = original_material
    bpy.data.materials.remove(copy)

motion.is_solo = True
try:
    export_geom(collection)
    raise AssertionError('Missing solo rest layer was accepted')
except ValueError as exc:
    assert 'Also solo' in str(exc)
finally:
    motion.is_solo = False
compare_skin()

renders = []
if args.render:
    points = []
    for obj in meshes:
        face_groups = {group.index for group in obj.vertex_groups if group.name in profile['scales']}
        points.extend(obj.matrix_world @ vertex.co for vertex in obj.data.vertices
                      if any(group.group in face_groups and group.weight > .5 for group in vertex.groups))
    lo = Vector([min(point[i] for point in points) for i in range(3)])
    hi = Vector([max(point[i] for point in points) for i in range(3)])
    center = (lo + hi) / 2
    face_center = sum((rig.pose.bones[name].head for name in profile['scales']), Vector()) / len(profile['scales'])
    rest_head_rotation = rig.data.bones['J_head'].matrix_local.to_quaternion()
    camera_data = bpy.data.cameras.new('Face review')
    camera = bpy.data.objects.new('Face review', camera_data)
    scene.collection.objects.link(camera)
    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = max(hi.x - lo.x, hi.z - lo.z) * 1.7
    camera.location = center + Vector((0, -max(camera_data.ortho_scale * 4, 10), 0))
    camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
    scene.camera = camera
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = 'BOTH'
    scene.render.resolution_x = scene.render.resolution_y = 700
    scene.render.resolution_percentage = 100
    for label, frame, corrected in [('before-neutral', None, False), ('after-neutral', None, True),
                                    ('before-active', active_frame, False), ('after-active', active_frame, True)]:
        # Reproduce the old unit rest basis without touching pose RNA while
        # the dependency graph owns animated channels.
        for curve in iter_action_fcurves(base.strips[0].action, rig):
            if curve.data_path.endswith('.scale'):
                name = curve.group.name if curve.group else ''
                value = profile['scales'].get(name, 1) if corrected else 1
                for key in curve.keyframe_points:
                    key.co.y = value
                curve.update()
        base.strips[0].action.update_tag()
        rig.update_tag()
        motion.mute = frame is None
        scene.frame_set(-1)
        scene.frame_set(1 if frame is None else frame + 1)
        rotation = rig.pose.bones['J_head'].matrix.to_quaternion() @ rest_head_rotation.inverted()
        current_center = sum((rig.pose.bones[name].head for name in profile['scales']), Vector()) / len(profile['scales'])
        target = current_center + rotation @ (center - face_center)
        camera.location = target + rotation @ Vector((0, -max(camera_data.ortho_scale * 4, 10), 0))
        camera.rotation_euler = (target - camera.location).to_track_quat('-Z', 'Y').to_euler()
        path = args.output / (label + '.png')
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        renders.append(str(path))
    base.mute = False
    compare_skin()

# Save/reopen retains the correction and the independent native source data.
blend_path = args.output / 'chr189-review.blend'
collection_name, rig_name = collection.name, rig.name
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
def reload_scene():
    global collection, rig, tracks, base, motion, scene
    bpy.ops.wm.open_mainfile(filepath=str(blend_path))
    scene = bpy.context.scene
    collection = bpy.data.collections[collection_name]
    rig = bpy.data.objects[rig_name]
    tracks = rig.animation_data.nla_tracks
    base = next(track for track in tracks if is_rest_track(track))
    motion = next(track for track in tracks if track.name == args.source.stem + '_bs01')
reload_scene()
compare_skin()
assert export_geom(collection).bind_pose_snapshot == native.bind_pose_snapshot

# An unrelated rest edit invalidates whole-skeleton source IBM reuse too.
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones['J_center'].head.x += .05
bpy.ops.object.mode_set(mode='OBJECT')
scene.frame_set(1)
try:
    export_geom(collection)
    raise AssertionError('Unrelated bind edit accepted stale authored-pose exemption')
except RuntimeError as exc:
    assert 'pose-mode transforms' in str(exc)
reload_scene()

# A real bind edit must not retain the authored-pose exemption.
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode='EDIT')
changed = rig.data.edit_bones['J_close_l']
old_parent, old_matrix = changed.parent.name, changed.matrix.copy()
changed.parent = next(bone for bone in rig.data.edit_bones if bone != changed and bone.name != old_parent)
changed.matrix = old_matrix
bpy.ops.object.mode_set(mode='OBJECT')
scene.frame_set(1)
try:
    export_geom(collection)
    raise AssertionError('Reparented leaf accepted stale authored-pose exemption')
except RuntimeError as exc:
    assert 'pose-mode transforms' in str(exc)
reload_scene()
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones['J_close_l'].head.x += .05
bpy.ops.object.mode_set(mode='OBJECT')
scene.frame_set(1)
try:
    export_geom(collection)
    raise AssertionError('Edited bind rest accepted stale authored-pose exemption')
except RuntimeError as exc:
    assert 'pose-mode transforms' in str(exc)

report = {'status': 'pass', 'source': str(args.source), 'source_sha256': hashlib.sha256(source_raw).hexdigest(),
          'profile_bones': profile['source_bones'], 'retained_backend_diagnostics': collection['DSTS_backend_diagnostics'].splitlines(),
          'skin_matrix_errors': comparisons, 'animation_scale_roundtrip_error': scale_error,
          'bone_trs_exact': True, 'inverse_bind_bytes_exact': True,
          'material_count': len(readback.materials), 'mesh_material_assignments_exact': True,
          'internal_animation_export_rejected': True, 'copied_material_rejected': True,
          'unsupported_solo_rejected': True, 'edited_rest_rejected': True,
          'reparented_rest_rejected': True,
          'unrelated_rest_edit_rejected': True, 'save_reopen_verified': True,
          'render_paths': renders, 'blender': bpy.app.version_string}
(args.output / 'native-rest-scale-audit.json').write_text(json.dumps(report, indent=2) + '\n')
print('PASS native authored rest scale:', json.dumps(report))
