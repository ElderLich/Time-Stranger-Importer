"""Preserve camera GEOM records while exposing their animation rig for editing.

Camera lens fields are retained verbatim: their runtime meaning is not yet
verified. This module intentionally does not invent a native camera writer.
"""

import base64
import json
from pathlib import Path
import struct


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
    if offset < 0xB0 or offset + 64 * count > len(raw):
        raise ValueError("Invalid camera record range")
    records = [raw[offset + 64*i:offset + 64*(i+1)] for i in range(count)]
    collection['DSTS_camera_source'] = base64.b64encode(raw).decode('ascii')
    collection['DSTS_camera_rest'] = rest_signature(armature)
    collection['DSTS_camera_records'] = json.dumps([
        {'hash': f'{struct.unpack_from("<I", r)[0]:08x}',
         'unverified_parameters': list(struct.unpack_from('<7f', r, 4)),
         'raw_hex': r.hex()} for r in records])
    nlst = Path(filepath).with_suffix('.nlst')
    collection['DSTS_camera_nlst'] = nlst.read_text(encoding='utf-8-sig') if nlst.exists() else ''
    collection['DSTS_camera_note'] = 'Native camera rig. Edit/export its animations; lens records remain byte-preserved.'
    return True


class PreservedCameraGeom:
    def __init__(self, collection, armature, skeleton):
        if rest_signature(armature) != collection['DSTS_camera_rest']:
            raise ValueError('Camera rest rig changed. Only camera animation editing is supported; restore its original rest pose.')
        self.skeleton = skeleton
        self.meshes = []
        self.materials = []
        self._raw = base64.b64decode(collection['DSTS_camera_source'])
        self._nlst = collection['DSTS_camera_nlst']
        self.export_note = 'Camera GEOM lens/rig records preserved byte for byte; export edited motion as .anim.'

    def to_file(self, filepath):
        Path(filepath).write_bytes(self._raw)

    def nlst_text(self):
        return self._nlst
