import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import sync_installed_addon as installer


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.zip = self.root / 'addon.zip'
        with zipfile.ZipFile(self.zip, 'w') as archive:
            archive.writestr('Time_Stranger_Importer/__init__.py', b'new source')
            archive.writestr('Time_Stranger_Importer/src/dsts_formats.cp313-win_amd64.pyd', b'test backend')
        self.targets = []
        for version in ('5.1', '5.3'):
            target = self.root / version / 'scripts/addons/Time_Stranger_Importer'
            target.mkdir(parents=True)
            (target / '__init__.py').write_bytes(b'old source')
            (target / 'custom.txt').write_bytes(b'user local data')
            self.targets.append(target)

    def test_plan_install_backup_and_repeat(self):
        plan = installer.sync(self.zip, self.root, ['5.1', '5.3'])
        self.assertFalse(plan['applied'])
        self.assertEqual((self.targets[0] / '__init__.py').read_bytes(), b'old source')
        report = installer.sync(self.zip, self.root, ['5.1', '5.3'], True)
        for result in report['installations']:
            backup = Path(result['backup'])
            self.assertEqual((backup / 'custom.txt').read_bytes(), b'user local data')
            self.assertEqual((backup / '__init__.py').read_bytes(), b'old source')
            self.assertTrue(result['verified'])
        repeat = installer.sync(self.zip, self.root, ['5.1', '5.3'], True)
        self.assertTrue(all(not item['changed'] and item['verified'] for item in repeat['installations']))

    def test_second_install_failure_restores_both_versions(self):
        rename = installer.os.rename
        def fail_second(source, target):
            if Path(target) == self.targets[1] and '.dsts-stage-' in str(source):
                raise PermissionError('backend locked')
            return rename(source, target)
        with patch.object(installer.os, 'rename', side_effect=fail_second):
            with self.assertRaises(PermissionError):
                installer.sync(self.zip, self.root, ['5.1', '5.3'], True)
        for target in self.targets:
            self.assertEqual((target / '__init__.py').read_bytes(), b'old source')
            self.assertEqual((target / 'custom.txt').read_bytes(), b'user local data')

    def test_unexpected_zip_path_rejected_before_mutation(self):
        with zipfile.ZipFile(self.zip, 'a') as archive:
            archive.writestr('Time_Stranger_Importer/../unrelated.txt', b'bad path')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            installer.sync(self.zip, self.root, ['5.1', '5.3'], True)
        self.assertEqual((self.targets[0] / '__init__.py').read_bytes(), b'old source')

    def test_unreadable_installation_is_not_treated_as_empty(self):
        with patch.object(installer.os, 'scandir', side_effect=PermissionError('access denied')):
            with self.assertRaises(PermissionError):
                installer.sync(self.zip, self.root, ['5.1', '5.3'], True)
        for target in self.targets:
            self.assertEqual((target / '__init__.py').read_bytes(), b'old source')
            self.assertEqual((target / 'custom.txt').read_bytes(), b'user local data')

    def test_new_installation_requires_explicit_option_and_only_selected_version(self):
        fresh = self.root / 'fresh'
        with self.assertRaises(FileNotFoundError):
            installer.sync(self.zip, fresh, ['5.3'], True)
        plan = installer.sync(self.zip, fresh, ['5.3'], allow_new=True)
        self.assertTrue(plan['installations'][0]['new'])
        self.assertFalse(fresh.exists())
        report = installer.sync(self.zip, fresh, ['5.3'], True, allow_new=True)
        result = report['installations'][0]
        self.assertTrue(result['verified'])
        self.assertIsNone(result['backup'])
        self.assertFalse((fresh / '5.1').exists())
        self.assertEqual(installer.hashes(Path(result['target'])),
                         {name: installer.digest(raw) for name, raw in installer.read_package(self.zip).items()})

    def test_failed_second_fresh_install_restores_absence(self):
        fresh = self.root / 'fresh'
        rename = installer.os.rename
        def fail_second(source, target):
            if Path(target) == fresh / '5.3/scripts/addons/Time_Stranger_Importer':
                raise PermissionError('publication blocked')
            return rename(source, target)
        with patch.object(installer.os, 'rename', side_effect=fail_second):
            with self.assertRaises(PermissionError):
                installer.sync(self.zip, fresh, ['5.1', '5.3'], True, allow_new=True)
        for version in ('5.1', '5.3'):
            self.assertFalse((fresh / version / 'scripts/addons/Time_Stranger_Importer').exists())


if __name__ == '__main__':
    unittest.main()
