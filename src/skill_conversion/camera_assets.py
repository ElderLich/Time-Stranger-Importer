"""Rename a single native camera and every name/hash binding, preserving its rig.

This is the existing native-camera codec's bounded rename promoted for reuse.
Auxiliary NLST entries are retained, including ordinary camera aim locators.
"""
import csv
import re
import struct
import zlib


def _need(ok, message):
    if not ok:
        raise ValueError(message)


def camera_record(raw):
    _need(len(raw) >= 0xB0 and struct.unpack_from('<I', raw)[0] == 316, 'Expected camera GEOM v316')
    _need(struct.unpack_from('<4H', raw, 4) == (0, 0, 0, 1), 'Expected one camera and no meshes/materials/lights')
    offset = struct.unpack_from('<Q', raw, 0x60)[0]
    _need(0xB0 <= offset <= len(raw)-56, 'Invalid camera record bounds')
    return offset, struct.unpack_from('<I7f', raw, offset)


def rename_camera_bindings(raw, nlst, name):
    """Return byte-preserving GEOM and NLST renames; ANIM stays index-bound."""
    _need(isinstance(name, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', name), 'Invalid native camera name')
    record_offset, record = camera_record(raw)
    old_hash = record[0]
    new_hash = zlib.crc32(name.encode('ascii')) ^ 0xFFFFFFFF
    skeleton = struct.unpack_from('<Q', raw, 0x98)[0]
    strings = struct.unpack_from('<Q', raw, 0x78)[0]
    table = struct.unpack_from('<Q', raw, 0x90)[0]
    _need(0xA8 <= table <= len(raw)-24 and 0xB0 <= strings < skeleton <= len(raw)-44,
          'Invalid camera table or skeleton bounds')
    count, materials, offsets, _ = struct.unpack_from('<IIQQ', raw, table)
    bone_count, float_count = struct.unpack_from('<HH', raw, skeleton+16)
    _need(materials == 0 and 0 < count == bone_count <= 65535, 'Unsupported camera name table')
    hashes = skeleton+32+struct.unpack_from('<I', raw, skeleton+32)[0]
    floats = skeleton+40+struct.unpack_from('<I', raw, skeleton+40)[0]
    _need(0 <= offsets <= len(raw)-count*8 and 0 <= hashes <= len(raw)-count*4 and
          0 <= floats <= len(raw)-float_count*4, 'Camera binding table exceeds file')
    values = struct.unpack_from('<'+'I'*count, raw, hashes)
    matches = [i for i,v in enumerate(values) if v == old_hash]
    _need(len(matches) == 1, 'Camera must bind exactly one bone')
    selected = matches[0]
    _need(new_hash == old_hash or new_hash not in values, 'Camera hash collides with another bone')
    output = bytearray(raw)
    rebuilt = bytearray(b'\0')
    old_name = None
    for index in range(count):
        at = strings+struct.unpack_from('<Q', raw, offsets+index*8)[0]
        _need(strings <= at < skeleton, 'Camera bone name exceeds string section')
        end = raw.find(b'\0', at, skeleton)
        _need(end >= at, 'Unterminated camera bone name')
        text = raw[at:end].decode('ascii')
        if index == selected:
            old_name, text = text, name
        struct.pack_into('<Q', output, offsets+index*8, len(rebuilt))
        rebuilt.extend(text.encode('ascii')+b'\0')
    _need(len(rebuilt) <= skeleton-strings, 'Camera rename exceeds string capacity')
    output[strings:skeleton] = rebuilt+bytes(skeleton-strings-len(rebuilt))
    struct.pack_into('<I', output, record_offset, new_hash)
    struct.pack_into('<I', output, hashes+selected*4, new_hash)
    for index in range(float_count):
        at = floats+index*4
        if struct.unpack_from('<I', raw, at)[0] == old_hash:
            struct.pack_into('<I', output, at, new_hash)
    lines = nlst.splitlines(keepends=True)
    found = 0
    for index, line in enumerate(lines):
        row = next(csv.reader([line], skipinitialspace=True))
        if not row or not any(row):
            continue
        _need(len(row) == 4, 'Unsupported camera NLST row')
        if row[0] == old_name:
            _need(int(row[1], 16) == old_hash and row[3].strip() == 'camera', 'Camera NLST hash/type differs')
            ending = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
            lines[index] = f'{name}, {new_hash:08x}, {row[2]}, camera'+ending
            found += 1
        else:
            _need(row[0] != name and int(row[1], 16) != new_hash, 'Camera NLST name/hash collision')
            if row[2].strip().lower() != 'null' and int(row[2], 16) == old_hash:
                ending = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
                lines[index] = f'{row[0]}, {row[1]}, {new_hash:08x}, {row[3]}'+ending
    _need(found == 1, 'Expected one matching camera NLST entry')
    return bytes(output), ''.join(lines)
