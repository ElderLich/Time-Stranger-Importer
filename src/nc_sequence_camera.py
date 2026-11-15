"""Build native player and enemy cameras for frame-indexed skill phases."""
import hashlib
import json
import math
import struct
from pathlib import Path

import numpy as np
from mathutils import Matrix, Quaternion, Vector

from .backend import load_format_modules
from .cinematic import camera_transform
from .native_camera import patch_camera_lenses
from .nc_skill_sequence import _sample, read_anim
from .skill_conversion.camera_assets import camera_record, rename_camera_bindings


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _camera_rig(path):
    if path.suffix.lower() != '.geom':
        raise ValueError(f'Expected a camera GEOM: {path}')
    raw = path.read_bytes()
    _, record = camera_record(raw)
    fmt = load_format_modules()[0]
    previous_throw, previous_errors = fmt.get_throw_errors(), fmt.get_error_list()
    try:
        fmt.set_throw_errors(False)
        fmt.set_error_list([])
        geom = fmt.Geom.from_file(str(path))
    finally:
        fmt.set_throw_errors(previous_throw)
        fmt.set_error_list(previous_errors)
    rig = geom.skeleton
    matches = [i for i, b in enumerate(rig.bones) if b.name_hash == record[0]]
    if len(matches) != 1 or rig.bones[matches[0]].parent is not None:
        raise ValueError(f'Select a root-bound native camera: {path}')
    ci = matches[0]
    clip = read_anim(path.with_suffix('.anim'))
    if len(rig.bones) != clip.bone_count or clip.frame_count < 1 or clip.playback_rate <= 0:
        raise ValueError(f'Camera donor skeleton or timing differs: {path}')
    skeleton = struct.unpack_from('<Q', raw, 0x98)[0]
    count = struct.unpack_from('<H', raw, skeleton+18)[0]
    if count != clip.float_channel_count or count not in (0, 1):
        raise ValueError(f'Expected a static lens or a single FOV binding: {path}')
    if count:
        at = skeleton+40+struct.unpack_from('<I', raw, skeleton+40)[0]
        if struct.unpack_from('<I', raw, at)[0] != record[0]:
            raise ValueError(f'Float channel does not bind the camera: {path}')
    bones = rig.bones[ci].transform
    return raw, record, ci, clip, (list(bones.position), list(bones.quaternion))


def _preview_samples(path):
    data = json.loads(path.read_bytes())
    camera = data.get('camera') or {}
    if (data.get('format') not in ('dsts-cinematic-preview-v1', 'dsts-cinematic-preview-v2')
            or data.get('fps') != 30 or camera.get('coordinate_system') != 'unity-left-handed-y-up'):
        raise ValueError(f'Expected a 30 Hz captured Unity camera preview: {path}')
    # Same camera basis as the Blender cinematic, with Blender's Z-up conversion removed.
    native_axes = Matrix.Rotation(-math.pi / 2, 4, 'X')
    rows = {}
    for sample in camera['samples']:
        at = sample['frame']
        if type(at) is not int or at < 0 or (rows and at != next(reversed(rows))+1):
            raise ValueError(f'Camera samples must use consecutive source frames: {path}')
        transform = native_axes @ camera_transform(sample['position'], sample['rotation'])
        q = transform.to_quaternion().normalized()
        rows[at] = (list(transform.translation), [q.x, q.y, q.z, q.w], sample['fov_degrees'])
    if not rows:
        raise ValueError(f'Camera has no samples: {path}')
    first, last = next(iter(rows)), next(reversed(rows))
    def sample_frame(at):
        if at > last:
            raise ValueError(f'Phase extends past the captured shot at {last}: {path}')
        return rows[max(at, first)]
    return sample_frame, (camera['near'], camera['far'])


def _native_samples(path):
    _, record, ci, clip, rest = _camera_rig(path)
    channels = (clip.positions[ci] or {0: rest[0]}, clip.rotations[ci] or {0: rest[1]},
                clip.float_channels.get(0) or {0: record[1]})
    keys = [sorted(channel) for channel in channels]
    def sample_frame(at):
        source_frame = min(at * clip.playback_rate / 30, clip.frame_count-1)
        return [_sample(channel, times, source_frame, i == 1)
                for i, (channel, times) in enumerate(zip(channels, keys))]
    return sample_frame, (record[3], record[4])


