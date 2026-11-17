"""Retain authored culling bounds when the imported spatial data is unchanged."""
import hashlib
import json
import struct


def spatial_signature(collection):
    digest = hashlib.sha256()
    for obj in collection.objects:
        if obj.type != 'MESH':
            continue
        digest.update(struct.pack('<I?', len(obj.data.vertices), obj.data.get('DSTS_flag_2', False)))
        digest.update(json.dumps([g.name for g in obj.vertex_groups]).encode())
        for vertex in obj.data.vertices:
            digest.update(struct.pack('<3fI', *vertex.co, len(vertex.groups)))
            for group in vertex.groups:
                digest.update(struct.pack('<If', group.group, group.weight))
    return digest.hexdigest()


def preserve_bounds(collection, geom):
    bounds = getattr(geom, 'authored_bounds', [])
    if bounds:
        collection['DSTS_authored_bounds'] = json.dumps(bounds)
        collection['DSTS_bounds_signature'] = spatial_signature(collection)


def restore_bounds(collection, geom):
    if ('DSTS_authored_bounds' in collection and geom.bind_pose_snapshot_matches and
            spatial_signature(collection) == collection.get('DSTS_bounds_signature')):
        bounds = json.loads(collection['DSTS_authored_bounds'])
        # Palette splitting changes the draw list and needs fresh bounds.
        if len(bounds) == len(geom.meshes) + 1:
            geom.authored_bounds = bounds
