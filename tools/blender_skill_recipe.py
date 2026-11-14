"""Exercise saved source recipes and native candidate operators in real Blender."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--addon-root', type=Path, default=ROOT)
parser.add_argument('--model', type=Path, required=True)
parser.add_argument('--donor', type=Path, required=True)
parser.add_argument('--recipe', type=Path)
parser.add_argument('--work-dir', type=Path, required=True)
parser.add_argument('--preview', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
root, work = args.addon_root.resolve(), args.work_dir.resolve()
work.mkdir(parents=True, exist_ok=False)
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root / '__init__.py',
                                            submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src import nc_native_skill_ui as ui
from Time_Stranger_Importer.src import skill_conversion as core
from Time_Stranger_Importer.src.backend import load_format_modules

checks = []
backend = load_format_modules()[0]
assert Path(addon.__file__).resolve().parent == root
assert Path(backend.__file__).resolve().is_relative_to(root)
assert Path(core.__file__).resolve().is_relative_to(root)
for cls in ui.CLASSES:
    if issubclass(cls, bpy.types.Operator):
        namespace, name = cls.bl_idname.split('.')
        description = getattr(getattr(bpy.ops, namespace), name).get_rna_type().description
        assert description and 'undocumented' not in description.lower()
checks.append('actual module/backend paths and every new operator tooltip')


def reject(operation, text):
    try:
        result = operation()
    except (RuntimeError, ValueError) as exc:
        assert text.lower() in str(exc).lower(), str(exc)
    else:
        assert result == {'CANCELLED'}, result


assert bpy.ops.import_scene.dsts_native_skill_recipe(filepath=str(args.model), phase='2:1') == {'FINISHED'}
scene = bpy.context.scene
scene.name = 'Recipe persistence'
state = scene.dsts_native_skill
recipe = json.loads(state.recipe_json)
assert recipe['source']['phase_id'] == '2:1'
assert recipe['source']['files']
assert all(row['sha256'] for row in recipe['source']['files'])
state.model_stem, state.motion_suffix = 'chr39511', 'bs02'
state.animation_selector, state.primary_effect_id, state.target_effect_id = 4, 239511, 239513
state.caster_donor = str(args.donor)
state.output = str(work / 'unbuilt')
for item in state.sockets:
    if item.side == 'caster' and item.source_socket == 'skill2-foot':
        item.native_root = True
for item in state.cues:
    item.cue_id = 329512
    item.component = 'explicit-review-component'
assert bpy.ops.dsts.native_skill_inspect() == {'FINISHED'}
review = json.loads(state.report_json)
assert review['plan_sha256'] and review['blockers']
assert not review.get('installable', False)
checks.append('real ordinary Cast source pinned and unresolved conversion requirements exposed')

saved_recipe, saved_report = ui.scene_recipe(scene), state.report_json
saved_state = state.recipe_json
bad = copy.deepcopy(saved_recipe)
bad['socket_mappings'].append({'side': 'invented', 'source_socket': 'foot', 'native_socket': ''})
reject(lambda: ui.load_recipe(scene, bad), 'socket')
assert state.recipe_json == saved_state and ui.scene_recipe(scene) == saved_recipe
checks.append('invalid recipe leaves prior scene mappings intact')
bad = copy.deepcopy(saved_recipe)
bad['donor_template_set']['caster'] = 7
reject(lambda: ui.load_recipe(scene, bad), 'template')
assert state.recipe_json == saved_state and ui.scene_recipe(scene) == saved_recipe
checks.append('invalid donor-template values reject before changing scene state')

state.primary_effect_id += 1
reject(lambda: bpy.ops.export_scene.dsts_native_skill_candidate(), 'inspect')
assert not (work / 'unbuilt').exists()
state.primary_effect_id -= 1
checks.append('changed mappings reject stale inspected plan before output')

scene.dsts_nc_model_path = str(work / 'different.glb')
reject(lambda: ui.scene_recipe(scene), 'source model changed')
scene.dsts_nc_model_path = str(args.model)
assert ui.scene_recipe(scene) == saved_recipe
checks.append('source selection cannot silently reuse old source hashes')

other = bpy.data.scenes.new('Independent recipe')
ui.load_recipe(other, saved_recipe)
other.dsts_native_skill.primary_effect_id = 239599
assert state.primary_effect_id == 239511
assert other.dsts_native_skill.primary_effect_id != state.primary_effect_id
checks.append('scene recipes and mappings remain independent')

blend = work / 'saved-recipe.blend'
assert bpy.ops.wm.save_as_mainfile(filepath=str(blend)) == {'FINISHED'}
assert bpy.ops.wm.open_mainfile(filepath=str(blend)) == {'FINISHED'}
scene = bpy.data.scenes['Recipe persistence']
bpy.context.window.scene = scene
state = scene.dsts_native_skill
assert ui.scene_recipe(scene) == saved_recipe
assert state.report_json == saved_report
assert state.output == str(work / 'unbuilt')
assert state.inspected_recipe_json == ui._json(saved_recipe)
checks.append('save/reopen retains source pins, mappings, inspection and output choice')

if args.preview:
    assert bpy.ops.dsts.native_skill_preview() == {'FINISHED'}
    preview = bpy.context.scene
    assert preview != scene
    assert preview.get('DSTS_preview_actor_clip') == 'skill2'
    assert preview.get('DSTS_preview_reset_actor')
    assert ui.scene_recipe(preview) == saved_recipe
    bpy.context.window.scene = scene
    checks.append('ordinary Cast preview imports actor/reset and preserves its recipe')

if args.recipe:
    assert bpy.ops.import_scene.dsts_native_skill_recipe_json(filepath=str(args.recipe.resolve())) == {'FINISHED'}
    state = scene.dsts_native_skill
    assert bpy.ops.dsts.native_skill_inspect() == {'FINISHED'}
    inspection = json.loads(state.report_json)
    assert inspection['buildable'], inspection['errors']
    state.output = str(work / 'candidate')
    full_recipe = ui.scene_recipe(scene)
    full_report = state.report_json
    full_blend = work / 'full-recipe.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(full_blend)) == {'FINISHED'}
    assert bpy.ops.wm.open_mainfile(filepath=str(full_blend)) == {'FINISHED'}
    scene = bpy.data.scenes['Recipe persistence']
    bpy.context.window.scene = scene
    state = scene.dsts_native_skill
    assert ui.scene_recipe(scene) == full_recipe
    assert state.report_json == full_report
    assert state.inspected_recipe_json == ui._json(full_recipe)
    assert state.output == str(work / 'candidate')
    assert [(row.side, row.source_socket, row.native_root) for row in state.sockets] == [
        (row['side'], row['source_socket'], row.get('native_socket') == '@root')
        for row in full_recipe['socket_mappings']]
    assert len(state.components) == len(full_recipe['components'])
    checks.append('full component pins, native root, NLST inventory and reviewed plan survive save/reopen')
    assert bpy.ops.export_scene.dsts_native_skill_candidate() == {'FINISHED'}
    output = work / 'candidate'
    assert output.is_dir() and not (output / 'dsts-loader').exists()
    assert list(output.rglob('*.bson'))
    before = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in output.rglob('*') if p.is_file()}
    reject(lambda: bpy.ops.export_scene.dsts_native_skill_candidate(), 'exist')
    after = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in output.rglob('*') if p.is_file()}
    assert before == after
    checks.append('real component/BSON candidate built with existing-destination preservation')
    if state.components:
        component = state.components[0]
        pin = json.loads(component.pin_json)
        copied = work / 'stale-component.json'
        copied.write_bytes(Path(pin['path']).read_bytes())
        pin = core.pin_file(copied)
        component.path, component.pin_json = pin['path'], ui._json(pin)
        # A copied manifest may use relative resources; inspection must not silently rebase it.
        copied.write_bytes(copied.read_bytes() + b'\n')
        stale_output = work / 'stale-output'
        state.output = str(stale_output)
        reject(lambda: core.build_candidate(ui.scene_recipe(scene), stale_output,
               expected_plan_sha256=inspection['plan_sha256']), '')
        assert not stale_output.exists()
        checks.append('changed component identity rejects output publication')

report = {'blender': bpy.app.version_string, 'addon_root': str(root),
          'addon_version': list(addon.bl_info['version']), 'backend': str(backend.__file__),
          'backend_sha256': hashlib.sha256(Path(backend.__file__).read_bytes()).hexdigest(),
          'checks': checks, 'checks_passed': len(checks), 'game_acceptance': False}
(work / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
addon.unregister()
addon.register()
addon.unregister()
print('NATIVE SKILL RECIPE WORKFLOW PASS', json.dumps(report))
