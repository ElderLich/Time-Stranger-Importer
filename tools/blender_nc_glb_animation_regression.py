"""Blender-only independent GLB deformation check, including direct action and NLA.

Run with Blender --background --factory-startup --python-exit-code 1 --python
this_file -- --glb model.glb --donor chr050.geom --output new-directory.
The reference evaluates the original glTF channels and inverse-bind matrices,
without using the importer's animation capture, baking, or alignment routines.
"""
import argparse
import bisect
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector


def pin(path):
    payload = path.read_bytes()
    return {'path': str(path.resolve()), 'bytes': len(payload),
            'sha256': hashlib.sha256(payload).hexdigest()}


def load_source(path):
    payload = path.read_bytes()
    assert payload[:4] == b'glTF'
    size, kind = struct.unpack_from('<II', payload, 12)
    assert kind == 0x4e4f534a
    source = json.loads(payload[20:20 + size])
    binary_size, binary_kind = struct.unpack_from('<II', payload, 20 + size)
    assert binary_kind == 0x004e4942
    return source, payload[28 + size:28 + size + binary_size]


def accessor(source, binary, index):
    item = source['accessors'][index]
    assert item['componentType'] == 5126 and not item.get('sparse')
    count = {'SCALAR': 1, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}[item['type']]
    view = source['bufferViews'][item['bufferView']]
    offset = view.get('byteOffset', 0) + item.get('byteOffset', 0)
    stride = view.get('byteStride', 4 * count)
    return [struct.unpack_from('<' + 'f' * count, binary, offset + n * stride)
            for n in range(item['count'])]


def sample(times, values, interpolation, path, time):
    def normalized(value):
        if path != 'rotation':
            return value
        q = Quaternion((value[3], *value[:3])).normalized()
        return (q.x, q.y, q.z, q.w)
    if time <= times[0]:
        return normalized(values[1 if interpolation == 'CUBICSPLINE' else 0])
    if time >= times[-1]:
        return normalized(values[-2 if interpolation == 'CUBICSPLINE' else -1])
    hi = bisect.bisect_right(times, time)
    lo = hi - 1
    dt = times[hi] - times[lo]
    u = (time - times[lo]) / dt
    if interpolation == 'STEP':
        return values[lo]
    if interpolation == 'CUBICSPLINE':
        v0, tangent0 = values[3 * lo + 1:3 * lo + 3]
        tangent1, v1 = values[3 * hi:3 * hi + 2]
        return normalized(tuple((2*u**3 - 3*u*u + 1)*x + (u**3 - 2*u*u + u)*dt*tx
                                + (-2*u**3 + 3*u*u)*y + (u**3 - u*u)*dt*ty
                                for x, tx, y, ty in zip(v0, tangent0, v1, tangent1)))
    assert interpolation == 'LINEAR'
    a, b = values[lo], values[hi]
    if path == 'rotation':
        q = Quaternion((a[3], *a[:3])).slerp(Quaternion((b[3], *b[:3])), u)
        return (q.x, q.y, q.z, q.w)
    return tuple(x + (y-x)*u for x, y in zip(a, b))


