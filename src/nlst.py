from collections import Counter
from pathlib import Path
import zlib


def nlst_rows(text):
    """Compare NLST identities by numeric hash, retaining duplicate draws."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = tuple(part.strip() for part in line.rsplit(',', 3))
        if len(parts) != 4:
            raise ValueError('Unsupported NLST record; model name list requires four columns')
        name, hash_text, parent_text, kind = parts
        name_hash = int(hash_text, 16)
        parent_hash = None if parent_text.lower() == 'null' else int(parent_text, 16)
        if not 0 <= name_hash <= 0xFFFFFFFF or parent_hash is not None and not 0 <= parent_hash <= 0xFFFFFFFF:
            raise ValueError('NLST hash exceeds uint32')
        rows.append((name, name_hash, parent_hash, kind))
    return Counter(rows)


def external_mesh_names(rows):
    """Only an unambiguous mesh spelling with the correct game CRC can label a draw."""
    candidates = {}
    for name, name_hash, parent, kind in rows:
        if (kind == 'mesh' and parent is None and name
                and (zlib.crc32(name.encode('utf8')) ^ 0xFFFFFFFF) == name_hash):
            candidates.setdefault(name_hash, set()).add(name)
    return {key: next(iter(names)) for key, names in candidates.items() if len(names) == 1}


def matches_geom(geom, text):
    rows = nlst_rows(text)
    return rows == nlst_rows(write_nlst(geom, external_mesh_names(rows)))


def import_mesh_names(geom, filepath):
    """Resolve hash-only mesh headers without making a sidecar mandatory."""
    hashes = {mesh.name_hash for mesh in geom.meshes if not mesh.name and mesh.name_hash}
    if not hashes:
        return {}, []
    path = Path(filepath).with_suffix('.nlst')
    try:
        names = external_mesh_names(nlst_rows(path.read_text(encoding='utf-8-sig')))
    except (OSError, ValueError) as exc:
        return {}, [f'Mesh names could not be read from {path.name}: {exc}. Original draw hashes are preserved.']
    missing = hashes.difference(names)
    warnings = []
    if missing:
        warnings.append(f'{path.name} has no unambiguous matching mesh name for {len(missing)} draw hashes; original hashes are preserved.')
    return names, warnings


def write_nlst(geom, mesh_names=None):
    if hasattr(geom, "nlst_text"):
        return geom.nlst_text()

    lines = []
    mesh_names = mesh_names or {}
    optical = {r.name_hash: 'camera' for r in getattr(geom, 'cameras', ())}
    optical.update((r.name_hash, 'light') for r in getattr(geom, 'lights', ()))

    for bone in geom.skeleton.bones:
        if bone.name != "mvglDummy0000":
            parent = f"{bone.parent.name_hash:08x}" if bone.parent else "null"
            kind = "geometry" if bone.is_geometry else optical.get(bone.name_hash, "joint")
            lines.append(f"{bone.name}, {bone.name_hash:08x}, {parent}, {kind}")

    for mesh in geom.meshes:
        name = mesh.name or mesh_names.get(mesh.name_hash, '')
        lines.append(f"{name}, {mesh.name_hash:08x}, null, mesh")

    return "\r\n".join(lines) + "\r\n"
