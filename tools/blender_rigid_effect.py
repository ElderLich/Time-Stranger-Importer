"""Native rigid-effect import, animation and GEOM/ANIM roundtrip regression.

Run with Blender --background --factory-startup --python this_file --
--source path/to/ef_b_chr220_bs01.geom --output an/isolated/directory.
"""
import argparse
import importlib.util
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--addon-root', type=Path, default=Path(__file__).resolve().parents[1])
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
args.output.mkdir(parents=True, exist_ok=False)
root = args.addon_root.resolve()
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root/'__init__.py',
                                            submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.backend import load_format_modules
from Time_Stranger_Importer.src.geom import import_geom, export_geom
from Time_Stranger_Importer.src.animation import build_from_action
from Time_Stranger_Importer.src.native_rest_scale import is_rest_track

formats = load_format_modules()[0]
formats.set_throw_errors(True)
native = formats.Geom.from_file(str(args.source))
clip = addon._read_anim_file(str(args.source.with_suffix('.anim')))
collection = import_geom(bpy.context, str(args.source))
rig = next(o for o in collection.objects if o.type == 'ARMATURE')
meshes = [o for o in collection.objects if o.type == 'MESH']
assert native.is_rigid_scene and rig.get('DSTS_rigid_scene')
assert not collection.get('DSTS_import_warnings')
assert len(meshes) == len(native.meshes)
assert all(o.parent_type == 'BONE' and len(o.vertex_groups) == 1 and not o.modifiers for o in meshes)
tracks = rig.animation_data.nla_tracks
motion = next(t for t in tracks if not is_rest_track(t))
assert motion.strips[0].blend_type == 'REPLACE'
indices = {b.name: i for i, b in enumerate(native.skeleton.bones)}
axis = Matrix.Rotation(math.pi/2, 4, 'X')


def sample(channels, index, frame, default, rotation=False):
    values = channels.get(index, {})
    keys = sorted(values)
    if not keys:
        return default
    lo = max((k for k in keys if k <= frame), default=keys[0])
    hi = min((k for k in keys if k >= frame), default=keys[-1])
    t = (frame-lo)/(hi-lo) if lo != hi else 0
    if rotation:
        a, b = values[lo], values[hi]
        return Quaternion((a[3], *a[:3])).slerp(Quaternion((b[3], *b[:3])), t)
    return Vector(values[lo]).lerp(Vector(values[hi]), t)


def check_frame(frame=None):
    worlds = {}
    def world(bone):
        if bone.name not in worlds:
            index = indices[bone.name]
            rest = bone.transform
            p = Vector(rest.position[:3])
            q = Quaternion((rest.quaternion[3], *rest.quaternion[:3]))
            s = Vector(rest.scale[:3])
            if frame is not None:
                p = sample(clip.positions, index, frame, p)
                q = sample(clip.rotations, index, frame, q, True)
                s = sample(clip.scales, index, frame, s)
            local = Matrix.LocRotScale(p, q, s)
            worlds[bone.name] = world(bone.parent) @ local if bone.parent else local
        return worlds[bone.name]
    motion.mute = frame is None
    bpy.context.scene.frame_set(1 if frame is None else int(1 + frame * motion.strips[0].scale))
    bpy.context.view_layer.update()
    for bone in native.skeleton.bones:
        actual = rig.pose.bones[bone.name].matrix
        expected = axis @ world(bone)
        error = max(abs(actual[r][c]-expected[r][c]) for r in range(4) for c in range(4))
        assert error < 3e-4, (frame, bone.name, error, actual, expected)
    for obj, mesh in zip(meshes, native.meshes):
        expected = axis @ world(mesh.matrix_palette[0]) @ Vector(mesh.vertices[0].position[:3])
        actual = obj.matrix_world @ obj.data.vertices[0].co
        assert (actual-expected).length < 5e-4, (frame, obj.name, actual, expected)
    print('PASS native rigid transform/attachment oracle', frame, flush=True)


# Equal source/scene rates make independent sampling frame-exact.
bpy.context.scene.render.fps = int(clip.playback_rate)
bpy.context.scene.render.fps_base = 1
motion.strips[0].scale = 1
for frame in (None, 0, 500, 1000, 1348, 1349, 1350, 1400, clip.frame_count-1):
    check_frame(frame)
exported_clip = build_from_action(rig, motion.strips[0].action, clip.playback_rate)
for channel in ('positions', 'scales', 'rotations'):
    for index, keys in getattr(clip, channel).items():
        for frame, value in keys.items():
            got = getattr(exported_clip, channel)[index][frame]
            if channel == 'rotations':
                a, b = Quaternion((value[3], *value[:3])), Quaternion((got[3], *got[:3]))
                assert abs(a.normalized().dot(b.normalized())) > .99999, (channel, index, frame)
            else:
                assert max(abs(a-b) for a,b in zip(got, value)) < 1e-4, (channel, index, frame, got, value)
print('PASS original absolute animation keys roundtrip', flush=True)
check_frame()
exported = export_geom(collection)
out = args.output/args.source.name
exported.to_file(str(out))
reloaded = formats.Geom.from_file(str(out))
assert reloaded.authored_bounds == native.authored_bounds
assert reloaded.bind_pose_snapshot == native.bind_pose_snapshot
assert len(reloaded.meshes) == len(native.meshes)
assert [b.name for b in reloaded.skeleton.bones] == list(indices)
print('PASS GEOM bounds, bind matrices and rigid attachments roundtrip', flush=True)
mesh = meshes[0]
old_parent = mesh.parent_bone
old_inverse = mesh.matrix_parent_inverse.copy()
mesh.parent_bone = rig.data.bones[-1].name
try:
    export_geom(collection)
except ValueError as exc:
    assert 'native bone attachment' in str(exc)
else:
    raise AssertionError('Changed rigid attachment must not silently export')
mesh.parent_bone = old_parent
mesh.matrix_parent_inverse = old_inverse
old_vertex = mesh.data.vertices[0].co.copy()
mesh.data.vertices[0].co.x += 1
assert not export_geom(collection).authored_bounds
mesh.data.vertices[0].co = old_vertex
print('PASS attachment guard and edited-geometry bounds invalidation', flush=True)
collection_name = collection.name
bpy.ops.wm.save_as_mainfile(filepath=str(args.output/'effect.blend'))
bpy.ops.wm.open_mainfile(filepath=str(args.output/'effect.blend'))
collection = bpy.data.collections[collection_name]
rig = next(o for o in collection.objects if o.type == 'ARMATURE')
meshes = [o for o in collection.objects if o.type == 'MESH']
motion = next(t for t in rig.animation_data.nla_tracks if not is_rest_track(t))
check_frame(1349)
check_frame()
assert export_geom(collection).authored_bounds == native.authored_bounds
print('PASS rigid effect saved and reopened', flush=True)