def references(source, binary, rig):
    nodes = source['nodes']
    parents = {child: i for i, node in enumerate(nodes) for child in node.get('children', [])}
    skin = max(source['skins'], key=lambda s: sum(nodes[j].get('name') in rig.data.bones for j in s['joints']))
    # Include companion body skins sharing the selected skeleton, while
    # excluding separate submodels that merely reuse the same bone names.
    selected = set(skin['joints'])
    joint_map = {}
    for candidate in source['skins']:
        if not selected.intersection(candidate['joints']):
            continue
        inverse_binds = [Matrix([row[n:n+4] for n in range(0, 16, 4)]).transposed()
                         for row in accessor(source, binary, candidate['inverseBindMatrices'])]
        for index, inverse in zip(candidate['joints'], inverse_binds):
            if index in joint_map:
                assert max(abs(inverse[r][c] - joint_map[index][r][c]) for r in range(4) for c in range(4)) < .0001
            joint_map[index] = inverse
    joints = [(index, nodes[index]['name'], inverse) for index, inverse in joint_map.items()]
    conversion = Matrix.Rotation(math.pi/2, 4, 'X')
    clips = {}
    for animation in source['animations']:
        channels = []
        for channel in animation['channels']:
            target = channel['target']
            if target['path'] == 'weights':
                continue
            s = animation['samplers'][channel['sampler']]
            channels.append((target['node'], target['path'],
                             [v[0] for v in accessor(source, binary, s['input'])],
                             accessor(source, binary, s['output']), s.get('interpolation', 'LINEAR')))
        clips[animation['name']] = channels

    def evaluate(name, time):
        changes = {}
        for node, path, times, values, interpolation in clips[name]:
            changes.setdefault(node, {})[path] = sample(times, values, interpolation, path, time)
        worlds = {}
        def world(index):
            if index not in worlds:
                node = dict(nodes[index]); node.update(changes.get(index, {}))
                if 'matrix' in node:
                    values = node['matrix']
                    local = Matrix([values[n:n+4] for n in range(0, 16, 4)]).transposed()
                else:
                    q = node.get('rotation', (0, 0, 0, 1))
                    local = Matrix.LocRotScale(Vector(node.get('translation', (0, 0, 0))),
                                              Quaternion((q[3], *q[:3])), Vector(node.get('scale', (1, 1, 1))))
                worlds[index] = world(parents[index]) @ local if index in parents else local
            return worlds[index]
        # IBM matrices are already in the extractor's Z-up bind-vertex space.
        return {name: conversion @ world(index) @ ibm for index, name, ibm in joints}
    return evaluate, len(joints)


