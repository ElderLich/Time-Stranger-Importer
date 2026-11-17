"""Check ef_b_chr220_bs01 material preview against GEOM, ANIM and rendered pixels."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import sys

import bpy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--addon-root', type=Path, default=Path(__file__).resolve().parents[1])
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
args.output = args.output.resolve()
args.output.mkdir(parents=True, exist_ok=True)
root = args.addon_root.resolve()
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root / '__init__.py',
                                            submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

from Time_Stranger_Importer.src.backend import load_format_modules
from Time_Stranger_Importer.src.effect_material_preview import configure_effect_materials
from Time_Stranger_Importer.src.geom import import_geom, export_geom
from Time_Stranger_Importer.src.material import find_data_group
from Time_Stranger_Importer.src.native_rest_scale import is_rest_track

backend = load_format_modules()[0]
native = backend.Geom.from_file(str(args.source))
anim = addon._read_anim_file(str(args.source.with_suffix('.anim')))
collection = import_geom(bpy.context, str(args.source))
rig = next(o for o in collection.objects if o.type == 'ARMATURE')
rig_name = rig.name
motion = next(t for t in rig.animation_data.nla_tracks if not is_rest_track(t))
motion.strips[0].scale = 1


def material_state(m):
    return ([s.name for s in m.shaders],
            [(u.parameter_id, u.value if u.uniform_type == 'texture' else list(u.value),
              u.unknown_0xC if u.uniform_type == 'texture' else bytes(u.float_payload_padding),
              u.unknown_0x14, u.unknown_0x18, u.unknown_0x1C) for u in m.uniforms],
            [(s.parameter_id, bytes(s.value), s.unknown_0x12, s.unknown_0x14,
              s.unknown_0x18, s.unknown_0x1C) for s in m.settings])


def verify_values(frame, muted):
    motion.mute = muted
    bpy.context.scene.frame_set(frame + 1)
    graph = bpy.context.evaluated_depsgraph_get()
    for m in native.materials:
        mat = bpy.data.materials[m.name]
        assert mat.get('DSTS_effect_material_preview') and mat.use_backface_culling
        tree = find_data_group(mat).node_tree.evaluated_get(graph)
        assert all(curve.driver.is_valid for curve in tree.animation_data.drivers)
        for parameter, name in ((25, 'Diffuse RGB'), (401, 'Material visibility')):
            values = next(u.value for u in m.uniforms if u.parameter_id == parameter)
            for component, rest in enumerate(values):
                binding = next((i for i, ch in enumerate(native.skeleton.float_channels)
                                if ch.flags == 8 and ch.name_hash == m.name_hash and
                                ch.array_index == (parameter << 4 | component)), None)
                expected = rest
                keys = anim.float_channels.get(binding, {})
                if not muted and keys:
                    lo = max((k for k in keys if k <= frame), default=min(keys))
                    hi = min((k for k in keys if k >= frame), default=max(keys))
                    t = (frame - lo) / (hi - lo) if lo != hi else 0
                    expected = (1 - t) * keys[lo] + t * keys[hi]
                if parameter == 25 and component == 3:
                    actual = tree.nodes['Diffuse alpha'].outputs[0].default_value
                elif parameter == 401:
                    actual = tree.nodes[name].outputs[0].default_value
                else:
                    actual = tree.nodes[name].inputs[component].default_value
                assert abs(expected - actual) < 1e-4, (m.name, parameter, component, frame, expected, actual)


for frame in (0, 52, 564, 854, 871, 1315, 1348, 1349, 1419):
    verify_values(frame, muted=False)
verify_values(1349, muted=True)
assert not collection.get('DSTS_effect_material_preview_notes')
out_geom = args.output / args.source.name
export_geom(collection).to_file(str(out_geom))
written = backend.Geom.from_file(str(out_geom))
assert [material_state(m) for m in written.materials] == [material_state(m) for m in native.materials]
assert written.authored_bounds == native.authored_bounds
print('PASS all native material colors, fade/visibility curves, render-state metadata and authored bounds')

blend = args.output / 'effect-materials.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(blend))
bpy.ops.wm.open_mainfile(filepath=str(blend))
rig = bpy.data.objects[rig_name]
motion = next(t for t in rig.animation_data.nla_tracks if not is_rest_track(t))
verify_values(1349, muted=False)
verify_values(52, muted=True)
print('PASS material animation and authored rest values survive save/reopen')

# Use the actual imported material on a simple front-facing test surface.
# This isolates shader coverage from camera angle, bone motion and other draws.
scene = bpy.context.scene
for obj in scene.objects:
    if obj.type == 'MESH':
        obj.hide_render = True
bpy.ops.mesh.primitive_plane_add(size=2)
plane = bpy.context.object
plane.data.materials.append(bpy.data.materials['FX_000'])
bpy.ops.object.camera_add(location=(0, 0, 3))
camera = bpy.context.object
camera.data.type = 'ORTHO'
camera.data.ortho_scale = 1.5
scene.camera = camera
scene.render.engine = 'CYCLES'
scene.cycles.samples = 8
scene.render.film_transparent = True
scene.render.resolution_x = scene.render.resolution_y = 32
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_mode = 'RGBA'


def render_pixel(label):
    path = args.output / (label + '.exr')
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path))
    pixel = list(image.pixels[4 * (16 * 32 + 16):4 * (16 * 32 + 16) + 4])
    bpy.data.images.remove(image)
    return pixel


black = render_pixel('authored-black')
assert max(black[:3]) < .01 and black[3] > .99, black
verify_values(0, muted=False)
hidden = render_pixel('animated-invisible')
assert hidden[3] < .01, hidden
verify_values(0, muted=True)
plane.data.materials[0] = bpy.data.materials['FX_003']
red = render_pixel('authored-emissive-red')
assert red[0] > 2 and red[0] > 10 * red[1] and .35 < red[3] < 1, red
print('PASS rendered black, transparent and HDR red swatches', black, hidden, red)

# A standalone GEOM can lack all shader files. Its generic material and
# editable metadata should remain intact even when some bindings are unclear.
isolated = args.output / 'without-shaders' / args.source.name
isolated.parent.mkdir(exist_ok=True)
shutil.copyfile(args.source, isolated)
fallback = import_geom(bpy.context, str(isolated))
owner = next(o for o in fallback.objects if o.type == 'ARMATURE')
materials = {m['DSTS_source_material_name']: m for obj in fallback.objects if obj.type == 'MESH'
             for m in obj.data.materials}
assert len(materials) == 16 and all(not m.get('DSTS_effect_material_preview') for m in materials.values())
assert fallback['DSTS_effect_material_preview_notes'].count('unreviewed') == 16
channels = json.loads(owner['DSTS_float_bindings'])
channels.append(channels[0])
owner['DSTS_float_bindings'] = json.dumps(channels)
group = find_data_group(materials['FX_003']).node_tree
shader = next(n for n in group.nodes if n.bl_idname == 'DSTS_ShaderData')
shader.shader_strings[0].value = '../outside'
group = find_data_group(materials['FX_007']).node_tree
setting = next(n for n in group.nodes if n.get('DSTS_parameter_id') == 125)
setting.values[0].value = 0   # unsupported source blend factor 0x300
group = find_data_group(materials['FX_015']).node_tree
cull = group.nodes.new('DSTS_ShaderSetting')
cull['DSTS_parameter_id'] = 128
for byte in struct.pack('<4I', 0x404, 0, 0, 0):
    cull.values.add().value = byte
configure_effect_materials(fallback, owner, args.source)
notes = fallback['DSTS_effect_material_preview_notes']
for name, reason in (('FX_000', 'ambiguous native material bindings'),
                     ('FX_003', 'primary shader pair unavailable or unreviewed'),
                     ('FX_007', 'unreviewed blend factors'), ('FX_015', 'unreviewed face culling mode')):
    assert f'{materials[name].name}: {reason}' in notes and not materials[name].get('DSTS_effect_material_preview')
assert sum(bool(m.get('DSTS_effect_material_preview')) for m in materials.values()) == 12
print('PASS unavailable/malformed programs, duplicate bindings and unsupported blend/cull retain generic preview')
