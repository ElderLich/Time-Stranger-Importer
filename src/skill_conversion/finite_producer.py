"""Author finite current-owner Model4 components from reviewed source inputs.

Source sampling and material translation remain explicit pinned inputs. This
producer authors the native graph, particle motion/clock streams and wrappers;
it neither invokes research scripts nor guesses an emission schedule.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import uuid

from .compiler import _check_pin, _destination, _json, _require, _sha, _source_snapshot, pin_file
from .mesh_profile import PROFILE, prepare_local_mesh_samples, validate_local_mesh
try:
    from ..spark import SparkDocument, Key
    from ..model_package import validate_matrix_alignment, validate_runtime_skinning, _validate_shader, _validate_texture
except ImportError:
    from spark import SparkDocument, Key
    from model_package import validate_matrix_alignment, validate_runtime_skinning, _validate_shader, _validate_texture

JOB_FORMAT = 'nc-finite-local-mesh-job-v1'
CARRIER_FORMAT = 'nc-finite-local-mesh-carriers-v1'


def material_fingerprint(node):
    """Stable fingerprint of the exact captured single material record."""
    _require(len(node.get('material_infos', [])) == 1, 'Expected one captured material')
    return _sha(_json(node['material_infos'][0]))


def node_fingerprint(node):
    return _sha(_json(node))


def _bindings(raw):
    _require(len(raw) >= 176 and struct.unpack_from('<I', raw)[0] == 316, 'Carrier requires GEOM316')
    meshes, materials, lights, cameras = struct.unpack_from('<4H', raw, 4)
    _require(meshes > 0 and materials == 1 and lights == cameras == 0, 'Carrier requires mesh geometry and exactly one material')
    skeleton = struct.unpack_from('<Q', raw, 152)[0]
    _require(skeleton + 64 <= len(raw) and raw[skeleton:skeleton+4] == b'60SE', 'Invalid carrier skeleton')
    bones, count = struct.unpack_from('<HH', raw, skeleton+16)
    _require(bones == 1, 'Carrier requires exactly one bone')
    tables = []
    for field, kind in ((36, 'I'), (40, 'I'), (44, 'B')):
        start = skeleton + field + struct.unpack_from('<I', raw, skeleton+field)[0]
        size = struct.calcsize(kind) * count
        _require(start + size <= len(raw), 'Carrier float-binding table exceeds file')
        tables.append(struct.unpack_from('<'+kind*count, raw, start))
    indices, names, flags = tables
    plain = (301 << 4 | 2, 178 << 4, 178 << 4 | 1, 178 << 4 | 2)
    _require(indices in (plain, (302 << 4,) + plain) and all(flag == 8 for flag in flags),
             'Carrier requires optional Custom2.w, sheet301.z and world178.xyz bindings in that order')
    _require(len(set(names)) == 1, 'Carrier bindings must target one material')
    validate_matrix_alignment(raw, 1)
    return len(indices) == 5


def _template(raw, identity, kind):
    document = SparkDocument.from_bytes(raw)
    _require(type(identity) is int and identity in document._node_chunks, 'Selected donor node does not exist')
    chunk = document._node_chunks[identity]
    payload = document._payload(chunk)
    _require(struct.unpack_from('<H', payload)[0] == kind and len(payload) >= (656 if kind == 4 else 552),
             'Selected donor node has the wrong native kind or size')
    _require(raw[17] == 0, 'Only the established effect-local donor clock is supported')
    return {'header': raw[:24], 'payload': bytes(payload),
            'prefix': raw[chunk.offset+16:chunk.start], 'suffix': raw[chunk.end:chunk.offset+chunk.size]}


def _constant(curve):
    _require(isinstance(curve, dict), 'Invalid captured custom-data curve')
    if curve.get('minMaxState', 0) == 0:
        return float(curve.get('scalar', 0))
    keys = curve.get('maxCurve', {}).get('m_Curve', [])
    _require(curve.get('minMaxState') == 1 and keys and all(
        key['value'] == keys[0]['value'] and key.get('inSlope', 0) == key.get('outSlope', 0) == 0 for key in keys),
        'Animated Custom1/Custom2.xyz requires another material profile')
    return float(curve.get('scalar', 1)) * float(keys[0]['value'])


def _inspect(job):
    inputs, cache, selected, omitted, errors = {}, {}, [], [], []
    dependency_claims = {}
    source = None
    report = {'format': 'nc-finite-local-mesh-plan-v1', 'profile': PROFILE, 'errors': errors,
              'blockers': ['Source sampling is a deterministic finite preview, not Unity seed/runtime equivalence',
                           'Material carriers require prior reviewed source conversion',
                           'External owner sockets, event routes and game acceptance remain separate'],
              'selected_nodes': selected, 'omitted_nodes': omitted, 'buildable': False, 'installable': False}
    report['validation_boundary'] = 'Pure inspection validates pinned source/profile inputs; generation loads and pins the ABI backend and validates full native materials/geometry'

    def read(pin, base=None):
        path = Path(pin['path'])
        if not path.is_absolute():
            _require(base is not None, 'Producer job pins must be absolute')
            path = base / path
        key = str(path.resolve()), pin['sha256'], pin['bytes']
        if key not in cache:
            resolved, raw = _check_pin({**pin, 'path': str(path)})
            cache[key] = raw
            inputs[str(resolved)] = {'path': str(resolved), 'sha256': pin['sha256'], 'bytes': pin['bytes']}
        return Path(key[0]), cache[key]

    state = {}
    try:
        _require(isinstance(job, dict) and job.get('format') == JOB_FORMAT, 'Unsupported finite producer job format')
        _json(job)
        for implementation in (Path(__file__), Path(__file__).with_name('mesh_profile.py'),
                               Path(__file__).with_name('compiler.py'), Path(__file__).parents[1] / 'spark.py',
                               Path(__file__).parents[1] / 'model_package.py'):
            read(pin_file(implementation))
        source = job['source']
        _require(_source_snapshot(source['model_path'], source['phase_id'], source['role']) == source,
                 'Producer source snapshot changed; recreate the job')
        for pin in source['files']:
            read(pin)
        order = job['event_order']
        _require(type(order) is int, 'Producer event order must be an integer')
        matches = [event for event in source['effects'] if event['event_order'] == order]
        _require(len(matches) == 1, 'Producer event is absent from captured source')
        event = matches[0]
        asset_pin = next(pin for pin in source['files'] if Path(pin['path']).resolve() == Path(event['path']).resolve())
        _, asset_raw = read(asset_pin)
        asset = json.loads(asset_raw)
        bake_path, bake_raw = read(job['bake'])
        bake = json.loads(bake_raw)
        _require(bake.get('format') == 'nc-native-particle-bake-v1' and bake.get('fps') == 30 and bake.get('local_mounted') is True,
                 'Producer requires a mounted 30 Hz source particle bake')
        _require(type(bake.get('duration_frames')) is int and 0 < bake['duration_frames'] < 32764, 'Bake duration exceeds finite native frame range')
        _require(isinstance(job.get('bake_reference_root'), str) and Path(job['bake_reference_root']).is_absolute(),
                 'Choose an explicit absolute bake reference root')
        baked_source = Path(bake['source'])
        if not baked_source.is_absolute():
            baked_source = Path(job['bake_reference_root']) / baked_source
        read({**asset_pin, 'path': str(baked_source)})
        carrier_path, carrier_raw = read(job['carriers'])
        carriers = json.loads(carrier_raw)
        _require(carriers.get('format') == CARRIER_FORMAT and carriers.get('profile') == PROFILE, 'Unsupported reviewed carrier manifest')
        _, carrier_source = read(carriers['source_asset'], carrier_path.parent)
        _, carrier_bake = read(carriers['source_bake'], carrier_path.parent)
        _require(carrier_source == asset_raw and carrier_bake == bake_raw, 'Carrier source/bake identity differs from selected event')
        prefix = job['prefix']
        _require(isinstance(prefix, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,17}', prefix), 'Choose an ASCII native prefix of at most 18 characters')
        indices = job['node_indices']
        _require(isinstance(indices, list) and indices and len(indices) == len(set(indices))
                 and all(type(index) is int and 0 <= index < len(asset['nodes']) for index in indices), 'Select distinct existing source node indices')
        donor = job['donors']
        _, group_raw = read(donor['group_sprk'])
        _, model_raw = read(donor['model_sprk'])
        _, wrapper_raw = read(donor['wrapper_geom'])
        group = _template(group_raw, donor['group_node_id'], 0)
        model = _template(model_raw, donor['model_node_id'], 4)
        _require(len(wrapper_raw) >= 176 and struct.unpack_from('<I', wrapper_raw)[0] == 316, 'Wrapper donor requires GEOM316')
        _require(isinstance(carriers.get('nodes'), list), 'Carrier node list is missing')
        for index, node in enumerate(asset['nodes']):
            if index not in indices:
                omitted.append({'node_index': index, 'node_path': node.get('path'), 'render_mode': node.get('ps', {}).get('renderMode'),
                                'reason': 'Not selected; this source system remains in normal recipe coverage'})
        native_nodes = []
        for index in indices:
            node = asset['nodes'][index]
            try:
                validate_local_mesh(node)
                _require(not any(row.get('nodeIndex') == index for row in bake.get('unresolved', []) + bake.get('skipped', [])),
                         'Selected source system is unresolved or skipped by its bake')
                baked_nodes = [row for row in bake.get('nodes', []) if row.get('index') == index]
                _require(len(baked_nodes) == 1 and baked_nodes[0].get('path') == node['path'], 'Bake node identity differs')
                rows = [row for row in carriers['nodes'] if row.get('node_index') == index]
                _require(len(rows) == 1, 'No unique reviewed carrier for selected source system')
                carrier = rows[0]
                _require(carrier.get('node_path') == node['path'] and carrier.get('source_material_sha256') == material_fingerprint(node)
                         and carrier.get('source_node_sha256') == node_fingerprint(node), 'Carrier source node/material fingerprint differs')
                _require(isinstance(carrier.get('provenance'), list) and carrier['provenance'], 'Carrier requires reviewed provenance pins')
                for proof in carrier['provenance']:
                    read(proof, carrier_path.parent)
                _, geom_raw = read(carrier['geometry'], carrier_path.parent)
                custom = _bindings(geom_raw)
                _require(custom == bool(node['material_infos'][0].get('floats', {}).get('_UseCustomData')), 'Carrier custom stream binding differs from source material')
                deps = {}
                for dependency in carrier['dependencies']:
                    name = _destination(dependency['destination'])
                    _require((name.startswith('images/') and name.endswith('.img')) or
                             (name.startswith('shaders/') and name.endswith('.shad')), 'Carriers may declare only native texture/shader dependencies')
                    _, data = read(dependency, carrier_path.parent)
                    _require(name.casefold() not in {key.casefold() for key in deps}, 'Carrier dependency destination collision')
                    claim = (name, _sha(data))
                    _require(name.casefold() not in dependency_claims or dependency_claims[name.casefold()] == claim,
                             'Selected carriers have conflicting dependency destinations')
                    dependency_claims[name.casefold()] = claim
                    (_validate_shader if name.endswith('.shad') else _validate_texture)(data, name)
                    deps[name] = data
                for dependency in carrier['dependencies']:
                    _require(all(reference in deps for reference in dependency.get('references', [])), 'Carrier dependency closure is incomplete')
                particles = []
                for particle_index, particle in enumerate(bake['particles']):
                    if particle.get('nodeIndex') != index:
                        continue
                    _require(particle.get('path') == node['path'] and particle.get('materialIndex') == 0, 'Bake particle source identity differs')
                    samples = prepare_local_mesh_samples(node, particle)
                    if custom:
                        stream = node['ps'].get('customData', {})
                        expected = [[_constant(stream.get(f'vector{s}_{axis}', {})) for axis in range(count)] for s, count in ((0, 4), (1, 3))]
                        _require(all(sample['customData'] == expected[0] and sample['customData2'][:3] == expected[1] for sample in samples),
                                 'Sampled custom streams differ from reviewed material constants')
                    _require(all(0 <= sample['frame'] <= bake['duration_frames'] for sample in samples), 'Particle sample exceeds bake horizon')
                    _require(type(particle.get('sortingOrder')) is int and -32768 <= particle['sortingOrder'] <= 32767, 'Particle sorting order exceeds native range')
                    particles.append({'index': particle_index, 'source': particle, 'samples': samples})
                _require(particles, 'Selected source system has no sampled particles; it cannot be silently omitted')
                selected.append({'node_index': index, 'node_path': node['path'], 'particles': len(particles), 'profile': PROFILE,
                                 'figure_stem': f'{prefix}_s{index:03d}', 'source_material_sha256': material_fingerprint(node)})
                native_nodes.append({'index': index, 'node': node, 'carrier': carrier, 'geom': geom_raw,
                                     'custom': custom, 'dependencies': deps, 'particles': particles})
            except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error, OverflowError) as exc:
                errors.append({'code': 'selected-source-unsupported', 'message': str(exc), 'node_index': index, 'node_path': node.get('path')})
        state = {'event': event, 'asset_pin': asset_pin, 'asset': asset, 'bake': bake, 'nodes': native_nodes,
                 'group': group, 'model': model, 'wrapper': wrapper_raw, 'inputs': inputs}
        report['sampling'] = {key: bake.get(key) for key in ('fps', 'duration_frames', 'birth_duration_frames',
                                                          'coordinates', 'local_mounted', 'provenance', 'limitations')}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, struct.error, OverflowError) as exc:
        errors.append({'code': 'producer-input', 'message': str(exc)})
    report['inputs'] = sorted(inputs.values(), key=lambda pin: pin['path'])
    report['buildable'] = bool(selected) and not errors
    try:
        report['plan_sha256'] = _sha(_json({'job': job, 'report': report}))
    except (TypeError, ValueError):
        report['plan_sha256'] = None
    return report, state


def inspect_finite_job(job):
    return _inspect(copy.deepcopy(job))[0]


def _fixed(payload, offset, length, value):
    raw = value.encode('ascii')
    _require(len(raw) < length and b'\0' not in raw, 'Native name exceeds field')
    payload[offset:offset+length] = raw + bytes(length-len(raw))


def _chunk(tag, payload, prefix=b'', suffix=b''):
    start = 16 + len(prefix)
    end = start + len(payload)
    return struct.pack('<4sIII', tag, end+len(suffix), start, end) + prefix + payload + suffix


def _common(template, identity, name, delay, life, sorting=0):
    payload = bytearray(template['payload'])
    struct.pack_into('<I', payload, 4, identity)
    _fixed(payload, 8, 64, name)
    payload[72:460] = bytes(388)
    for offset in (136, 424):
        struct.pack_into('<6f', payload, offset, *([1.0] * 6))
    struct.pack_into('<I', payload, 460, 0x79)  # Direct current external owner.
    struct.pack_into('<HBB6h', payload, 464, sorting+32768, 0, 0, delay, delay, life, life, 0, 0)
    payload[520:552] = bytes(32)
    struct.pack_into('<I', payload, 520, 0xffffffff)
    struct.pack_into('<I', payload, 532, 0x38)
    struct.pack_into('<I', payload, 536, 0xffffffff)
    return payload


def _animation(samples):
    channels = []
    first = samples[0]['frame']
    for field, start, width in (('position', 1, 3), ('rotationDegrees', 4, 3), ('size', 7, 3), ('color', 149, 4)):
        for axis in range(width):
            values = [float(sample[field][axis]) for sample in samples]
            header = struct.pack('<HBBHHffII', start+axis, 1, 0, len(values), 0, min(values), max(values), 0xffffffff, 0)
            channels.append(header + b''.join(Key(int(sample['frame']-first+1), 1, value).pack() for sample, value in zip(samples, values)))
    payload = bytearray(16+8*len(channels))
    struct.pack_into('<H', payload, 0, len(channels))
    offset = 0
    for index, channel in enumerate(channels):
        struct.pack_into('<I', payload, 16+index*8, offset)
        offset += len(channel)
    return bytes(payload) + b''.join(channels)


def author_current_owner_graph(group, model, stem, particles, duration):
    """Author only the proven direct Group0→Model4 finite ownership profile."""
    _require(particles and len(particles) <= 65534, 'Finite graph needs bounded particles')
    root = _common(group, 0, stem, 0, duration+2)
    struct.pack_into('<I', root, 520, 0)
    struct.pack_into('<I', root, 528, len(particles))
    chunks = [_chunk(b'NODE', root, group['prefix'], group['suffix'])]
    for identity, particle in enumerate(particles, 1):
        samples = particle['samples']
        first, last = int(samples[0]['frame']), int(samples[-1]['frame'])
        child = _common(model, identity, particle['stem'], first, last-first+1, particle['sorting_order'])
        _fixed(child, 552, 32, particle['stem'])
        _fixed(child, 584, 32, '')
        child[616:] = bytes(len(child)-616)
        struct.pack_into('<I', child, 536, identity-1)
        chunks.extend((_chunk(b'ANIM', _animation(samples)), _chunk(b'NODE', child, model['prefix'], model['suffix'])))
    chunks.append(_chunk(b'NLST', struct.pack('<'+'I'*len(particles), *range(1, len(particles)+1))))
    header = bytearray(group['header'])
    header[16] = 30
    struct.pack_into('<3H', header, 10, len(particles)+1, len(particles), len(particles))
    raw = bytes(header) + b''.join(chunks) + _chunk(b'TERM', b'')
    parsed = SparkDocument.from_bytes(raw)
    nodes = parsed.nodes
    _require(len(nodes) == len(particles)+1 and nodes[0].children == tuple(range(1, len(particles)+1)), 'Generated graph reference readback differs')
    _require(parsed.to_bytes() == raw, 'Generated graph byte readback differs')
    return raw


def _native_modules():
    try:
        from .. import dsts_formats as fmt
        from ..anim.InterfaceHundredLine import AnimFileHundredLine
        from ..anim.BinaryHundredLine import AnimFileBinary
        from ..nlst import write_nlst
    except ImportError as exc:
        raise RuntimeError('Generation requires the installed add-on package and matching native backend/Python ABI') from exc
    return fmt, AnimFileHundredLine, AnimFileBinary, write_nlst


def _filesystem_path(path):
    """Keep reviewed paths canonical; extend only filesystem I/O on Windows."""
    value = str(path)
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        value = '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return Path(value)


def _runtime_provenance(fmt, Anim, Binary, write_nlst):
    package = Anim.__module__.split('.anim.')[0]
    modules = [fmt, sys.modules[write_nlst.__module__]]
    modules.extend(module for name, module in list(sys.modules.items())
                   if (name in (package+'.anim', package+'.serialization')
                       or name.startswith((package+'.anim.', package+'.serialization.')))
                   and getattr(module, '__file__', None))
    pins = {str(Path(module.__file__).resolve()): pin_file(module.__file__) for module in modules}
    return {'backend': pin_file(fmt.__file__), 'backend_version': getattr(fmt, '__version__', None),
            'python': sys.version, 'platform': sys.platform, 'inputs': sorted(pins.values(), key=lambda pin: pin['path'])}


def produce_finite_components(job, output, *, expected_plan_sha256=None):
    """Generate new review resources and component manifests without overwrites."""
    job = copy.deepcopy(job)
    report, state = _inspect(job)
    _require(report['buildable'], 'Producer job is not buildable: ' + '; '.join(error['message'] for error in report['errors']))
    _require(expected_plan_sha256 is None or expected_plan_sha256 == report['plan_sha256'], 'Reviewed producer plan is stale')
    _require(os.name == 'nt', 'Native finite publication currently requires Windows atomic no-replace directory rename')
    destination = Path(output).absolute()
    _require(not destination.exists() and not destination.is_symlink(), 'Producer output already exists')
    _require(destination.parent.is_dir() and not any(p.is_symlink() for p in destination.parents), 'Choose an existing regular output parent')
    destination = destination.parent.resolve() / destination.name
    _require(not destination.is_relative_to(Path(job['source']['library']).resolve()), 'Producer output must be outside the source library')
    for pin in report['inputs']:
        _require(not Path(pin['path']).is_relative_to(destination), 'Producer output would contain a source input')
    fmt, Anim, Binary, write_nlst = _native_modules()
    runtime = _runtime_provenance(fmt, Anim, Binary, write_nlst)
    pending = destination.parent / ('.' + destination.name + '.pending-' + uuid.uuid4().hex)
    # Unlike tempfile.mkdtemp(mode0700), this inherits the workspace ACL so the
    # user's installed Blender can read the published output on Windows.
    pending.mkdir(mode=0o777)
    emitted, component_reports = {}, []
    old_throw, old_errors = fmt.get_throw_errors(), fmt.get_error_list()

    def emit(name, raw):
        name = _destination(name)
        key = name.casefold()
        _require(key not in emitted or emitted[key]['path'] == name and emitted[key]['sha256'] == _sha(raw), 'Generated dependency collision')
        if key in emitted:
            return
        path = _filesystem_path(pending / name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
        emitted[key] = {'path': name, 'sha256': _sha(raw), 'bytes': len(raw)}

    def anim_bytes(animation):
        raw = animation.to_binary().tobytes()
        binary = Binary(); binary.frombytes(raw)
        parsed = Anim.from_binary(binary)
        _require(parsed.bone_count == animation.bone_count and parsed.frame_count == animation.frame_count
                 and parsed.float_channel_count == animation.float_channel_count, 'Authored ANIM header readback differs')
        for channel, frames in animation.float_channels.items():
            for frame, value in frames.items():
                keys = parsed.float_channels[channel]
                actual = keys.get(frame, keys.get(0) if len(keys) == 1 else None)
                _require(actual is not None and abs(actual-value) <= 1e-5, 'Authored material stream readback differs')
        return raw

    try:
        fmt.set_throw_errors(True)
        fmt.set_error_list([])
        emit('job.json', _json(job))
        emit('inspection.json', _json(report))
        snapshots = [{'path': str(destination / name), 'sha256': emitted[name]['sha256'], 'bytes': emitted[name]['bytes']}
                     for name in ('job.json', 'inspection.json')]
        wrapper_donor = fmt.Geom.from_bytes(state['wrapper'])
        for selected in state['nodes']:
            index, source_node = selected['index'], selected['node']
            root_stem = f"{job['prefix']}_s{index:03d}"
            resources, graph_particles = set(), []
            for particle in selected['particles']:
                samples = particle['samples']
                stem = f"{job['prefix']}_p{particle['index']:05d}"
                native = fmt.Geom.from_bytes(selected['geom'])
                validate_runtime_skinning(selected['geom'], native)
                material = native.materials[0]
                expected_dependencies = set()
                for shader in material.shaders:
                    if shader.name and any(c != '0' for c in shader.name.replace('_', '')):
                        expected_dependencies.update('shaders/' + shader.name + suffix + '.shad' for suffix in ('_vp', '_fp'))
                for uniform in material.uniforms:
                    if uniform.uniform_type == 'texture' and uniform.value:
                        expected_dependencies.add('images/' + uniform.value + '.img')
                _require(expected_dependencies == set(selected['dependencies']), 'Carrier actual native texture/shader closure differs from manifest')
                first, last = int(samples[0]['frame']), int(samples[-1]['frame'])
                for identifier, value in ((301, [first/30, 0, samples[0]['flipbook']]), (178, samples[0]['worldOffset'])):
                    matches = [uniform for uniform in material.uniforms if uniform.parameter_id == identifier]
                    _require(len(matches) == 1, 'Carrier lacks a required native material uniform')
                    matches[0].value = value
                if selected['custom']:
                    next(uniform for uniform in material.uniforms if uniform.parameter_id == 302).value = [samples[0]['customData2'][3]]
                raw = native.to_bytes()
                validate_matrix_alignment(raw, 1)
                validate_runtime_skinning(raw, fmt.Geom.from_bytes(raw))
                a, b = struct.unpack_from('<QQ', selected['geom'], 0x48)
                c, d = struct.unpack_from('<QQ', raw, 0x48)
                _require(selected['geom'][a:b] == raw[c:d], 'Carrier vertex/index bytes changed')
                _require(_bindings(raw) == selected['custom'], 'Carrier float binding order changed')
                emit('resources/patch/' + stem + '.geom', raw)
                emit('resources/patch/' + stem + '.nlst', write_nlst(native).encode('utf8'))
                animation = Anim(); animation.bone_count = 1; animation.bone_blend_factors = [255]
                animation.playback_rate = 30; animation.frame_count = last-first+1
                animation.positions = {0: {0: [0, 0, 0]}}; animation.rotations = {0: {0: [0, 0, 0, 1]}}; animation.scales = {0: {0: [1, 1, 1]}}
                streams = ([[sample['customData2'][3] for sample in samples]] if selected['custom'] else [])
                streams += [[sample['flipbook'] for sample in samples]]
                streams += [[sample['worldOffset'][axis] for sample in samples] for axis in range(3)]
                animation.float_channel_count = len(streams); animation.float_channel_blend_factors = [255] * len(streams)
                animation.float_channels = {channel: {frame: float(value) for frame, value in enumerate(values)} for channel, values in enumerate(streams)}
                emit('resources/patch/' + stem + '.anim', anim_bytes(animation))
                resources.update(stem + suffix for suffix in ('.geom', '.nlst', '.anim'))
                graph_particles.append({'stem': stem, 'samples': samples, 'sorting_order': particle['source']['sortingOrder']})
            spark = author_current_owner_graph(state['group'], state['model'], root_stem, graph_particles, state['bake']['duration_frames'])
            emit('resources/patch/' + root_stem + '.sprk', spark)
            wrapper = fmt.Geom()
            for key in ('unknown_0x10', 'unknown_0x30', 'unknown_0x34'):
                setattr(wrapper, key, getattr(wrapper_donor, key))
            wrapper.unknown_0x30 |= 8; wrapper.unknown_0x34 = len(spark); wrapper.clut = bytes(wrapper_donor.clut)
            wrapper.skeleton = fmt.Skeleton(); bone = fmt.Bone(); bone.name = root_stem
            bone.transform.position = [0, 0, 0, 1]; bone.transform.quaternion = [0, 0, 0, 1]; bone.transform.scale = [1, 1, 1, 1]
            wrapper.skeleton.bones.append(bone)
            raw = wrapper.to_bytes()
            validate_matrix_alignment(raw, 1); validate_runtime_skinning(raw, fmt.Geom.from_bytes(raw))
            emit('resources/patch/' + root_stem + '.geom', raw)
            emit('resources/patch/' + root_stem + '.nlst', write_nlst(wrapper).encode('utf8'))
            animation = Anim(); animation.bone_count = 1; animation.bone_blend_factors = [255]
            animation.playback_rate = 30; animation.frame_count = state['bake']['duration_frames']+1
            emit('resources/patch/' + root_stem + '.anim', anim_bytes(animation))
            resources.update(root_stem + suffix for suffix in ('.geom', '.anim', '.nlst', '.sprk'))
            for name, raw in selected['dependencies'].items():
                emit('resources/patch/' + name, raw); resources.add(name)
            component = {'format': 'nc-native-skill-component-v1', 'id': root_stem, 'kind': 'effect',
                         'source': {**{key: job['source'][key] for key in ('model_code', 'role', 'phase_id')},
                                    'asset': state['asset_pin'], 'event_order': job['event_order'], 'source_event': state['event']['source_event'],
                                    'node_index': index, 'node_path': source_node['path']},
                         'native': {'figure_stem': root_stem}, 'sampling': {'fps': 30, 'duration_frames': state['bake']['duration_frames']},
                         'profiles': {'coordinate': state['bake']['coordinates'], 'render': 'Mesh', 'ownership': PROFILE},
                         'dependencies': [{'path': '../resources/patch/' + name, 'destination': name,
                                           'sha256': emitted[('resources/patch/'+name).casefold()]['sha256'],
                                           'bytes': emitted[('resources/patch/'+name).casefold()]['bytes']}
                                          for name in sorted(resources)],
                         'provenance': report['inputs'] + runtime['inputs'] + snapshots, 'blockers': report['blockers']}
            component_path = 'components/' + root_stem + '.json'
            emit(component_path, _json(component))
            component_reports.append({'path': str(destination / component_path), 'sha256': _sha(_json(component)), 'bytes': len(_json(component))})
        current = inspect_finite_job(job)
        _require(current['plan_sha256'] == report['plan_sha256'], 'Producer inputs changed during generation')
        for pin in runtime['inputs']:
            _check_pin(pin)
        result = {'format': 'nc-finite-local-mesh-production-v1', 'installable': False, 'output': str(destination),
                  'plan_sha256': report['plan_sha256'], 'components': component_reports,
                  'selected_nodes': report['selected_nodes'], 'omitted_nodes': report['omitted_nodes'],
                  'inputs': report['inputs'], 'runtime': runtime, 'blockers': report['blockers'],
                  'files': sorted(emitted.values(), key=lambda row: row['path'])}
        emit('producer-report.json', _json(result))
        _require(not destination.exists() and not destination.is_symlink(), 'Producer destination appeared during generation')
        os.rename(_filesystem_path(pending), _filesystem_path(destination))
        return result
    finally:
        fmt.set_throw_errors(old_throw)
        fmt.set_error_list(old_errors)
        if pending.exists():
            _require(pending.parent.resolve() == destination.parent and pending.name.startswith('.'+destination.name+'.pending-'),
                     'Unexpected producer staging path')
            shutil.rmtree(_filesystem_path(pending))
