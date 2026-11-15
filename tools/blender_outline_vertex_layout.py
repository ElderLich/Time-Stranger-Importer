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
    check_nc(export(new_nc(),'fresh'),baseline)
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
