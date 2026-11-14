"""PHYS body/shape edits through Blender, with exact source retention checks."""
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import tempfile

import bpy
from mathutils import Quaternion

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', ROOT / '__init__.py',
                                            submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.physics import PhysicsDocument
from Time_Stranger_Importer.src.native_collision import (
    import_collision, document_from_collection, export_collision, refresh_primitive)


def fixture():
    data = bytearray(744)
    struct.pack_into('<6I4Q', data, 0, 0x50485953, 2, 3, 3, 2, 2, 56, 288, 480, 608)
    for i in range(3):
        at = 56 + i * 76
        struct.pack_into('<3f4f4f2I', data, at, 1, 2, 3, 0, 0, 0, 1, .2, .3, .6, 0, i, 1)
        data[at+52:at+59] = b'body_%d\0' % i
    data[284:288] = b'KEEP'
    struct.pack_into('<3Q', data, 288, 312, 336, 352)
    struct.pack_into('<II3f2H', data, 312, 0, 0xABCD, 2, 3, 4, 0, 1)
    struct.pack_into('<IIf2H', data, 336, 1, 0xABCD, 5, 1, 0)
    struct.pack_into('<4I6f4Q', data, 352, 2, 0xABCD, 3, 1, *([17]*6), 424, 436, 472, 474)
    struct.pack_into('<3I', data, 424, 0, 1, 2)
    struct.pack_into('<9f', data, 436, 0,0,0, 1,0,0, 0,1,0)
    struct.pack_into('<2H', data, 472, 0, 1)
    for at, name in ((480,b'floor'),(544,b'wall'),(608,b'object_0'),(672,b'object_1')):
        data[at:at+len(name)] = name
    data[736:] = b'opaque!!'
    return bytes(data)


checks = []
with tempfile.TemporaryDirectory(prefix='dsts_collision_') as folder:
    folder = Path(folder)
    path = folder/'source.phys'
    raw = fixture()
    path.write_bytes(raw)
    collection = import_collision(bpy.context, path)
    assert document_from_collection(collection).to_bytes() == raw
    objects = {obj['DSTS_phys_body_index']: obj for obj in collection.objects}
    box, sphere, mesh = (objects[i] for i in range(3))
    assert tuple(box.location) == (1,-3,2)
    box.location.z += .5
    box.rotation_quaternion = Quaternion((0, 0, 1), math.pi / 2)
    box['DSTS_phys_mass'] = 2.0
    box.data['DSTS_phys_half_extents'] = [3.,3.,4.]
    refresh_primitive(box.data)
    sphere.data['DSTS_phys_radius'] = 6.0
    refresh_primitive(sphere.data)
    mesh.data.vertices[1].co.x += .5
    bpy.context.view_layer.objects.active = mesh
    mesh.select_set(True)
    mesh.data.polygons[0].select = True
    assert bpy.ops.dsts.collision_associations(table_a_index=1, table_b_index=0) == {'FINISHED'}
    name = collection.name
    blend = folder/'collision.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    collection = bpy.data.collections[name]
    output = folder/'edited.phys'
    export_collision(collection, output)
    doc = PhysicsDocument.read(output)
    assert doc.bodies[0].position == (1,2.5,3) and doc.bodies[0].mass == 2
    # Blender +Z rotation is native +Y under the established basis.
    expected_rotation = (0, math.sqrt(.5), 0, math.sqrt(.5))
    assert all(abs(a-b) < 1e-5 for a,b in zip(doc.bodies[0].rotation_xyzw, expected_rotation))
    assert doc.shapes[0].half_extents == (3,3,4)
    assert doc.shapes[1].radius == 6
    assert doc.shapes[2].vertices[1] == (1.5,0,0)
    assert doc.shapes[2].table_a_indices == (1,) and doc.shapes[2].table_b_indices == (0,)
    assert b'opaque!!' in output.read_bytes() and output.read_bytes()[284:288] == b'KEEP'
    allowed = set(range(56,84)) | set(range(96,100)) | set(range(320,332)) | set(range(344,348)) | set(range(448,460)) | set(range(472,476))
    assert all(i in allowed for i,(a,b) in enumerate(zip(raw, output.read_bytes())) if a != b)
    checks.append('Native basis, body position/rotation/mass, box/sphere, mesh vertex and face associations save/reopen/export with scoped bytes')
    # A successful same-source save establishes the next cooperative guard.
    export_collision(collection, path, overwrite=True)
    export_collision(collection, path, overwrite=True)
    path.write_bytes(b'external modification')
    try:
        export_collision(collection, path, overwrite=True)
        raise AssertionError('Stale original accepted')
    except ValueError:
        pass
    assert path.read_bytes() == b'external modification'
    objects = {obj['DSTS_phys_body_index']: obj for obj in collection.objects}
    box, sphere, mesh = (objects[i] for i in range(3))
    mesh.shape_key_add(name='Basis')
    try:
        document_from_collection(collection)
        raise AssertionError('Unsupported shape keys accepted')
    except ValueError:
        pass
    mesh.shape_key_clear()
    box.scale.x = 2
    try:
        document_from_collection(collection)
        raise AssertionError('Unsupported body scale accepted')
    except ValueError:
        pass
    box.scale.x = 1
    checks.append('Repeated own saves work; external changes, shape keys and body scale reject safely')
    native_root = Path(r'D:\Digimon Modding\Time Stranger Extracted\app_0.dx11')
    for filename in ('d0101p.phys', 'd0101c.phys'):
        source = native_root/filename
        if not source.exists():
            continue
        imported = import_collision(bpy.context, source)
        assert document_from_collection(imported).to_bytes() == source.read_bytes()
        obj = next(iter(imported.objects))
        obj.location.x += .125
        destination = folder/filename
        export_collision(imported, destination)
        edited = PhysicsDocument.read(destination)
        original = PhysicsDocument.read(source)
        assert abs(edited.bodies[obj['DSTS_phys_body_index']].position[0] - original.bodies[obj['DSTS_phys_body_index']].position[0] - .125) < 1e-5
        checks.append('Actual native '+filename+' exact import and rigid edit/export/reimport')

addon.unregister()
addon.register()
addon.unregister()
result = {'blender': bpy.app.version_string, 'checks': checks, 'runtime_acceptance': 'pending; no live game asset changed'}
path = ROOT/'build'/f'collision-workflows-{bpy.app.version[0]}{bpy.app.version[1]}.json'
path.write_text(json.dumps(result, indent=2)+'\n')
print('PASS', result)
