"""Bake a captured NC socket camera and retain cinematic cue dependencies.

Requires numpy. This exports Blender preview data, not Time Stranger particles.
Only unambiguous animated sockets and supported glTF TRS channels are admitted.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import wave
import copy

import numpy as np

MAX_PREVIEW_FRAMES = 216000


def _integer(value, label, minimum=0, maximum=MAX_PREVIEW_FRAMES):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be an integer in {minimum}..{maximum}')
    return value


def _finite_vector(value, width, label):
    if not isinstance(value, (list, tuple)) or len(value) != width:
        raise ValueError(f'{label} must contain {width} numbers')
    result = np.asarray(value, dtype=float)
    if not np.all(np.isfinite(result)):
        raise ValueError(f'{label} contains a nonfinite number')
    return result


def _validate_phase(phase):
    # Projectile math in the extracted timelines serializes whole frames as e.g. 41.0.
    # Canonicalize scheduling fields only. glTF indices still require actual integers.
    def frame(value, label):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return _integer(value, label)

    for key in ('totalFrames', 'previewFrames', 'actorDelayFrames'):
        if key in phase:
            phase[key] = frame(phase[key], key)
    if not isinstance(phase.get('actorClip'), str) or not phase['actorClip']:
        raise ValueError('Phase actorClip is missing')
    if not isinstance(phase.get('kind'), str) or not phase['kind']:
        raise ValueError('Phase kind is missing')
    for collection in ('events', 'sounds'):
        records = phase.get(collection, [])
        if not isinstance(records, list):
            raise ValueError(f'Phase {collection} must be a list')
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f'Phase {collection} record must be an object')
            record['frame'] = frame(record.get('frame'), f'{collection} frame')
            if 'lifeFrames' in record:
                record['lifeFrames'] = frame(record['lifeFrames'], 'Effect lifeFrames')
                _integer(record['frame']+record['lifeFrames'], 'Effect end frame')
            if collection == 'events':
                anchor = record.get('anchor')
                if not isinstance(anchor, dict) or anchor.get('side') not in ('caster', 'target', 'scene') or not isinstance(anchor.get('name'), str) or not anchor['name']:
                    raise ValueError('Effect anchor must name a caster, target or scene location')
                if anchor['side'] == 'scene' and anchor.get('origin') not in ('caster', 'target'):
                    raise ValueError('Scene location requires its captured battle origin')
                if 'battleOffset' in anchor:
                    _finite_vector(anchor['battleOffset'], 3, 'Battle offset')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _reference_path(path, directory):
    """Use portable relative paths when possible, absolute paths across drives."""
    try:
        return os.path.relpath(path, directory).replace('\\', '/')
    except ValueError:
        # Windows cannot express D: source relative to a C: temporary manifest.
        return Path(path).resolve().as_posix()


def quaternion_matrix(q):
    q = np.asarray(q, dtype=float)
    q /= np.linalg.norm(q)
    x, y, z, w = q
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def matrix_quaternion(m):
    # Symmetric eigenproblem avoids a near-180-degree trace singularity.
    r = np.asarray(m, dtype=float)
    r = r / np.linalg.norm(r, axis=0)
    xx, xy, xz = r[0]
    yx, yy, yz = r[1]
    zx, zy, zz = r[2]
    k = np.array([[xx-yy-zz, xy+yx, xz+zx, zy-yz],
                  [xy+yx, yy-xx-zz, yz+zy, xz-zx],
                  [xz+zx, yz+zy, zz-xx-yy, yx-xy],
                  [zy-yz, xz-zx, yx-xy, xx+yy+zz]]) / 3
    _, v = np.linalg.eigh(k)
    q = v[:, -1]
    return q if q[3] >= 0 else -q


def trs(position, rotation, scale):
    m = np.eye(4)
    m[:3, :3] = quaternion_matrix(rotation) @ np.diag(scale)
    m[:3, 3] = position
    return m


class Glb:
    def __init__(self, path, clip):
        raw = Path(path).read_bytes()
        if len(raw) < 20:
            raise ValueError('Truncated glTF binary header')
        magic, version, size = struct.unpack_from('<III', raw)
        if magic != 0x46546C67 or version != 2 or size != len(raw):
            raise ValueError('Expected complete glTF 2 binary')
        chunks, offset = {}, 12
        while offset < size:
            if offset+8 > size:
                raise ValueError('Truncated glTF chunk header')
            length, kind = struct.unpack_from('<II', raw, offset)
            if length % 4 or offset+8+length > size or kind in chunks:
                raise ValueError('Invalid, duplicate or out-of-bounds glTF chunk')
            chunks[kind] = raw[offset+8:offset+8+length]
            offset += length+8
        if 0x4E4F534A not in chunks or 0x004E4942 not in chunks:
            raise ValueError('glTF binary requires JSON and BIN chunks')
        self.doc = json.loads(chunks[0x4E4F534A])
        self.binary = chunks[0x004E4942]
        nodes = self.doc.get('nodes', [])
        if not isinstance(nodes, list) or not nodes or len(nodes) > 100000:
            raise ValueError('Invalid glTF node list')
        self.parents = {}
        for index, node in enumerate(nodes):
            for child in node.get('children', []):
                _integer(child, 'glTF child node', maximum=len(nodes)-1)
                if child in self.parents or child == index:
                    raise ValueError('glTF node has multiple parents or a self-parent')
                self.parents[child] = index
            for field, width in (('translation', 3), ('rotation', 4), ('scale', 3), ('matrix', 16)):
                if field in node:
                    value = _finite_vector(node[field], width, f'glTF node {field}')
                    if field == 'rotation' and np.linalg.norm(value) < 1e-12:
                        raise ValueError('Zero-length glTF rotation')
            if 'matrix' in node and any(field in node for field in ('translation', 'rotation', 'scale')):
                raise ValueError('glTF node mixes matrix and TRS')
        for index in range(len(nodes)):
            seen = set()
            while index in self.parents:
                if index in seen or len(seen) > 512:
                    raise ValueError('Cyclic or excessively deep glTF hierarchy')
                seen.add(index)
                index = self.parents[index]
        matches = [a for a in self.doc['animations'] if a.get('name') == clip]
        if len(matches) != 1:
            raise ValueError(f'Expected one animation {clip!r}, found {len(matches)}')
        self.channels = {}
        for channel in matches[0]['channels']:
            sampler_index = _integer(channel['sampler'], 'glTF sampler', maximum=len(matches[0]['samplers'])-1)
            sampler = matches[0]['samplers'][sampler_index]
            key = channel['target']['node'], channel['target']['path']
            _integer(key[0], 'glTF animation target', maximum=len(nodes)-1)
            if key in self.channels or key[1] not in ('translation', 'rotation', 'scale'):
                raise ValueError('Duplicate or unsupported glTF animation channel')
            if 'matrix' in nodes[key[0]]:
                raise ValueError('Animated matrix nodes are invalid glTF')
            input_values, output_values = self.accessor(sampler['input']), self.accessor(sampler['output'])
            interpolation = sampler.get('interpolation', 'LINEAR')
            if interpolation not in ('STEP', 'LINEAR', 'CUBICSPLINE'):
                raise ValueError('Unsupported glTF interpolation')
            if input_values.shape[1] != 1 or output_values.shape[1] != (4 if key[1] == 'rotation' else 3):
                raise ValueError('glTF animation accessor type mismatch')
            times = input_values[:, 0]
            if not len(times) or times[0] < 0 or np.any(np.diff(times) <= 0) or times[-1]*30 > MAX_PREVIEW_FRAMES:
                raise ValueError('glTF animation times must be nonnegative and strictly increasing within preview bounds')
            if len(output_values) != len(times)*(3 if interpolation == 'CUBICSPLINE' else 1):
                raise ValueError('glTF animation input/output counts differ')
            values = output_values[1::3] if interpolation == 'CUBICSPLINE' else output_values
            if key[1] == 'rotation' and np.any(np.linalg.norm(values, axis=1) < 1e-12):
                raise ValueError('Zero-length glTF animation rotation')
            self.channels[key] = (times, output_values, interpolation)
        self.duration_seconds = max((float(times[-1]) for times, _, _ in self.channels.values() if len(times)), default=0.0)

    def accessor(self, index):
        _integer(index, 'glTF accessor', maximum=len(self.doc.get('accessors', []))-1)
        a = self.doc['accessors'][index]
        if a['componentType'] != 5126 or 'sparse' in a:
            raise ValueError('Only dense float32 animation accessors are supported')
        view_index = _integer(a['bufferView'], 'glTF bufferView', maximum=len(self.doc.get('bufferViews', []))-1)
        view = self.doc['bufferViews'][view_index]
        if view.get('buffer', 0) != 0:
            raise ValueError('External glTF buffers are not supported')
        if a['type'] not in ('SCALAR', 'VEC3', 'VEC4'):
            raise ValueError('Unsupported glTF animation accessor shape')
        width = {'SCALAR': 1, 'VEC3': 3, 'VEC4': 4}[a['type']]
        count = _integer(a['count'], 'glTF accessor count', minimum=1, maximum=10000000)
        start = _integer(view.get('byteOffset', 0), 'glTF bufferView offset', maximum=len(self.binary))
        length = _integer(view['byteLength'], 'glTF bufferView length', maximum=len(self.binary))
        local = _integer(a.get('byteOffset', 0), 'glTF accessor offset', maximum=length)
        stride = _integer(view.get('byteStride', width*4), 'glTF accessor stride', minimum=width*4, maximum=252)
        if start+length > len(self.binary) or start % 4 or local % 4 or stride % 4 or local+(count-1)*stride+width*4 > length:
            raise ValueError('Out-of-bounds or unaligned glTF accessor')
        result = np.ndarray((count, width), '<f4', self.binary, start+local, strides=(stride, 4)).copy()
        if not np.all(np.isfinite(result)):
            raise ValueError('Nonfinite glTF animation samples')
        return result

    def node_path(self, index):
        path = [index]
        while path[-1] in self.parents:
            path.append(self.parents[path[-1]])
        return tuple(reversed(path))

    def socket(self, name):
        nodes = self.doc['nodes']
        paths = [self.node_path(i) for i, node in enumerate(nodes) if node.get('name') == name]
        if not paths:
            raise ValueError(f'Missing socket {name!r}')
        if len(paths) == 1:
            return paths[0][-1]

        # An NC GLB can include several complete prefab hierarchies. Identify
        # the ones that drive a skinned mesh in this particular animation.
        animated = {index for index, _ in self.channels}
        actor_roots = set()
        skins = self.doc.get('skins', [])
        for skin_index in {node['skin'] for node in nodes if 'mesh' in node and 'skin' in node}:
            _integer(skin_index, 'glTF mesh skin', maximum=len(skins)-1)
            for joint in skins[skin_index]['joints']:
                _integer(joint, 'glTF skin joint', maximum=len(nodes)-1)
                path = self.node_path(joint)
                if animated.intersection(path):
                    actor_roots.add(path[0])
        paths = [path for path in paths if path[0] in actor_roots] or paths
        # Direct socket keys take precedence in the owning hierarchy. Static
        # child sockets can also inherit all their motion from a keyed parent.
        keyed = [path for path in paths if path[-1] in animated]
        # A common animated ancestor moves every remaining copy equally.
        shared = set(paths[0]).intersection(*paths[1:])
        branch_keys = animated - shared
        inherited = [path for path in paths if branch_keys.intersection(path)]
        matches = keyed or inherited or paths
        if len(matches) != 1:
            descriptions = ['/'.join(f"{nodes[i].get('name', '')}[{i}]" for i in p) for p in matches]
            raise ValueError(f'Ambiguous socket {name!r}: ' + ', '.join(descriptions))
        return matches[0][-1]

    def sample(self, index, path, time, default):
        channel = self.channels.get((index, path))
        if channel is None:
            return np.array(default, dtype=float)
        times, values, mode = channel
        lo = max(0, min(len(times)-1, bisect.bisect_right(times, time)-1))
        hi = min(lo+1, len(times)-1)
        dt = float(times[hi]-times[lo])
        t = max(0, min(1, (time-float(times[lo]))/dt)) if dt else 0
        if mode == 'STEP':
            result = values[lo]
        elif mode == 'CUBICSPLINE':
            a, b = values[3*lo+1], values[3*hi+1]
            result = ((2*t**3-3*t*t+1)*a + (t**3-2*t*t+t)*dt*values[3*lo+2]
                      + (-2*t**3+3*t*t)*b + (t**3-t*t)*dt*values[3*hi])
        elif mode == 'LINEAR':
            a, b = values[lo].astype(float), values[hi].astype(float)
            if path == 'rotation':
                dot = float(np.dot(a, b))
                if dot < 0:
                    b, dot = -b, -dot
                angle = math.acos(min(1, dot))
                result = ((math.sin((1-t)*angle)*a + math.sin(t*angle)*b)/math.sin(angle)
                          if angle > 1e-6 else (1-t)*a+t*b)
            else:
                result = (1-t)*a+t*b
        else:
            raise ValueError(f'Unsupported interpolation: {mode}')
        return result / np.linalg.norm(result) if path == 'rotation' else result

    def world(self, index, time):
        n = self.doc['nodes'][index]
        if 'matrix' in n:
            if any((index, p) in self.channels for p in ('translation', 'rotation', 'scale')):
                raise ValueError('Animated matrix nodes are invalid glTF')
            local = np.array(n['matrix']).reshape(4, 4).T
        else:
            local = trs(self.sample(index, 'translation', time, n.get('translation', [0, 0, 0])),
                        self.sample(index, 'rotation', time, n.get('rotation', [0, 0, 0, 1])),
                        self.sample(index, 'scale', time, n.get('scale', [1, 1, 1])))
        return self.world(self.parents[index], time) @ local if index in self.parents else local


def _model_dir(path):
    path = Path(path).resolve()
    if path.is_file():
        path = path.parent.parent if path.parent.name == 'model' else path.parent
    if not (path/'model/model.glb').is_file():
        raise ValueError(f'Expected an extracted model directory with model/model.glb: {path}')
    return path


def catalog_model(model_dir, role=None):
    """Return stable source phase choices for one extracted model, without guessing clips.

    Source role variants remain selectable. The primary info.json ID is the
    default; otherwise the numeric-first source role is used explicitly.
    """
    model = _model_dir(model_dir)
    library = model.parent.parent
    timeline = library/'MANIFEST/skill_timelines.json'
    data = json.loads(timeline.read_text(encoding='utf8'))
    info_path = model/'info.json'
    info = json.loads(info_path.read_text(encoding='utf8')) if info_path.is_file() else {}
    code = info.get('code') or model.name.rsplit('_', 1)[-1]
    available = sorted((key for key, value in data.items() if value.get('code') == code),
                       key=lambda key: (not str(key).isdigit(), int(key) if str(key).isdigit() else str(key)))
    if not available:
        raise ValueError(f'No captured skill timeline for model {code}')
    primary = next((str(identity) for identity in info.get('ids', []) if str(identity) in available), available[0])
    selected = str(role) if role is not None else primary
    if selected not in available:
        raise ValueError(f'Role {selected} does not belong to model {code}; available: {available}')
    phases = []
    seen_slots = set()
    for skill in data[selected].get('equippedSkills', []):
        slot = skill['slot']
        if slot in seen_slots:
            raise ValueError(f'Ambiguous equipped skill slot {slot}')
        seen_slots.add(slot)
        for index, phase in enumerate(skill.get('phases', [])):
            kind = phase['kind']
            clip = phase['actorClip']
            presentation = ('Cinematic' if kind == 'showcase' else 'Impact / held pose' if phase.get('keepActorPose')
                            else 'Cast' if kind == 'action' else kind.capitalize())
            label = f'Skill {slot} · {skill["name"]} — {presentation} · {clip}'
            if phase.get('triggered'):
                label += ' (triggered)'
            phases.append(dict(id=f'{slot}:{index}', label=label, slot=slot, skill_name=skill['name'],
                               phase_kind=kind, actor_clip=clip, role=selected, model_code=code,
                               source_phase=copy.deepcopy(phase)))
    return dict(format='nc-skill-catalog-v1', model_dir=str(model), actor_path=str(model/'model/model.glb'),
                library=str(library), model_code=code, role=selected, available_roles=available, phases=phases)


def _write_json(path, result, overwrite=False):
    """Publish a fully prepared manifest, leaving an existing output intact on failure."""
    path = Path(path)
    raw = (json.dumps(result, indent=2, ensure_ascii=False)+'\n').encode('utf8')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Hard-link creation is atomic and cannot replace an existing file.
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _camera_local_frames(node, camera):
    """Return captured 30 Hz Unity prefab transforms and vertical FOV at cue-local time."""
    track = node.get('cameraAnimation')
    if not track:
        basis = _finite_vector(node['prefabBasis'], 9, 'Camera basis').reshape(3, 3)
        position = _finite_vector(node['prefabPos'], 3, 'Camera position')
        values = np.append(np.column_stack((basis, position)).ravel(), camera['fieldOfView'])[None, :]
    else:
        if track.get('startDelaySeconds') or track.get('activationDelaySeconds') or track.get('physical'):
            raise ValueError('Delayed or physical animated prefab cameras require explicit clock/lens support')
        values = np.asarray(track['frames'], dtype=float)
        if values.ndim != 2 or values.shape[1] != 13 or not 0 < len(values) <= MAX_PREVIEW_FRAMES:
            raise ValueError('Expected 3x4 prefab camera samples with vertical FOV')
        active = track.get('activeFrames', [1] * len(values))
        if len(active) != len(values) or not all(flag == 1 for flag in active):
            raise ValueError('Camera activation cuts require a second shot')
        if track.get('loop') and (not math.isfinite(track['duration']) or track['duration'] <= 0):
            raise ValueError('Invalid source camera loop duration')
    if not np.isfinite(values).all() or np.any((values[:, 12] <= 0) | (values[:, 12] >= 179)):
        raise ValueError('Invalid camera samples or field of view')
    return values


def export_preview(model_dir, phase_entry, output_path, *, overwrite=False):
    """Export one captured phase, with camera optional and source clocks unmodified.

    ``duration_frames`` is the inspection horizon, not the source scheduler's
    ``totalFrames``. A cast can schedule hits after one frame while its actor
    and effect continue. Those independent values are retained separately.
    """
    model, output = _model_dir(model_dir), Path(output_path).resolve()
    catalog = catalog_model(model, phase_entry.get('role'))
    choices = [item for item in catalog['phases'] if item['id'] == phase_entry['id']]
    if len(choices) != 1 or choices[0] != phase_entry:
        raise ValueError('Phase selection is stale or edited; select it again from the source catalog')
    phase = copy.deepcopy(phase_entry['source_phase'])
    _validate_phase(phase)
    library, code = Path(catalog['library']), catalog['model_code']
    timeline = library/'MANIFEST/skill_timelines.json'
    glb_path = model/'model/model.glb'
    glb = Glb(glb_path, phase['actorClip'])
    fps = 30
    actor_frames = int(round(glb.duration_seconds*fps))
    delay = phase.get('actorDelayFrames', 0)
    if not isinstance(delay, int) or delay < 0:
        raise ValueError('Actor delay must be a nonnegative source frame count')
    sources = {timeline, glb_path}
    relative = lambda path: _reference_path(path, output.parent)
    effects, candidates, unresolved = [], [], []
    horizon = max(1, actor_frames+delay, phase.get('totalFrames', 0), phase.get('previewFrames', 0))
    for index, cue in enumerate(phase.get('events', [])):
        owner, slot = cue.get('owner', code), cue['slot']
        if any(not value or Path(value).name != value or '/' in value or '\\' in value for value in (owner, slot)):
            raise ValueError('Source effect owner and slot must be plain names')
        path = library/'Vfx_assets'/owner/(slot+'.json')
        source_event = copy.deepcopy(cue)
        effects.append(dict(frame=cue['frame'], path=relative(path), prefab=cue['prefab'],
                            anchor=cue.get('anchor'), life_frames=cue.get('lifeFrames'), source_event=source_event,
                            source_order=index, status='source-prefab-reference'))
        horizon = max(horizon, cue['frame']+cue.get('lifeFrames', 0))
        if not path.is_file():
            unresolved.append(dict(kind='effect', cue=source_event, reason='Captured effect JSON is missing'))
            continue
        sources.add(path)
        doc = json.loads(path.read_text('utf8'))
        for node in doc.get('effect_nodes', []):
            if node.get('prefab', '').lower() != cue['prefab'].lower() or not node.get('cameras'):
                continue
            for camera in node['cameras']:
                if not camera.get('enabled', True) or node.get('active') is False:
                    continue
                if camera.get('orthographic'):
                    raise ValueError('Orthographic source cameras require a supported projection')
                if cue.get('anchor', {}).get('side') != 'caster':
                    raise ValueError('A target-owned source camera requires explicit target motion')
                candidates.append((camera.get('depth', 0), cue, node, camera))
    audio = []
    for sound in phase.get('sounds', []):
        key = sound['key']
        if not key or Path(key).name != key or any(char in key for char in '\\/*?[]'):
            raise ValueError('Audio key must be a plain source name')
        matches = sorted((model/'audio').glob(key+'__*.wav'))
        if len(matches) != 1:
            unresolved.append(dict(kind='audio', cue=copy.deepcopy(sound), reason='No unique exported WAV'))
            continue
        with wave.open(str(matches[0]), 'rb') as wav:
            sound_frames = int(math.ceil(wav.getnframes()/wav.getframerate()*fps))
        horizon = max(horizon, sound['frame']+sound_frames)
        sources.add(matches[0])
        audio.append(dict(frame=sound['frame'], path=relative(matches[0]), volume=1, source_key=key,
                          duration_frames=sound_frames, source_event=copy.deepcopy(sound)))
    # Display disposal bounds its own preview; ordinary phase lifetimes stay separate.
    if phase['kind'] == 'showcase' and phase.get('totalFrames'):
        horizon = phase['totalFrames']
    _integer(horizon, 'Preview duration', minimum=1)
    camera_output = None
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            raise ValueError('Multiple equal-depth cameras require explicit selection')
        _, cue, node, camera = candidates[0]
        local_frames = _camera_local_frames(node, camera)
        track = node.get('cameraAnimation') or {}
        optical_values = _finite_vector([camera['fieldOfView'], camera['nearClip'], camera['farClip']], 3, 'Camera optics')
        if not 0 < optical_values[0] < 180 or not 0 < optical_values[1] < optical_values[2]:
            raise ValueError('Invalid source camera optics')
        socket = glb.socket(cue['anchor']['name'])
        local = np.eye(4)
        reflection = np.diag([-1., 1., 1., 1.])
        samples = []
        for frame in range(cue['frame'], horizon+1):
            elapsed = frame - cue['frame']
            if track.get('loop'):
                elapsed = int((elapsed / fps % track['duration']) * fps + 1e-5)
            row = local_frames[min(elapsed, len(local_frames)-1)]
            local[:3, :] = row[:12].reshape(3, 4)
            world = reflection @ glb.world(socket, max(0, frame-delay)/fps) @ reflection @ local
            samples.append(dict(frame=frame, position=world[:3, 3].round(8).tolist(),
                                rotation=matrix_quaternion(world[:3, :3]).round(9).tolist(), fov_degrees=float(row[12])))
        camera_output = dict(coordinate_system='unity-left-handed-y-up', socket=cue['anchor']['name'],
                             socket_node_index=socket, socket_node_path=list(glb.node_path(socket)),
                             near=camera['nearClip'], far=camera['farClip'], samples=samples)
    result = dict(format='dsts-cinematic-preview-v2', fps=fps, model_code=code, role=catalog['role'],
                  phase_id=phase_entry['id'], phase_kind=phase['kind'], source_phase=phase,
                  skill_slot=phase_entry['slot'], skill_name=phase_entry['skill_name'],
                  actor_clip=phase['actorClip'], actor_path=relative(glb_path), actor_duration_frames=actor_frames,
                  actor_delay_frames=delay, actor_keep_pose=bool(phase.get('keepActorPose')),
                  duration_frames=horizon, frame_origin=0, camera=camera_output, audio=audio, effects=effects,
                  unresolved=unresolved, limitations=[
                      'Preview only: effect source references do not establish native shader/particle parity.',
                      'One phase is inspected independently; concurrent gameplay/cinematic clocks are not concatenated.',
                      'Target sockets require an explicit target actor; no caster socket is substituted.',
                      'Held-pose phases require prior actor state; the stand clip is a source label, not a replacement attack.',
                      'No native Time Stranger package is generated by this manifest.'],
                  sources=[dict(path=relative(path), sha256=digest(path)) for path in sorted(sources)])
    _write_json(output, result, overwrite=overwrite)
    return result


def export(library, role, slot, output):
    library, output = Path(library).resolve(), Path(output).resolve()
    timeline_path = library/'MANIFEST/skill_timelines.json'
    role_data = json.loads(timeline_path.read_text('utf8'))[str(role)]
    code = role_data['code']
    model_dirs = [p for p in (library/'Digimon_Library').iterdir() if p.name.lower().endswith('_'+code.lower())]
    if len(model_dirs) != 1:
        raise ValueError(f'Expected unique model directory for {code}')
    model = model_dirs[0]
    skills = [s for s in role_data['equippedSkills'] if s['slot'] == slot]
    if len(skills) != 1:
        raise ValueError('Select an unambiguous equipped skill')
    phases = [p for p in skills[0]['phases'] if p['kind'] == 'showcase']
    if len(phases) != 1:
        raise ValueError('Expected exactly one captured cinematic phase')
    phase = phases[0]
    glb_path = model/'model/model.glb'
    glb = Glb(glb_path, phase['actorClip'])
    sources = {timeline_path, glb_path}
    camera_candidates, effects = [], []
    relative = lambda p: _reference_path(p, output.parent)
    for cue in phase['events']:
        path = library/'Vfx_assets'/cue.get('owner', code)/(cue['slot']+'.json')
        sources.add(path)
        doc = json.loads(path.read_text('utf8'))
        effects.append(dict(frame=cue['frame'], path=relative(path), prefab=cue['prefab'],
                            anchor=cue.get('anchor'), life_frames=cue.get('lifeFrames'),
                            status='source-prefab-reference', description='Unity prefab; native TS conversion unresolved'))
        for n in doc.get('effect_nodes', []):
            if n.get('prefab', '').lower() != cue['prefab'].lower() or not n.get('cameras'):
                continue
            c = n['cameras'][0]
            if not c.get('enabled', True) or c.get('orthographic') or n.get('active') is False:
                continue
            if n.get('cameraAnimation'):
                raise ValueError('This exporter currently admits static socket-local prefab cameras only')
            camera_candidates.append((c.get('depth', 0), cue, n, c))
    if not camera_candidates:
        raise ValueError('No supported captured camera')
    camera_candidates.sort(key=lambda x: x[0], reverse=True)
    if len(camera_candidates) > 1 and camera_candidates[0][0] == camera_candidates[1][0]:
        raise ValueError('Multiple equal-depth cameras require explicit selection')
    _, cue, n, camera = camera_candidates[0]
    socket = glb.socket(cue['anchor']['name'])
    local = np.eye(4)
    local[:3, :3] = np.array(n['prefabBasis']).reshape(3, 3)
    local[:3, 3] = n['prefabPos']
    reflection = np.diag([-1., 1., 1., 1.])
    frame_count = phase.get('totalFrames') or phase.get('previewFrames')
    if not isinstance(frame_count, int) or frame_count <= 0:
        raise ValueError('Cinematic phase has no explicit positive frame count')
    samples = []
    for frame in range(cue['frame'], frame_count+1):
        # GLB exports reflect X. Undo that basis before composing Unity camera-local data.
        world = reflection @ glb.world(socket, frame/30) @ reflection @ local
        q = matrix_quaternion(world[:3, :3])
        samples.append(dict(frame=frame, position=world[:3, 3].round(8).tolist(),
                            rotation=q.round(9).tolist(), fov_degrees=camera['fieldOfView']))
    audio, unresolved = [], []
    for sound in phase.get('sounds', []):
        matches = list((model/'audio').glob(sound['key']+'__*.wav'))
        if len(matches) != 1:
            unresolved.append(dict(kind='audio', cue=sound, reason='No unique exported WAV'))
            continue
        sources.add(matches[0])
        audio.append(dict(frame=sound['frame'], path=relative(matches[0]), volume=1, source_key=sound['key']))
    if not phase.get('sounds'):
        unresolved.append(dict(kind='audio', reason='Timeline does not yet contain cinematic sound cues'))
    result = dict(format='dsts-cinematic-preview-v1', fps=30, model_code=code, role=str(role),
                  skill_slot=slot, actor_clip=phase['actorClip'], actor_path=relative(glb_path),
                  duration_frames=frame_count, frame_origin=0,
                  camera=dict(coordinate_system='unity-left-handed-y-up', socket=cue['anchor']['name'],
                              near=camera['nearClip'], far=camera['farClip'], samples=samples),
                  audio=audio, effects=effects, unresolved=unresolved,
                  limitations=['Preview only: Unity particles/shaders are retained as source references.',
                               'Camera follows source actor GLB coordinates; target rig/scale edits require rebaking.',
                               'No native Time Stranger effect, sound-bank or camera package is generated.'],
                  sources=[dict(path=relative(p), sha256=digest(p)) for p in sorted(sources)])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf8')
    print(json.dumps(dict(path=str(output), camera_samples=len(samples), audio_cues=len(audio),
                          effect_cues=len(effects), unresolved=len(unresolved))))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--role', required=True)
    parser.add_argument('--slot', type=int, default=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.library, args.role, args.slot, args.output)
