import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import stage_native_asset_package as pack


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        baseline = self.root / 'baseline'
        baseline.mkdir()
        config = baseline / 'ModConfig.json'
        config.write_bytes(b'preserved user mod config')
        table = baseline / 'data.csv'
        table.write_bytes(b'old table')
        manifest = baseline / 'assembly-report.json'
        manifest.write_text(json.dumps({'files': [dict(path=p.name, sha256=pack.pin_file(p)['sha256']) for p in (config, table)]}))
        candidate = self.root / 'candidate'
        motion = candidate / 'resources/patch/actor_bs03.anim'
        motion.parent.mkdir(parents=True)
        motion.write_bytes(b'new animation')
        self.candidate_manifest = candidate / 'candidate.json'
        self.candidate_manifest.write_text(json.dumps({'format': 'nc-native-skill-candidate-v1',
            'coverage': {'represented': 1, 'systems': 1, 'uncovered': []}, 'plan_sha256': 'plan',
            'files': [dict(path='resources/patch/actor_bs03.anim', sha256=pack.pin_file(motion)['sha256'], bytes=motion.stat().st_size)]}))
        self.job = {'format': 'nc-native-asset-package-job-v1', 'baseline': pack.pin_file(manifest),
                    'candidates': [pack.pin_file(self.candidate_manifest)], 'overlays': []}
        self.motion = motion

    def test_native_layout_and_baseline_preserved(self):
        report = pack.stage(self.job, self.root / 'out')
        self.assertEqual(report['managed_files'], 3)
        self.assertEqual(report['new_files'], 1)
        self.assertEqual((self.root / 'out/dsts-loader/patch/actor_bs03.anim').read_bytes(), b'new animation')
        self.assertEqual((self.root / 'out/ModConfig.json').read_bytes(), b'preserved user mod config')
        self.assertEqual(report['live_payload_writes'], 0)

    def test_table_replacement_requires_exact_baseline_hash(self):
        overlay = self.root / 'tables/new.csv'
        overlay.parent.mkdir()
        overlay.write_bytes(b'new table')
        self.job['overlays'] = [{'file': pack.pin_file(overlay), 'destination': 'data.csv'}]
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            pack.stage(self.job, self.root / 'out')
        self.job['overlays'][0]['replaces_sha256'] = pack.pin_file(self.root / 'baseline/data.csv')['sha256']
        report = pack.stage(self.job, self.root / 'out')
        self.assertEqual(len(report['replacements']), 1)
        self.assertEqual((self.root / 'baseline/data.csv').read_bytes(), b'old table')

    def test_unrepresented_source_and_stale_resource_rejected(self):
        doc = json.loads(self.candidate_manifest.read_bytes())
        doc['coverage']['systems'] = 2
        self.candidate_manifest.write_text(json.dumps(doc))
        self.job['candidates'] = [pack.pin_file(self.candidate_manifest)]
        with self.assertRaisesRegex(ValueError, 'unrepresented'):
            pack.stage(self.job, self.root / 'out')
        doc['coverage']['systems'] = 1
        self.candidate_manifest.write_text(json.dumps(doc))
        self.job['candidates'] = [pack.pin_file(self.candidate_manifest)]
        self.motion.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Stale'):
            pack.stage(self.job, self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

    def test_existing_output_and_failed_publication_preserved(self):
        existing = self.root / 'existing'
        existing.mkdir()
        sentinel = existing / 'keep'
        sentinel.write_bytes(b'unchanged')
        with self.assertRaises(ValueError):
            pack.stage(self.job, existing)
        with patch.object(pack.os, 'rename', side_effect=OSError('publication blocked')):
            with self.assertRaises(OSError):
                pack.stage(self.job, self.root / 'out')
        self.assertEqual(sentinel.read_bytes(), b'unchanged')
        self.assertFalse((self.root / 'out').exists())
        self.assertFalse(list(self.root.glob('.out.pending-*')))

    def test_case_aliases_and_directories_cannot_overwrite_package_metadata(self):
        names = ('Assembly-Report.json', 'PACKAGE-JOB.JSON',
                 'assembly-report.json/child.anim', 'Package-Job.json/child.anim')
        for index, name in enumerate(names):
            candidate = self.root / ('metadata-candidate-' + str(index))
            relative = 'resources/mod/' + name
            resource = candidate / relative
            resource.parent.mkdir(parents=True)
            resource.write_bytes(b'candidate resource must remain unchanged')
            manifest = candidate / 'candidate.json'
            document = json.loads(self.candidate_manifest.read_bytes())
            document['files'] = [dict(path=relative, sha256=pack.pin_file(resource)['sha256'], bytes=resource.stat().st_size)]
            manifest.write_text(json.dumps(document))
            self.job['candidates'] = [pack.pin_file(manifest)]
            with self.subTest(destination=name), self.assertRaisesRegex(ValueError, 'Reserved package metadata'):
                pack.stage(self.job, self.root / 'out')
            self.assertEqual(resource.read_bytes(), b'candidate resource must remain unchanged')
            self.assertFalse((self.root / 'out').exists())
            self.assertFalse(list(self.root.glob('.out.pending-*')))


if __name__ == '__main__':
    unittest.main()
