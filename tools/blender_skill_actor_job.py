"""Verify audited actor reuse through the installed Blender controls."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import bpy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--addon-root', type=Path, required=True)
parser.add_argument('--recipe', type=Path, required=True)
parser.add_argument('--audit', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
root, output = args.addon_root.resolve(), args.output.resolve()
output.mkdir(mode=0o777)
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root / '__init__.py', submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src import nc_native_skill_ui as ui
from Time_Stranger_Importer.src.skill_conversion import reviewed_actor

assert Path(reviewed_actor.__file__).resolve().is_relative_to(root)
recipe = json.loads(args.recipe.read_bytes())
recipe['components'] = [pin for pin in recipe['components'] if json.loads(Path(pin['path']).read_bytes())['kind'] != 'actor']
scene = bpy.context.scene
ui.load_recipe(scene, recipe)
checks = []
assert bpy.ops.import_scene.dsts_native_skill_actor_audit(filepath=str(args.audit)) == {'FINISHED'}
state = scene.dsts_native_skill
job = ui.producer_job(scene)
inspection = json.loads(state.producer_report_json)
assert inspection['buildable'] and inspection['kind'] == 'actor'
state.producer_output = str(output / 'actor')
saved = copy.deepcopy(job)
scene_name = scene.name
blend = output / 'Actor Job.blend'
assert bpy.ops.wm.save_as_mainfile(filepath=str(blend)) == {'FINISHED'}
assert bpy.ops.wm.open_mainfile(filepath=str(blend)) == {'FINISHED'}
scene = bpy.data.scenes[scene_name]
state = scene.dsts_native_skill
assert ui.producer_job(scene) == saved
assert state.producer_output == str(output / 'actor')
checks.append('audited actor choice and destination survive save/reopen')
old_suffix = state.motion_suffix
state.motion_suffix = old_suffix + '_changed'
try:
    ui.producer_job(scene)
    raise AssertionError('Changed mapping accepted')
except ValueError as exc:
    assert 'mapping changed' in str(exc)
state.motion_suffix = old_suffix
checks.append('changed native motion slot rejects stale saved job')
assert bpy.ops.export_scene.dsts_native_skill_components() == {'FINISHED'}
result = json.loads(state.producer_result_json)
component_path = Path(result['components'][0]['path'])
component = json.loads(component_path.read_bytes())
dependency = component['dependencies'][0]
motion = component_path.parent / dependency['path']
audit = json.loads(args.audit.read_bytes())
original = args.audit.parent / job['motion_file']
assert motion.read_bytes() == original.read_bytes()
decoded = addon._read_anim_file(str(motion))
assert decoded.bone_count == component['sampling']['bones']
assert decoded.frame_count == component['sampling']['frames'] and decoded.playback_rate == 30
checks.append('generated destination motion preserves every audited ANIM byte and native header')
# Loading the saved job file must enforce the same target checks as choosing
# the audit directly. Otherwise a changed UI slot can publish an old binding.
assert bpy.ops.import_scene.dsts_native_skill_producer_job(filepath=str(component_path.parent / 'job.json')) == {'FINISHED'}
state.motion_suffix = old_suffix + '_changed'
try:
    ui.producer_job(scene)
    raise AssertionError('Saved job accepted changed mapping')
except ValueError as exc:
    assert 'mapping changed' in str(exc)
state.motion_suffix = old_suffix
checks.append('saved actor job file also rejects a changed native motion slot')
saved_components = ui.scene_recipe(scene)['components']
state.producer_output = str(output / 'duplicate')
try:
    bpy.ops.export_scene.dsts_native_skill_components()
    raise AssertionError('Duplicate actor accepted')
except RuntimeError as exc:
    assert 'existing actor' in str(exc)
assert not (output / 'duplicate').exists() and ui.scene_recipe(scene)['components'] == saved_components
checks.append('duplicate actor rejected before publication and scene mutation')
assert bpy.ops.dsts.native_skill_inspect() == {'FINISHED'}
inspection = json.loads(state.report_json)
assert inspection['buildable'] and not inspection['errors']
assert not any(row['code'] in ('actor-missing', 'actor-fragment') for row in inspection['blockers'])
state.output = str(output / 'candidate')
assert bpy.ops.export_scene.dsts_native_skill_candidate() == {'FINISHED'}
candidate = json.loads((output / 'candidate/candidate.json').read_bytes())
files = [row for row in candidate['files'] if Path(row['path']).name == motion.name]
assert len(files) == 1 and files[0]['sha256'] == hashlib.sha256(original.read_bytes()).hexdigest()
checks.append('complete recipe accepts actor component and builds native dependency candidate')
report = {'blender': bpy.app.version_string, 'addon_root': str(root), 'version': list(addon.bl_info['version']),
          'checks': checks, 'actor_component': str(component_path), 'animation_sha256': result['animation_sha256'],
          'destination_motion': motion.name, 'resources': len(candidate['files']), 'game_acceptance': False}
(output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
addon.unregister()
print('AUDITED ACTOR JOB PASS', json.dumps(report))
