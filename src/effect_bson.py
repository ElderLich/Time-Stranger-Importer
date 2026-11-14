"""MVGL BSON v10 effect timing, audio, drawable and attachment editing.

This is the game's length-delimited BSON container, not MongoDB BSON. The
bounded reader is promoted from the project's mvgl_bson research. Fixed-size
scalar patches retain original object ordering/length headers/opaque records;
new strings append to the original pool without changing any existing index.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
import struct

try:
    from .spark import atomic_write
except ImportError:  # Direct import for non-Blender callers.
    from spark import atomic_write

API_VERSION = 1
FPS = 30
END = b"\x03\x00\x04\x00"
HEADER = b"BSON\x0a\0\0\0"
MAX_DEPTH = 128


def _require(condition, message):
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class _Node:
    kind: int
    key: int
    start: int
    end: int
    children: tuple["_Node", ...] | None = None


@dataclass(frozen=True)
class EffectField:
    path: tuple[str, ...]
    label: str
    value: int | float | str
    value_type: str
    units: str


@dataclass(frozen=True)
class EffectSet:
    name: str
    id: str | None
    drawables: tuple[str, ...]
    sound_name: str | None
    sound_start_frame: int | None
    attachment_owner: str | None
    position_socket: str | None
    rotation_socket: str | None
    scale_socket: str | None


# Entries are established native reader fields, not guesses from a spelling.
# Root:140CCB120; sound:140CCBAA0/140CBA280/140CBA150; drawable:140CD0B80.
_AUDIO = {
    "se_name": ("Sound cue", "string", "cue ID", 0x14),
    "se_offset": ("Sound start", "integer", "frames (30 Hz)", 0x10),
    "se_stop": ("Stop sound at effect cleanup", "boolean", "", 0x10),
    "se_fadeout": ("Sound cleanup fade", "integer", "frames (30 Hz)", 0x10),
    "se_volume": ("Sound volume", "float", "gain", 0x11),
    "se_stream": ("Streaming sound", "boolean", "", 0x10),
    "se_3D": ("Spatial sound", "boolean", "", 0x10),
}


class EffectDocument:
    def __init__(self):
        raise TypeError("Use EffectDocument.from_bytes() or read()")

    @classmethod
    def from_bytes(cls, data):
        data = bytes(data)
        _require(data[:8] == HEADER, "Expected MVGL BSON v10 header")

        def parse(at, limit, depth):
            _require(depth <= MAX_DEPTH, "BSON nesting exceeds supported depth")
            _require(at + 8 <= limit, "Truncated BSON node")
            kind, high, low, key = struct.unpack_from("<BBHI", data, at)
            length = low + 65535 * high
            end = at + length
            _require(length >= 8 and end <= limit, "BSON node exceeds parent span")
            if kind == 1:
                _require(length >= 16 and data[end - 4:end] == END, "Invalid BSON object boundary")
                count = struct.unpack_from("<I", data, at + 8)[0]
                _require(count <= (length - 16) // 8, "Invalid BSON child count")
                children, cursor = [], at + 12
                for _ in range(count):
                    child = parse(cursor, end - 4, depth + 1)
                    children.append(child)
                    cursor = child.end
                _require(cursor == end - 4, "BSON children do not cover object")
                return _Node(kind, key, at, end, tuple(children))
            _require(kind not in (3, 0x20), "Unexpected BSON terminator or pool in object")
            _require(kind not in (0x10, 0x11, 0x14) or length == 12, "Known BSON scalar has incorrect width")
            return _Node(kind, key, at, end)

        root = parse(8, len(data), 0)
        pool_at = root.end
        _require(root.kind == 1 and pool_at + 8 <= len(data), "Missing BSON root/string pool")
        kind, high, low = struct.unpack_from("<BBH", data, pool_at)
        size = low + 65535 * high
        _require(kind == 0x20 and size >= 1 and pool_at + 4 + size + 4 == len(data)
                 and data[-4:] == END, "Invalid BSON string pool boundary")
        result = object.__new__(cls)
        result._source = data
        result._prefix = bytearray(data[:pool_at])
        result._pool_header = data[pool_at:pool_at + 4]
        result._source_pool = data[pool_at + 4:-4]
        result._pool = bytearray(result._source_pool)
        result._root = root
        result._interned = {}
        for node in result._walk(root):
            result._text(node.key)
            if node.kind == 0x14:
                result._text(struct.unpack_from("<I", data, node.start + 8)[0])
        return result

    @classmethod
    def read(cls, path):
        return cls.from_bytes(Path(path).read_bytes())

    @property
    def source_sha256(self):
        return hashlib.sha256(self._source).hexdigest()

    @staticmethod
    def _walk(root):
        stack = [root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children or ()))

    def _text(self, index):
        _require(0 <= index < len(self._pool), "BSON string index exceeds pool")
        end = self._pool.find(0, index)
        _require(end >= index, "Unterminated BSON string")
        try:
            return bytes(self._pool[index:end]).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid UTF-8 in referenced BSON string") from exc

    def _intern(self, value):
        _require(isinstance(value, str) and "\0" not in value, "Expected a string without NUL")
        if not self._interned:
            cursor = 0
            while cursor < len(self._pool):
                end = self._pool.find(0, cursor)
                # Unreferenced opaque pool bytes are retained; skip a malformed
                # UTF-8 segment instead of treating it as a reusable string.
                if end < cursor:
                    break
                try:
                    text = bytes(self._pool[cursor:end]).decode("utf-8")
                except UnicodeDecodeError:
                    pass
                else:
                    self._interned.setdefault(text, cursor)
                cursor = end + 1
        if value not in self._interned:
            encoded = value.encode("utf-8") + b"\0"
            _require(len(self._pool) + len(encoded) <= 65535 * 256 - 1, "BSON string pool exceeds native extended length")
            self._interned[value] = len(self._pool)
            self._pool.extend(encoded)
        return self._interned[value]

    def _get(self, path):
        _require(isinstance(path, (tuple, list)) and all(isinstance(v, str) for v in path), "BSON paths must be string tuples")
        node = self._root
        for key in path:
            found = [child for child in node.children or () if self._text(child.key) == key]
            _require(len(found) == 1, f"Expected one BSON field {key!r}; found {len(found)}")
            node = found[0]
        return node

    def _value(self, node):
        if node.kind == 0x10:
            return struct.unpack_from("<i", self._prefix, node.start + 8)[0]
        if node.kind == 0x11:
            return struct.unpack_from("<f", self._prefix, node.start + 8)[0]
        if node.kind == 0x14:
            return self._text(struct.unpack_from("<I", self._prefix, node.start + 8)[0])
        raise ValueError("Unsupported scalar type; record is preservation-only")

    def value(self, path):
        """Inspect an existing known scalar; this does not authorize edits."""
        return self._value(self._get(path))

    @staticmethod
    def _spec(path):
        if path == ("mf",):
            return "Effect duration", "integer", "frames (30 Hz)", 0x10
        if len(path) == 2 and path[0] == "hf" and re.fullmatch(r"hf[0-7]", path[1]):
            return "Hit " + path[1][2:] + " frame", "integer", "frames (30 Hz)", 0x10
        if len(path) >= 3 and re.fullmatch(r"set\d+", path[0]) and path[1] == "common":
            if len(path) == 4 and path[2] == "se":
                return _AUDIO.get(path[3])
            if len(path) == 4 and re.fullmatch(r"emitter\d+", path[2]) and path[3] == "dn":
                return "Drawable resource", "string", "native basename", 0x14
        return None

    @property
    def fields(self):
        result = []
        stack = [(self._root, ())]
        while stack:
            node, path = stack.pop()
            if node.children is not None:
                stack.extend((child, (*path, self._text(child.key))) for child in reversed(node.children))
            else:
                spec = self._spec(path)
                if spec is not None and node.kind == spec[3]:
                    result.append(EffectField(path, *spec[:1], self._value(node), *spec[1:3]))
        return tuple(result)

    @property
    def sets(self):
        result = []
        for node in self._root.children or ():
            name = self._text(node.key)
            if not re.fullmatch(r"set\d+", name):
                continue
            prefix = (name, "common")
            common = self._get(prefix)

            def optional(*path):
                found = common
                for key in path:
                    matches = [child for child in found.children or () if self._text(child.key) == key]
                    if not matches:
                        return None
                    _require(len(matches) == 1, f"Ambiguous field in {name}: {key}")
                    found = matches[0]
                return self._value(found)

            resources = []
            for child in common.children or ():
                emitter_name = self._text(child.key)
                if re.fullmatch(r"emitter\d+", emitter_name):
                    resource = optional(emitter_name, "dn")
                    if resource is not None:
                        resources.append(resource)
            result.append(EffectSet(name, optional("id"), tuple(resources),
                                    optional("se", "se_name"), optional("se", "se_offset"),
                                    optional("bt", "bt_id"), optional("bt", "bt_pos"),
                                    optional("bt", "bt_rot"), optional("bt", "bt_sca")))
        return tuple(result)

    def _encode_scalar(self, node, value):
        if node.kind == 0x10:
            _require(type(value) is int and -2147483648 <= value <= 2147483647, "Value must fit native int32")
            return struct.pack("<i", value)
        if node.kind == 0x11:
            _require(type(value) in (int, float) and math.isfinite(value), "Value must be a finite float32")
            try:
                return struct.pack("<f", value)
            except (OverflowError, struct.error) as exc:
                raise ValueError("Value exceeds native float32") from exc
        if node.kind == 0x14:
            return struct.pack("<I", self._intern(value))
        raise ValueError("Unsupported scalar type")

    def set_field(self, path, value):
        path = tuple(path)
        spec = self._spec(path)
        _require(spec is not None, "Field semantics are not implemented; record is preservation-only")
        node = self._get(path)
        _require(node.kind == spec[3], "Field does not have its established native scalar type")
        if spec[1] == "boolean":
            _require(type(value) is int and value in (0, 1), "Boolean fields require integer 0 or 1")
        elif spec[1] == "integer":
            lower = -1 if path == ("mf",) else 0
            _require(type(value) is int and lower <= value <= 2147483647, f"Frame must be in [{lower}, 2147483647]")
        elif spec[1] == "float":
            _require(type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value), "Sound gain must be finite in [0, 1]")
        elif spec[2] == "native basename":
            _require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value), "Drawable must be a native resource basename")
            _require(len(value.encode("ascii")) < 32, "Drawable name exceeds the native 31-byte field")
        elif spec[2] == "cue ID":
            _require(isinstance(value, str) and (value == "" or re.fullmatch(r"[0-9]{1,10}", value)), "Sound cue must be empty or a decimal native sound ID")
            _require(value == "" or int(value) <= 2147483647, "Sound ID exceeds native int32")
        encoded = self._encode_scalar(node, value)
        self._prefix[node.start + 8:node.end] = encoded
        return self

    def set_attachment(self, set_name, *, owner_id, position_socket, rotation_socket,
                       scale_socket, available_sockets):
        """Edit an existing inter-set attachment using a verified owner NLST.

        The caller supplies the selected owner's actual socket names. This
        method does not guess caster/target aliases or resolve external owners.
        """
        _require(isinstance(owner_id, str) and owner_id, "Attachment owner must be an existing nonempty effect-set ID")
        sets = self.sets
        selected = [item for item in sets if item.name == set_name]
        owners = [item for item in sets if item.id == owner_id]
        _require(len(selected) == len(owners) == 1, "Attachment needs unique selected and owner sets")
        _require(selected[0].name != owners[0].name, "An effect set cannot attach to itself")
        _require(not isinstance(available_sockets, (str, bytes)), "Socket inventory must be a collection of names")
        available_sockets = set(available_sockets)
        _require(all(isinstance(v, str) and v and "\0" not in v for v in available_sockets), "Socket inventory must contain native names")
        values = {"bt_id": owner_id, "bt_pos": position_socket,
                  "bt_rot": rotation_socket, "bt_sca": scale_socket}
        for key, value in values.items():
            _require(isinstance(value, str) and "\0" not in value, "Invalid attachment string")
            _require(len(value.encode("utf-8")) < 32, "Attachment name exceeds the native 31-byte field")
            if key != "bt_id":
                _require(value == "" or value in available_sockets, f"Socket is absent from selected owner: {value}")
        # Validate the edited owner chain before patching any string/index.
        ids = {}
        for item in sets:
            if item.id:
                _require(item.id not in ids, "Duplicate effect-set IDs prevent attachment edits")
                ids[item.id] = item
        cursor, seen = owners[0], {set_name}
        while cursor:
            _require(cursor.name not in seen, "Cyclic effect-set attachment")
            seen.add(cursor.name)
            if not cursor.attachment_owner:
                break
            _require(cursor.attachment_owner in ids, "External attachment owner cannot be validated")
            cursor = ids[cursor.attachment_owner]
        records = []
        for key, value in values.items():
            node = self._get((set_name, "common", "bt", key))
            _require(node.kind == 0x14, "Attachment fields must be existing native strings")
            records.append((node, value))
        # Stage every patch on an isolated snapshot, including pool growth.
        staged = type(self).from_bytes(self.to_bytes())
        for node, value in records:
            encoded = staged._encode_scalar(node, value)
            staged._prefix[node.start + 8:node.end] = encoded
        self._prefix, self._pool, self._interned = staged._prefix, staged._pool, staged._interned
        return self

    def to_bytes(self):
        header = self._pool_header
        if self._pool != self._source_pool:
            high, low = divmod(len(self._pool), 65535)
            _require(high <= 255, "BSON string pool exceeds native extended length")
            header = struct.pack("<BBH", 0x20, high, low)
        result = bytes(self._prefix) + header + bytes(self._pool) + END
        type(self).from_bytes(result)
        return result

    def write(self, path, *, overwrite=False, expected_sha256=None):
        raw = self.to_bytes()
        atomic_write(path, raw, overwrite=overwrite, expected_sha256=expected_sha256)
        return hashlib.sha256(raw).hexdigest()
