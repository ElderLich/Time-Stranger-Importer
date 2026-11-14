"""Exercise user-facing effect edits and source skill imports on Blender 5.x.

--manual-output PATH retains the two requested real skill scenes for inspection.
No game payload, save, existing mod or source capture is modified.
"""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', ROOT / '__init__.py',
                                            submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src import spark
from Time_Stranger_Importer.src.native_effects import import_effect, document_from_collection, export_effect
from Time_Stranger_Importer.src.nc_skill_ui import catalog, primary_phases


def chunk(tag, payload):
    return struct.pack('<4sIII', tag, 16 + len(payload), 16, 16 + len(payload)) + payload


def fixture():
    header = bytearray(24)
    header[:4] = b'SPAK'
    struct.pack_into('<4H', header, 8, 140, 1, 1, 0)
    header[16] = 30
    node = bytearray(680)
    struct.pack_into('<H', node, 0, 0)
    struct.pack_into('<I', node, 4, 8)
    node[8:17] = b'test-node'
    for offset in spark.TRANSFORM_FIELDS.values():
        struct.pack_into('<6f', node, offset, 1, 1, 1, 1, 1, 1)
    struct.pack_into('<6f', node, 424, -2, -2, 0, 0, 1, 1)
    struct.pack_into('<I', node, 520, spark.NONE)
    struct.pack_into('<I', node, 536, 0)
    struct.pack_into('<2h', node, 472, -1, -1)
    node[96:104] = b'opaque!!'
    anim = bytearray(96)
    struct.pack_into('<H', anim, 0, 1)
    struct.pack_into('<HBBHHffII', anim, 24, 1, 0, 0, 2, 0, 2, 4, spark.NONE, 0)
    anim[48:72] = spark.Key(1, 1, 2).pack()
    anim[72:96] = spark.Key(10, 0, 4).pack()
    return bytes(header) + chunk(b'NODE', node) + chunk(b'ANIM', anim) + struct.pack('<4sIII', b'TERM', 16, 0, 0)


