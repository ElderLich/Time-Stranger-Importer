"""Check native donor splitting, saved metadata and GEOM material round trips.

Run with Blender --background --factory-startup --python-exit-code 1 --python
tools/blender_material_contract.py. Local game fixtures are never modified.
"""
import importlib.util
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'Time_Stranger_Importer', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.backend import load_format_modules
from Time_Stranger_Importer.src.material import (
    copy_native_material, export_material, find_data_group,
    make_data_group_single_user, resolve_material,
)
backend = load_format_modules()[0]
print('RUNTIME', bpy.app.version_string, sys.version)

MATERIAL_WORDS = ('unknown_0x314', 'unknown_0x318', 'unknown_0x31C',
                  'unknown_0x324', 'unknown_0x326')
PARAMETER_WORDS = ('unknown_0x14', 'unknown_0x18', 'unknown_0x1C')


def snapshot(native):
    def words(value, names):
        return {name: int(getattr(value, name)) for name in names if hasattr(value, name)}
    return {
        'metadata': words(native, MATERIAL_WORDS),
        'shaders': [shader.name for shader in native.shaders],
        'uniforms': [dict(id=u.parameter_id, kind=u.uniform_type,
                          value=u.value if u.uniform_type == 'texture' else list(map(float, u.value)),
                          **(dict(unknown_0xC=u.unknown_0xC) if u.uniform_type == 'texture' else {}),
                          **(dict(float_payload_padding=bytes(u.float_payload_padding).hex())
                             if u.uniform_type == 'float' and hasattr(u, 'float_payload_padding') else {}),
                          **words(u, PARAMETER_WORDS)) for u in native.uniforms],
        'settings': [dict(id=s.parameter_id, value=bytes(s.value).hex(), unknown_0x12=s.unknown_0x12,
                          **words(s, PARAMETER_WORDS)) for s in native.settings],
    }


source_path = ROOT.parent / 'Time Stranger/local/leviamon-native/body-material/before-role300.geom'
if not source_path.exists():
    source_path = Path(r'D:\Digimon Modding\Time Stranger Extracted\app_0.dx11\chr050.geom')
if not source_path.exists():
    raise RuntimeError('The native body donor fixture is required for this regression')
source = backend.Geom.from_file(str(source_path))
native = next(m for m in source.materials if m.unknown_0x324 & 0x400)
assert any(u.uniform_type == 'float' for u in native.uniforms)
assert len(native.settings) > 0
expected = snapshot(native)
collection = bpy.data.collections.new('Native material contract')
bpy.context.scene.collection.children.link(collection)
original = bpy.data.materials.new('Contract donor')
original.use_fake_user = True
resolve_material(collection, original, native, [])
assert snapshot(export_material(original)) == expected

split = copy_native_material(original, 'Contract split')
split.use_fake_user = True
assert find_data_group(split).node_tree != find_data_group(original).node_tree
assert snapshot(export_material(split)) == expected
split_tree = find_data_group(split).node_tree
texture = next(n for n in split_tree.nodes if n.type == 'TEX_IMAGE' and 'DSTS_parameter_id' in n)
texture.image = bpy.data.images.new('contract_atlas_b.img', 1, 1)
texture.label = 'Edited preview label'
number = next(n for n in split_tree.nodes if n.bl_idname == 'DSTS_ShaderFloatUniform')
number.values[0].value += 0.25
number.label = 'Edited numeric preview label'
setting = next(n for n in split_tree.nodes if n.bl_idname == 'DSTS_ShaderSetting')
setting.label = 'Edited state preview label'
assert snapshot(export_material(original)) == expected
changed = snapshot(export_material(split))
assert any(u['value'] == 'contract_atlas_b' for u in changed['uniforms'])
assert changed['settings'] == expected['settings']
assert changed['shaders'] == expected['shaders']
assert changed['metadata'] == expected['metadata']
assert changed['uniforms'] != expected['uniforms']
print('PASS split atlas and numeric edits are isolated; all native passes/settings/metadata survive')

# Blender's ordinary Duplicate Material also shares the nested native group.
# Retarget Textures must make that group independent before its first edit.
manual = original.copy()
manual.name = 'Contract manual copy'
manual.use_fake_user = True
assert find_data_group(manual).node_tree == find_data_group(original).node_tree
make_data_group_single_user(manual)
assert find_data_group(manual).node_tree != find_data_group(original).node_tree
assert snapshot(export_material(manual)) == expected

# Exercise uint32 values beyond Blender's signed-property limit and opaque
# per-parameter words, with no interpretation or replacement by defaults.
original['DSTS_unknown_0x314'] = str(0xFEDCBA98)
if hasattr(native.uniforms[0], 'unknown_0x14'):
    first = next(n for n in find_data_group(original).node_tree.nodes if 'DSTS_parameter_id' in n)
    assert all(key in first for key in PARAMETER_WORDS)
    assert int(first['unknown_0x14']) == 0xFFFFFFFF
    assert int(first['unknown_0x18']) == 0xFFFFFFFF
else:
    print('LIMIT backend lacks parameter trailer API; run again with updated Formats backend')
before_save = snapshot(export_material(original))
with tempfile.TemporaryDirectory(prefix='dsts_material_contract_') as folder:
    blend = Path(folder) / 'native-contract.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    original = bpy.data.materials['Contract donor']
    split = bpy.data.materials['Contract split']
    assert snapshot(export_material(original)) == before_save
    assert snapshot(export_material(split)) == changed
    assert find_data_group(split).node_tree != find_data_group(original).node_tree

    restored = export_material(original)
    restored.name = native.name
    source.materials = [restored if m.name == native.name else m for m in source.materials]
    for mesh in source.meshes:
        if mesh.material.name == native.name:
            mesh.material = restored
    output = Path(folder) / 'material-contract.geom'
    source.to_file(str(output))
    reread = backend.Geom.from_file(str(output))
    assert snapshot(next(m for m in reread.materials if m.name == native.name)) == before_save
print('PASS saved .blend and native GEOM preserve all exported material fields')

tree = find_data_group(original).node_tree
if hasattr(backend, 'validate_material_texture_binding'):
    texture = next(n for n in tree.nodes if n.type == 'TEX_IMAGE' and 'DSTS_parameter_id' in n)
    previous_id = texture['DSTS_parameter_id']
    texture['DSTS_parameter_id'] = 304  # Native global LightPixelProj matrix, never a texture.
    try:
        export_material(original)
        raise AssertionError('A matrix parameter cannot be a texture binding')
    except (ValueError, RuntimeError) as exc:
        assert 'texture' in str(exc).lower()
    texture['DSTS_parameter_id'] = previous_id
    print('PASS native typed descriptor rejects matrix-as-texture binding')
data_node = next(n for n in tree.nodes if n.bl_idname == 'DSTS_ShaderData')
data_node.string_count = 13
try:
    export_material(original)
    raise AssertionError('Truncated pass table must fail')
except ValueError as exc:
    assert '14 shader slots' in str(exc)
print('PASS incomplete native pass table is rejected')
addon.unregister()