def check(rig, reference, *, all_frames):
    ad = rig.animation_data
    results = []
    for track in ad.nla_tracks:
        track.mute = True; track.is_solo = False
    strips = [(track.name, track, track.strips[0]) for track in ad.nla_tracks]
    for name, track, strip in strips:
        start, end = int(round(strip.action_frame_start)), int(round(strip.action_frame_end))
        frames = list(range(start, end+1)) if all_frames else sorted({start, (start+end)//2, end})
        row = {'clip': name, 'frames': len(frames), 'action_range': [start, end]}
        for route in ('direct', 'nla'):
            ad.action = None
            for item in ad.nla_tracks: item.mute = True
            for bone in rig.pose.bones: bone.matrix_basis = Matrix.Identity(4)
            ad.use_nla = route == 'nla'; ad.action_influence = 1.0
            if route == 'direct':
                ad.action = strip.action; ad.action_slot = strip.action_slot
            else:
                track.mute = False; strip.mute = False; strip.influence = 1.0
            # Force evaluation after changing the animation source, even at frame 1.
            bpy.context.scene.frame_set(-1000)
            worst = (0.0, None, None)
            for frame in frames:
                scene_frame = frame if route == 'direct' else strip.frame_start + (frame - strip.action_frame_start) * strip.scale
                bpy.context.scene.frame_set(math.floor(scene_frame), subframe=scene_frame % 1)
                bpy.context.view_layer.update()
                expected = reference(name, frame/30)
                for bone_name, matrix in expected.items():
                    bone = rig.pose.bones[bone_name]
                    actual = rig.matrix_world @ bone.matrix @ bone.bone.matrix_local.inverted()
                    error = max(abs(actual[r][c] - matrix[r][c]) for r in range(4) for c in range(4))
                    if error > worst[0]: worst = (error, frame, bone_name)
            row[route] = {'maximum_matrix_error': worst[0], 'worst_frame': worst[1], 'worst_bone': worst[2]}
        ad.action = None; track.mute = True
        results.append(row)
        print('SOURCE_POSE', name, row['direct'], row['nla'], flush=True)
    ad.use_nla = True
    return results


def previews(rig, reference, output):
    """Render the same bind mesh with imported and independent source poses."""
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x = 720; scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.background_type = 'WORLD'
    scene.world.color = (.06, .06, .06)
    meshes = [obj for obj in bpy.data.objects if obj.type == 'MESH']
    coordinates = [obj.matrix_world @ vertex.co for obj in meshes for vertex in obj.data.vertices]
    low = Vector(tuple(min(v[i] for v in coordinates) for i in range(3)))
    high = Vector(tuple(max(v[i] for v in coordinates) for i in range(3)))
    center = (low + high)/2
    camera_data = bpy.data.cameras.new('Regression preview camera')
    camera = bpy.data.objects.new('Regression preview camera', camera_data)
    scene.collection.objects.link(camera); scene.camera = camera
    camera.location = center + Vector((4.0, -7.0, 2.5)).normalized() * 10
    camera.rotation_euler = (center-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera_data.type = 'ORTHO'; camera_data.ortho_scale = max(high-low)*1.8
    ad = rig.animation_data; ad.action = None; ad.use_nla = True
    for track in ad.nla_tracks: track.mute = True
    names = []
    def render(name):
        scene.render.filepath = str(output/name); bpy.ops.render.render(write_still=True)
        names.append(name)
    for bone in rig.pose.bones: bone.matrix_basis = Matrix.Identity(4)
    scene.frame_set(-1000); bpy.context.view_layer.update()
    render('rest.png')
    track = next(item for item in ad.nla_tracks if item.name == 'win')
    strip = track.strips[0]
    start, end = int(round(strip.action_frame_start)), int(round(strip.action_frame_end))
    for label, frame in (('first', start), ('middle', (start+end)//2)):
        track.mute = False
        scene.frame_set(int(strip.frame_start + frame - strip.action_frame_start))
        bpy.context.view_layer.update(); render('win-'+label+'-imported.png')
        track.mute = True; ad.use_nla = False
        scene.frame_set(-1000)
        matrices = reference('win', frame/30)
        # Compare deformation, not Blender's arbitrary edit-bone roll.
        for bone in rig.pose.bones:
            if bone.name in matrices:
                bone.matrix = matrices[bone.name] @ bone.bone.matrix_local
                bpy.context.view_layer.update()
        render('win-'+label+'-source.png')
        ad.use_nla = True
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--addon-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--glb', type=Path, required=True)
    parser.add_argument('--donor', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--all-frames', action='store_true')
    parser.add_argument('--existing-blend', type=Path)
    parser.add_argument('--tolerance', type=float, default=.002)
    parser.add_argument('--previews', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    for name in ('addon_root', 'glb', 'donor', 'output', 'existing_blend'):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    args.output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', args.addon_root/'__init__.py', submodule_search_locations=[str(args.addon_root)])
    addon = importlib.util.module_from_spec(spec); sys.modules[spec.name] = addon; spec.loader.exec_module(addon); addon.register()
    from Time_Stranger_Importer.src.nc_glb import import_nc_model
    source, binary = load_source(args.glb)
    if args.existing_blend:
        bpy.ops.wm.open_mainfile(filepath=str(args.existing_blend))
        rig = next(obj for obj in bpy.data.objects if obj.type == 'ARMATURE')
        notes = []
    else:
        bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
        _collection, rig, notes = import_nc_model(bpy.context, str(args.glb), str(args.donor), False)
    reference, joint_count = references(source, binary, rig)
    result = {'format': 'nc-glb-animation-regression-v1', 'source': pin(args.glb),
              'importer': pin(args.addon_root/'src/nc_glb.py'), 'blender': bpy.app.version_string,
              'joint_count': joint_count, 'all_frames': args.all_frames, 'notes': notes}
    if args.existing_blend:
        result['existing_blend'] = pin(args.existing_blend)
    result['imported'] = check(rig, reference, all_frames=args.all_frames)
    blend = args.output/'converted.blend'
    rig_name = rig.name
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    result['reopened'] = check(bpy.data.objects[rig_name], reference, all_frames=args.all_frames)
    result['maximum_matrix_error'] = max(row[route]['maximum_matrix_error'] for phase in ('imported', 'reopened') for row in result[phase] for route in ('direct', 'nla'))
    result['passed'] = result['maximum_matrix_error'] <= args.tolerance
    if args.previews:
        result['previews'] = previews(bpy.data.objects[rig_name], reference, args.output)
    (args.output/'report.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf8')
    assert result['passed'], result['maximum_matrix_error']


if __name__ == '__main__':
    main()
