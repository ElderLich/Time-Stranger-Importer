"""Reopen any saved skill workbench and rehash all native review resources."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import bpy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--addon-root', type=Path, required=True)
parser.add_argument('--workbench', type=Path, required=True)
parser.add_argument('--recipes', type=Path, nargs='+', required=True)
parser.add_argument('--report', type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
root = args.addon_root.resolve()
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root / '__init__.py', submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src import nc_native_skill_ui as ui

recipes = {}
for path in args.recipes:
    recipe = json.loads(path.read_bytes())
    key = (recipe['source']['model_code'], recipe['source']['phase_id'])
    assert key not in recipes
    for side in ('caster', 'target'):
        recipe['target'].setdefault('socket_inventory', {}).setdefault(side, None)
    recipes[key] = recipe
prior = json.loads((args.workbench / 'workbench-report.json').read_bytes())
blend = Path(prior['blend'])
assert bpy.ops.wm.open_mainfile(filepath=str(blend)) == {'FINISHED'}
records = []
for record in prior['scenes']:
    scene = bpy.data.scenes[record['scene']]
    recipe = ui.scene_recipe(scene)
    assert recipe == recipes.pop((recipe['source']['model_code'], record['phase']))
    assert scene.get('DSTS_preview_actor_clip') == record['source_clip']
    assert scene.get('DSTS_preview_reset_actor') and scene.get('DSTS_skill_review_only')
    assert scene.dsts_native_skill.producer_job_pin_json == record['producer_job_pin']
    assert scene.dsts_native_skill.producer_output == record['producer_output']
    if record['producer_job_pin']:
        assert ui.producer_job(scene)['source'] == recipe['source']
    inspection = json.loads(scene.dsts_native_skill.report_json)
    assert inspection['plan_sha256'] == record['plan_sha256']
    candidate_root = Path(record['candidate']).resolve()
    candidate = json.loads((candidate_root / 'candidate.json').read_bytes())
    assert candidate['plan_sha256'] == record['plan_sha256'] and not candidate['installable']
    assert candidate['coverage'] == inspection['coverage']
    for entry in candidate['files']:
        path = (candidate_root / entry['path']).resolve()
        assert path.is_relative_to(candidate_root)
        raw = path.read_bytes()
        assert len(raw) == entry['bytes'] and hashlib.sha256(raw).hexdigest() == entry['sha256']
    records.append({'phase': record['phase'], 'source_clip': record['source_clip'],
                    'represented': candidate['coverage']['represented'], 'systems': candidate['coverage']['systems'],
                    'resources': len(candidate['files']), 'reset_preserved': True})
assert not recipes, 'Some requested recipes were absent from the workbench'
missing = [s.filepath for s in bpy.data.sounds if not Path(bpy.path.abspath(s.filepath)).is_file()]
assert not missing
report = {'blender': bpy.app.version_string, 'version': list(addon.bl_info['version']), 'addon_root': str(root),
          'blend': str(blend), 'blend_sha256': hashlib.sha256(blend.read_bytes()).hexdigest(),
          'scenes': records, 'missing_sounds': missing, 'installable': False, 'game_acceptance': False}
args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
addon.unregister()
print('WORKBENCH REOPEN PASS', json.dumps(report))
