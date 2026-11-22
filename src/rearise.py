"""Import the skinned character and embedded motions from a ReArise library GLB."""

import math
import re
import struct
import tempfile
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from . import nc_glb as glb
from .blender_compat import create_nla_track
from .material import copy_native_material
from .rearise_plan import body_atlas, motion_track
from .mesh import set_vertex_layout_override
from .scene_state import purge_new_orphans, rollback, snapshot


def _rows(doc, binary, index):
    accessor = doc['accessors'][index]
    if accessor['componentType'] == 5126:
        return glb._read_float_accessor(doc, binary, index)
    fmt = {5121: 'B', 5123: 'H', 5125: 'I'}[accessor['componentType']]
    count = {'SCALAR': 1, 'VEC3': 3, 'VEC4': 4}[accessor['type']]
    if accessor.get('sparse'):
        raise ValueError('Unsupported sparse integer accessor')
    record = struct.Struct('<' + fmt * count)
    view = doc['bufferViews'][accessor['bufferView']]
    start = view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
    stride = view.get('byteStride', record.size)
    values = [record.unpack_from(binary, start + i * stride) for i in range(accessor['count'])]
    if accessor.get('normalized'):
        maximum = (1 << (8 * struct.calcsize(fmt))) - 1
        values = [tuple(x / maximum for x in row) for row in values]
    return [item[0] for item in values] if count == 1 else values


def _rig_nodes(doc):
    parents = glb._glb_node_parents(doc)
    required = {i for skin in doc['skins'] for i in skin['joints']}
    required.update(c['target']['node'] for a in doc.get('animations', []) for c in a['channels'])

    def ancestry(i):
        chain = [i]
        while chain[-1] in parents:
            chain.append(parents[chain[-1]])
        return chain[::-1]
    paths = [ancestry(i) for i in sorted(required)]
    shared = [level[0] for level in zip(*paths) if len(set(level)) == 1]
    if not shared:
        raise ValueError('Expected one connected ReArise skeleton')
    root = next((i for i in shared if doc['nodes'][i]['name'] == 'GRP_joint'), shared[-1])
    # Preserve unweighted attachment points such as FacePos as well as skin joints.
    indices = []

    def visit(i):
        if 'mesh' not in doc['nodes'][i]:
            indices.append(i)
            for child in doc['nodes'][i].get('children', ()):
                visit(child)
    visit(root)
    if not required.issubset(indices):
        raise ValueError('A captured animation targets a mesh outside the character rig')
    names = [doc['nodes'][i]['name'] for i in indices]
    if len(set(names)) != len(names):
        raise ValueError('Ambiguous joint names in ReArise skeleton')
    return indices, parents


def _build_rig(context, collection, nodes, indices, parents, rest):
    arm = bpy.data.armatures.new('Skeleton')
    rig = bpy.data.objects.new('Geom', arm)
    collection.objects.link(rig)
    glb._select_only(context, [rig], rig)
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        for i in indices:
            bone = arm.edit_bones.new(nodes[i]['name'])
            parent = parents.get(i)
            if parent in indices:
                bone.parent = arm.edit_bones[nodes[parent]['name']]
            bone.tail = (0, 0.1, 0)
            loc, rot, _ = rest[i].decompose()
            bone.matrix = Matrix.LocRotScale(loc, rot, None)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    rig.animation_data_create()
    return rig


