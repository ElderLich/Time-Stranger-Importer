"""Run independent profile/coverage regression cases against installed modules."""
import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

import bpy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--addon-root', type=Path, required=True)
parser.add_argument('--report', type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
root = args.addon_root.resolve()
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root / '__init__.py',
                                            submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
suite = unittest.TestSuite()
records = []
test_root = Path(__file__).resolve().parents[1] / 'tests'
for profile, filename, binding in (
        ('mesh_profile', 'test_skill_mesh_profile.py', 'profile'),
        ('billboard_profile', 'test_skill_billboard_profile.py', 'profile'),
        ('coverage', 'test_skill_coverage.py', 'inspect_coverage')):
    actual = importlib.import_module('Time_Stranger_Importer.src.skill_conversion.' + profile)
    assert Path(actual.__file__).resolve().is_relative_to(root)
    testspec = importlib.util.spec_from_file_location('installed_cases_' + profile, test_root / filename)
    cases = importlib.util.module_from_spec(testspec)
    testspec.loader.exec_module(cases)
    # Test fixtures and independent expected values stay unchanged. All calls
    # under test are rebound to the actual installed implementation, not a copy.
    setattr(cases, binding, actual if binding == 'profile' else actual.inspect_coverage)
    assert getattr(cases, binding) is (actual if binding == 'profile' else actual.inspect_coverage)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(cases))
    records.append({'module': profile, 'path': str(Path(actual.__file__).resolve()),
                    'sha256': hashlib.sha256(Path(actual.__file__).read_bytes()).hexdigest()})
result = unittest.TextTestRunner(verbosity=1).run(suite)
report = {'blender': bpy.app.version_string, 'addon_root': str(root),
          'version': list(addon.bl_info['version']), 'modules': records,
          'checks': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
          'skipped': len(result.skipped), 'game_acceptance': False}
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
if not result.wasSuccessful():
    raise AssertionError('Installed profile regression failed')
print('INSTALLED PROFILE CHECKS PASS', json.dumps(report))
