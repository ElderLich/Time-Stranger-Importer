"""Exercise component generation and saved recipe integration in actual Blender."""
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
parser.add_argument('--job', type=Path, required=True)
parser.add_argument('--recipe', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--preview', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
root, output = args.addon_root.resolve(), args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root / '__init__.py',
                                            submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src import nc_native_skill_ui as ui
from Time_Stranger_Importer.src import skill_conversion as compiler
from Time_Stranger_Importer.src.skill_conversion import finite_producer

assert Path(finite_producer.__file__).resolve().is_relative_to(root)
checks = []
for cls in (ui.DSTS_OT_NativeSkillProducerLoad, ui.DSTS_OT_NativeSkillProducerInspect,
            ui.DSTS_OT_NativeSkillProduce):
    namespace, name = cls.bl_idname.split('.')
    description = getattr(getattr(bpy.ops, namespace), name).get_rna_type().description
    assert description and 'undocumented' not in description.lower()
checks.append('actual producer module and meaningful operator descriptions')


def reject(operation, message):
    try:
        result = operation()
    except (ValueError, RuntimeError) as exc:
        assert message.lower() in str(exc).lower(), str(exc)
    else:
        assert result == {'CANCELLED'}, result


recipe = json.loads(args.recipe.read_text(encoding='utf-8'))
recipe['components'] = []
scene = bpy.context.scene
scene.name = 'Component generation workflow'
ui.load_recipe(scene, recipe)
job_path = output / 'reviewed-job.json'
job_raw = args.job.read_bytes()
job_path.write_bytes(job_raw)
job = json.loads(job_raw)
assert job['source'] == recipe['source']
assert bpy.ops.import_scene.dsts_native_skill_producer_job(filepath=str(job_path)) == {'FINISHED'}
state = scene.dsts_native_skill
review = json.loads(state.producer_report_json)
assert review['buildable'], review['errors']
assert ui.producer_job(scene) == job
checks.append('reviewed source job pins and supported node inspection')
assert bpy.ops.dsts.native_skill_report(section='producer') == {'FINISHED'}
assert json.loads(bpy.data.texts['Component Generation Review'].as_string()) == review
assert review['omitted_nodes'] and review['blockers']
checks.append('full component review preserves omitted systems and generation limits')

saved = {field: getattr(state, field) for field in ui._PRODUCER_STATE_FIELDS}
foreign = copy.deepcopy(job)
foreign['source']['phase_id'] += '-different'
foreign_path = output / 'foreign-job.json'
foreign_path.write_text(json.dumps(foreign), encoding='utf-8')
reject(lambda: bpy.ops.import_scene.dsts_native_skill_producer_job(filepath=str(foreign_path)), 'source phase')
assert saved == {field: getattr(state, field) for field in ui._PRODUCER_STATE_FIELDS}
checks.append('foreign source job rejects without replacing saved state')

other = bpy.data.scenes.new('Independent generation')
ui.load_recipe(other, recipe)
assert not other.dsts_native_skill.producer_job_json
assert ui.producer_job(scene) == job
checks.append('component jobs remain scene-local')

state.producer_output = str(output / 'components')
saved = {field: getattr(state, field) for field in ui._PRODUCER_STATE_FIELDS}
blend = output / 'component-generation.blend'
assert bpy.ops.wm.save_as_mainfile(filepath=str(blend)) == {'FINISHED'}
assert bpy.ops.wm.open_mainfile(filepath=str(blend)) == {'FINISHED'}
scene = bpy.data.scenes['Component generation workflow']
bpy.context.window.scene = scene
state = scene.dsts_native_skill
assert saved == {field: getattr(state, field) for field in ui._PRODUCER_STATE_FIELDS}
assert ui.producer_job(scene) == job
checks.append('job pins, inspection and new output choice survive save/reopen')

job_path.write_bytes(job_raw + b'\n')
reject(lambda: bpy.ops.export_scene.dsts_native_skill_components(), 'changed')
assert not (output / 'components').exists() and not state.components
job_path.write_bytes(job_raw)
tampered = copy.deepcopy(job)
tampered['prefix'] += '_changed'
state.producer_job_json = json.dumps(tampered)
reject(lambda: bpy.ops.export_scene.dsts_native_skill_components(), 'pinned file')
assert not (output / 'components').exists() and not state.components
state.producer_job_json = saved['producer_job_json']
checks.append('changed job bytes or saved snapshot reject before output')

assert bpy.ops.dsts.native_skill_producer_inspect() == {'FINISHED'}
assert bpy.ops.export_scene.dsts_native_skill_components() == {'FINISHED'}
result = json.loads(state.producer_result_json)
assert len(result['components']) == len(job['node_indices'])
generated_recipe = ui.scene_recipe(scene)
assert generated_recipe['components'] == result['components']
assert not state.report_json and not state.inspected_recipe_json
inspection = compiler.inspect_recipe(generated_recipe)
assert inspection['buildable'], inspection['errors']
assert inspection['coverage']['represented'] == len(job['node_indices'])
for pin in generated_recipe['components']:
    assert compiler.pin_file(pin['path']) == pin
checks.append('real source-derived components validate and enter recipe coverage')

state.producer_output = str(output / 'duplicate')
reject(lambda: bpy.ops.export_scene.dsts_native_skill_components(), 'existing components')
assert not (output / 'duplicate').exists()
assert ui.scene_recipe(scene) == generated_recipe
checks.append('duplicate node selection rejects before publication or scene changes')

assert bpy.ops.dsts.native_skill_inspect() == {'FINISHED'}
state.output = str(output / 'candidate')
assert bpy.ops.export_scene.dsts_native_skill_candidate() == {'FINISHED'}
candidate = json.loads((output / 'candidate/candidate.json').read_text(encoding='utf-8'))
assert candidate['installable'] is False
assert candidate['coverage']['represented'] == len(job['node_indices'])
for item in candidate['files']:
    raw = (output / 'candidate' / item['path']).read_bytes()
    assert len(raw) == item['bytes'] and hashlib.sha256(raw).hexdigest() == item['sha256']
checks.append('generated components compose into a hash-verified native review candidate')

saved = {field: getattr(state, field) for field in ui._PRODUCER_STATE_FIELDS}
assert bpy.ops.wm.save_as_mainfile(filepath=str(blend)) == {'FINISHED'}
assert bpy.ops.wm.open_mainfile(filepath=str(blend)) == {'FINISHED'}
scene = bpy.data.scenes['Component generation workflow']
bpy.context.window.scene = scene
state = scene.dsts_native_skill
assert saved == {field: getattr(state, field) for field in ui._PRODUCER_STATE_FIELDS}
assert ui.scene_recipe(scene) == generated_recipe
checks.append('generated component pins and result survive save/reopen')

if args.preview:
    assert bpy.ops.dsts.native_skill_preview() == {'FINISHED'}
    preview = bpy.context.scene
    assert preview is not scene
    assert preview.get('DSTS_preview_actor_clip') == job['source']['phase']['actor_clip']
    assert preview.get('DSTS_preview_reset_actor')
    assert ui.scene_recipe(preview) == generated_recipe
    assert saved == {field: getattr(preview.dsts_native_skill, field) for field in ui._PRODUCER_STATE_FIELDS}
    checks.append('source preview/reset retains generated recipe and component job')

report = {'blender': bpy.app.version_string, 'addon_root': str(root), 'version': list(addon.bl_info['version']),
          'producer_module': str(Path(finite_producer.__file__).resolve()),
          'producer_module_sha256': hashlib.sha256(Path(finite_producer.__file__).read_bytes()).hexdigest(),
          'checks': checks, 'checks_passed': len(checks), 'generated_components': len(result['components']),
          'candidate_resources': len(candidate['files']), 'blend': str(blend), 'game_acceptance': False}
(output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
addon.unregister()
print('COMPONENT GENERATION WORKFLOW PASS', json.dumps(report))
