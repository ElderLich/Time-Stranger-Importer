"""Native BSON operator edits, marker timing and saved-scene preservation."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', ROOT / '__init__.py',
                                            submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.effect_bson import EffectDocument, END
from Time_Stranger_Importer.src.native_timeline import (
    document_from_controller, controller, export_timeline, apply_marker_timing, refresh_markers)


def fixture():
    pool, strings = bytearray(b'\0'), {'': 0}
    def string(value):
        if value not in strings:
            strings[value] = len(pool)
            pool.extend(value.encode() + b'\0')
        return strings[value]
    def node(name, value):
        key = string(name)
        if isinstance(value, list):
            kind, payload = 1, struct.pack('<I', len(value)) + b''.join(node(k, v) for k, v in value) + END
        elif type(value) is int:
            kind, payload = 0x10, struct.pack('<i', value)
        elif type(value) is float:
            kind, payload = 0x11, struct.pack('<f', value)
        elif isinstance(value, str):
            kind, payload = 0x14, struct.pack('<I', string(value))
        else:
            kind, payload = 0x7f, value
        high, low = divmod(len(payload) + 8, 65535)
        return struct.pack('<BBHI', kind, high, low, key) + payload
    raw = node('', [('mf', 90), ('hf', [('hf0', 20)]), ('set0', [('common', [
        ('id', 'test'), ('se', [('se_name', '320101'), ('se_offset', 5), ('se_stop', 1),
         ('se_fadeout', 4), ('se_volume', 1.0), ('se_stream', 0), ('se_3D', 0)]),
        ('emitter0', [('dn', 'original')])])]), ('unknown', b'opaque!!')])
    high, low = divmod(len(pool), 65535)
    return b'BSON\x0a\0\0\0' + raw + struct.pack('<BBH', 0x20, high, low) + pool + END


checks = []
with tempfile.TemporaryDirectory(prefix='dsts_timeline_') as folder:
    folder = Path(folder)
    source = folder / 'source.bson'
    raw = fixture()
    source.write_bytes(raw)
    assert bpy.ops.import_scene.dsts_effect_timeline(filepath=str(source)) == {'FINISHED'}
    obj = controller(bpy.context)
    assert document_from_controller(obj).to_bytes() == raw
    fields = {tuple(json.loads(field.path_json)): field for field in obj.dsts_effect_fields}
    fields[('mf',)].value_integer = 120
    fields[('set0', 'common', 'se', 'se_volume')].value_float = .5
    assert bpy.context.scene.frame_end == 121
    hit = next(marker for marker in bpy.context.scene.timeline_markers if 'Hit 0' in marker.name)
    hit.frame = 31
    try:
        document_from_controller(obj)
        raise AssertionError('Unapplied timeline edits accepted')
    except ValueError:
        pass
    # Editing gain must not silently erase a pending marker edit.
    fields[('set0', 'common', 'se', 'se_volume')].value_float = .25
    assert hit.frame == 31
    assert bpy.ops.dsts.effect_timeline_apply() == {'FINISHED'}
    assert fields[('hf', 'hf0')].value_integer == 30
    assert document_from_controller(obj).value(('set0', 'common', 'se', 'se_volume')) == .25
    name = obj.name
    blend = folder / 'timeline.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    obj = bpy.data.objects[name]
    output = folder / 'edited.bson'
    export_timeline(obj, output)
    doc = EffectDocument.read(output)
    assert doc.value(('mf',)) == 120 and doc.value(('hf', 'hf0')) == 30
    assert doc.value(('set0', 'common', 'se', 'se_volume')) == .25
    assert b'opaque!!' in output.read_bytes()
    assert len(raw) == output.stat().st_size
    old = EffectDocument.from_bytes(raw)
    changed = {i for i, (a, b) in enumerate(zip(raw, output.read_bytes())) if a != b}
    allowed = set()
    for path in [('mf',), ('hf', 'hf0'), ('set0', 'common', 'se', 'se_volume')]:
        record = old._get(path)
        allowed.update(range(record.start + 8, record.end))
    assert changed <= allowed
    checks.append('Duration, gain and dragged hit timing survive save/reopen/export with exact scoped changes')
    source.write_bytes(b'external-change')
    try:
        export_timeline(obj, source, overwrite=True)
        raise AssertionError('Stale original overwritten')
    except ValueError:
        pass
    assert source.read_bytes() == b'external-change'
    fields = {tuple(json.loads(field.path_json)): field for field in obj.dsts_effect_fields}
    cue = fields[('set0', 'common', 'se', 'se_name')]
    cue.value_string = '320102'
    try:
        document_from_controller(obj)
        raise AssertionError('Unvalidated bank mapping accepted')
    except ValueError:
        pass
    cue.value_string = '320101'
    hit = next(marker for marker in bpy.context.scene.timeline_markers if 'Hit 0' in marker.name)
    hit.frame = 41
    fields[('hf', 'hf0')].value_integer = 35
    try:
        apply_marker_timing(obj)
        raise AssertionError('Conflicting marker/field edits accepted')
    except ValueError:
        pass
    refresh_markers(obj)
    assert fields[('hf', 'hf0')].value_integer == 35
    checks.append('Stale original, unvalidated sound identity and conflicting field/marker edits reject safely')
    native = Path(r'D:\Digimon Modding\Time Stranger Extracted\app_0.dx11\ef_b_chr395_bs01.bson')
    if native.exists():
        assert bpy.ops.import_scene.dsts_effect_timeline(filepath=str(native)) == {'FINISHED'}
        actual = controller(bpy.context)
        assert document_from_controller(actual).to_bytes() == native.read_bytes()
        duration = next(field for field in actual.dsts_effect_fields if json.loads(field.path_json) == ['mf'])
        duration.value_integer += 1
        export_timeline(actual, folder / 'native-edited.bson')
        assert EffectDocument.read(folder / 'native-edited.bson').value(('mf',)) == duration.value_integer
        checks.append('Actual native chr395 effect import and duration edit/export/reimport')
        drawable = Path(r'D:\Digimon Modding\Time Stranger Extracted\app_0.dx11\chr050.geom')
        if drawable.exists():
            index = next(i for i, field in enumerate(actual.dsts_effect_fields) if field.units == 'native basename')
            actual.dsts_effect_field_index = index
            assert bpy.ops.dsts.effect_drawable_select(filepath=str(drawable)) == {'FINISHED'}
            field = actual.dsts_effect_fields[index]
            assert document_from_controller(actual).value(tuple(json.loads(field.path_json))) == 'chr050'
            checks.append('Drawable picker validates actual native GEOM/NLST and changes the selected source resource only')

addon.unregister()
addon.register()
addon.unregister()
result = {'blender': bpy.app.version_string, 'checks': checks}
path = ROOT / 'build' / f'native-timeline-{bpy.app.version[0]}{bpy.app.version[1]}.json'
path.write_text(json.dumps(result, indent=2) + '\n')
print('PASS', result)
