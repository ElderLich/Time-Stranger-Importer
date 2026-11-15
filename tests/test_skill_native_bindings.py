"""Native hit/audio bindings use the game's BSON fields and target origin."""
import unittest

import test_skill_conversion as fixtures
from test_skill_conversion import bson_fixture
from effect_bson import EffectDocument
from skill_conversion.bson import compose_event
from skill_conversion.compiler import inspect_recipe, validate_native_bindings


class NativeBindingTests(unittest.TestCase):
    def test_hit_frames_and_sound_are_native_fields(self):
        original = bson_fixture(with_audio=True)
        raw, proof = compose_event(original, ['first', 'second'], socket='@root', duration_frames=120,
            hit_frames=[35, 50, 60], audio={'cue_id': 329513, 'frame': 10, 'stop_on_end': True}, sound_template_set='set1')
        doc = EffectDocument.from_bytes(raw)
        self.assertEqual([doc.value(('hf', f'hf{i}')) for i in range(8)], [35, 50, 60, 0, 0, 0, 0, 0])
        self.assertEqual(doc.sets[0].sound_name, '329513')
        self.assertEqual(doc.sets[0].sound_start_frame, 10)
        self.assertIsNone(doc.sets[1].sound_name)
        self.assertEqual(doc.value(('set0', 'common', 'se', 'se_stop')), 1)
        self.assertEqual(doc.value(('set0', 'common', 'se', 'se_fadeout')), 0)
        self.assertEqual(EffectDocument.from_bytes(original).value(('set1', 'common', 'se', 'se_name')), 'old_audio')
        self.assertEqual(proof['hit_frames'], [35, 50, 60])

    def test_outer_hit_origin_does_not_override_target_locator(self):
        raw, proof = compose_event(bson_fixture(), ['target'], socket='@effect', duration_frames=120)
        doc = EffectDocument.from_bytes(raw)
        self.assertEqual(doc.sets[0].attachment_owner, '')
        for key in ('bt_pos', 'bt_rot', 'bt_sca'):
            self.assertEqual(doc.value(('set0', 'common', 'bt', key)), '')
        for key in ('ca_pos', 'ca_rot', 'ca_sca', 'ca_ref'):
            self.assertEqual(doc.value(('set0', 'common', 'ca', key)), 0)
        self.assertEqual(proof['socket'], '@effect')

    def test_invalid_native_timing_and_audio_rejected(self):
        for frames in ([0], [1, 1], [2, 1], [121], [True], list(range(1, 10))):
            with self.assertRaises(ValueError):
                compose_event(bson_fixture(), ['figure'], socket='@root', duration_frames=120, hit_frames=frames)
        for audio in ({'cue_id': 0, 'frame': 1, 'stop_on_end': True},
                      {'cue_id': 22, 'frame': -1, 'stop_on_end': True},
                      {'cue_id': 22, 'frame': 1, 'stop_on_end': 1}):
            with self.assertRaises(ValueError):
                compose_event(bson_fixture(True), ['figure'], socket='@root', duration_frames=120, audio=audio, sound_template_set='set1')

    def test_named_target_candidate_keeps_explicit_native_origin(self):
        fixture = fixtures.SkillConversionTests()
        fixture.setUp()
        try:
            recipe = fixture.recipe
            recipe['socket_mappings'][0]['native_socket'] = '@effect'
            recipe['native_bindings'] = {'caster_basename': 'ef_caster', 'target_basename': 'ef_target',
                'hit_frames': [], 'include_audio': False, 'sound_template_set': 'set1', 'stop_audio_on_end': True}
            report = inspect_recipe(recipe)
            self.assertTrue(report['buildable'], report['errors'])
            self.assertEqual(report['compositions'][0]['path'], 'ef_target.bson')
            self.assertEqual(report['compositions'][0]['provenance']['socket'], '@effect')
            self.assertFalse(any(row['code'] == 'socket-unverified' for row in report['blockers']))
            recipe['native_bindings']['hit_frames'] = [7]
            self.assertTrue(any(row['code'] == 'native-binding-caster-missing' for row in inspect_recipe(recipe)['errors']))
            recipe['native_bindings']['hit_frames'] = []
            recipe['native_bindings']['include_audio'] = True
            self.assertTrue(any(row['code'] == 'native-binding-caster-missing' for row in inspect_recipe(recipe)['errors']))
            recipe['native_bindings']['include_audio'] = False
            recipe['native_bindings']['target_basename'] = 'EF_CASTER'
            self.assertTrue(inspect_recipe(recipe)['errors'])
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
