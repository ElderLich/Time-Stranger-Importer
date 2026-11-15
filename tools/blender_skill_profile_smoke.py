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
        ('view_mesh_profile', 'test_skill_view_mesh_profile.py', 'profile'),
        ('velocity_billboard_profile', 'test_skill_velocity_billboard_profile.py', 'p'),
        ('sampled_particle_profile', 'test_skill_sampled_particle_profile.py', 'profile'),
        ('dissolve_profile', 'test_skill_dissolve_profile.py', 'profile'),
        ('mixed_owner_profile', 'test_skill_mixed_owner_profile.py', 'profile'),
        ('reviewed_actor', 'test_skill_reviewed_actor.py', 'actor'),
        ('finite_producer', 'test_skill_finite_producer.py', 'producer'),
        ('coverage', 'test_skill_coverage.py', 'inspect_coverage')):
    actual = importlib.import_module('Time_Stranger_Importer.src.skill_conversion.' + profile)
    assert Path(actual.__file__).resolve().is_relative_to(root)
    testspec = importlib.util.spec_from_file_location('installed_cases_' + profile, test_root / filename)
    cases = importlib.util.module_from_spec(testspec)
    testspec.loader.exec_module(cases)
    # Test fixtures and independent expected values stay unchanged. All calls
    # under test are rebound to the actual installed implementation, not a copy.
    implementation = actual if binding in ('profile', 'producer', 'p', 'actor') else getattr(actual, binding)
    setattr(cases, binding, implementation)
    assert getattr(cases, binding) is implementation
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(cases))
    records.append({'module': profile, 'path': str(Path(actual.__file__).resolve()),
                    'sha256': hashlib.sha256(Path(actual.__file__).read_bytes()).hexdigest()})
# Exercise native bindings against the installed compiler and BSON writer too.
sys.path.insert(0, str(test_root))
cases = importlib.import_module('test_skill_native_bindings')
for module_name, names in (('bson', ('compose_event',)),
                           ('compiler', ('inspect_recipe', 'validate_native_bindings'))):
    actual = importlib.import_module('Time_Stranger_Importer.src.skill_conversion.' + module_name)
    assert Path(actual.__file__).resolve().is_relative_to(root)
    for name in names:
        setattr(cases, name, getattr(actual, name))
    records.append({'module': module_name, 'path': str(Path(actual.__file__).resolve()),
                    'sha256': hashlib.sha256(Path(actual.__file__).read_bytes()).hexdigest()})
suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(cases))
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