report = {'blender': bpy.app.version_string, 'python': sys.version, 'checks': []}
with tempfile.TemporaryDirectory(prefix='dsts_effect_workflows_') as folder:
    folder = Path(folder)
    source = folder / 'source.sprk'
    original = fixture()
    source.write_bytes(original)
    collection = import_effect(bpy.context, source)
    assert document_from_collection(collection).to_bytes() == original
    node = next(iter(collection.objects))
    assert tuple(node.dsts_spark_node.secondary_scale_low) == (-2, 0, 1)
    node.dsts_spark_node.primary_translation_low = (2, 3, 4)
    node.dsts_spark_node.primary_translation_high = (5, 6, 7)
    node.dsts_spark_node.delay_range = (4, 8)
    node.dsts_spark_node.channels[0].keys[0].value = 10
    collection_name, node_name = collection.name, node.name
    blend = folder / 'edited.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    collection, node = bpy.data.collections[collection_name], bpy.data.objects[node_name]
    edited = document_from_collection(collection)
    output = folder / 'edited.sprk'
    edited.write(output)
    restored = spark.SparkDocument.read(output)
    assert restored.nodes[8].primary_translation.low == (2, 3, 4)
    assert restored.nodes[8].primary_translation.high == (5, 6, 7)
    assert restored.nodes[8].secondary_scale.low == (-2, 0, 1)
    assert restored.nodes[8].delay_range == (4, 8)
    assert restored.channels(0)[0].keys[0].value == 10
    assert b'opaque!!' in output.read_bytes()
    changed_offsets = {i for i, (a, b) in enumerate(zip(original, output.read_bytes())) if a != b}
    allowed = set(range(40 + 72, 40 + 96)) | set(range(40 + 468, 40 + 472))
    anim_start = 40 + 680 + 16
    allowed |= set(range(anim_start + 24 + 8, anim_start + 24 + 16)) | set(range(anim_start + 48 + 4, anim_start + 48 + 8))
    assert changed_offsets <= allowed, sorted(changed_offsets - allowed)
    try:
        edited.write(output)
        raise AssertionError('Existing effect output was replaced')
    except (FileExistsError, ValueError):
        pass
    sentinel = output.read_bytes()
    node.dsts_spark_node.channels[0].keys[0].frame = 11
    try:
        document_from_collection(collection).write(output, overwrite=True)
        raise AssertionError('Unsorted keys accepted')
    except ValueError:
        pass
    assert output.read_bytes() == sentinel
    node.dsts_spark_node.channels[0].keys[0].frame = 1
    source.write_bytes(b'external change')
    try:
        export_effect(collection, source, overwrite=True)
        raise AssertionError('Stale source overwrite accepted')
    except ValueError:
        pass
    assert source.read_bytes() == b'external change'
    for prop, value, restore in [('rotation_mode', 'QUATERNION', 'XYZ'),
                                 ('delta_location', (1, 0, 0), (0, 0, 0)),
                                 ('rotation_quaternion', (.5, .5, .5, .5), (1, 0, 0, 0))]:
        setattr(node, prop, value)
        try:
            document_from_collection(collection)
            raise AssertionError(f'Unsupported guide {prop} edit accepted')
        except ValueError:
            pass
        setattr(node, prop, restore)
    node['DSTS_spark_children'] = '999'
    try:
        document_from_collection(collection)
        raise AssertionError('Unsupported graph edit accepted')
    except ValueError:
        pass
    report['checks'].append('SPRK ranges/keys edit-save-reopen-export, opaque/negative/zero preservation, scoped diff and failure protection')
    print('PASS', report['checks'][-1])

    model = Path(r'D:\Digimon Modding\Digimon Library\New Century\Digimon_Library\LeviamonX_x147\model\model.glb')
    if model.is_file():
        phases = primary_phases(catalog(model))
        assert [phase['actor_clip'] for phase in phases] == ['attack_move_to', 'skill2']
        old_scenes = set(bpy.data.scenes)
        result = bpy.ops.import_scene.dsts_nc_skill(filepath=str(model), phase='__PRIMARY__')
        assert result == {'FINISHED'}
        scenes = [scene for scene in bpy.data.scenes if scene not in old_scenes]
        assert len(scenes) == 2
        for scene in scenes:
            assert scene.camera is None, 'Ordinary cast/approach must not inherit cinematic camera'
            expected = 135 if scene['DSTS_preview_actor_clip'] == 'skill2' else 71
            rigs = [obj for obj in scene.objects if obj.type == 'ARMATURE']
            active = [strip for rig in rigs for track in rig.animation_data.nla_tracks if not track.mute for strip in track.strips
                      if strip.action.name.split('.')[0] == scene['DSTS_preview_actor_clip']]
            assert active and all(abs(strip.frame_end-strip.frame_start-expected) < .01 for strip in active)
            print('SOURCE_STRIPS', scene['DSTS_preview_actor_clip'], [(strip.frame_start, strip.frame_end, strip.action_frame_start, strip.action_frame_end) for strip in active])
            assert all(abs(strip.frame_start - 1) < .01 for strip in active)
            assert scene.frame_end >= expected + 1
            assert len(scene.timeline_markers) == 2
            assert len(scene.sequence_editor.strips) == 1
            bpy.context.window.scene = scene
            scene.frame_set(1)
            rig = rigs[0]
            start_z = rig.matrix_world.translation.z
            scene.frame_set(expected + 2)
            reset = rig.matrix_world.copy()
            assert abs(reset.translation.z - start_z) < .1, (start_z, reset.translation.z)
            scene.frame_set(scene.frame_end)
            assert abs(rig.matrix_world.translation.z - start_z) < .1
            scene.frame_set(expected + 2)
            assert max(abs(reset[r][c] - rig.matrix_world[r][c]) for r in range(4) for c in range(4)) < 1e-6
            assert rig.get('DSTS_preview_idle_clip') == 'stand'
            assert all(obj.hide_get() for obj in scene.objects if obj.get('DSTS_source_helper'))
        primary_names = [scene.name for scene in scenes]
        cast = next(scene for scene in scenes if scene['DSTS_preview_actor_clip'] == 'skill2')
        bpy.context.window.scene = cast
        cast.frame_set(1)
        # Remove only synthetic test data from the manual project.
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)
        for scene in list(old_scenes):
            if scene.name in bpy.data.scenes:
                bpy.data.scenes.remove(scene)
        manual = folder / 'skills.blend'
        if '--manual-output' in sys.argv:
            manual = Path(sys.argv[sys.argv.index('--manual-output') + 1]).resolve()
            manual.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(manual))
        bpy.ops.wm.open_mainfile(filepath=str(manual))
        assert all(name in bpy.data.scenes for name in primary_names)
        assert bpy.context.scene['DSTS_preview_actor_clip'] == 'skill2'
        bpy.context.scene.frame_set(60)
        assert bpy.context.scene.sequence_editor.strips[0].sound is not None
        report['manual_project'] = str(manual) if '--manual-output' in sys.argv else None
        report['checks'].append('Real operator imports Triple Demon Jaw approach and Sevenfold cast, exact clip duration/start, audio, camera absence, save/reopen')
        print('PASS', report['checks'][-1])

addon.unregister()
addon.register()
addon.unregister()
report['checks'].append('Registration lifecycle')
destination = ROOT / 'build' / f'effect-workflows-{bpy.app.version[0]}{bpy.app.version[1]}.json'
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(report, indent=2) + '\n')
print('PASS effect workflows', destination)
