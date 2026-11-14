"""Bounded, source-preserving SPAK v140 editing (no Blender dependency).

Promoted from the project's native_spark/compose_native_spark research, based on
the pinned Time Stranger loader 0x14036B040 and common reader 0x140358750.
This module does not infer emitter, shader, socket-owner or collision semantics.
Named-Blade / Nymic_Razor geometry-backend attribution remains in provenance.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Iterable

API_VERSION = 1
NONE = 0xFFFFFFFF
COMMON_KINDS = frozenset({0, 2, 3, 4})
SUPPORTED_KINDS = COMMON_KINDS | {9, 10}
TRANSFORM_FIELDS = {
    ("primary", "translation"): 72, ("primary", "rotation"): 104,
    ("primary", "scale"): 136, ("secondary", "translation"): 360,
    ("secondary", "rotation"): 392, ("secondary", "scale"): 424,
}
TEXTURE_SLOTS = {
    "ColorSampler": (224, 232), "NormalSampler": (240, 248),
    "LightSampler": (256, 264), "EmissiveSampler": (376, 384),
    "CLUTSampler": (392, 400),
}
COMMON_CHANNELS = frozenset(range(1, 10)) | frozenset(range(28, 37))
EDITABLE_CHANNELS = {
    0: COMMON_CHANNELS, 2: COMMON_CHANNELS,
    3: COMMON_CHANNELS | frozenset(range(112, 116)) | {140},
    4: COMMON_CHANNELS | frozenset(range(149, 153)),
    9: frozenset(range(136, 140)), 10: frozenset({66, 146}),
}
CAPABILITIES = {
    "version": 140, "common_node_kinds": tuple(sorted(COMMON_KINDS)),
    "transform_stages": ("primary", "secondary"),
    "transform_components": ("translation", "rotation", "scale"),
    "curve_interpolation": ("constant", "linear"),
    "curve_extrapolation": ("clamp",),
    "socket_owner_editing": False, "emitter_module_editing": False,
    "light_editing": False, "collision_editing": False,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _integer(value, lo: int, hi: int, label: str) -> int:
    _require(type(value) is int and lo <= value <= hi, f"{label} must be an integer in [{lo}, {hi}]")
    return value


def _floats(values: Iterable[float], count: int, label: str) -> tuple[float, ...]:
    values = tuple(float(v) for v in values)
    _require(len(values) == count and all(math.isfinite(v) for v in values), f"{label} requires {count} finite values")
    try:
        encoded = struct.pack("<" + "f" * count, *values)
    except (OverflowError, struct.error) as exc:
        raise ValueError(f"{label} exceeds native float32") from exc
    _require(all(math.isfinite(v) for v in struct.unpack("<" + "f" * count, encoded)), f"{label} exceeds native float32")
    return values


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _text(data, offset, size):
    return bytes(data[offset:offset + size]).split(b"\0", 1)[0].decode("utf-8", "replace")


@dataclass(frozen=True)
class VectorRange:
    low: tuple[float, float, float]
    high: tuple[float, float, float]

    @property
    def deterministic(self):
        return self.low == self.high


@dataclass(frozen=True)
class SparkNode:
    id: int
    kind: int
    name: str
    animation_index: int | None
    children: tuple[int, ...]
    editable: bool
    primary_translation: VectorRange | None = None
    primary_rotation: VectorRange | None = None
    primary_scale: VectorRange | None = None
    secondary_translation: VectorRange | None = None
    secondary_rotation: VectorRange | None = None
    secondary_scale: VectorRange | None = None
    delay_range: tuple[int, int] | None = None
    lifetime_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class Key:
    frame: int
    interpolation: int
    value: float
    in_time: float = -5.0
    in_value: float = 0.0
    out_time: float = 5.0
    out_value: float = 0.0

    def pack(self):
        _integer(self.frame, -32768, 32767, "Key frame")
        _integer(self.interpolation, 0, 65535, "Interpolation")
        values = _floats((self.value, self.in_time, self.in_value, self.out_time, self.out_value), 5, "Key")
        return struct.pack("<hH5f", self.frame, self.interpolation, *values)


@dataclass(frozen=True)
class Channel:
    id: int
    header: bytes
    keys: tuple[Key, ...]

    @property
    def editable(self):
        return self.header[3] == 0 and bool(self.keys) and all(
            k.interpolation in (0, 1) and all(math.isfinite(v) for v in
                (k.value, k.in_time, k.in_value, k.out_time, k.out_value)) for k in self.keys)

    def sample(self, frame: float):
        """Evaluate the supported clamped constant/linear profile only."""
        _require(self.editable, "Preview supports only nonempty clamped constant/linear channels")
        _require(math.isfinite(frame), "Sample frame must be finite")
        if frame <= self.keys[0].frame:
            return self.keys[0].value
        for left, right in zip(self.keys, self.keys[1:]):
            if frame < right.frame:
                if left.interpolation == 0:
                    return left.value
                alpha = (frame - left.frame) / (right.frame - left.frame)
                return left.value * (1 - alpha) + right.value * alpha
        return self.keys[-1].value


@dataclass
class _Chunk:
    tag: bytes
    offset: int
    size: int
    start: int
    end: int


def atomic_write(path, data: bytes, *, overwrite=False, expected_sha256=None):
    """Publish in the destination directory; failures never truncate a file.

    The caller must opt into replacement. expected_sha256 detects a stale
    selected destination immediately before publication. It is a cooperative
    stale-write guard, not an interprocess lock.
    """
    path = Path(path)
    _require(path.parent.is_dir(), f"Output directory does not exist: {path.parent}")
    _require(not path.is_symlink(), "Refusing to replace a symbolic-link destination")
    if path.exists():
        _require(overwrite, f"Destination exists: {path}")
        _require(path.is_file(), "Destination is not a regular file")
    if expected_sha256 is not None:
        _require(isinstance(expected_sha256, str) and re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256), "Invalid expected SHA256")
        _require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256.lower(), "Destination changed since import")
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if expected_sha256 is not None:
            _require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256.lower(), "Destination changed before publication")
        if overwrite:
            os.replace(temporary, path)
        else:
            # A hard link publishes the fully written inode and refuses a raced
            # destination on Windows and POSIX. Never use check-then-replace.
            os.link(temporary, path)
    finally:
        if fd is not None:
            os.close(fd)
        Path(temporary).unlink(missing_ok=True)


class SparkDocument:
    """A source snapshot plus scoped byte patches; no implicit normalization."""

    def __init__(self):
        raise TypeError("Use SparkDocument.from_bytes() or read()")

    @classmethod
    def from_bytes(cls, data: bytes):
        data = bytes(data)
        _require(len(data) >= 40 and data[:4] == b"SPAK", "Expected SPAK header and terminal chunk")
        _require(struct.unpack_from("<H", data, 8)[0] == 140, "Only SPAK version 140 is supported")
        result = object.__new__(cls)
        result._source = data
        result._data = bytearray(data)
        result._chunks = []
        result._changed = False
        offset = 24
        while True:
            _require(offset + 16 <= len(data), "Missing terminal chunk")
            tag, size, start, end = struct.unpack_from("<4sIII", data, offset)
            _require(size >= 16 and size <= len(data) - offset, "Chunk exceeds file")
            # Native traversal terminates on size16 before reading the tag or
            # payload fields. Preserve those exact words, including noncanonical
            # terminal metadata, instead of replacing them with a fresh header.
            if size == 16:
                result._terminal = (offset, tag)
                result._tail = data[offset + size:]
                break
            _require(16 <= start <= end <= size, "Chunk payload span exceeds chunk")
            result._chunks.append(_Chunk(tag, offset, size, offset + start, offset + end))
            offset += size
        result._node_chunks = {}
        result._animations = []
        result._lists = []
        for chunk in result._chunks:
            if chunk.tag == b"NODE":
                _require(chunk.end - chunk.start >= 72, "Truncated node identity")
                identity = _u32(data, chunk.start + 4)
                _require(identity not in result._node_chunks, "Duplicate SPAK node ID")
                result._node_chunks[identity] = chunk
            elif chunk.tag == b"ANIM":
                result._animations.append(chunk)
                result._channel_spans(chunk)
            elif chunk.tag == b"NLST":
                _require((chunk.end - chunk.start) % 4 == 0, "NLST size must be a multiple of uint32")
                result._lists.append(chunk)
        result._validate_counts()
        return result

    @classmethod
    def read(cls, path):
        return cls.from_bytes(Path(path).read_bytes())

    @property
    def source_sha256(self):
        return hashlib.sha256(self._source).hexdigest()

    @property
    def fps(self):
        return self._data[16]

    @property
    def clock_mode(self):
        """Serialized clock byte; intentionally read-only, not an enum guess."""
        return self._data[17]

    def _payload(self, chunk):
        return self._data[chunk.start:chunk.end]

    def _validate_counts(self):
        nodes, animations, names = struct.unpack_from("<3H", self._data, 10)
        _require(nodes == len(self._node_chunks) and animations == len(self._animations), "SPAK node/animation count mismatch")
        _require(names == sum((c.end - c.start) // 4 for c in self._lists), "SPAK NLST count mismatch")

    def _names(self):
        _require(len(self._lists) <= 1, "Multiple SPAK NLST chunks are unsupported for semantic editing")
        if not self._lists:
            return ()
        payload = self._payload(self._lists[0])
        return tuple(v[0] for v in struct.iter_unpack("<I", payload))

    def _span(self, payload, start_at, count_at):
        _require(len(payload) >= count_at + 4, "Truncated node reference span")
        start, count = _u32(payload, start_at), _u32(payload, count_at)
        names = self._names()
        _require(not count or start != NONE and start <= len(names) and count <= len(names) - start, "Node reference span exceeds NLST")
        return names[start:start + count] if count else ()

    @property
    def nodes(self):
        result = {}
        for identity, chunk in self._node_chunks.items():
            payload = self._payload(chunk)
            kind = struct.unpack_from("<H", payload)[0]
            fields = {}
            anim = None
            children = ()
            if kind in SUPPORTED_KINDS:
                anim_at = {9: 128, 10: 1000}.get(kind, 536)
                _require(len(payload) >= anim_at + 4, "Truncated known node animation reference")
                anim = _u32(payload, anim_at)
                _require(anim == NONE or anim < len(self._animations), "Node animation reference exceeds ANIM array")
                anim = None if anim == NONE else anim
            if kind in COMMON_KINDS:
                children = self._span(payload, 520, 528)
                for (stage, component), at in TRANSFORM_FIELDS.items():
                    values = struct.unpack_from("<6f", payload, at)
                    fields[stage + "_" + component] = VectorRange(values[::2], values[1::2])
                fields["delay_range"] = struct.unpack_from("<2h", payload, 468)
                fields["lifetime_range"] = struct.unpack_from("<2h", payload, 472)
            result[identity] = SparkNode(identity, kind, _text(payload, 8, 64), anim, children, kind in COMMON_KINDS, **fields)
        return result

    def _channel_spans(self, chunk):
        payload = self._payload(chunk)
        _require(len(payload) >= 16, "Truncated ANIM header")
        count = struct.unpack_from("<H", payload)[0]
        base = 16 + 8 * count
        _require(base <= len(payload), "ANIM offset table exceeds payload")
        result, occupied, prior_id = [], [], -1
        for index in range(count):
            start = base + _u32(payload, 16 + index * 8)
            _require(start <= len(payload) - 24, "ANIM channel header exceeds payload")
            identity, keys = struct.unpack_from("<H", payload, start)[0], struct.unpack_from("<H", payload, start + 4)[0]
            end = start + 24 + keys * 24
            _require(end <= len(payload), "ANIM key array exceeds payload")
            _require(identity > prior_id, "ANIM binary search requires ascending distinct channel IDs")
            _require(not any(start < b and a < end for a, b in occupied), "Overlapping ANIM channel storage")
            occupied.append((start, end))
            prior_id = identity
            frames = [struct.unpack_from("<h", payload, start + 24 + k * 24)[0] for k in range(keys)]
            _require(all(a < b for a, b in zip(frames, frames[1:])), "ANIM key frames must strictly increase")
            result.append((identity, start, end))
        return result

    def channels(self, animation_index):
        _integer(animation_index, 0, len(self._animations) - 1, "Animation index")
        chunk = self._animations[animation_index]
        payload = self._payload(chunk)
        return tuple(Channel(identity, bytes(payload[start:start + 24]),
                             tuple(Key(*struct.unpack_from("<hH5f", payload, at))
                                   for at in range(start + 24, end, 24)))
                     for identity, start, end in self._channel_spans(chunk))

    def validate_editable(self):
        """Validate the established graph subset before any semantic mutation."""
        self._validate_counts()
        _require(not self._tail, "Trailing SPAK data is preservation-only")
        _require(self._terminal[1] == b"TERM", "Unknown terminal tag is preservation-only")
        _require(all(c.tag in (b"NODE", b"ANIM", b"NLST") for c in self._chunks), "Unknown SPAK chunks are preservation-only")
        _require(self.fps > 0, "SPAK frame rate must be positive")
        nodes = self.nodes
        _require(nodes, "SPAK has no root")
        _require(all(n.kind in SUPPORTED_KINDS for n in nodes.values()), "Unsupported node kind is preservation-only")
        roots = [n.id for n in nodes.values() if n.kind == 0]
        _require(len(roots) == 1, "Native SPAK needs exactly one type0 root")
        graph = {}
        for identity, node in nodes.items():
            payload = self._payload(self._node_chunks[identity])
            refs = list(node.children)
            if node.kind in COMMON_KINDS:
                _require(all(math.isfinite(v) for stage, component in TRANSFORM_FIELDS
                             for v in (*getattr(node, stage + "_" + component).low,
                                       *getattr(node, stage + "_" + component).high)),
                         "Nonfinite common transforms are preservation-only")
            if node.kind == 2:
                refs.extend(self._span(payload, 576, 584))
            if node.kind == 3:
                _require(len(payload) >= 732, "Truncated sprite material reference")
                ref = _u32(payload, 728)
                _require(ref in nodes and nodes[ref].kind == 10, "Sprite material must reference a type10 node")
                refs.append(ref)
            if node.kind == 10:
                for flag, pointer in TEXTURE_SLOTS.values():
                    if payload[flag] & 1:
                        ref = _u32(payload, pointer)
                        _require(ref in nodes and nodes[ref].kind == 9, "Material texture must reference a type9 node")
                        refs.append(ref)
            _require(all(ref in nodes for ref in refs), "SPAK node references a missing node")
            graph[identity] = refs
        # Iterative DFS also handles large imported graphs without recursion.
        active, seen, stack = set(), set(), [(roots[0], False)]
        while stack:
            identity, leaving = stack.pop()
            if leaving:
                active.remove(identity)
                seen.add(identity)
            elif identity not in seen:
                _require(identity not in active, "Cyclic SPAK graph")
                active.add(identity)
                stack.append((identity, True))
                stack.extend((child, False) for child in reversed(graph[identity]))
        _require(seen == set(nodes), "Unreachable SPAK nodes are preservation-only")
        return self

    def _common_chunk(self, node_id):
        self.validate_editable()
        _require(node_id in self._node_chunks, f"Unknown node ID: {node_id}")
        _require(self.nodes[node_id].editable, "This node has no supported common transform/timing record")
        return self._node_chunks[node_id]

    def _patch(self, offset, data):
        self._data[offset:offset + len(data)] = data
        self._changed = True

    def set_transform(self, node_id, stage, component, low, high=None):
        chunk = self._common_chunk(node_id)
        _require((stage, component) in TRANSFORM_FIELDS, "Unknown transform stage or component")
        low = _floats(low, 3, "Transform minimum")
        high = low if high is None else _floats(high, 3, "Transform maximum")
        _require(all(a <= b for a, b in zip(low, high)), "Transform range minimum exceeds maximum")
        encoded = struct.pack("<6f", *(v for pair in zip(low, high) for v in pair))
        self._patch(chunk.start + TRANSFORM_FIELDS[stage, component], encoded)
        return self

    def set_timing(self, node_id, *, delay_range=None, lifetime_range=None):
        chunk = self._common_chunk(node_id)
        patches = []
        for values, offset, label in ((delay_range, 468, "Delay"), (lifetime_range, 472, "Lifetime")):
            if values is None:
                continue
            values = tuple(values)
            _require(len(values) == 2, f"{label} requires two integer frame endpoints")
            lo, hi = (_integer(v, -1 if label == "Lifetime" else 0, 32767, label) for v in values)
            _require(lo <= hi and (lo != -1 or hi == -1), f"{label} range is invalid; infinite lifetime is (-1, -1)")
            patches.append((chunk.start + offset, struct.pack("<2h", lo, hi)))
        for offset, encoded in patches:
            self._patch(offset, encoded)
        return self

    def set_resource(self, node_id, resource, stem):
        self.validate_editable()
        _require(node_id in self._node_chunks, f"Unknown node ID: {node_id}")
        node = self.nodes[node_id]
        fields = {(4, "model"): (552, 32), (4, "motion"): (584, 32),
                  (9, "texture"): (72, 32), (10, "shader"): (496, 128)}
        _require((node.kind, resource) in fields, "Unsupported resource field for this node")
        _require(isinstance(stem, str) and (not stem and resource == "motion" or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", stem)), "Expected a native resource basename, without directories")
        offset, size = fields[node.kind, resource]
        encoded = stem.encode("ascii")
        _require(len(encoded) < size, f"Resource name exceeds {size - 1} bytes")
        chunk = self._node_chunks[node_id]
        _require(chunk.end - chunk.start >= offset + size, "Truncated resource field")
        self._patch(chunk.start + offset, encoded + bytes(size - len(encoded)))
        return self

    def set_channel_keys(self, animation_index, channel_id, keys):
        self.validate_editable()
        _integer(animation_index, 0, len(self._animations) - 1, "Animation index")
        chunk = self._animations[animation_index]
        existing = next((c for c in self.channels(animation_index) if c.id == channel_id), None)
        _require(existing is not None, "Channel does not exist; adding channels is not implemented")
        owners = [node for node in self.nodes.values() if node.animation_index == animation_index]
        _require(owners and all(channel_id in EDITABLE_CHANNELS[node.kind] for node in owners),
                 "Channel semantic editing is not established for every referencing node")
        _require(existing.editable, "Editing supports only clamped constant/linear source channels")
        keys = tuple(keys)
        _require(len(keys) == len(existing.keys), "Changing key count is not implemented; storage/opaque offsets must be preserved")
        _require(all(isinstance(k, Key) and k.interpolation in (0, 1) for k in keys), "Only constant/linear Key records are editable")
        _require(all(a.frame < b.frame for a, b in zip(keys, keys[1:])), "Key frames must strictly increase")
        encoded = b"".join(key.pack() for key in keys)
        _, start, _ = next(item for item in self._channel_spans(chunk) if item[0] == channel_id)
        bounds = struct.pack("<2f", min(k.value for k in keys), max(k.value for k in keys))
        self._patch(chunk.start + start + 8, bounds)
        self._patch(chunk.start + start + 24, encoded)
        return self

    def to_bytes(self):
        if self._changed:
            self.validate_editable()
        return bytes(self._data)

    def write(self, path, *, overwrite=False, expected_sha256=None):
        raw = self.to_bytes()
        # Reparse every emitted byte before opening the destination.
        check = type(self).from_bytes(raw)
        if self._changed:
            check.validate_editable()
        atomic_write(path, raw, overwrite=overwrite, expected_sha256=expected_sha256)
        return hashlib.sha256(raw).hexdigest()
