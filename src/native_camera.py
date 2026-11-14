"""Preserve native camera GEOM data and edit proven lens fields only.

Pinned native evidence: 0x1402E05E0 copies 56-byte file records and binds their
bone hashes; 0x1402E2D80 reads FOV degrees (+4), near (+12), far (+16).
0x1405018E0 constructs vertical FOV frusta. Unknown optical fields stay intact.
"""

import base64
import json
import math
from pathlib import Path
import struct

CAMERA_RECORD_SIZE = 56
LENS_FIELDS = {'fov_degrees': 4, 'near': 12, 'far': 16}


def patch_camera_lenses(raw, changes):
    """Bounded byte edit; unrelated records, alignment and sections survive."""
    if len(raw) < 0xB0 or struct.unpack_from('<I', raw)[0] != 316:
        raise ValueError('Expected GEOM v316')
    count = struct.unpack_from('<H', raw, 0xA)[0]
    offset = struct.unpack_from('<Q', raw, 0x60)[0]
    if offset < 0xB0 or offset + count * CAMERA_RECORD_SIZE > len(raw):
        raise ValueError('Invalid camera record bounds')
    output = bytearray(raw)
    for index, fields in changes.items():
        if not 0 <= index < count or set(fields) - LENS_FIELDS.keys():
            raise ValueError('Unknown camera record or unverified field')
        record = offset + CAMERA_RECORD_SIZE * index
        lens = {name: struct.unpack_from('<f', raw, record + at)[0] for name, at in LENS_FIELDS.items()}
        lens.update(fields)
        if not all(math.isfinite(float(v)) for v in lens.values()) or not 0 < lens['fov_degrees'] < 179 or not 0 < lens['near'] < lens['far']:
            raise ValueError('Camera requires 0 < FOV < 179 and 0 < near < far')
        for name, value in fields.items():
            struct.pack_into('<f', output, record + LENS_FIELDS[name], float(value))
    return bytes(output)


def rest_signature(armature):
    return json.dumps([(b.name, b.parent.name if b.parent else None,
                        [round(float(v), 5) for row in b.matrix_local for v in row])
                       for b in armature.data.bones])


def preserve_camera_source(collection, armature, filepath):
    raw = Path(filepath).read_bytes()
    if len(raw) < 0xB0:
        raise ValueError("Truncated GEOM header")
    count = struct.unpack_from('<H', raw, 0xA)[0]
    if not count:
        return False
    meshes, materials, lights = struct.unpack_from('<HHH', raw, 4)
    if meshes or materials or lights:
        raise ValueError("Mixed camera/mesh/light GEOM export is not supported; keep the original file")
    offset = struct.unpack_from('<Q', raw, 0x60)[0]
    if offset < 0xB0 or offset + CAMERA_RECORD_SIZE * count > len(raw):
        raise ValueError("Invalid camera record range")
    records = [raw[offset + CAMERA_RECORD_SIZE*i:offset + CAMERA_RECORD_SIZE*(i+1)] for i in range(count)]
    collection['DSTS_camera_source'] = base64.b64encode(raw).decode('ascii')
    collection['DSTS_camera_rest'] = rest_signature(armature)
    collection['DSTS_camera_records'] = json.dumps([
        {'hash': f'{struct.unpack_from("<I", r)[0]:08x}',
         'unverified_parameters': list(struct.unpack_from('<7f', r, 4)),
         'raw_hex': r.hex()} for r in records])
    for index, record in enumerate(records):
        for name, at in LENS_FIELDS.items():
            prop = f'DSTS_camera_{index}_{name}'
            collection[prop] = struct.unpack_from('<f', record, at)[0]
            collection.id_properties_ui(prop).update(description='Native camera ' + name.replace('_', ' '), min=.00001)
    nlst = Path(filepath).with_suffix('.nlst')
    collection['DSTS_camera_nlst'] = nlst.read_text(encoding='utf-8-sig') if nlst.exists() else ''
    collection['DSTS_camera_note'] = 'Native camera rig. Lens FOV/near/far are editable; other data is preserved. ANIM lens tracks can override static FOV.'
    return True


class PreservedCameraGeom:
    def __init__(self, collection, armature, skeleton):
        if rest_signature(armature) != collection['DSTS_camera_rest']:
            raise ValueError('Camera rest rig changed. Only camera animation editing is supported; restore its original rest pose.')
        self.skeleton = skeleton
        self.meshes = []
        self.materials = []
        raw = base64.b64decode(collection['DSTS_camera_source'])
        changes = {}
        for index in range(struct.unpack_from('<H', raw, 0xA)[0]):
            fields = {name: collection[f'DSTS_camera_{index}_{name}'] for name in LENS_FIELDS
                      if f'DSTS_camera_{index}_{name}' in collection}
            if fields:
                changes[index] = fields
        self._raw = patch_camera_lenses(raw, changes)
        self._nlst = collection['DSTS_camera_nlst']
        self.export_note = 'Camera GEOM preserves its rig and unknown data; verified lens fields are editable. Export motion as .anim.'

    def to_file(self, filepath):
        Path(filepath).write_bytes(self._raw)

    def nlst_text(self):
        return self._nlst
