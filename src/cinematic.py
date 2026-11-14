"""Source-backed NC cinematic preview in a separate Blender scene."""

import json
import math
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion, Vector

from .animation import _write_curve

UNITY_TO_BLENDER = Matrix(((-1, 0, 0, 0), (0, 0, -1, 0),
                           (0, 1, 0, 0), (0, 0, 0, 1)))
CAMERA_AXES = Matrix.Diagonal((1, 1, -1, 1))


def camera_transform(position, rotation):
    x, y, z, w = rotation
    source = Quaternion((w, x, y, z)).normalized().to_matrix().to_4x4()
    source.translation = Vector(position)
    return UNITY_TO_BLENDER @ source @ CAMERA_AXES


def import_preview(context, filepath, import_actor=True):
    path = Path(filepath).resolve()
    source = json.loads(path.read_text(encoding='utf-8-sig'))
    if source.get('format') != 'dsts-cinematic-preview-v1':
        raise ValueError('Expected dsts-cinematic-preview-v1 JSON')
    camera_source = source.get('camera', {})
    if camera_source.get('coordinate_system') != 'unity-left-handed-y-up':
        raise ValueError('Preview requires explicitly declared Unity camera coordinates')
    samples = camera_source.get('samples', [])
    if not samples:
        raise ValueError('Preview contains no sampled camera motion')
    fps = float(source.get('fps', 30))
    if not math.isfinite(fps) or not 1 <= fps <= 240:
        raise ValueError('Preview FPS must be between 1 and 240')
    origin = float(source.get('frame_origin', 0))
    frame = lambda value: float(value) - origin + 1
    transforms = []
    for sample in samples:
        values = [sample['frame'], *sample['position'], *sample['rotation'], sample['fov_degrees']]
        if not all(math.isfinite(float(v)) for v in values) or not 0 < sample['fov_degrees'] < 179:
            raise ValueError('Invalid camera sample')
        transforms.append((frame(sample['frame']), camera_transform(sample['position'], sample['rotation']),
                           math.radians(sample['fov_degrees'])))
    if any(b[0] <= a[0] for a, b in zip(transforms, transforms[1:])):
        raise ValueError('Camera sample frames must be strictly increasing')
    resolve = lambda value: (path.parent / value).resolve()
    actor_path = resolve(source['actor_path']) if import_actor and source.get('actor_path') else None
    if actor_path and not actor_path.is_file():
        raise FileNotFoundError(actor_path)
    audio = [(item, resolve(item['path'])) for item in source.get('audio', [])]
    for _, audio_path in audio:
        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)

    from .scene_state import snapshot, rollback
    before = snapshot()
    old_scene = context.window.scene
    scene = bpy.data.scenes.new(f"NC {source.get('model_code', '')} · {source.get('actor_clip', 'Cinematic')}")
    context.window.scene = scene
    try:
        scene.render.fps = round(fps)
        scene.render.fps_base = round(fps) / fps
        scene.frame_start = 1
        scene.frame_end = max(1, math.ceil(frame(source.get('duration_frames', samples[-1]['frame']))))
        scene['DSTS_preview_manifest'] = str(path)
        scene['DSTS_preview_limitations'] = '\n'.join(source.get('limitations', []))
        if actor_path:
            result = bpy.ops.import_scene.gltf(filepath=str(actor_path))
            if result != {'FINISHED'}:
                raise RuntimeError(f'Actor GLB import failed: {result}')
            clip = source.get('actor_clip', '')
            assigned = 0
            for obj in scene.objects:
                ad = obj.animation_data
                if not ad:
                    continue
                candidates = []
                for track in ad.nla_tracks:
                    track.mute = True
                    for strip in track.strips:
                        if strip.action and (strip.action.name == clip or strip.action.name.startswith(clip + '.')):
                            candidates.append(strip)
                if len(candidates) == 1:
                    ad.action = candidates[0].action
                    ad.action_slot = candidates[0].action_slot
                    assigned += 1
            if not assigned:
                raise ValueError(f'Actor GLB has no unambiguous NLA clip {clip!r}')

        camera_data = bpy.data.cameras.new('NC source camera')
        camera = bpy.data.objects.new('NC source camera', camera_data)
        scene.collection.objects.link(camera)
        scene.camera = camera
        camera.rotation_mode = 'QUATERNION'
        camera_data.sensor_fit = 'VERTICAL'
        camera_data.sensor_height = 24
        camera_data.clip_start = max(.001, float(camera_source.get('near', .1)))
        camera_data.clip_end = max(camera_data.clip_start + 1, float(camera_source.get('far', 1000)))
        action = bpy.data.actions.new('NC cinematic camera motion')
        rotations, positions, lenses = [], [], []
        previous = None
        for at, matrix, fov in transforms:
            location, rotation, _ = matrix.decompose()
            if previous is not None and previous.dot(rotation) < 0:
                rotation.negate()
            previous = rotation
            positions.append((at, location))
            rotations.append((at, rotation))
            lenses.append((at, camera_data.sensor_height / (2 * math.tan(fov / 2))))
        for component in range(3):
            _write_curve(action, camera, 'location', component, [(f, v[component]) for f, v in positions], 'Camera')
        for component in range(4):
            _write_curve(action, camera, 'rotation_quaternion', component, [(f, q[component]) for f, q in rotations], 'Camera')
        lens_action = bpy.data.actions.new('NC cinematic camera lens')
        _write_curve(lens_action, camera_data, 'lens', 0, lenses, 'Lens')
        if audio:
            editor = scene.sequence_editor_create()
            strips = getattr(editor, 'strips', None)
            if strips is None:
                strips = editor.sequences
            for channel, (cue, sound_path) in enumerate(audio, 1):
                sound = strips.new_sound(sound_path.stem, str(sound_path), channel=channel,
                                         frame_start=round(frame(cue['frame'])))
                sound.volume = max(0, float(cue.get('volume', 1)))
        effects = bpy.data.collections.new('Source effect references (not rendered)')
        scene.collection.children.link(effects)
        for index, cue in enumerate(source.get('effects', [])):
            reference = bpy.data.objects.new(f"Effect {index + 1}: {cue.get('prefab', cue.get('description', 'source'))}", None)
            reference.hide_render = True
            reference.hide_viewport = True
            reference['DSTS_source_cue'] = json.dumps(cue, ensure_ascii=False)
            if cue.get('path'):
                reference['DSTS_source_prefab'] = str(resolve(cue['path']))
            effects.objects.link(reference)
            scene.timeline_markers.new(reference.name, frame=round(frame(cue['frame'])))
        scene.frame_set(scene.frame_start)
        return scene
    except Exception:
        context.window.scene = old_scene
        bpy.data.scenes.remove(scene)
        rollback(before)
        raise
