"""Assemble a native presentation from authored, frame-indexed actor and camera phases."""
from bisect import bisect_left
from contextlib import contextmanager
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import uuid

import numpy as np

from .anim import AnimFileBinaryHundredLine, AnimFileHundredLine


def read_anim(path):
    binary = AnimFileBinaryHundredLine()
    binary.read(str(path))
    return AnimFileHundredLine.from_binary(binary)


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


@contextmanager
def _pending_folder(destination):
    # Python's private temporary directories remove inherited Windows read
    # access. A published Blender export must remain readable by its owner.
    folder = destination.parent/('.dsts-sequence-'+uuid.uuid4().hex)
    folder.mkdir(mode=0o777)
    try:
        yield folder
    finally:
        shutil.rmtree(folder)


def _sample(keys, ordered, at, rotation):
    if at in keys:
        return keys[at]
    index = bisect_left(ordered, at)
    if index in (0, len(ordered)):
        return keys[ordered[0 if not index else -1]]
    lo, hi = ordered[index-1:index+1]
    a, b = np.asarray(keys[lo]), np.asarray(keys[hi])
    t = (at-lo)/(hi-lo)
    if rotation:
        dot = float(np.dot(a, b))
        if dot < 0:
            b, dot = -b, -dot
        if dot < 0.9995:
            angle = math.acos(min(1, dot))
            return (a*math.sin((1-t)*angle)+b*math.sin(t*angle))/math.sin(angle)
        value = (1-t)*a+t*b
        return value/np.linalg.norm(value)
    return (1-t)*a+t*b


def compose_actor(clips, phases):
    result = copy.deepcopy(clips[0])
    result.frame_count = sum(p['count']+p['hold'] for p in phases)
    # Missing native actor components evaluate to identity deltas on the GEOM rest.
    defaults = {'positions': [0, 0, 0], 'rotations': [0, 0, 0, 1], 'scales': [1, 1, 1]}
    for attr, default in defaults.items():
        channels = {}
        for bone in range(result.bone_count):
            values = [getattr(clip, attr).get(bone, {}) for clip in clips]
            if not any(values):
                continue
            output = {}
            for keys, p in zip(values, phases):
                if not keys:
                    keys = {0: default}
                ordered = sorted(keys)
                start, offset, last = p['start'], p['offset'], p['start']+p['count']-1
                # Hard cuts need both adjacent integer samples. Keep internal source keys sparse.
                frames = [start, *(f for f in ordered if start < f < last), last]
                for f in frames:
                    output[offset+f-start] = _sample(keys, ordered, f, attr == 'rotations')
                if p['hold']:
                    output[offset+p['count']+p['hold']-1] = output[offset+p['count']-1]
            first = output[0]
            if all(np.array_equal(first, val) for val in output.values()):
                output = {0: first}
            channels[bone] = output
        setattr(result, attr, channels)
    return result


def export_sequence(recipe, destination):
    recipe = Path(recipe).resolve()
    job = json.loads(recipe.read_bytes())
    if job.get('format') != 'dsts-native-skill-sequence-v1':
        raise ValueError('Select a dsts-native-skill-sequence-v1 recipe')
    stem = job.get('stem', '')
    if not re.fullmatch(r'chr\d+_bs\d{2}', stem):
        raise ValueError('Expected a native actor special-motion stem, such as chr050_bs01')
    if not isinstance(job.get('phases'), list) or not job['phases']:
        raise ValueError('Provide at least one actor phase')
    destination = Path(destination).resolve()
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError('Choose a new output folder with an existing parent')
    clips, phases, offset = [], [], 0
    for entry in job['phases']:
        path = (recipe.parent/entry['motion']).resolve()
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if entry.get('sha256', digest) != digest:
            raise ValueError(f'Source motion changed: {path}')
        clip = read_anim(path)
        if not 0 < clip.frame_count <= 65535 or not math.isfinite(clip.playback_rate) or clip.playback_rate <= 0:
            raise ValueError(f'Invalid source timing: {path}')
        if clip.float_channel_count:
            raise ValueError('Actor sequences with material float channels require a matching float-channel plan')
        if clips and (clip.bone_count != clips[0].bone_count or clip.playback_rate != clips[0].playback_rate or
                      list(clip.bone_blend_factors) != list(clips[0].bone_blend_factors)):
            raise ValueError('All actor phases must use the same rig, masks and sample rate')
        start = _integer(entry.get('start', 0), 'start')
        count = _integer(entry.get('count', clip.frame_count-start), 'count', 1)
        hold = _integer(entry.get('hold', 0), 'hold')
        if start+count > clip.frame_count or offset+count+hold > 65535:
            raise ValueError(f'Source or native frame range exceeded: {path.name}')
        if 'offset' in entry and _integer(entry['offset'], 'offset') != offset:
            raise ValueError(f'Non-contiguous actor phase after frame {offset-1}')
        phases.append(dict(entry, motion=str(path), sha256=digest, start=start, count=count, hold=hold, offset=offset))
        clips.append(clip)
        offset += count+hold
    cameras = [bool(p.get('camera')) for p in phases]
    if any(cameras) and not all(cameras):
        raise ValueError('Select a camera for every phase or omit all cameras')
    with _pending_folder(destination) as temp:
        out = temp/'output'
        out.mkdir()
        motion = out/(stem+'.anim')
        first = phases[0]
        if len(phases) == 1 and first['start'] == 0 and first['count'] == clips[0].frame_count and not first['hold']:
            motion.write_bytes(Path(first['motion']).read_bytes())
        else:
            compose_actor(clips, phases).to_file(str(motion))
        verified = read_anim(motion)
        if (verified.frame_count, verified.bone_count, verified.playback_rate) != (
                offset, clips[0].bone_count, clips[0].playback_rate):
            raise ValueError('Native motion readback differs from the authored sequence')
        camera_report = []
        if all(cameras):
            from .nc_sequence_camera import build_cameras
            camera_report = build_cameras(job, phases, out, recipe.parent)
        from .nc_sequence_effect import build_effect
        effect_report = build_effect(job, phases, out, recipe.parent)
        report = {'stem': stem, 'fps': verified.playback_rate, 'frames': verified.frame_count,
                  'bones': verified.bone_count, 'phases': phases, 'cameras': camera_report, 'effect': effect_report,
                  'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()}}
        (out/'sequence-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf8')
        out.rename(destination)
    return report
