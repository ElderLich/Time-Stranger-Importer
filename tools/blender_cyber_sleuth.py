"""Real Cyber Sleuth model import, action evaluation and saved-scene checks."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', ROOT / '__init__.py',
                                            submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src import cyber_sleuth as cs
from Time_Stranger_Importer.src.blender_compat import iter_action_fcurves


def check(path, related_cameras):
    previous = set(bpy.data.objects)
    assert bpy.ops.import_scene.cyber_sleuth_model(filepath=str(path), import_cameras=related_cameras) == {'FINISHED'}
    root = next(o for o in set(bpy.data.objects) - previous if o.get('dscs_source') == str(path.resolve().with_suffix('')))
    warnings = json.loads(root['dscs_warnings'])
    rig = next(o for o in root.children if o.type == 'ARMATURE')
    meshes = [o for o in rig.children if o.type == 'MESH']
    actions = [strip.action for track in rig.animation_data.nla_tracks for strip in track.strips]
    assert len(rig.data.bones) > 0
    for mesh in meshes:
        assert mesh.data.vertices and mesh.data.polygons
        assert mesh.data.materials
        assert mesh.modifiers[0].object == rig
        assert all(math.isfinite(v) for vert in mesh.data.vertices for v in vert.co)
    keys = sum(len(fc.keyframe_points) for action in actions for fc in iter_action_fcurves(action, rig))
    assert keys > 0
    materials = {material for mesh in meshes for material in mesh.data.materials}
    packed = [node.image for material in materials
              for node in material.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image]
    assert all(image.packed_file and image.size[0] > 0 for image in packed)
    # Exercise each native clip at its midpoint and ensure transforms remain finite.
    for track in rig.animation_data.nla_tracks:
        if track.name != 'Base':
            track.mute = False
        strip = track.strips[0]
        bpy.context.scene.frame_set(int((strip.frame_start + strip.frame_end) / 2))
        assert all(math.isfinite(v) for bone in rig.pose.bones for row in bone.matrix for v in row)
        if track.name != 'Base':
            track.mute = True
    bpy.context.scene.frame_set(1)
    # Camera objects must evaluate at their actual named native bone, including
    # the shared Y-up to Blender Z-up root, without a second root rotation.
    cameras = [obj for obj in bpy.data.objects if obj.type == 'CAMERA' and cs.is_cyber_sleuth(obj)]
    for camera in cameras:
        constraint = camera.constraints[0]
        expected = constraint.target.matrix_world @ constraint.target.pose.bones[constraint.subtarget].matrix
        assert max(abs(a - b) for ra, rb in zip(camera.matrix_world, expected)
                   for a, b in zip(ra, rb)) < 1e-4
        source = json.loads(camera['dscs_camera_record'])
        expected_lens = camera.data.sensor_height / (2 * math.tan(math.radians(source['fov']) / 2))
        assert abs(camera.data.lens - expected_lens) < 1e-4
    selected_track = next((i for i, track in enumerate(rig.animation_data.nla_tracks) if track.name.endswith('_bs01')), 0)
    bpy.context.view_layer.objects.active = rig
    assert bpy.ops.dscs.preview_clip(index=selected_track) == {'FINISHED'}
    if meshes:
        mesh = meshes[0]
        graph = bpy.context.evaluated_depsgraph_get()
        start = [v.co.copy() for v in mesh.evaluated_get(graph).data.vertices]
        bpy.context.scene.frame_set(max(2, bpy.context.scene.frame_end // 2))
        end = [v.co.copy() for v in mesh.evaluated_get(graph).data.vertices]
        deformation = max(((a-b).length for a,b in zip(start, end)), default=0)
        if path.stem == 'chr002':
            assert deformation > .01, 'Real skill does not deform the imported mesh'
    else:
        deformation = 0
    cs.activate_clip(rig, 0, bpy.context.scene)
    if cameras:
        assert bpy.ops.dscs.use_camera(object_name=cameras[0].name) == {'FINISHED'}
        assert bpy.context.scene.camera == cameras[0]
    return {'source': str(path), 'root': root.name, 'rig': rig.name,
            'meshes': len(meshes), 'bones': len(rig.data.bones), 'actions': len(actions),
            'keys': keys, 'packed_images': len(set(packed)), 'skill_vertex_motion': deformation,
            'related_cameras': len(json.loads(root['dscs_related_cameras'])), 'warnings': warnings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('models', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--related-cameras', action='store_true')
    parser.add_argument('--preview', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    report = {'blender': bpy.app.version_string, 'models': []}
    for path in args.models:
        report['models'].append(check(path, args.related_cameras))
    if args.preview:
        rig = bpy.data.objects[report['models'][0]['rig']]
        visible = {obj for obj in rig.children if obj.type == 'MESH'}
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                obj.hide_render = obj not in visible
        points = [obj.matrix_world @ Vector(corner) for obj in visible for corner in obj.bound_box]
        low = Vector(tuple(min(v[i] for v in points) for i in range(3)))
        high = Vector(tuple(max(v[i] for v in points) for i in range(3)))
        center, radius = (low + high) / 2, max((high - low).length / 2, .1)
        camera_data = bpy.data.cameras.new('Audit overview')
        camera = bpy.data.objects.new('Audit overview', camera_data)
        bpy.context.scene.collection.objects.link(camera)
        camera.location = center + Vector((1.1, -2.5, .8)) * radius
        camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
        camera_data.lens = 45
        camera_data.clip_end = radius * 100
        bpy.context.scene.camera = camera
        light_data = bpy.data.lights.new('Audit illumination', 'SUN')
        light_data.energy = 3
        light = bpy.data.objects.new('Audit illumination', light_data)
        bpy.context.scene.collection.objects.link(light)
        light.rotation_euler = (.4, -.5, -.5)
        scene = bpy.context.scene
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = 16
        scene.render.resolution_x = scene.render.resolution_y = 512
        scene.render.resolution_percentage = 100
        scene.world.color = (.18, .18, .18)
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        scene.render.filepath = str(args.preview.resolve())
        bpy.ops.render.render(write_still=True)
        report['preview'] = str(args.preview)
    # A failed import after geometry creation must restore the existing scene.
    before_failure = {name: set(getattr(bpy.data, name)) for name in ('objects', 'meshes', 'materials', 'actions', 'images')}
    previous_meshes = cs._meshes
    def fail_after_mesh(*args, **kwargs):
        previous_meshes(*args, **kwargs)
        raise ValueError('injected after geometry creation')
    cs._meshes = fail_after_mesh
    try:
        cs.import_model(bpy.context, str(args.models[0]))
        raise AssertionError('Injected failure did not occur')
    except ValueError as error:
        assert str(error) == 'injected after geometry creation'
    finally:
        cs._meshes = previous_meshes
    assert all(set(getattr(bpy.data, name)) == previous for name, previous in before_failure.items())
    report['failed_import_rollback'] = 'passed'
    before = {r['root']: bpy.data.objects[r['root']]['dscs_summary'] for r in report['models']}
    with tempfile.TemporaryDirectory(prefix='dscs_blend_test_') as folder:
        filepath = str(Path(folder) / 'cyber-sleuth.blend')
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        bpy.ops.wm.open_mainfile(filepath=filepath)
        for name, summary in before.items():
            assert bpy.data.objects[name]['dscs_summary'] == summary
            assert cs.is_cyber_sleuth(bpy.data.objects[name])
        assert all(image.packed_file for image in bpy.data.images if image.get('asset_game') == cs.GAME)
        assert all(action.get('asset_game') == cs.GAME for action in bpy.data.actions)
    try:
        cs.reject_export()
        raise AssertionError('Cyber Sleuth export incorrectly accepted')
    except ValueError:
        pass
    # Test native exporter guards on an actual CS collection and action.
    from Time_Stranger_Importer.src.geom import export_geom
    from Time_Stranger_Importer.src.animation import build_from_action
    rig = bpy.data.objects[report['models'][0]['rig']]
    collection = rig.users_collection[0]
    for call in (lambda: export_geom(collection),
                 lambda: build_from_action(rig, rig.animation_data.nla_tracks[0].strips[0].action)):
        try:
            call()
            raise AssertionError('Time Stranger export accepted Cyber Sleuth input')
        except (ValueError, TypeError) as error:
            # The function signatures below are checked against the maintained
            # exporter API; TypeError must not count as a successful guard.
            assert isinstance(error, ValueError) and 'Cyber Sleuth' in str(error), str(error)
    report['save_reopen'] = 'passed'
    report['native_export_rejection'] = 'passed'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
