"""Create a saved review workbench from explicit recipes using installed UI operators."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

import bpy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--addon-root', type=Path, required=True)
parser.add_argument('--recipes', type=Path, nargs='+', required=True)
parser.add_argument('--output', type=Path, required=True, help='New workbench folder')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
root, output = args.addon_root.resolve(), args.output.resolve()
if output.exists():
    raise FileExistsError('Workbench output already exists')
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root / '__init__.py',
                                            submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src import nc_native_skill_ui as ui
from Time_Stranger_Importer.src import nc_skill_ui

output.mkdir()
records = []
start_scene = bpy.context.scene
for path in args.recipes:
    recipe = json.loads(path.read_text(encoding='utf-8'))
    bpy.context.window.scene = start_scene
    ui.load_recipe(start_scene, recipe)
    assert bpy.ops.dsts.native_skill_preview() == {'FINISHED'}
    scene = bpy.context.scene
    assert scene is not start_scene
    scene.name = nc_skill_ui.phase_label(recipe['source']['phase'])
    assert bpy.ops.dsts.native_skill_inspect() == {'FINISHED'}
    state = scene.dsts_native_skill
    inspection = json.loads(state.report_json)
    assert inspection['buildable'], inspection['errors']
    name = recipe['source']['model_code'] + '-' + recipe['source']['phase_id'].replace(':', '-')
    state.output = str(output / (name + '-candidate'))
    assert bpy.ops.export_scene.dsts_native_skill_candidate() == {'FINISHED'}
    records.append({'scene': scene.name, 'phase': recipe['source']['phase_id'],
                    'source_clip': recipe['source']['phase']['actor_clip'],
                    'recipe': ui.scene_recipe(scene), 'plan_sha256': inspection['plan_sha256'],
                    'candidate': state.output})
    scene['DSTS_skill_review_only'] = True
    scene['DSTS_skill_review_note'] = 'Native review assets; target routing and full gameplay acceptance remain incomplete.'

bpy.context.window.scene = bpy.data.scenes[records[0]['scene']]
bpy.data.scenes.remove(start_scene)
blend = output / 'Native Skill Review.blend'
assert bpy.ops.wm.save_as_mainfile(filepath=str(blend)) == {'FINISHED'}
assert bpy.ops.wm.open_mainfile(filepath=str(blend)) == {'FINISHED'}
for record in records:
    scene = bpy.data.scenes[record['scene']]
    assert scene.get('DSTS_preview_actor_clip') == record['source_clip']
    assert scene.get('DSTS_preview_reset_actor')
    assert ui.scene_recipe(scene) == record['recipe']
    assert json.loads(scene.dsts_native_skill.report_json)['plan_sha256'] == record['plan_sha256']
    candidate = json.loads((Path(record['candidate']) / 'candidate.json').read_text(encoding='utf-8'))
    assert candidate['installable'] is False
    record['resources'] = len(candidate['files'])
    del record['recipe']
report = {'blender': bpy.app.version_string, 'addon_root': str(root), 'version': list(addon.bl_info['version']),
          'blend': str(blend), 'scenes': records, 'save_reopen_verified': True, 'installable': False,
          'game_acceptance': False}
(output / 'workbench-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print('SAVED SKILL WORKBENCH', json.dumps(report))
addon.unregister()
