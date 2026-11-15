"""Pinned native row edits, collision checks and publication preservation."""
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare_native_table_overlay as overlay


class NativeTableOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        source_root = self.root / 'inputs'
        source_root.mkdir()
        self.source = source_root / 'baseline.ap.csv'
        self.source.write_bytes(b'\xef\xbb\xbfid,label,route\r\n1,"old, label",10\r\n2,keep,20\r\n')
        self.donor = source_root / 'donor.ap.csv'
        self.donor.write_bytes(b'id,label,route\n8,"donor\nlabel",80\n')
        self.source_bytes = self.source.read_bytes()
        self.donor_bytes = self.donor.read_bytes()
        self.job = {'format':'nc-native-table-overlay-job-v1', 'tables':[{
            'source':overlay.pin_file(self.source), 'destination':'data/skill.mbe/001_rows.ap.csv',
            'operations':[
                {'kind':'update', 'source_id':1, 'fields':{'2':{'before':'10', 'after':'11'}}},
                {'kind':'clone', 'source':overlay.pin_file(self.donor), 'source_id':8, 'new_id':3,
                 'fields':{'1':{'before':'donor\nlabel', 'after':'new, label'}, '2':{'before':80, 'after':30}}}
            ]}]}
        self.output = self.root / 'review'

    def assert_preserved(self):
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertEqual(self.donor.read_bytes(), self.donor_bytes)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.review.pending-*')))

    def test_explicit_update_clone_and_audit_preserve_original_sources(self):
        job_before = copy.deepcopy(self.job)
        report = overlay.prepare(self.job, self.output)
        path = self.output / self.job['tables'][0]['destination']
        got = list(csv.reader(io.StringIO(path.read_text(encoding='utf8'), newline='')))
        self.assertEqual(got, [['id','label','route'], ['1','old, label','11'], ['2','keep','20'], ['3','new, label','30']])
        self.assertEqual(report['overlays'][0]['file'], overlay.pin_file(path))
        self.assertEqual(report['overlays'][0]['replaces_sha256'], self.job['tables'][0]['source']['sha256'])
        changes = report['changes'][0]['operations']
        self.assertEqual(changes[0]['fields'], [{'column':2,'before':'10','after':'11'}])
        self.assertEqual(changes[1]['fields'][0], {'column':0,'before':'8','after':'3'})
        self.assertEqual(changes[1]['source'], overlay.pin_file(self.donor))
        self.assertEqual(len(report['inputs']),2)
        self.assertEqual(json.loads((self.output/'table-overlay-report.json').read_text()), report)
        self.assertEqual(self.job,job_before)
        self.assertEqual(self.source.read_bytes(),self.source_bytes)
        self.assertEqual(self.donor.read_bytes(),self.donor_bytes)
        self.assertFalse(list(self.root.glob('.review.pending-*')))

    def test_sequential_updates_check_the_current_row(self):
        self.job['tables'][0]['operations'] = [
            {'kind':'update','source_id':1,'fields':{'2':{'before':'10','after':'11'}}},
            {'kind':'update','source_id':1,'fields':{'2':{'before':'11','after':'12'}}}]
        report=overlay.prepare(self.job,self.output)
        self.assertEqual(report['changes'][0]['operations'][1]['fields'],[{'column':2,'before':'11','after':'12'}])

    def test_stale_pin_and_wrong_precondition_fail_before_publication(self):
        for mutation in ('pin','precondition'):
            job=copy.deepcopy(self.job)
            if mutation=='pin':job['tables'][0]['source']['sha256']='0'*64
            else:job['tables'][0]['operations'][0]['fields']['2']['before']='9'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                overlay.prepare(job,self.output)
            self.assert_preserved()

    def test_windows_destination_collisions_and_traversal_fail_before_writes(self):
        original=self.job['tables'][0]['destination']
        for name in (original.upper(), original+'/child.ap.csv', '../outside.ap.csv'):
            job=copy.deepcopy(self.job)
            table=copy.deepcopy(job['tables'][0]);table['destination']=name
            job['tables'].append(table)
            with self.subTest(destination=name),self.assertRaises(ValueError):
                overlay.prepare(job,self.output)
            self.assert_preserved()

    def test_clone_collisions_and_external_updates_are_rejected(self):
        for kind in ('collision','external-update','missing-row','blank-id'):
            job=copy.deepcopy(self.job);op=job['tables'][0]['operations'][1]
            if kind=='collision':op['new_id']='2'
            elif kind=='external-update':op['kind']='update'
            elif kind=='missing-row':op['source_id']='404'
            else:op['new_id']=''
            with self.subTest(kind=kind),self.assertRaises(ValueError):overlay.prepare(job,self.output)
            self.assert_preserved()

    def test_invalid_field_indices_and_cells_are_rejected(self):
        fields=[{'0':{'before':'1','after':'4'}}, {'3':{'before':'10','after':'4'}},
                {'01':{'before':'old, label','after':'x'}},
                {'2':{'before':'10','after':float('nan')}}, {'2':{'before':'10','after':None}},
                {'2':{'before':'10','after':{'arbitrary':'object'}}},
                {'2':{'before':'10','after':'bad\0cell'}},
                {'2':{'before':'10','after':'11'},2:{'before':'11','after':'12'}}]
        for edits in fields:
            job=copy.deepcopy(self.job);job['tables'][0]['operations'][0]['fields']=edits
            with self.subTest(fields=edits),self.assertRaises(ValueError):overlay.prepare(job,self.output)
            self.assert_preserved()

    def test_malformed_baseline_and_donor_schema_fail(self):
        for raw in (b'',b'\n',b'id,label\n1,a\n1,b\n',b'id,label\n1\n',b'id,label\n,a\n'):
            malformed=self.source.parent/'malformed.ap.csv';malformed.write_bytes(raw)
            job=copy.deepcopy(self.job);job['tables'][0]['source']=overlay.pin_file(malformed)
            with self.subTest(raw=raw),self.assertRaises(ValueError):overlay.prepare(job,self.output)
            self.assert_preserved()
        wrong=self.source.parent/'wrong-schema.ap.csv';wrong.write_bytes(b'id,label,other\n8,donor,80\n')
        job=copy.deepcopy(self.job);job['tables'][0]['operations'][1]['source']=overlay.pin_file(wrong)
        with self.assertRaisesRegex(ValueError,'schema'):overlay.prepare(job,self.output)
        self.assert_preserved()

    def test_existing_destination_and_source_tree_are_preserved(self):
        self.output.mkdir();sentinel=self.output/'user.txt';sentinel.write_bytes(b'keep')
        with self.assertRaises(ValueError):overlay.prepare(self.job,self.output)
        self.assertEqual(sentinel.read_bytes(),b'keep')
        with self.assertRaisesRegex(ValueError,'overlaps'):overlay.prepare(self.job,self.source.parent/'new')
        self.assertFalse((self.source.parent/'new').exists())
        self.assertEqual(self.source.read_bytes(),self.source_bytes)

    def test_write_failure_removes_only_private_stage(self):
        write=overlay._write
        def fail_report(path,raw):
            if Path(path).name=='table-overlay-report.json':raise OSError('disk full')
            write(path,raw)
        with patch.object(overlay,'_write',side_effect=fail_report),self.assertRaisesRegex(OSError,'disk full'):
            overlay.prepare(self.job,self.output)
        self.assert_preserved()

    def test_mid_build_source_change_blocks_publication(self):
        write=overlay._write
        def change_input(path,raw):
            write(path,raw)
            if Path(path).name=='table-overlay-report.json':self.source.write_bytes(b'user changed source')
        with patch.object(overlay,'_write',side_effect=change_input),self.assertRaisesRegex(ValueError,'Stale'):
            overlay.prepare(self.job,self.output)
        self.assertEqual(self.source.read_bytes(),b'user changed source')
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.review.pending-*')))

    def test_competing_destination_is_not_replaced(self):
        write=overlay._write
        def make_destination(path,raw):
            write(path,raw)
            if Path(path).name=='table-overlay-report.json':
                self.output.mkdir();(self.output/'user.txt').write_bytes(b'keep')
        with patch.object(overlay,'_write',side_effect=make_destination),self.assertRaisesRegex(ValueError,'appeared'):
            overlay.prepare(self.job,self.output)
        self.assertEqual((self.output/'user.txt').read_bytes(),b'keep')
        self.assertEqual(self.source.read_bytes(),self.source_bytes)
        self.assertFalse(list(self.root.glob('.review.pending-*')))


if __name__=='__main__':
    unittest.main()
