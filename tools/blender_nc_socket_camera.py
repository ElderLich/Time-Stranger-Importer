"""Verify cinematic cameras through Skill Phases against an identified source socket.

Run inside Blender with -- --addon-root DIR --glb FILE --phase 2:0 2:2
--socket-path u288_b/root/JT1/JT2 --output NEW_DIR. Writes a review .blend,
one still per phase and the per-phase comparison results.
"""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_nc_glb_animation_regression import accessor, load_source, pin, sample


def find_path(nodes, names):
    children = {i for node in nodes for i in node.get('children', [])}
    search = [i for i in range(len(nodes)) if i not in children]
    chain = []
    for name in names:
        matches = [i for i in search if nodes[i].get('name') == name]
        assert len(matches) == 1, (name, matches)
        chain.append(matches[0])
        search = nodes[chain[-1]].get('children', [])
    return chain


def socket_motion(gltf, binary, chain, clip):
    animation, = [a for a in gltf['animations'] if a['name'] == clip]
    tracks = {}
    for channel in animation['channels']:
        index, path = channel['target']['node'], channel['target']['path']
        if index not in chain:
            continue
        sampler = animation['samplers'][channel['sampler']]
        tracks[index, path] = ([v[0] for v in accessor(gltf, binary, sampler['input'])],
                               accessor(gltf, binary, sampler['output']), sampler.get('interpolation', 'LINEAR'))

    def evaluate(seconds):
        world = Matrix.Identity(4)
        for index in chain:
            node = gltf['nodes'][index]
            def value(path, default):
                track = tracks.get((index, path))
                return sample(*track, path, seconds) if track else node.get(path, default)
            xyzw = value('rotation', (0, 0, 0, 1))
            world @= Matrix.LocRotScale(Vector(value('translation', (0, 0, 0))),
                                        Quaternion((xyzw[3], *xyzw[:3])),
                                        Vector(value('scale', (1, 1, 1))))
        return world
    return evaluate


def verify(scene, gltf, binary, chain, model):
    bpy.context.window.scene = scene
    manifest = json.loads(scene['DSTS_preview_source_json'])
    source = manifest['source_phase']
    actor_clip = source['actorClip']
    camera = scene.camera
    assert manifest['camera']['socket_node_path'] == chain
    assert camera.animation_data.action and camera.data.animation_data.action
    assert not manifest['unresolved']
    rigs = [o for o in scene.objects if o.type == 'ARMATURE']
    assert any(t.name == actor_clip and not t.mute for rig in rigs for t in rig.animation_data.nla_tracks)
    assert len(scene.sequence_editor.strips) == len(source['sounds'])
    assert len([o for o in scene.objects if 'DSTS_source_cue' in o]) == len(source['events'])
    socket_name = gltf['nodes'][chain[-1]]['name']
    cue, = [cue for cue in source['events'] if cue['anchor'] == {'side': 'caster', 'name': socket_name}]
    library = model.parent.parent.parent.parent
    effect = library / 'Vfx_assets' / cue.get('owner', manifest['model_code']) / (cue['slot'] + '.json')
    nodes = json.loads(effect.read_bytes())['effect_nodes']
    node, = [n for n in nodes if n['prefab'].casefold() == cue['prefab'].casefold() and n.get('cameras')]
    optics, = node['cameras']
    basis = node['prefabBasis']
    attachment = Matrix([basis[i:i+3] for i in (0, 3, 6)]).to_4x4()
    attachment.translation = Vector(node['prefabPos'])
    gltf_to_blender = Matrix.Rotation(math.pi / 2, 4, 'X')
    unity_attachment = Matrix.Diagonal((-1, 1, 1, 1)) @ attachment @ Matrix.Diagonal((1, 1, -1, 1))
    world = socket_motion(gltf, binary, chain, actor_clip)
    error = 0.0
    first = None
    travel = 0.0
    start, end = cue['frame'], source['totalFrames']
    for frame in range(start, end + 1):
        scene.frame_set(frame + 1)
        seconds = max(0, frame - source.get('actorDelayFrames', 0)) / 30
        expected = gltf_to_blender @ world(seconds) @ unity_attachment
        location, orientation, _ = expected.decompose()
        expected = Matrix.LocRotScale(location, orientation, None)
        error = max(error, max(abs(expected[i][j] - camera.matrix_world[i][j])
                               for i in range(4) for j in range(4)))
        assert abs(camera.data.angle_y - math.radians(optics['fieldOfView'])) < 1e-5
        if first is None:
            first = location
        travel = max(travel, (location - first).length)
    assert travel > 0.1 and error < 3e-5, (travel, error)
    return {'frames_checked': end-start+1, 'maximum_matrix_error': error,
            'maximum_displacement': travel, 'fov_degrees': optics['fieldOfView'],
            'audio_cues': len(source['sounds']), 'effect_references': len(source['events'])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--addon-root', type=Path, required=True)
    parser.add_argument('--glb', type=Path, required=True)
    parser.add_argument('--phase', nargs='+', required=True)
    parser.add_argument('--socket-path', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    root, model, output = args.addon_root.resolve(), args.glb.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root/'__init__.py',
                                                submodule_search_locations=[str(root)])
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    spec.loader.exec_module(addon)
    addon.register()
    gltf, binary = load_source(model)
    chain = find_path(gltf['nodes'], args.socket_path.split('/'))
    results = {}
    for phase in args.phase:
        bpy.context.scene.dsts_nc_model_path = str(model)
        assert bpy.ops.import_scene.dsts_nc_skill(filepath=str(model), phase=phase) == {'FINISHED'}
        scene = bpy.context.scene
        results[phase] = {'scene': scene.name, 'imported': verify(scene, gltf, binary, chain, model)}
        scene.frame_set((scene.frame_start + scene.frame_end) // 2)
        # Use Blender's viewport rendering so the actor and source camera can
        # be checked without source Unity shaders or GPU rendering plugins.
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.display.shading.color_type = 'TEXTURE'
        scene.render.resolution_x, scene.render.resolution_y = 800, 450
        scene.render.resolution_percentage = 100
        scene.render.filepath = str(output / ('phase-' + phase.replace(':', '-') + '.png'))
        bpy.ops.render.render(write_still=True)
    blend = output / 'Cinematic review.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    for result in results.values():
        result['reopened'] = verify(bpy.data.scenes[result['scene']], gltf, binary, chain, model)
    report = {'blender': bpy.app.version_string, 'addon_version': addon.bl_info['version'],
              'preview_module': pin(root/'src/nc_preview_source.py'), 'model': pin(model),
              'socket_path': chain, 'phases': results, 'blend': str(blend)}
    (output/'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
