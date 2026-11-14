"""Exact source coverage, evidence identity and no-silent-omission checks."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from skill_conversion.coverage import inspect_coverage


def pin(path):
    raw = path.read_bytes()
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.asset = self.root / 'captured.json'
        self.document = {'prefabs': ['effect@socket'], 'nodes': [
            {'path': 'Effect@Socket/active', 'active': True, 'rendererEnabled': True,
             'ps': {'renderMode': 'Mesh', 'rotation3D': True, 'dynamics': {'simulationSpace': 0},
                    'emission': {'enabled': True, 'rateOverTime': {'minMaxState': 0, 'scalar': 0.0}, 'bursts': []}},
             'renderer': {'alignment': 2, 'pivot': [0, 0, 0]}},
            {'path': 'Effect@Socket/disabled', 'active': False, 'rendererEnabled': False,
             'ps': {'renderMode': 'Billboard', 'emits': False,
                    'emission': {'enabled': False, 'rateOverTime': {'minMaxState': 0, 'scalar': 0.0}, 'bursts': []}},
             'renderer': {'alignment': 4}}],
            'effect_nodes': [{'path': 'Effect@Socket/active', 'prefab': 'effect@socket',
                              'components': ['Transform', 'ParticleSystem', 'ParticleSystemRenderer']},
                             {'path': 'Effect@Socket/disabled', 'prefab': 'effect@socket',
                              'components': ['Transform', 'ParticleSystem', 'ParticleSystemRenderer']}]}
        self.event = {'prefab': 'Effect@Socket', 'slot': 'cast', 'frame': 4,
                      'anchor': {'side': 'caster', 'name': 'socket'}}
        self.source = {'model_code': 'x901', 'role': '9001', 'phase_id': '2:0', 'files': [],
                       'effects': [{'event_order': 0, 'path': str(self.asset), 'source_event': self.event}]}
        self.refresh()
        self.proof = self.root / 'audit.txt'
        self.proof.write_text('A bounded producer audit, not in-game acceptance')
        self.evidence_path = self.root / 'coverage.json'

    def tearDown(self):
        self.temp.cleanup()

    def refresh(self):
        self.asset.write_text(json.dumps(self.document))
        self.source['files'] = [pin(self.asset)]

    def component(self, index=0, order=0):
        event = next(event for event in self.source['effects'] if event['event_order'] == order)
        return {'id': f'component-{order}-{index}', 'kind': 'effect',
                'source': {**{key: self.source[key] for key in ('model_code', 'role', 'phase_id')},
                           'asset': pin(self.asset), 'event_order': order, 'source_event': copy.deepcopy(event['source_event']),
                           'node_index': index, 'node_path': self.document['nodes'][index]['path']},
                'profiles': {'coordinate': 'source profile', 'render': 'mesh', 'ownership': 'current owner'}}

    def evidence(self, status='rejected', index=1, reason='Unsupported exact producer feature'):
        origin = self.component(index)['source']
        record = {'source': {key: value for key, value in origin.items() if key not in ('node_index', 'node_path')},
                  'node_index': index, 'node_path': origin['node_path'], 'status': status,
                  'reason': reason, 'provenance': [pin(self.proof)]}
        self.evidence_path.write_text(json.dumps({'format': 'nc-native-skill-coverage-evidence-v1', 'records': [record]}))
        return pin(self.evidence_path)

    def test_exact_features_and_disabled_zero_rate_nodes_remain_uncovered(self):
        result = inspect_coverage(self.source, [self.component()])
        self.assertEqual(result['errors'], [])
        self.assertEqual((result['represented'], result['systems']), (1, 2))
        self.assertEqual(result['uncovered'][0]['node_path'], 'Effect@Socket/disabled')
        row = result['events'][0]['systems'][1]
        self.assertFalse(row['features']['active'])
        self.assertFalse(row['features']['emission']['enabled'])
        self.assertEqual(row['status'], 'missing')
        self.assertFalse(result['coverage_complete'])
        self.assertFalse(result['game_acceptance'])

    def test_same_asset_events_keep_independent_owner_order_and_frame(self):
        target = copy.deepcopy(self.event)
        target.update(frame=35, anchor={'side': 'target', 'name': 'hurt1'})
        self.source['effects'].append({'event_order': 1, 'path': str(self.asset), 'source_event': target})
        result = inspect_coverage(self.source, [self.component()])
        self.assertEqual((result['represented'], result['systems']), (1, 4))
        self.assertEqual([event['source_event']['frame'] for event in result['events']], [4, 35])
        self.assertEqual([row['side'] for row in result['events'][1]['systems']], ['target', 'target'])
        self.assertEqual(result['events'][1]['represented'], 0)

    def test_prefab_scope_uses_exact_casefolded_boundary(self):
        self.document['prefabs'].append('effect@socket_extra')
        self.document['nodes'].append({'path': 'effect@socket_extra/other', 'ps': {'renderMode': 'Billboard'}})
        self.refresh()
        result = inspect_coverage(self.source, [self.component()])
        self.assertEqual(result['systems'], 2)
        self.assertEqual(result['events'][0]['other_prefab_node_indices'], [2])
        self.assertEqual(result['events'][0]['source_event'], self.event)

    def test_hashed_producer_reason_is_preserved_with_source_identity(self):
        evidence = self.evidence(reason='Frozen birth position needs a second current orientation input')
        result = inspect_coverage(self.source, [self.component()], producer_evidence=[evidence, evidence])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['uncovered'][0]['status'], 'rejected')
        self.assertIn('Frozen birth position', result['uncovered'][0]['reasons'][-1])
        self.assertEqual(len(result['evidence']), 1)
        self.assertEqual(result['events'][0]['systems'][1]['evidence'][0]['provenance'], [pin(self.proof)])

    def test_producer_covered_does_not_replace_selected_component(self):
        result = inspect_coverage(self.source, [], producer_evidence=[self.evidence('covered')])
        self.assertEqual(result['represented'], 0)
        self.assertEqual(result['uncovered'][1]['status'], 'missing')

    def test_inactive_evidence_stays_visible_without_inferred_omission(self):
        result = inspect_coverage(self.source, [self.component()], producer_evidence=[self.evidence('inactive')])
        self.assertEqual(result['uncovered'][0]['status'], 'inactive')
        self.assertFalse(result['coverage_complete'])

    def test_selected_component_conflicting_with_rejection_is_error(self):
        result = inspect_coverage(self.source, [self.component(0), self.component(1)], producer_evidence=[self.evidence()])
        self.assertEqual(result['represented'], 1)
        self.assertEqual(result['uncovered'][0]['status'], 'conflict')
        self.assertTrue(any(row['code'] == 'coverage-conflict' for row in result['errors']))

    def test_stale_provenance_and_changed_event_cannot_supply_reason(self):
        evidence = self.evidence()
        self.proof.write_text('Changed audit')
        result = inspect_coverage(self.source, [], producer_evidence=[evidence])
        self.assertTrue(result['errors'])
        self.assertEqual(result['uncovered'][1]['status'], 'missing')
        evidence = self.evidence()
        raw = json.loads(self.evidence_path.read_text())
        raw['records'][0]['source']['source_event']['frame'] += 1
        self.evidence_path.write_text(json.dumps(raw))
        result = inspect_coverage(self.source, [], producer_evidence=[pin(self.evidence_path)])
        self.assertTrue(result['errors'])
        self.assertEqual(result['uncovered'][1]['status'], 'missing')

    def test_duplicate_or_mismatched_component_identity_does_not_cover_other_node(self):
        first = self.component()
        wrong = self.component(1)
        wrong['source']['node_path'] = first['source']['node_path']
        result = inspect_coverage(self.source, [first, first, wrong])
        self.assertEqual(result['represented'], 1)
        self.assertEqual(len(result['errors']), 2)
        self.assertEqual(result['uncovered'][0]['node_index'], 1)

    def test_additional_scene_records_prevent_complete_coverage(self):
        self.document['effect_nodes'].append({'path': 'Effect@Socket/light', 'prefab': 'effect@socket',
                                              'components': ['Transform', 'Light']})
        self.refresh()
        result = inspect_coverage(self.source, [self.component(0), self.component(1)])
        self.assertEqual(result['represented'], 2)
        self.assertEqual(result['uncovered'], [])
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['events'][0]['other_records'][0]['node_path'], 'Effect@Socket/light')


if __name__ == '__main__':
    unittest.main()
