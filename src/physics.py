"""Native PHYS v2 collision documents with bounded, existing-record edits.

Layout evidence: Time Stranger loader 0x1403B7420 and shape constructors
0x1403A9420 / 0x1403A99E0. No inferred GEOM collision representation.
Unknown bytes are retained exactly; topology allocation/rebuilding is rejected.
"""
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import struct

try:
    from .spark import atomic_write
except ImportError:  # Standalone codec consumers; no Blender package import.
    from spark import atomic_write


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _index(value, count, label):
    _require(type(value) is int and 0 <= value < count, f"Invalid {label} index")
    return value


def _floats(value, count, label, nonnegative=False):
    _require(not isinstance(value, (str, bytes)), f"Invalid {label}")
    try:
        values = tuple(float(x) for x in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    _require(len(values) == count and all(math.isfinite(x) for x in values), f"Invalid {label}")
    _require(not nonnegative or all(x >= 0 for x in values), f"Negative {label}")
    try:
        packed = struct.pack('<' + 'f' * count, *values)
    except (OverflowError, struct.error) as exc:
        raise ValueError(f"Float32 overflow in {label}") from exc
    return packed


@dataclass(frozen=True)
class Body:
    position: tuple
    rotation_xyzw: tuple
    mass: float
    shape_index: int
    name: str


@dataclass(frozen=True)
class Box:
    half_extents: tuple
    table_a_index: int
    table_b_index: int
    kind: str = 'box'


@dataclass(frozen=True)
class Sphere:
    radius: float
    table_a_index: int
    table_b_index: int
    kind: str = 'sphere'


@dataclass(frozen=True)
class TriangleMesh:
    vertices: tuple
    triangles: tuple
    table_a_indices: tuple
    table_b_indices: tuple
    kind: str = 'triangle_mesh'


class PhysicsDocument:
    """Immutable views over a source snapshot plus explicitly scoped patches."""

    def __init__(self):
        raise TypeError('Use PhysicsDocument.from_bytes() or read()')

    @classmethod
    def read(cls, path):
        return cls.from_bytes(Path(path).read_bytes())

    @property
    def source_sha256(self):
        return hashlib.sha256(self._source).hexdigest()

    @classmethod
    def from_bytes(cls, data):
        data = bytes(data)
        _require(len(data) >= 56 and data[:8] == b'SYHP\x02\0\0\0', 'Expected native PHYS version 2')
        result = object.__new__(cls)
        result._source = data
        result._data = data
        result._parse()
        return result

    def _parse(self):
        data = self._data
        _, _, body_count, shape_count, a_count, b_count, bodies_at, shapes_at, a_at, b_at = struct.unpack_from('<6I4Q', data)
        # The loader allocates body_count but constructs shape_count bodies.
        _require(body_count == shape_count, 'Native PHYS body/shape counts must agree')
        regions = [(0, 56, 'header')]

        def region(at, count, stride, role):
            size = count * stride
            _require(at >= 56 and at <= len(data) and size <= len(data) - at, f'{role} exceeds file')
            if size:
                regions.append((at, at + size, role))
            return at

        def text_at(at, width):
            raw = data[at:at + width]
            # Shipped body names can fill all 24 bytes without a terminator.
            # Decode only the fixed field; never scan into the next record.
            try:
                return raw.split(b'\0', 1)[0].decode('utf8')
            except UnicodeError as exc:
                raise ValueError('Invalid UTF-8 PHYS name') from exc

        def numbers(at, count, label, nonnegative=False):
            values = struct.unpack_from('<' + 'f' * count, data, at)
            _floats(values, count, label, nonnegative)
            return values

        region(bodies_at, body_count, 76, 'bodies')
        region(shapes_at, shape_count, 8, 'shape pointers')
        region(a_at, a_count, 64, 'name table A')
        region(b_at, b_count, 64, 'name table B')
        self.table_a = tuple(text_at(a_at + 64*i, 64) for i in range(a_count))
        self.table_b = tuple(text_at(b_at + 64*i, 64) for i in range(b_count))
        bodies = []
        for i in range(body_count):
            at = bodies_at + 76*i
            position = numbers(at, 3, 'position')
            rotation = numbers(at + 12, 4, 'rotation')
            _require(sum(x*x for x in rotation) > 1e-20, 'Zero PHYS quaternion')
            mass = numbers(at + 40, 1, 'mass', True)[0]
            shape = struct.unpack_from('<I', data, at + 44)[0]
            _index(shape, shape_count, 'body shape')
            bodies.append(Body(position, rotation, mass, shape, text_at(at + 52, 24)))
        shapes, layouts = [], []
        for i in range(shape_count):
            at = struct.unpack_from('<Q', data, shapes_at + 8*i)[0]
            _require(56 <= at <= len(data) - 8, 'Shape header exceeds file')
            kind = struct.unpack_from('<I', data, at)[0]
            _require(kind in (0, 1, 2), f'Unsupported PHYS shape kind {kind}')
            if kind in (0, 1):
                width, name_at = (24, 20) if kind == 0 else (16, 12)
                region(at, 1, width, 'box' if kind == 0 else 'sphere')
                a, b = struct.unpack_from('<2H', data, at + name_at)
                _index(a, a_count, 'table A'); _index(b, b_count, 'table B')
                if kind == 0:
                    shape = Box(numbers(at + 8, 3, 'box half extents', True), a, b)
                else:
                    shape = Sphere(numbers(at + 8, 1, 'sphere radius', True)[0], a, b)
                layout = (at, at + name_at, None, None)
            else:
                region(at, 1, 72, 'mesh header')
                vertex_count, triangle_count = struct.unpack_from('<2I', data, at + 8)
                _require(vertex_count > 0 and triangle_count > 0, 'Empty triangle mesh is unsupported')
                triangle_at, vertex_at, ai_at, bi_at = struct.unpack_from('<4Q', data, at + 40)
                region(vertex_at, vertex_count, 12, 'vertices')
                region(triangle_at, triangle_count, 12, 'triangles')
                region(ai_at, triangle_count, 2, 'table A indices')
                region(bi_at, triangle_count, 2, 'table B indices')
                vertices = tuple(struct.iter_unpack('<3f', data[vertex_at:vertex_at + 12*vertex_count]))
                _require(all(math.isfinite(x) for v in vertices for x in v), 'Nonfinite mesh vertex')
                triangles = tuple(struct.iter_unpack('<3I', data[triangle_at:triangle_at + 12*triangle_count]))
                _require(all(x < vertex_count for v in triangles for x in v), 'Triangle vertex index exceeds mesh')
                ai = tuple(x[0] for x in struct.iter_unpack('<H', data[ai_at:ai_at + 2*triangle_count]))
                bi = tuple(x[0] for x in struct.iter_unpack('<H', data[bi_at:bi_at + 2*triangle_count]))
                _require(all(x < a_count for x in ai) and all(x < b_count for x in bi), 'Mesh name-table index exceeds table')
                shape = TriangleMesh(vertices, triangles, ai, bi)
                layout = (at, ai_at, bi_at, (vertex_at, triangle_at))
            shapes.append(shape); layouts.append(layout)
        # Aliases/overlap could make a scoped edit alter a different record.
        ordered = sorted(regions)
        for prev, cur in zip(ordered, ordered[1:]):
            _require(prev[1] <= cur[0], f'Overlapping PHYS regions: {prev[2]} / {cur[2]}')
        self.bodies = tuple(bodies)
        self.shapes = tuple(shapes)
        self._bodies_at = bodies_at
        self._layouts = tuple(layouts)
        self._table_offsets = (a_at, b_at)

    def _apply(self, patches):
        updated = bytearray(self._data)
        for at, value in patches:
            updated[at:at + len(value)] = value
        candidate = type(self).from_bytes(updated)
        source = self._source
        self.__dict__.update(candidate.__dict__)
        self._source = source

    def set_body(self, index, *, position=None, rotation_xyzw=None, mass=None, shape_index=None, name=None):
        _index(index, len(self.bodies), 'body')
        at = self._bodies_at + 76*index
        patches = []
        for value, offset, count, label in ((position, 0, 3, 'position'), (rotation_xyzw, 12, 4, 'rotation')):
            if value is not None:
                patches.append((at + offset, _floats(value, count, label)))
        if mass is not None:
            patches.append((at + 40, _floats((mass,), 1, 'mass', True)))
        if shape_index is not None:
            patches.append((at + 44, struct.pack('<I', _index(shape_index, len(self.shapes), 'body shape'))))
        if name is not None:
            patches.append((at + 52, self._name_bytes(name, 24)))
        self._apply(patches)

    @staticmethod
    def _name_bytes(name, width):
        _require(isinstance(name, str) and '\0' not in name, 'Invalid PHYS name')
        encoded = name.encode('utf8')
        _require(len(encoded) < width, f'PHYS name must fit in {width - 1} UTF-8 bytes')
        return encoded + bytes(width - len(encoded))

    def set_table_name(self, table, index, name):
        _require(table in ('a', 'b'), 'Expected table a or b')
        t = 0 if table == 'a' else 1
        _index(index, len(self.table_a if t == 0 else self.table_b), 'name table')
        self._apply([(self._table_offsets[t] + 64*index, self._name_bytes(name, 64))])

    def set_box(self, index, *, half_extents):
        _index(index, len(self.shapes), 'shape')
        _require(isinstance(self.shapes[index], Box), 'Selected shape is not a box')
        self._apply([(self._layouts[index][0] + 8, _floats(half_extents, 3, 'box half extents', True))])

    def set_sphere(self, index, *, radius):
        _index(index, len(self.shapes), 'shape')
        _require(isinstance(self.shapes[index], Sphere), 'Selected shape is not a sphere')
        self._apply([(self._layouts[index][0] + 8, _floats((radius,), 1, 'sphere radius', True))])

    def _mesh(self, index):
        _index(index, len(self.shapes), 'shape')
        _require(isinstance(self.shapes[index], TriangleMesh), 'Selected shape is not a triangle mesh')
        return self.shapes[index]

    def set_vertex(self, shape_index, vertex_index, position):
        mesh = self._mesh(shape_index)
        _index(vertex_index, len(mesh.vertices), 'vertex')
        self._apply([(self._layouts[shape_index][3][0] + 12*vertex_index, _floats(position, 3, 'vertex'))])

    def set_mesh(self, index, *, vertices=None, triangles=None, table_a_indices=None, table_b_indices=None):
        """Apply complete existing arrays together, validating/reparsing once.

        Omitted arrays and bytes outside those arrays are unchanged. Array
        lengths cannot change. Use this for Blender Edit Mode exports rather
        than repeatedly reparsing a large document for individual vertices.
        """
        mesh = self._mesh(index)
        layout = self._layouts[index]
        patches = []
        if vertices is not None:
            vertices = tuple(vertices)
            _require(len(vertices) == len(mesh.vertices), 'Vertex count changes are unsupported')
            encoded = b''.join(_floats(v, 3, 'vertex') for v in vertices)
            patches.append((layout[3][0], encoded))
        if triangles is not None:
            triangles = tuple(tuple(t) for t in triangles)
            _require(len(triangles) == len(mesh.triangles), 'Triangle count changes are unsupported')
            for triangle in triangles:
                _require(len(triangle) == 3, 'Expected three triangle vertex indices')
                for vertex in triangle:
                    _index(vertex, len(mesh.vertices), 'vertex')
            patches.append((layout[3][1], b''.join(struct.pack('<3I', *t) for t in triangles)))
        for values, count, at in ((table_a_indices, len(self.table_a), layout[1]),
                                  (table_b_indices, len(self.table_b), layout[2])):
            if values is not None:
                values = tuple(values)
                _require(len(values) == len(mesh.triangles), 'Association count changes are unsupported')
                for value in values:
                    _index(value, min(count, 65536), 'name table')
                patches.append((at, b''.join(struct.pack('<H', v) for v in values)))
        self._apply(patches)

    def set_triangle(self, shape_index, triangle_index, vertices):
        mesh = self._mesh(shape_index)
        _index(triangle_index, len(mesh.triangles), 'triangle')
        vertices = tuple(vertices)
        _require(len(vertices) == 3, 'Expected three triangle vertex indices')
        for vertex in vertices:
            _index(vertex, len(mesh.vertices), 'vertex')
        self._apply([(self._layouts[shape_index][3][1] + 12*triangle_index, struct.pack('<3I', *vertices))])

    def set_shape_associations(self, index, *, table_a_index=None, table_b_index=None, triangle_index=None):
        _index(index, len(self.shapes), 'shape')
        shape = self.shapes[index]
        layout = self._layouts[index]
        if isinstance(shape, TriangleMesh):
            _index(triangle_index, len(shape.triangles), 'triangle')
            offsets = (layout[1] + 2*triangle_index, layout[2] + 2*triangle_index)
        else:
            _require(triangle_index is None, 'Primitive has no triangle index')
            offsets = (layout[1], layout[1] + 2)
        patches = []
        for value, offset, table in ((table_a_index, offsets[0], self.table_a), (table_b_index, offsets[1], self.table_b)):
            if value is not None:
                patches.append((offset, struct.pack('<H', _index(value, min(len(table), 65536), 'name table'))))
        self._apply(patches)

    def to_bytes(self):
        return self._data

    def write(self, path, *, overwrite=False, expected_sha256=None):
        raw = self.to_bytes()
        type(self).from_bytes(raw)
        atomic_write(path, raw, overwrite=overwrite, expected_sha256=expected_sha256)
        return hashlib.sha256(raw).hexdigest()