def _mesh(context, collection, rig, doc, binary, primitive, skin, transform, material, donor, atlas):
    if primitive.get('mode', 4) != 4 or primitive.get('targets'):
        raise ValueError('Expected skinned triangles without morph targets')
    attrs = {name: _rows(doc, binary, primitive['attributes'][name])
             for name in ('POSITION', 'NORMAL', 'TEXCOORD_0', 'JOINTS_0', 'WEIGHTS_0')}
    indices = _rows(doc, binary, primitive['indices'])
    used = sorted(set(indices))
    remap = {index: i for i, index in enumerate(used)}
    triangles = [tuple(remap[i] for i in indices[j:j + 3]) for j in range(0, len(indices), 3)]
    name = doc['materials'][primitive['material']]['name']
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([transform @ Vector(attrs['POSITION'][i]) for i in used], [], triangles)
    mesh.update()
    normal_matrix = transform.to_3x3().inverted().transposed()
    mesh.normals_split_custom_set_from_vertices(
        [(normal_matrix @ Vector(attrs['NORMAL'][i])).normalized() for i in used])
    uv = mesh.uv_layers.new(name='uv1')
    for loop in mesh.loops:
        u, v = attrs['TEXCOORD_0'][used[loop.vertex_index]]
        uv.data[loop.index].uv = (u, 1 - v)
    if 'COLOR_0' in primitive['attributes']:
        tint = _rows(doc, binary, primitive['attributes']['COLOR_0'])
        colors = mesh.color_attributes.new(name='Color', type='FLOAT_COLOR', domain='POINT')
        for dest, i in enumerate(used):
            rgba = list(tint[i])
            colors.data[dest].color = rgba if len(rgba) == 4 else [*rgba, 1.]
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.parent = rig
    obj.modifiers.new('Armature', 'ARMATURE').object = rig
    groups = [obj.vertex_groups.new(name=doc['nodes'][j]['name']) for j in skin['joints']]
    for dest, i in enumerate(used):
        for joint, weight in zip(attrs['JOINTS_0'][i], attrs['WEIGHTS_0'][i]):
            if weight > 0:
                groups[joint].add([dest], weight, 'REPLACE')
    mesh.materials.append(material)
    set_vertex_layout_override(mesh, material, donor['layout_override'])
    for key, value in donor['flags'].items():
        mesh[key] = value
    obj['DSTS_nc_atlas'] = atlas
    return obj


def _bind_diffuse(material, doc, binary, descriptor):
    from .operators.materials import _iter_material_texture_nodes, _texture_uniform_from_node
    texture = descriptor['pbrMetallicRoughness']['baseColorTexture']['index']
    image = doc['images'][doc['textures'][texture]['source']]
    view = doc['bufferViews'][image['bufferView']]
    offset = view.get('byteOffset', 0)
    suffix = {'image/png': '.png', 'image/jpeg': '.jpg'}[image['mimeType']]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ('source' + suffix)
        path.write_bytes(binary[offset:offset + view['byteLength']])
        imported = bpy.data.images.load(str(path), check_existing=False)
        imported.name = Path(image['name']).stem + suffix
        imported.pack()
    targets = [n for n in _iter_material_texture_nodes(material)
               if _texture_uniform_from_node(n) in ('ColorSampler', 'DiffuseColor')]
    if not targets:
        raise ValueError(f'{material.name} needs a native body color sampler')
    for target in targets:
        target.image = imported


def _actions(doc, binary, indices, parents, root_parent, rest, rig, code):
    nodes = doc['nodes']
    offsets = {i: rest[i].inverted() @ rig.data.bones[nodes[i]['name']].matrix_local for i in indices}
    static = {i: glb._node_local_matrix(nodes[i]) for i in indices}
    for clip in doc.get('animations', []):
        if not clip.get('channels'):
            continue
        channels = []
        for ch in clip['channels']:
            target = ch['target']
            path = target['path']
            if path not in ('translation', 'scale', 'rotation'):
                raise ValueError(f'Unsupported ReArise animation channel {path}')
            s = clip['samplers'][ch['sampler']]
            if s.get('interpolation', 'LINEAR') not in ('LINEAR', 'STEP', 'CUBICSPLINE'):
                raise ValueError(f"Unsupported interpolation in {clip['name']}")
            channels.append((target['node'], path, _rows(doc, binary, s['input']),
                             _rows(doc, binary, s['output']), s.get('interpolation', 'LINEAR')))
        end = round(30 * max(times[-1] for _, _, times, _, _ in channels))
        frames = {}
        for frame in range(end + 1):
            changes = {}
            for i, path, times, values, interpolation in channels:
                value = glb._sample_gltf_track(times, values, interpolation, path, frame / 30)
                changes.setdefault(i, {})[path] = value
            worlds = {}
            for i in indices:
                local = glb._node_local_matrix({**nodes[i], **changes[i]}) if i in changes else static[i]
                worlds[i] = worlds.get(parents.get(i), root_parent) @ local
            frames[frame] = {nodes[i]['name']: worlds[i] @ offsets[i] for i in indices}
        action = bpy.data.actions.new(clip['name'])
        action['DSTS_source_clip'] = clip['name']
        glb._bake_source_world_poses(bpy.context, rig, {action: (None, frames)})
        # ReArise shares clips across species, with both "_rd" and bare slot names.
        create_nla_track(rig, action, motion_track(code, clip['name']), 1, blend_type='REPLACE')


