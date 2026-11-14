"""Combined model/camera/light ownership and export using native fixtures."""
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import bpy
from mathutils import Matrix, Vector
from bpy_extras.object_utils import world_to_camera_view

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', ROOT/'__init__.py',
                                            submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src import geom as adapter, native_scene, native_lights
from Time_Stranger_Importer.src.backend import load_format_modules
from Time_Stranger_Importer.src.nlst import write_nlst
from Time_Stranger_Importer.src.scene_state import snapshot
backend = load_format_modules()[0]
backend.set_throw_errors(False)
SOURCE = Path(r'D:\Digimon Modding\Time Stranger Extracted')


def rest(collection):
    rig = next(obj for obj in collection.objects if obj.type == 'ARMATURE')
    if rig.animation_data:
        rig.animation_data.action = None
        for track in rig.animation_data.nla_tracks:
            track.mute = True
    for bone in rig.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    return rig


def rejects(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError('Unsupported scene mutation accepted')


report = {'blender': bpy.app.version_string, 'fixtures': []}
with tempfile.TemporaryDirectory(prefix='dsts_native_scene_') as temp:
    temp = Path(temp)
    original_state = snapshot()
    with patch.object(native_scene, '_camera_data', side_effect=ValueError('simulated camera preview failure')):
        rejects(lambda: adapter.import_geom(bpy.context,
            str(SOURCE/'addcont_01.dx11/cam_chr690_bs01_pc.geom'), include_scene_records=True))
    assert snapshot() == original_state
    report['failed_import_rollback'] = True
    for name in ('cam_chr690_bs01_em', 'cam_chr690_bs01_pc', 'dep01t0103p'):
        source = SOURCE/'addcont_01.dx11'/(name+'.geom')
        before = set(bpy.data.collections)
        assert bpy.ops.import_scene.dsts_geom(filepath=str(source), include_scene_records=True) == {'FINISHED'}
        collection = next(col for col in set(bpy.data.collections)-before if 'DSTS_scene_source' in col)
        rig = rest(collection)
        native = backend.Geom.from_file(str(source))
        result = adapter.export_geom(collection)
        output = temp/(name+'.geom')
        result.to_file(str(output))
        output.with_suffix('.nlst').write_text(write_nlst(result), encoding='utf-8')
        parsed = backend.Geom.from_file(str(output))
        assert [r.raw for r in parsed.lights] == [r.raw for r in native.lights]
        assert [r.raw for r in parsed.cameras] == [r.raw for r in native.cameras]
        assert len(parsed.meshes) == len(native.meshes)
        if not native.meshes and not native.materials:
            assert output.read_bytes() == source.read_bytes()
        for child in collection.children:
            for obj in child.objects:
                native_scene.validate_preview_binding(child, obj)
        if native.cameras:
            collection['DSTS_camera_0_fov_degrees'] = 45.0
            collection.update_tag()
            bpy.context.view_layer.update()
            camera_child = next(child for child in collection.children if child.get('DSTS_scene_role') == 'cameras')
            camera_obj = camera_child.objects[0]
            assert camera_obj.type == 'CAMERA'
            assert abs(camera_obj.data.angle_y - math.radians(45)) < 1e-5
            point = camera_obj.matrix_world @ Vector((0, 0, -10))
            projected = world_to_camera_view(bpy.context.scene, camera_obj, point)
            assert abs(projected.x-.5) < 1e-5 and abs(projected.y-.5) < 1e-5 and projected.z > 0
            bpy.context.view_layer.objects.active = camera_obj
            assert bpy.ops.dsts.use_native_camera() == {'FINISHED'}
            assert bpy.context.scene.camera == camera_obj
            camera_obj.data.dof.use_dof = True
            rejects(lambda: adapter.export_geom(collection))
            camera_obj.data.dof.use_dof = False
            curve = camera_obj.data.animation_data.drivers[0]
            expression = curve.driver.expression
            curve.driver.expression = '1'
            rejects(lambda: adapter.export_geom(collection))
            curve.driver.expression = expression
            adapter.export_geom(collection).to_file(str(output))
            edited = backend.Geom.from_file(str(output))
            assert edited.cameras[0].fov_degrees == 45
            old_bytes = output.read_bytes()
            with patch('Time_Stranger_Importer.src.spark.os.replace', side_effect=OSError('simulated locked destination')):
                try:
                    adapter.export_geom(collection).to_file(str(output))
                except OSError:
                    pass
                else:
                    raise AssertionError('Expected failed atomic camera write')
            assert output.read_bytes() == old_bytes
        light_child = next(child for child in collection.children if child.get('DSTS_scene_role') == 'lights')
        rejects(lambda: native_lights.export_lights(light_child, temp/'divergent.geom'))
        # One pose-bone move updates the owned point/guide location at the bound head.
        bound = next((obj for obj in light_child.objects if obj.get('DSTS_scene_bone')), None)
        if bound:
            bone = rig.pose.bones[bound['DSTS_scene_bone']]
            initial = bound.matrix_world.translation.copy()
            bone.location.x += 1
            bpy.context.view_layer.update()
            assert (bound.matrix_world.translation-initial).length > .5
            bone.matrix_basis = Matrix.Identity(4)
            bpy.context.view_layer.update()
            con = bound.constraints[0]
            con.mute = True
            rejects(lambda: adapter.export_geom(collection))
            con.mute = False
        report['fixtures'].append(dict(name=name, meshes=len(native.meshes), cameras=len(native.cameras), lights=len(native.lights)))
    # Compose verified native records into a model fixture to exercise concurrent
    # mesh and optical edits; this test fixture is never offered as a game asset.
    native = backend.Geom.from_file(str(SOURCE/'app_0.dx11/chr050.geom'))
    lights = backend.Geom.from_file(str(SOURCE/'addcont_01.dx11/ef_b_chr689_bs01.geom'))
    cameras = backend.Geom.from_file(str(SOURCE/'app_0.dx11/cam_chr395_bs01_pc.geom'))
    light = next(record for record in lights.lights if record.native_type == 0)
    light.name_hash = native.skeleton.bones[0].name_hash
    camera = cameras.cameras[0]
    camera.name_hash = native.skeleton.bones[0].name_hash
    native.lights.append(light)
    native.cameras.append(camera)
    combined = temp/'combined-native.geom'
    native.to_file(str(combined))
    combined.with_suffix('.nlst').write_text(write_nlst(native), encoding='utf-8')
    collection = adapter.import_geom(bpy.context, str(combined), include_scene_records=True)
    rest(collection)
    light_child = next(child for child in collection.children if child.get('DSTS_scene_role') == 'lights')
    point = light_child.objects[0]
    assert point.type == 'LIGHT'
    point.dsts_native_light.color = (.25, .5, .75)
    point.dsts_native_light.intensity = 2
    collection['DSTS_camera_0_fov_degrees'] = 50.0
    model = next(obj for obj in collection.objects if obj.type == 'MESH')
    model.data.vertices[0].co.x += .125
    output = temp/'combined-edited.geom'
    result = adapter.export_geom(collection)
    result.to_file(str(output))
    edited = backend.Geom.from_bytes(output.read_bytes())
    assert tuple(edited.lights[0].color_rgb) == (.25,.5,.75) and edited.lights[0].intensity == 2
    assert edited.cameras[0].fov_degrees == 50 and len(edited.meshes) == len(native.meshes)
    assert edited.cameras[0].raw[:4] == camera.raw[:4] and edited.cameras[0].raw[8:] == camera.raw[8:]
    allowed = set(range(8,12)) | set(range(40,52))
    assert all(i in allowed for i,(a,b) in enumerate(zip(edited.lights[0].raw, light.raw)) if a != b)
    rejects(lambda: native_lights.export_lights(light_child, temp/'separate.geom'))
    point.constraints[0].subtarget = 'wrong bone'
    rejects(lambda: adapter.export_geom(collection))
    point.constraints[0].subtarget = point['DSTS_scene_bone']
    report['concurrent_mesh_camera_point_edits'] = True
    # Disabling previews still retains scene records in the owning export.
    no_preview = adapter.import_geom(bpy.context, str(combined), include_scene_records=False)
    rest(no_preview)
    preserved = adapter.export_geom(no_preview)
    assert not no_preview.children
    assert [item.raw for item in preserved.lights] == [item.raw for item in native.lights]
    assert [item.raw for item in preserved.cameras] == [item.raw for item in native.cameras]
    names = [col.name for col in bpy.data.collections if 'DSTS_scene_source' in col]
    bpy.ops.wm.save_as_mainfile(filepath=str(temp/'combined.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(temp/'combined.blend'))
    for name in names:
        collection = bpy.data.collections[name]
        rest(collection)
        adapter.export_geom(collection)
    report['save_reopen'] = True
    report['single_export_ownership'] = True
    report['camera_projection_fov_and_export_guards'] = True
    report['native_game_acceptance'] = False
addon.unregister()
addon.register()
addon.unregister()
output = ROOT/'build'/f'native-scene-{bpy.app.version[0]}{bpy.app.version[1]}.json'
output.write_text(json.dumps(report, indent=2)+'\n')
print('NATIVE SCENE PASS', report)