def build_cameras(job, phases, output_dir, recipe_dir):
    """Bake both camera tracks into the sequence builder's private output directory."""
    template = (recipe_dir/job['camera_template']).resolve()
    template_raw, _, ci, clip, _ = _camera_rig(template)
    if clip.float_channel_count != 1:
        raise ValueError('Select a native template with one indexed FOV channel')
    if read_anim(Path(phases[0]['motion'])).playback_rate != 30:
        raise ValueError('Camera sequences require 30 Hz actor phases')
    nlst = template.with_suffix('.nlst').read_bytes().decode('ascii')
    reports = []
    for side in ('pc', 'em'):
        name = f"cam_{job['stem']}_{side}"
        positions, rotations, fields = {}, {}, {}
        planes, inputs = [], {}
        previous = None
        for phase in phases:
            camera = phase['camera']
            if set(camera) == {'preview'}:
                path = (recipe_dir / camera['preview']).resolve()
                sample_frame, lens = _preview_samples(path)
                inputs[str(path)] = _digest(path)
            elif set(camera) == {'pc', 'em'}:
                path = (recipe_dir / camera[side]).resolve()
                sample_frame, lens = _native_samples(path)
                for ext in ('.geom', '.anim', '.nlst'):
                    sibling = path.with_suffix(ext)
                    inputs[str(sibling)] = _digest(sibling)
            else:
                raise ValueError('Each phase needs a captured preview or a native player and enemy camera pair')
            if not (math.isfinite(lens[1]) and 0 < lens[0] < lens[1]):
                raise ValueError(f'Invalid camera clipping planes: {path}')
            planes.append(lens)
            for local in range(phase['count'] + phase['hold']):
                at = phase['offset'] + local
                pos, quat, field = sample_frame(phase['start'] + min(local, phase['count']-1))
                if not (len(pos) == 3 and len(quat) == 4 and np.isfinite([*pos, *quat, field]).all()
                        and np.linalg.norm(quat) > 1e-8 and 0 < field < 179):
                    raise ValueError(f'Invalid native camera pose or FOV at frame {at}: {path}')
                q = Quaternion((quat[3], *quat[:3])).normalized()
                if previous is not None and previous.dot(q) < 0:
                    q.negate()
                positions[at], rotations[at], fields[at] = list(pos), [q.x, q.y, q.z, q.w], float(field)
                previous = q
        clip.frame_count, clip.playback_rate = len(positions), 30.
        clip.positions, clip.rotations = {ci: positions}, {ci: rotations}
        clip.float_channels, clip.scales = {0: fields}, {}
        # The record's clipping planes are fixed for the complete shot.
        raw = patch_camera_lenses(template_raw, {0: {'fov_degrees': fields[0], 'near': min(p[0] for p in planes),
                                                      'far': max(p[1] for p in planes)}})
        internal_name = 'Cam_' + job['stem'] + '_' + side.upper()
        raw, renamed = rename_camera_bindings(raw, nlst, internal_name)
        out = output_dir/name
        out.with_suffix('.geom').write_bytes(raw)
        out.with_suffix('.nlst').write_bytes(renamed.encode('ascii'))
        clip.to_file(str(out.with_suffix('.anim')))
        restored = read_anim(out.with_suffix('.anim'))
        if restored.frame_count != clip.frame_count or restored.float_channel_count != 1:
            raise ValueError('Native camera readback timing or FOV binding differs')
        peak_position = peak_angle = peak_fov = 0.
        for frame, position in positions.items():
            peak_position = max(peak_position, (Vector(restored.positions[ci][frame])-Vector(position)).length)
            dot = abs(float(np.dot(restored.rotations[ci][frame], rotations[frame])))
            peak_angle = max(peak_angle, 2*math.acos(min(1., dot / np.linalg.norm(restored.rotations[ci][frame]) /
                                                         np.linalg.norm(rotations[frame]))))
            peak_fov = max(peak_fov, abs(restored.float_channels[0][frame]-fields[frame]))
        if peak_position > .001 or peak_angle > .002 or peak_fov > .001:
            raise ValueError(f'Native camera readback exceeded tolerance: {peak_position}, {peak_angle}, {peak_fov}')
        reports.append(dict(stem=name, frames=clip.frame_count, source_files=inputs,
                            template=str(template), template_sha256=_digest(template),
                            max_position_error=peak_position, max_rotation_error_radians=peak_angle,
                            max_fov_error_degrees=peak_fov))
    return reports

