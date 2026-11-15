"""Audited motion reuse preserves bytes, source identity and output boundaries."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from skill_conversion import reviewed_actor as actor
from skill_conversion.compiler import pin_file


class ReviewedActorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.library = self.root / 'library'
        self.library.mkdir()
        self.model = self.library / 'model.glb'
        self.model.write_bytes(b'captured model')
        self.producer = self.root / 'audited'
        self.producer.mkdir()
        geom = self.producer / 'chr701.geom'
        geom.write_bytes(b'audited native skeleton')
        self.motion = self.producer / 'chr701_ba02.anim'
        self.original = b'existing audited motion bytes'
        self.motion.write_bytes(self.original)
        self.audit_path = self.producer / 'audit.json'
        self.audit = {'format': 'ordinary-attack-actor-fragments-v1',
            'source_files': {str(p): pin_file(p)['sha256'] for p in (geom, self.model)},
            'clips': [{'file': self.motion.name, 'source_clip': 'approach', 'sha256': pin_file(self.motion)['sha256'],
                'frames': 72, 'playback_rate': 30, 'bone_count': 103, 'checked_frames': 72,
                'checked_source_bones': 101, 'maximum_position_error': .0005, 'maximum_matrix_error': .0004}]}
        self.write_audit()
        self.source = {'model_path': str(self.model), 'library': str(self.library), 'phase_id': '1:0',
            'role': '900', 'model_code': 'x701', 'phase': {'actor_clip': 'approach'}, 'files': [pin_file(self.model)]}
        self.recipe = {'source': self.source, 'target': {'model_stem': 'chr701', 'motion_suffix': 'bs03'}}
        snapshot = patch.object(actor, '_source_snapshot', return_value=self.source)
        snapshot.start()
        self.addCleanup(snapshot.stop)
        self.job = actor.create_actor_job(self.recipe, self.audit_path)

    def write_audit(self):
        self.audit_path.write_text(json.dumps(self.audit), encoding='utf8')

    def test_rebind_preserves_animation_and_audited_evidence(self):
        report = actor.inspect_actor_job(self.job)
        self.assertTrue(report['buildable'], report['errors'])
        result = actor.produce_actor_component(self.job, self.root / 'output', expected_plan_sha256=report['plan_sha256'])
        component = json.loads(Path(result['components'][0]['path']).read_bytes())
        output = self.root / 'output' / component['dependencies'][0]['path']
        self.assertEqual(output.name, 'chr701_bs03.anim')
        self.assertEqual(output.read_bytes(), self.original)
        self.assertEqual(self.motion.read_bytes(), self.original)
        self.assertEqual(component['source']['actor_clip'], 'approach')
        self.assertEqual(component['motion_binding']['battle_execution'], 'native game')
        self.assertIn(pin_file(self.audit_path), component['provenance'])

    def test_changed_animation_rejected(self):
        self.motion.write_bytes(b'changed')
        report = actor.inspect_actor_job(self.job)
        self.assertFalse(report['buildable'])
        self.assertIn('motion changed', report['errors'][0]['message'])

    def test_different_actor_rejected(self):
        self.job['target']['model_stem'] = 'chr702'
        self.assertFalse(actor.inspect_actor_job(self.job)['buildable'])

    def test_incomplete_or_failed_pose_audit_rejected(self):
        for key, value in [('checked_frames', 71), ('maximum_matrix_error', .1), ('maximum_position_error', float('nan'))]:
            audit = copy.deepcopy(self.audit)
            audit['clips'][0][key] = value
            self.audit_path.write_text(json.dumps(audit), encoding='utf8')
            self.job['audit'] = pin_file(self.audit_path)
            self.assertFalse(actor.inspect_actor_job(self.job)['buildable'])

    def test_wrong_clip_and_changed_source_rejected(self):
        self.audit['clips'][0]['source_clip'] = 'different'
        self.write_audit()
        self.job['audit'] = pin_file(self.audit_path)
        self.assertFalse(actor.inspect_actor_job(self.job)['buildable'])
        self.audit['clips'][0]['source_clip'] = 'approach'
        self.write_audit()
        self.job['audit'] = pin_file(self.audit_path)
        self.model.write_bytes(b'changed model')
        self.assertFalse(actor.inspect_actor_job(self.job)['buildable'])

    def test_existing_and_source_output_protected(self):
        output = self.root / 'existing'
        output.mkdir()
        sentinel = output / 'user.txt'
        sentinel.write_bytes(b'keep')
        for destination in (output, self.producer / 'new', self.library / 'new'):
            with self.assertRaises(ValueError):
                actor.produce_actor_component(self.job, destination)
        self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_changed_mapping_rejects_old_plan(self):
        report = actor.inspect_actor_job(self.job)
        self.job['target']['motion_suffix'] = 'bs04'
        with self.assertRaisesRegex(ValueError, 'stale'):
            actor.produce_actor_component(self.job, self.root / 'new', expected_plan_sha256=report['plan_sha256'])
        self.assertFalse((self.root / 'new').exists())

    def test_mid_build_change_does_not_publish(self):
        with patch.object(actor, 'inspect_actor_job', return_value={'plan_sha256': 'changed'}):
            with self.assertRaisesRegex(ValueError, 'changed during generation'):
                actor.produce_actor_component(self.job, self.root / 'new')
        self.assertFalse((self.root / 'new').exists())
        self.assertFalse(list(self.root.glob('.new.pending-*')))


if __name__ == '__main__':
    unittest.main()
