"""Bounded, read-only memory and pinned PE primitives. No game functions are called."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import struct

EXE_SHA256 = 'ff9de825a543bf874cfb7e73ed951256d3ce4e8702957afa3b26ca6487a81688'
PROFILE = 'time-stranger-ff9de825-passive-v1'
MAX_ADDRESS = 0x00007FFFFFFFFFFF


class Refused(ValueError):
    pass


def checked_range(address: int, size: int):
    if not isinstance(address, int) or not isinstance(size, int) or not 0x10000 <= address <= MAX_ADDRESS:
        raise Refused('Invalid user-space address')
    if not 0 < size <= 65536 or address + size - 1 > MAX_ADDRESS:
        raise Refused('Read outside address/size bounds')


class PinnedImage:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        if Path(path).stat().st_size > 512 * 1024 * 1024:
            raise Refused('Executable exceeds profile inspection limit')
        self.data = Path(path).read_bytes()
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        if self.sha256 != EXE_SHA256:
            raise Refused('Executable SHA256 differs from pinned profile')
        self.image_base, self.image_size, self.sections = self.parse_pe(self.data)

    @staticmethod
    def parse_pe(data):
        def unpack(fmt, off):
            if off < 0 or off + struct.calcsize(fmt) > len(data): raise Refused('Truncated PE')
            return struct.unpack_from(fmt, data, off)
        if len(data) < 64 or data[:2] != b'MZ': raise Refused('Missing DOS header')
        pe, = unpack('<I', 0x3c)
        if pe < 64 or data[pe:pe+4] != b'PE\0\0': raise Refused('Invalid PE offset/signature')
        machine, count = unpack('<HH', pe+4)
        optional_size, = unpack('<H', pe+20)
        if machine != 0x8664 or not 1 <= count <= 96 or optional_size < 112: raise Refused('Unsupported PE architecture/layout')
        optional = pe+24
        if unpack('<H', optional)[0] != 0x20b: raise Refused('Requires PE32+')
        base, = unpack('<Q', optional+24)
        image_size, = unpack('<I', optional+56)
        if base != 0x140000000 or not 0x1BA6B68 <= image_size <= 0x40000000: raise Refused('Native image bounds differ')
        sections = []
        for i in range(count):
            virtual_size, rva, raw_size, raw = unpack('<IIII', optional+optional_size+40*i+8)
            if rva + max(virtual_size, raw_size) > image_size or raw + raw_size > len(data): raise Refused('PE section outside image/file')
            sections.append((rva, raw_size, raw))
        return base, image_size, sections

    def code(self, rva, size=32):
        offsets = [raw+rva-start for start, count, raw in self.sections if start <= rva and rva+size <= start+count]
        if len(offsets) != 1: raise Refused('Code RVA not uniquely file-backed')
        return self.data[offsets[0]:offsets[0]+size]


class FixtureMemory:
    def __init__(self, segments):
        self.segments = []
        total = 0
        if not isinstance(segments, list) or len(segments) > 4096: raise Refused('Too many fixture segments')
        for segment in segments:
            address = int(segment['address'], 0)
            text = segment['hex']
            if len(text) > 131072: raise Refused('Oversized fixture segment')
            data = bytes.fromhex(text); checked_range(address, len(data)); total += len(data)
            if total > 1024*1024: raise Refused('Fixture exceeds read budget')
            self.segments.append((address, data))
        self.segments.sort()
        for (a, data), (b, _) in zip(self.segments, self.segments[1:]):
            if a + len(data) > b: raise Refused('Overlapping fixture segments')

    def read(self, address, size):
        checked_range(address, size)
        for start, data in self.segments:
            if start <= address and address+size <= start+len(data): return data[address-start:address-start+size]
        raise Refused(f'Uncaptured memory range {address:#x}+{size}')

    @classmethod
    def load(cls, path, image):
        if Path(path).stat().st_size > 4*1024*1024: raise Refused('Fixture file too large')
        value = json.loads(Path(path).read_text('utf8'))
        if value.get('schema') != 'skill-runtime-memory-fixture-v1' or value.get('profile') != PROFILE:
            raise Refused('Unknown fixture schema/profile')
        if value.get('executable_sha256') != image.sha256 or value.get('image_size') != image.image_size:
            raise Refused('Fixture/executable profile mismatch')
        return cls(value['segments']), int(value['module_base'], 0), value


class Reader:
    """One sample budget, plus evidence recording and double-read consistency guards."""
    def __init__(self, source):
        self.source = source; self.bytes_read = 0; self.calls = 0
        self.recorded = {}; self.guards = []; self.conflicts = False

    def read(self, address, size, *, guard=False):
        checked_range(address, size)
        self.bytes_read += size; self.calls += 1
        if self.bytes_read > 1024*1024 or self.calls > 4096: raise Refused('Sample read budget exceeded')
        data = self.source.read(address, size)
        if len(data) != size: raise Refused('Partial memory read')
        for offset, byte in enumerate(data):
            previous = self.recorded.setdefault(address+offset, byte)
            if previous != byte: self.conflicts = True
        if guard: self.guards.append((address, data))
        return data

    def integer(self, address, fmt='<Q', guard=False):
        return struct.unpack(fmt, self.read(address, struct.calcsize(fmt), guard=guard))[0]

    def ptr(self, address, guard=True):
        value = self.integer(address, guard=guard)
        if value and (value < 0x10000 or value > MAX_ADDRESS or value % 8): raise Refused('Invalid/alignment pointer')
        return value

    def floats(self, address, count):
        values = struct.unpack('<'+'f'*count, self.read(address, 4*count))
        if not all(math.isfinite(v) for v in values): raise Refused('Nonfinite native transform/tween value')
        return list(values)

    def finish(self):
        for address, expected in self.guards:
            if self.read(address, len(expected)) != expected: raise Refused('Control/identity data changed while reading; torn snapshot')
        if self.conflicts: raise Refused('Overlapping reads changed during snapshot')

    def fixture(self, module_base, image_size, sample):
        segments = []; start = None; values = bytearray(); previous = None
        for address in sorted(self.recorded):
            if previous is None or address != previous+1 or len(values) == 65536:
                if start is not None: segments.append({'address': hex(start), 'hex': values.hex()})
                start = address; values = bytearray()
            values.append(self.recorded[address]); previous = address
        if start is not None: segments.append({'address': hex(start), 'hex': values.hex()})
        return {'schema':'skill-runtime-memory-fixture-v1', 'origin':'passive-read-recording', 'profile':PROFILE, 'executable_sha256':EXE_SHA256,
                'module_base':hex(module_base), 'image_size':image_size, 'atomic':False, 'sample':sample, 'segments':segments}
