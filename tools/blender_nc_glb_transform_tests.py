"""Focused Blender tests for NC static-basis ownership and translated binds."""
import importlib.util
from pathlib import Path
import sys
import math

import bpy
from mathutils import Matrix, Vector

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root/'__init__.py',
                                            submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.nc_glb import fit_alignment_rotation, _capture_source_world_poses
from Time_Stranger_Importer.src.blender_compat import create_action_fcurve


def error(a, b):
    return max(abs(a[r][c]-b[r][c]) for r in range(4) for c in range(4))


points = {str(i): Vector(v) for i, v in enumerate(((0, 0, 0), (1, 0, 0), (0, 2, 0), (0, 0, 3)))}
transform = Matrix.Rotation(math.pi/2, 4, 'X')
transform.translation = Vector((.2, -.4, 1.2))
targets = {name: transform @ value for name, value in points.items()}
fitted, residual = fit_alignment_rotation(points, targets, allow_translation=True)
assert residual < 1e-6 and error(transform, fitted) < 1e-6
assert fit_alignment_rotation(points, targets)[1] > .01, 'Node-axis checks must remain origin-based'
targets['3'] += Vector((.1, .3, .5))
assert fit_alignment_rotation(points, targets, allow_translation=True)[1] > .01, 'Translation must not admit distorted binds'

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
rig = bpy.data.objects.new('NC synthetic rig', bpy.data.armatures.new('NC synthetic rig'))
bpy.context.scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
bone = rig.data.edit_bones.new('Pelvis')
bone.head = (0, 0, 1); bone.tail = (0, 1, 1)
bpy.ops.object.mode_set(mode='OBJECT')
action = bpy.data.actions.new('Source motion')
curve = create_action_fcurve(action, rig, 'pose.bones["Pelvis"].location', index=0, group_name='Pelvis')
curve.keyframe_points.insert(1, 1)
curve.keyframe_points.insert(2, 3)
ad = rig.animation_data_create()
ad.action = action
ad.action_influence = 0
conversion = Matrix.Rotation(math.pi/2, 4, 'X')
frames = _capture_source_world_poses(bpy.context, rig, [action], 'root', Matrix.Identity(4), conversion)[action][1]
assert abs(frames[1]['Pelvis'].translation.x - 1) < 1e-5
assert abs(frames[2]['Pelvis'].translation.x - 3) < 1e-5
assert error(frames[1]['root'], conversion) < 1e-5
assert error(frames[1]['Pelvis'].to_3x3().to_4x4(), Matrix.Identity(4)) < 1e-5, 'Do not rotate already-converted bone rests twice'

frames = _capture_source_world_poses(bpy.context, rig, [action], 'root', transform, transform)[action][1]
assert (frames[1]['Pelvis'].translation - transform @ Vector((1, 0, 1))).length < 1e-5
assert error(frames[1]['root'], transform) < 1e-5, 'A translated root-relative import must retain its source root'
print('PASS translated bind acceptance, distorted bind rejection, single static-axis conversion, zero-influence source capture, root-relative capture')
