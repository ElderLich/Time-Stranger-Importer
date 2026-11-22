"""Exercise the normal GEOM operators with the native Creepymon donor and its NC model.

Run Blender --factory-startup --background --python-exit-code 1 --python this.py --
<prepare|verify> <test-directory> [addon-directory]. Prepare with an older addon
checkout to verify saved scenes as well as new imports.
"""
import importlib.util
from pathlib import Path
import sys

import bpy
import numpy as np

mode, folder, *alternate = sys.argv[sys.argv.index('--')+1:]
folder = Path(folder)
folder.mkdir(parents=True, exist_ok=True)
addon_directory = Path(alternate[0]) if alternate else Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer',addon_directory/'__init__.py',
                                               submodule_search_locations=[str(addon_directory)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.backend import load_format_modules
fmt = load_format_modules()[0]
fmt.set_throw_errors(False)

donor_path = Path('D:/Digimon Modding/Time Stranger Extracted/app_0.dx11/chr038.geom')
model_path = Path('D:/Digimon Modding/Digimon Library/New Century/Digimon_Library/CreepymonX_x137/model/model.glb')
donor = fmt.Geom.from_file(str(donor_path))
saved = folder/'nc-existing.blend'


def imported(operator, **kwargs):
    existing = set(bpy.data.collections)
    # Factory reset/open_mainfile replaces the owning window in a GUI session.
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        assert operator('EXEC_DEFAULT', **kwargs) == {'FINISHED'}
    return next(c for c in bpy.data.collections if c not in existing and any(o.type=='MESH' for o in c.objects))


def new_nc():
    return imported(bpy.ops.import_scene.dsts_nc_glb, filepath=str(model_path),
                    donor_geom_path=str(donor_path),build_outline=True,import_related_skills=False)


def export(col, name):
    path = folder/(name+'.geom')
    assert bpy.ops.export_scene.dsts_geom('EXEC_DEFAULT', filepath=str(path),collection_name=col.name,
                                         export_textures=False)=={'FINISHED'}
    return fmt.Geom.from_file(str(path))


def reopen():
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    return next(c for c in bpy.data.collections if c.get('DSTS_material_donor_geom')
                and any(o.type=='MESH' for o in c.objects))


def layout(mesh):
    return mesh.bytes_per_vertex, [(a.atype,a.dtype,a.count,a.offset) for a in mesh.mesh_attributes]


def check_nc(result, baseline):
    by_name = {m.name:m for m in result.meshes}
    outline = by_name['outline']
    assert layout(outline) == layout(next(m for m in donor.meshes if m.material.name=='MTR_line01'))
    assert outline.bytes_per_vertex==44 and outline.material.name=='MTR_line01'
    # The existing tangent-bearing NC body, shader programs and material parameters
    # must still be identical after fixing its sibling outline.
    for previous in baseline.meshes:
        updated=by_name[previous.name]
        assert [s.name for s in previous.material.shaders]==[s.name for s in updated.material.shaders]
        if previous.name != 'outline':
            assert layout(previous)==layout(updated)
            assert previous.pack_vertices()[2]==updated.pack_vertices()[2]
            assert list(previous.indices)==list(updated.indices)
    print('PASS NC outline stride 44, tangent-bearing body buffer/indices and shader programs identical', flush=True)


def field(mesh, name):
    attrs, stride, raw = mesh.pack_vertices()
    attr = next(a for a in attrs if a.atype == name)
    dtype = np.dtype({'float': '<f4', 'float16': '<f2', 'uByte': 'u1'}[attr.dtype])
    return np.ndarray((len(raw)//stride, attr.count), dtype=dtype, buffer=raw,
                      offset=attr.offset, strides=(stride, dtype.itemsize))


def check_inversion(result):
    body, = [m for m in result.meshes if m.material.name != 'MTR_line01']
    outline, = [m for m in result.meshes if m.material.name == 'MTR_line01']
    # Compare each oriented face corner, including split normals at UV seams.
    bi = np.asarray(body.get_indices()).reshape(-1, 3)
    oi = np.asarray(outline.get_indices()).reshape(-1, 3)
    body_pos, shell_pos = field(body, 'position')[bi], field(outline, 'position')[oi]
    body_norm = field(body, 'normal')[bi].astype(np.float32)
    shell_norm = field(outline, 'normal')[oi].astype(np.float32)
    body_norm /= np.linalg.norm(body_norm, axis=2, keepdims=True)
    shell_norm /= np.linalg.norm(shell_norm, axis=2, keepdims=True)
    matches = np.zeros(len(bi), dtype=bool)
    for offset in range(3):
        order = (offset - np.arange(3)) % 3
        # Blender encodes split normals in each polygon's new smoothing basis.
        opposite = np.sum(-body_norm * shell_norm[:, order], axis=2)
        matches |= (np.all(body_pos == shell_pos[:, order], axis=(1, 2)) &
                    np.all(opposite >= np.cos(np.deg2rad(1)), axis=1))
    assert matches.all(), f'{np.count_nonzero(~matches)} shell faces disagree with the inverted body'
    print('PASS fresh outline matches every reversed body triangle, position and inverted corner normal', flush=True)


def check_manual_shell(collection):
    outline = next(o for o in collection.objects if o.type == 'MESH' and o.name.startswith('outline'))
    mat = outline.data.materials[0]
    bpy.data.objects.remove(outline, do_unlink=True)
    body = next(o for o in collection.objects if o.type == 'MESH')
    shell = body.copy()
    shell.data = body.data.copy()
    collection.objects.link(shell)
    shell.data.name = 'handmade'
    shell.data.materials[0] = mat
    for layer in list(shell.data.color_attributes):
        shell.data.color_attributes.remove(layer)
    for name in ('uv2', 'uv3'):
        if name not in shell.data.uv_layers:
            shell.data.uv_layers.new(name=name)
    bpy.ops.object.select_all(action='DESELECT')
    shell.select_set(True)
    bpy.context.view_layer.objects.active = shell
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=0.0001)
    bpy.ops.transform.shrink_fatten(value=0.006)
    bpy.ops.mesh.flip_normals()
    bpy.ops.object.mode_set(mode='OBJECT')
    # Exercise reusing a three-influence line material on a four-influence mesh.
    for g in shell.vertex_groups:
        g.remove([0])
    for g in list(shell.vertex_groups)[:4]:
        g.add([0], 0.25, 'REPLACE')
    # Another piece sharing the material must use the same padded stream while
    # retaining its authored line-width colors.
    accent_mesh = bpy.data.meshes.new('accent')
    accent_mesh.from_pydata([(0, 0, 0), (0, 1, 0), (1, 0, 0)], [], [(0, 1, 2)])
    accent_mesh.uv_layers.new(name='uv1')
    accent_mesh.materials.append(mat)
    color = accent_mesh.color_attributes.new(name='Color', type='FLOAT_COLOR', domain='POINT')
    color.data.foreach_set('color', [0.5, 0.25, 0.0, 1.0] * 3)
    accent = bpy.data.objects.new('accent', accent_mesh)
    collection.objects.link(accent)
    accent.parent = body.parent
    accent.vertex_groups.new(name=shell.vertex_groups[0].name).add([0, 1, 2], 1.0, 'REPLACE')
    result = export(collection, 'handmade')
    drawn, = [m for m in result.meshes if m.name == 'handmade']
    native = fmt.Geom.from_file(str(donor_path.with_name('chr039.geom')))
    reference = next(m for m in native.meshes if m.material.name == 'MTR_line01')
    assert layout(drawn) == layout(reference)
    assert [s.name for s in drawn.material.shaders] == [s.name for s in reference.material.shaders]
    assert np.all(field(drawn, 'color') == 255)
    accent_draw = next(m for m in result.meshes if m.name == 'accent')
    assert layout(accent_draw) == layout(reference)
    assert np.all(field(accent_draw, 'color') == [128, 64, 0, 255])
    assert np.all(field(accent_draw, 'weight') == [1, 0, 0, 0])
    expected = np.array([(v.co.x, v.co.z, -v.co.y) for v in shell.data.vertices])
    actual = field(drawn, 'position')
    assert np.allclose(actual.min(axis=0), expected.min(axis=0), atol=1e-6)
    assert np.allclose(actual.max(axis=0), expected.max(axis=0), atol=1e-6)
    print('PASS hand-built +0.006 shell kept edited bounds, white color and native four-weight stream/shaders', flush=True)


if mode=='prepare':
    col = new_nc()
    export(col,'before')
    assert bpy.ops.wm.save_as_mainfile(filepath=str(saved))=={'FINISHED'}
    # Blender reconstitutes custom split normals on open. Compare exports
    # with identical save/reopen histories for the bitwise body assertion.
    export(reopen(),'before-reopen')
    print('Saved actual importer scene:',saved,flush=True)
elif mode=='verify':
    baseline = fmt.Geom.from_file(str(folder/'before.geom'))
    check_nc(export(reopen(),'after'),fmt.Geom.from_file(str(folder/'before-reopen.geom')))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    col = new_nc()
    fresh = export(col,'fresh')
    check_nc(fresh,baseline)
    check_inversion(fresh)
    check_manual_shell(col)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # A legal native file with opposite tangent declarations on the same
    # material exercises both the native importer and the per-draw override.
    body = next(m for m in donor.meshes if any(a.atype=='tangent' for a in m.mesh_attributes))
    line = next(m for m in donor.meshes if m.material.name=='MTR_line01')
    body.name,line.name = 'with_tangent','without_tangent'
    line.material = body.material
    authored=fmt.Mesh()
    authored.name='authored_no_uv'
    authored.material=body.material
    authored.matrix_palette=[body.matrix_palette[0]]
    authored.set_vertex_count(3)
    authored.set_position(np.array([(0,0,0),(1,0,0),(0,1,0)],np.float32))
    authored.set_normal(np.tile(np.array([0,0,1],np.float16),(3,1)))
    authored.set_tangent(np.tile(np.array([1,0,0,-1],np.float16),(3,1)))
    authored.set_index(np.zeros((3,1),np.uint8))
    authored.set_weight(np.ones((3,1),np.float16))
    authored.primitive=1
    authored.indices=[0,1,2]
    donor.meshes.append(authored)
    path=folder/'shared-material.geom'
    path.write_bytes(donor.to_bytes())
    shared=imported(bpy.ops.import_scene.dsts_geom,filepath=str(path))
    result=export(shared,'shared-roundtrip')
    by_name={m.name:m for m in result.meshes}
    original=fmt.Geom.from_file(str(path))
    for m in (m for m in original.meshes if m.name in ('with_tangent','without_tangent','authored_no_uv')):
        assert layout(by_name[m.name])==layout(m), (m.name,layout(m),layout(by_name[m.name]))
        assert by_name[m.name].material.name==body.material.name
    assert np.allclose([v.tangent for v in by_name['authored_no_uv'].vertices], [1,0,0,-1], atol=1e-6)
    print('PASS shared material retained both native tangent declarations and UV-less authored tangents',flush=True)
else:
    raise ValueError(mode)
