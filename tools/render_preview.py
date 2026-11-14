"""Headless visual check: blender -b --python this.py -- manifest output_dir."""
import importlib.util
from pathlib import Path
import sys

import bpy

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root / '__init__.py',
                                            submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.cinematic import import_preview

args = sys.argv[sys.argv.index('--') + 1:]
manifest, output = Path(args[0]).resolve(), Path(args[1]).resolve()
output.mkdir(parents=True, exist_ok=True)
scene = import_preview(bpy.context, str(manifest))
scene.render.engine = 'BLENDER_WORKBENCH'
scene.render.resolution_x = 960
scene.render.resolution_y = 540
scene.render.resolution_percentage = 100
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'TEXTURE'
scene.display.shading.show_shadows = True
scene.display.shading.show_cavity = True
scene.display.shading.background_type = 'WORLD'
scene.world = bpy.data.worlds.new('Preview World')
scene.world.color = (.055, .065, .08)
for frame in (() if '--save-only' in args else (1, 90, 180)):
    scene.frame_set(min(frame, scene.frame_end))
    scene.render.filepath = str(output / f'leviamon-camera-{frame:03}.png')
    bpy.ops.render.render(write_still=True)
scene.frame_set(90)
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'Leviamon X Cinematic Preview.blend'))
print('PASS preview images and Blender scene', output)
