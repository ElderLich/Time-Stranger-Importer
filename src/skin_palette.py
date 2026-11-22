"""Build triangle draws for the game's signed 8-bit BLENDINDICES input."""
import numpy as np


PALETTE_LIMIT = 128
_ALPHABET = '0123456789abcdefghijklmnopqrstuvwxyz#$[]{}+@=&'


def _skin_word(key):
    if len(key) != 88 or key[18] != '_':
        raise ValueError(f'Invalid native shader key {key!r}')
    word = 0
    for c in key[19:25]:
        word = word * 46 + _ALPHABET.index(c)
    return word


def stock_outline_width(key):
    """Recognize the stock MTR_line01 color pass independently of Blender names."""
    if (not key.startswith('0f+w}200005@0e&k=g_')
            or key[25:] != '0f&jc5000001_0000b60e&k=g0mm8k{_0t=}+x000000000005_2r}tw{000005'):
        return None
    word = _skin_word(key)
    if word & 0x1fffffff or word >> 29 > 4:
        return None
    return word >> 29


def skin_variant(key, influences, *, include_rigid=False):
    """Word 3, bits 29..31 select body/outline VP blend count in native GEOM shader keys."""
    if not 1 <= influences <= 4:
        raise ValueError('Expected one to four skin influences')
    word = _skin_word(key)
    if not include_rigid and word >> 29 not in (1, 2, 3, 4):
        return key  # Empty or unrelated pass.
    value = (word & 0x1fffffff) | (influences << 29)
    encoded = ''
    for _ in range(6):
        value, digit = divmod(value, 46)
        encoded = _ALPHABET[digit] + encoded
    return key[:19] + encoded + key[25:]


def split_skin_palette(mesh, indices=None):
    """Keep triangle order, attributes and weight precision while remapping local bone indices."""
    palette = list(mesh.matrix_palette)
    if len(palette) <= PALETTE_LIMIT:
        if indices is not None:
            mesh.set_index(np.asarray(indices, dtype=np.uint8))
        return [mesh]

    vertices = mesh.vertices
    if indices is None:
        indices = np.asarray([v.index for v in vertices], dtype=np.int32)
    weights = np.asarray([v.weight for v in vertices])
    active = weights != 0
    if indices.shape != weights.shape or np.any(indices[active] < 0) or np.any(indices[active] >= len(palette)):
        raise ValueError(f'{mesh.name}: skin weights reference an invalid palette entry')
    triangles = np.asarray(mesh.get_indices(), dtype=np.int32).reshape(-1, 3)
    vertex_bones = [set(row[mask]) for row, mask in zip(indices, active)]
    output = []

    def add_draw(start, end, used):
        triangle_indices = triangles[start:end]
        originals, remap = np.unique(triangle_indices.ravel(), return_inverse=True)
        bones = sorted(used)
        lookup = np.zeros(len(palette), dtype=np.uint8)
        lookup[bones] = np.arange(len(bones), dtype=np.uint8)
        part = type(mesh)()
        # Stock GEOMs use the same name/hash on subdraws belonging to one mesh.
        part.name = mesh.name
        part.material = mesh.material
        part.primitive = 1
        part.mesh_attributes = mesh.mesh_attributes
        part.bytes_per_vertex = mesh.bytes_per_vertex
        for bit in range(8):
            setattr(part, f'flag_{bit}', getattr(mesh, f'flag_{bit}'))
        # This draw gathers a subset of the old palette in skeleton order.
        part.flag_2 = False
        part.vertices = [vertices[int(i)] for i in originals]
        part.indices = remap.astype(np.int32)
        part.matrix_palette = [palette[i] for i in bones]
        part.set_index(lookup[np.where(active[originals], indices[originals], 0)])
        output.append(part)

    start, used = 0, set()
    for i, (a, b, c) in enumerate(triangles):
        required = vertex_bones[a] | vertex_bones[b] | vertex_bones[c]
        if len(used | required) > PALETTE_LIMIT:
            add_draw(start, i, used)
            start, used = i, set()
        used.update(required)
    if len(triangles):
        add_draw(start, len(triangles), used)
    return output
