"""Validate and package one already-native model; never translate Unity effects.

Uses the maintained Formats reader and the existing animation serializer. Raw
matrix checks reflect the native aligned-load crash diagnosed at 0x14036680B.
No binary payload is rewritten and no installed mod is modified by this API.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
from collections import Counter


def _name(value):
    if (not value or value in ('.', '..') or value.rstrip(' .') != value
            or any(ord(c) < 32 or c in '/\\:*?"<>|' for c in value)
            or value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                *('COM'+str(i) for i in range(1, 10)), *('LPT'+str(i) for i in range(1, 10))}):
        raise ValueError(f'Unsafe native resource name: {value!r}')
    return value


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_matrix_alignment(raw, bone_count):
    """Check absolute native SIMD addresses before handing bytes to the reader."""
    if len(raw) < 0xB0 or struct.unpack_from('<I', raw)[0] != 316:
        raise ValueError('Model package requires GEOM v316')
    inverse = struct.unpack_from('<Q', raw, 0x68)[0]
    skeleton = struct.unpack_from('<Q', raw, 0x98)[0]
    if not 0xB0 <= skeleton <= len(raw)-28:
        raise ValueError('Missing or truncated GEOM skeleton')
    transforms = skeleton+24+struct.unpack_from('<I', raw, skeleton+24)[0]
    offsets = {'inverse_bind': inverse, 'skeleton': skeleton, 'transforms': transforms}
    if any(offset % 16 for offset in offsets.values()):
        raise ValueError(f'GEOM matrix alignment must be 16 bytes: {offsets}')
    if bone_count and (inverse < 0xB0 or inverse+48*bone_count > len(raw)
                       or transforms < skeleton or transforms+48*bone_count > len(raw)):
        raise ValueError('GEOM matrix table exceeds file bounds')
    return offsets


def validate_runtime_skinning(raw, geom):
    """Reject runtime-unsafe palettes/physical parent lanes without rewriting.

    The Formats reader intentionally retains forensic inputs. Native 2E63A0
    assumes a contiguous palette under mesh flag_2, while 364850 consumes all
    four lanes in a parent block, including bytes beyond a nonmultiple count.
    """
    bones = list(geom.skeleton.bones)
    bone_indices = {bone: index for index, bone in enumerate(bones)}
    fast_palettes = []
    for mesh in geom.meshes:
        if not mesh.flag_2:
            continue
        indices = [bone_indices[bone] for bone in mesh.matrix_palette]
        if not indices or indices != list(range(indices[0], indices[0]+len(indices))):
            raise ValueError(f'{mesh.name}: flag_2 requires a contiguous ascending skeleton matrix palette; '
                             'repair the model explicitly before packaging')
        fast_palettes.append({'mesh': mesh.name, 'first': indices[0], 'count': len(indices)})
    skeleton = struct.unpack_from('<Q', raw, 0x98)[0]
    if not 0xB0 <= skeleton <= len(raw)-64:
        raise ValueError('Truncated native skeleton parent header')
    count = struct.unpack_from('<I', raw, skeleton+60)[0]
    rounded = (count+3)//4*4
    transforms = skeleton+24+struct.unpack_from('<I', raw, skeleton+24)[0]
    if skeleton+64+rounded*4 > min(transforms, len(raw)):
        raise ValueError('Native SIMD parent block exceeds the physical parent table')
    for index in range(rounded):
        child, parent = struct.unpack_from('<HH', raw, skeleton+64+index*4)
        parent &= 0x7FFF
        if child >= len(bones) or parent < 0x4000 and parent >= len(bones):
            raise ValueError(f'Native SIMD parent lane {index} has an out-of-range bone')
        if child == parent:
            raise ValueError(f'Native SIMD parent lane {index} is self-parented; '
                             'physical padding would overwrite the root transform')
    return {'contiguous_fast_palettes': fast_palettes, 'declared_parent_pairs': count,
            'runtime_parent_lanes': rounded}


def _validate_shader(raw, name):
    if len(raw) < 32 or raw[:4] != b'DXBC':
        raise ValueError(f'{name}: expected native DXBC shader bytes')
    size, count = struct.unpack_from('<II', raw, 24)
    if size != len(raw) or not 0 < count <= (len(raw)-32)//4:
        raise ValueError(f'{name}: invalid DXBC container size/table')
    for index in range(count):
        offset = struct.unpack_from('<I', raw, 32+4*index)[0]
        if offset < 32+4*count or offset+8 > len(raw):
            raise ValueError(f'{name}: invalid DXBC chunk offset')
        length = struct.unpack_from('<I', raw, offset+4)[0]
        if offset+8+length > len(raw):
            raise ValueError(f'{name}: truncated DXBC chunk')


def _validate_texture(raw, name):
    if len(raw) < 128 or raw[:4] != b'DDS ' or struct.unpack_from('<I', raw, 4)[0] != 124:
        raise ValueError(f'{name}: native .img must contain a DDS header')
    height, width = struct.unpack_from('<II', raw, 12)
    if not height or not width or len(raw) <= (148 if raw[84:88] == b'DX10' else 128):
        raise ValueError(f'{name}: missing DDS dimensions or pixel payload')


def _nlst_rows(text):
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = tuple(part.strip() for part in line.split(','))
        if len(parts) != 4:
            raise ValueError('Unsupported NLST record; model name list requires four columns')
        name, hash_text, parent_text, kind = parts
        name_hash = int(hash_text, 16)
        parent_hash = None if parent_text.lower() == 'null' else int(parent_text, 16)
        if not 0 <= name_hash <= 0xFFFFFFFF or parent_hash is not None and not 0 <= parent_hash <= 0xFFFFFFFF:
            raise ValueError('NLST hash exceeds uint32')
        rows.append((name, name_hash, parent_hash, kind))
    # Native meshes can share names/hashes. Preserve multiplicity, and compare
    # numeric hashes so native unpadded hex equals the exporter's padded form.
    return Counter(rows)


def plan_model_package(geom_path, resource_root=None, include_motions=True):
    """Resolve a model's exact file dependencies without writing anything.

    Local images/shaders enter the package. Dependencies resolved only in the
    explicitly chosen native resource root are recorded as external, preserving
    existing game resources and other mods. No recursive directory guessing.
    """
    from .backend import load_format_modules
    from .anim.BinaryHundredLine import AnimFileBinary
    from .anim.InterfaceHundredLine import AnimFileHundredLine
    from .nlst import write_nlst

    path = Path(geom_path).resolve()
    if path.suffix.lower() != '.geom':
        raise ValueError('Select an already exported native .geom, not a GLB or preview manifest')
    _name(path.stem)
    root = path.parent
    native = Path(resource_root).resolve() if resource_root else None
    for directory in {root, native} - {None}:
        for suffix in ('.sprk', '.bson'):
            if (directory/(path.stem+suffix)).exists():
                raise ValueError(f'{path.stem}{suffix} requires an effect package; native SPRK/BSON packaging is not implemented')
    raw = path.read_bytes()
    if raw[:4] == b'd\0\0\0':
        raise ValueError('Cyber Sleuth GEOM v100 cannot be packaged as a Time Stranger model; native Cyber Sleuth export is not implemented')
    validate_matrix_alignment(raw, 0)
    meshes, materials, lights, cameras = struct.unpack_from('<4H', raw, 4)
    if not meshes or not materials or lights or cameras:
        raise ValueError('This package supports mesh models only; camera/light/scene packages are not implemented')
    backend, *_ = load_format_modules()
    old_throw, old_errors = backend.get_throw_errors(), backend.get_error_list()
    try:
        backend.set_throw_errors(True)
        backend.set_error_list([])
        geom = backend.Geom.from_bytes(raw)
    finally:
        backend.set_throw_errors(old_throw)
        backend.set_error_list(old_errors)
    bones, floats = len(geom.skeleton.bones), len(geom.skeleton.float_channels)
    alignment = validate_matrix_alignment(raw, bones)
    skinning = validate_runtime_skinning(raw, geom)
    nlst = path.with_suffix('.nlst')
    nlst_bytes = nlst.read_bytes()
    if _nlst_rows(nlst_bytes.decode('utf-8-sig')) != _nlst_rows(write_nlst(geom)):
        raise ValueError('NLST names, hashes, parents or record types do not match this GEOM')
    files = {path.name: raw, nlst.name: nlst_bytes}
    sources = {path.name: str(path), nlst.name: str(nlst)}
    base = path.with_suffix('.anim')
    animations = [base]
    if include_motions:
        animations += sorted(p for p in root.glob(path.stem+'_*.anim')
                             if not p.stem.startswith(path.stem+'_lod_'))
    animation_info = []
    for animation_path in animations:
        _name(animation_path.name)
        animation_bytes = animation_path.read_bytes()
        binary = AnimFileBinary()
        binary.frombytes(animation_bytes)
        animation = AnimFileHundredLine.from_binary(binary)
        if animation.bone_count != bones or animation.float_channel_count != floats:
            raise ValueError(f'{animation_path.name}: ANIM counts {animation.bone_count}/{animation.float_channel_count} '
                             f'do not match GEOM bones/float channels {bones}/{floats}')
        if not math.isfinite(animation.playback_rate) or animation.playback_rate <= 0 or animation.frame_count < 1:
            raise ValueError(f'{animation_path.name}: invalid native animation timing')
        for channels, count in ((animation.positions, bones), (animation.rotations, bones),
                                (animation.scales, bones), (animation.float_channels, floats)):
            if any(not 0 <= index < count for index in channels):
                raise ValueError(f'{animation_path.name}: animation target exceeds GEOM')
        files[animation_path.name] = animation_bytes
        sources[animation_path.name] = str(animation_path)
        animation_info.append({'path': animation_path.name, 'frames': animation.frame_count,
                               'fps': animation.playback_rate})
    references, pass_info = {}, []
    for material in geom.materials:
        active = list(material.active_shader_slots)
        if not active:
            raise ValueError(f'{material.name}: no native shader passes')
        pass_info.append({'material': material.name, 'active_slots': active})
        for slot in active:
            name = _name(material.shaders[slot].name)
            for stage in ('vp', 'fp'):
                references[f'shaders/{name}_{stage}.shad'] = _validate_shader
        for uniform in material.uniforms:
            if uniform.uniform_type == 'texture':
                backend.validate_material_texture_binding(uniform.parameter_id)
                references[f'images/{_name(uniform.value)}.img'] = _validate_texture
    external = {}
    for relative, validate in sorted(references.items()):
        local = root/relative
        fallback = native/relative if native else None
        selected = local if local.is_file() else fallback
        if selected is None or not selected.is_file():
            raise FileNotFoundError(f'Missing native dependency: {relative}; supply it locally or choose its extracted resource folder')
        data = selected.read_bytes()
        validate(data, relative)
        if selected == local:
            files[relative] = data
            sources[relative] = str(selected)
        else:
            external[relative] = {'sha256': _sha(data), 'bytes': len(data), 'source': str(selected)}
    report = {'format': 'dsts-native-model-package-v1', 'model': path.stem,
        'scope': 'One existing native mesh model with its base and optional same-stem motions',
        'runtime_verified': False, 'bones': bones, 'float_channels': floats,
        'matrix_offsets': alignment, 'material_passes': pass_info, 'animations': animation_info,
        'native_skinning': skinning,
        'files': {name: {'sha256': _sha(data), 'bytes': len(data), 'source': sources[name]} for name, data in sorted(files.items())},
        'external_native_dependencies': external,
        'limitations': ['No Unity shader or particle conversion; native shader bytes are retained exactly.',
            'No SPRK/BSON, scenery, cinematic phase handoff, sound-bank, MBE or LOD-package generation.',
            'Native dependency presence and container checks do not prove shader linkage or in-game rendering.',
            'External native dependencies are verified but not copied; deployer must preserve their availability.',
            'ANIM counts and NLST identity checks cannot prove a foreign animation uses the same bone ordering.']}
    return report, files


def package_native_model(geom_path, output, resource_root=None, include_motions=True):
    """Publish a new isolated package only after every dependency validates."""
    destination = Path(output).resolve()
    if destination.exists():
        raise FileExistsError('Choose a new package folder; existing folders are never merged or overwritten')
    report, files = plan_model_package(geom_path, resource_root, include_motions)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dsts-package-', dir=destination.parent) as temporary:
        payload = Path(temporary)/'payload'
        patch = payload/'dsts-loader/patch'
        for relative, raw in files.items():
            target = patch/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            if _sha(target.read_bytes()) != report['files'][relative]['sha256']:
                raise OSError(f'Package write verification failed: {relative}')
        (payload/'model-package.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        if destination.exists():
            raise FileExistsError('Package destination appeared during validation')
        os.rename(payload, destination)
    return report
