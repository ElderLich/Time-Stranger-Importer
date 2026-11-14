"""Actual light-record edit/save/reopen/scoped-export and failure checks."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', ROOT/'__init__.py',
                                            submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src import native_lights as lights
from Time_Stranger_Importer.src import spark

if not hasattr(bpy.types.Object, 'dsts_native_light'):
    for cls in lights.CLASSES:
        bpy.utils.register_class(cls)
    lights.register_properties()

parser = argparse.ArgumentParser()
parser.add_argument('--native-root', type=Path, required=True)
parser.add_argument('--report', type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])


def rejects(call, text):
    try:
        call()
    except Exception as exc:
        assert text in str(exc), (text, str(exc))
    else:
        raise AssertionError('Expected rejection: '+text)


def state():
    return len(bpy.data.objects), len(bpy.data.lights), len(bpy.data.collections)


rows = []
with tempfile.TemporaryDirectory(prefix='native-light-editor-') as folder:
    folder = Path(folder)
    collections = []
    for relative in ('app_0.dx11/d0101l.geom', 'app_0.dx11/d0101sun.geom',
                     'addcont_01.dx11/ef_b_chr689_bs01.geom'):
        path = args.native_root/relative
        raw = path.read_bytes()
        assert bpy.ops.import_scene.dsts_native_lights(filepath=str(path)) == {'FINISHED'}
        collection = lights.collection_for(bpy.context.object)
        assert lights.document_from_collection(collection).raw == raw
        geom, offset = lights._read(raw)
        allowed = set()
        for obj in collection.objects:
            index = obj['DSTS_light_index']
            source = geom.lights[index]
            if not source.supports_optical_editing:
                continue
            obj.dsts_native_light.color = (source.color_rgb[0]+.125, *source.color_rgb[1:])
            obj.dsts_native_light.intensity = source.intensity+.25
            allowed.update(range(offset+240*index+8, offset+240*index+12))
            allowed.update(range(offset+240*index+40, offset+240*index+52))
            if source.native_type == 0:
                obj.dsts_native_light.range = source.range+.5
                allowed.update(range(offset+240*index+12, offset+240*index+16))
        edited = lights.document_from_collection(collection).raw
        assert edited != raw
        differences = {i for i, (a, b) in enumerate(zip(raw, edited)) if a != b}
        assert len(edited) == len(raw) and differences <= allowed
        target = folder/(path.stem+'-光.geom')
        lights.export_lights(collection, target)
        assert target.read_bytes() == edited
        rejects(lambda: lights.export_lights(collection, target), 'Destination exists')
        lights.export_lights(collection, target, overwrite=True)
        assert path.read_bytes() == raw
        obj = collection.objects[0]
        obj.location.x += 1
        rejects(lambda: lights.document_from_collection(collection), 'placement/animation')
        obj.location.x -= 1
        # Restore precisely, including large-coordinate float32 roundoff.
        from mathutils import Matrix
        values = json.loads(obj['DSTS_light_matrix'])
        obj.matrix_basis = Matrix([values[i:i+4] for i in range(0, 16, 4)])
        collections.append((collection.name, edited, target))
        rows.append(dict(source=str(path), records=len(geom.lights),
            points=sum(o.type == 'LIGHT' for o in collection.objects), changed_bytes=len(differences),
            unrelated_bytes_preserved=True, source_sha256=hashlib.sha256(raw).hexdigest()))
    blend = folder/'light-edits.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    for name, expected, target in collections:
        collection = bpy.data.collections[name]
        assert lights.document_from_collection(collection).raw == expected
        lights.export_lights(collection, target, overwrite=True)
    collection = bpy.data.collections[collections[0][0]]
    target = collections[0][2]
    point = next(obj for obj in collection.objects if obj.type == 'LIGHT')
    energy = point.data.energy
    native_intensity = point.dsts_native_light.intensity
    point.data.energy += 1
    rejects(lambda: lights.document_from_collection(collection), 'preview-only')
    point.dsts_native_light.intensity += 1
    assert point.get('DSTS_light_preview_error')
    rejects(lambda: lights.document_from_collection(collection), 'preview-only')
    point.data.energy = energy
    point.dsts_native_light.intensity = native_intensity
    assert not point.get('DSTS_light_preview_error')
    point.dsts_native_light.intensity = -1
    rejects(lambda: lights.document_from_collection(collection), 'nonnegative')
    point.dsts_native_light.intensity = native_intensity
    assert lights.document_from_collection(collection).raw == collections[0][1]
    with patch.object(spark.os, 'replace', side_effect=OSError('injected publication failure')):
        rejects(lambda: lights.export_lights(collection, target, overwrite=True), 'publication failure')
    assert target.read_bytes() == collections[0][1]
    target.write_bytes(b'edited elsewhere')
    rejects(lambda: lights.export_lights(collection, target, overwrite=True), 'changed since import')
    before = state()
    broken = folder/'broken.geom'
    broken.write_bytes(b'broken')
    rejects(lambda: lights.import_lights(bpy.context, broken), 'range exceeds input')
    assert state() == before
    # Unsupported native types retain exact bytes and explicitly reject edits.
    raw = bytearray(lights._snapshot(collection))
    offset = struct.unpack_from('<Q', raw, 0x58)[0]
    struct.pack_into('<H', raw, offset+4, 7)
    opaque = folder/'opaque.geom'
    opaque.write_bytes(raw)
    unknown = lights.import_lights(bpy.context, opaque)
    assert lights.document_from_collection(unknown).raw == bytes(raw)
    unknown.objects[0].dsts_native_light.intensity = 2
    rejects(lambda: lights.document_from_collection(unknown), 'not supported')

report = dict(blender=bpy.app.version_string, fixtures=rows, save_reopen=True,
              failure_preservation=True, stale_write_rejected=True, unsupported_type_rejected=True,
              native_render_parity=False, game_acceptance=False)
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
print('NATIVE LIGHT EDITOR PASS', json.dumps(report))