def _skin_bind_transforms(doc, binary, rest):
    """Find one native bind pose and one mesh-to-bind transform per glTF skin."""
    assigned = set()
    transforms = {}
    for index in dict.fromkeys(node['skin'] for node in doc['nodes'] if 'mesh' in node and 'skin' in node):
        skin = doc['skins'][index]
        matrices = {i: Matrix([row[k:k+4] for k in range(0, 16, 4)]).transposed()
                    for i, row in zip(skin['joints'], _rows(doc, binary, skin['inverseBindMatrices']), strict=True)}
        anchor = next((i for i in matrices if i in assigned), skin['joints'][0])
        transform = rest[anchor] @ matrices[anchor]
        for i, inverse_bind in matrices.items():
            current = rest[i] @ inverse_bind
            if max(abs(a-b) for r1,r2 in zip(transform, current) for a,b in zip(r1,r2)) > .002:
                if i in assigned:
                    raise ValueError('Shared joint has incompatible bind poses across ReArise skins')
                # The default scene can already be posed. glTF skinning applies
                # jointWorld(t) * inverseBind * vertex even at the first frame.
                # Solving for rest preserves that deformation in every motion.
                rest[i] = transform @ inverse_bind.inverted()
        assigned.update(matrices)
        transforms[index] = transform
    return transforms


def import_model(context, path, donor_path, scale=1.0, outline=True, *, outline_exclusions=()):
    doc, binary = glb.read_glb_chunks(str(path))
    indices, parents = _rig_nodes(doc)
    nodes = doc['nodes']
    worlds = glb._glb_node_worlds(doc, range(len(nodes)))
    conversion = Matrix.Rotation(math.pi / 2, 4, 'X') @ Matrix.Scale(scale, 4)
    rest = {i: conversion @ worlds[i] for i in indices}
    skin_transforms = _skin_bind_transforms(doc, binary, rest)
    root_parent = conversion @ worlds.get(parents.get(indices[0]), Matrix.Identity(4))
    before = snapshot()
    scene = context.scene
    fps, base = scene.render.fps, scene.render.fps_base
    selected, active = list(context.selected_objects), context.view_layer.objects.active
    try:
        scene.render.fps, scene.render.fps_base = 30, 1
        collection, body, line = glb._import_donor(context, str(donor_path))
        donors = [info for info in body if info['material'] is not None]
        if not donors:
            raise ValueError('Choose a native donor with a body material')
        donor = next((info for info in donors if re.search(r'_b01(?:\.\d+)?$', info['material'].name)), donors[0])
        code = doc['asset']['extras']['nc']['model_id']
        collection.name = code
        collection['DSTS_material_donor_geom'] = str(donor_path)
        collection['DSTS_source_rearise_glb'] = str(path)
        if 'DSTS_bind_pose_snapshot' in collection:
            del collection['DSTS_bind_pose_snapshot']
        rig = _build_rig(context, collection, nodes, indices, parents, rest)
        materials, objects = {}, []
        for node in nodes:
            if 'mesh' not in node:
                continue
            if 'skin' not in node:
                raise ValueError('Separate rigid ReArise draw requires an attachment binding')
            skin = doc['skins'][node['skin']]
            transform = skin_transforms[node['skin']]
            for p in doc['meshes'][node['mesh']]['primitives']:
                mat_id = p['material']
                descriptor = doc['materials'][mat_id]
                name = descriptor['name']
                atlas = body_atlas(descriptor)
                if not atlas:
                    raise ValueError(f'{name} needs an explicit native material mapping')
                if mat_id not in materials:
                    mat = copy_native_material(donor['material'], name)
                    mat['DSTS_nc_atlas'] = atlas
                    _bind_diffuse(mat, doc, binary, descriptor)
                    materials[mat_id] = mat
                obj = _mesh(context, collection, rig, doc, binary, p, skin, transform,
                            materials[mat_id], donor, atlas)
                objects.append(obj)
        if not objects:
            raise ValueError('ReArise GLB contains no body draws')
        if outline and line:
            solid = [o for o in objects if o.data.materials[0].name not in outline_exclusions]
            if solid:
                objects.append(glb._build_outline_mesh(context, solid, rig, line, collection))
        for obj in objects:
            glb._match_skin_programs(obj, rig, str(donor_path))
        _actions(doc, binary, indices, parents, root_parent, rest, rig, code)
        for info in donors + ([line] if line and line['material'] else []):
            info['material'].use_fake_user = False
        purge_new_orphans(before)
        glb._select_only(context, [rig], rig)
        return collection, rig
    except Exception:
        rollback(before)
        scene.render.fps, scene.render.fps_base = fps, base
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
        raise
