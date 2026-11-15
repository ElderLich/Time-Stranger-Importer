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
        for frames in ([-1], [1, 1], [2, 1], [121], [True], list(range(1, 10))):
            with self.assertRaises(ValueError):
                compose_event(bson_fixture(), ['figure'], socket='@root', duration_frames=120, hit_frames=frames)
        for audio in ({'cue_id': 0, 'frame': 1, 'stop_on_end': True},
                      {'cue_id': 22, 'frame': -1, 'stop_on_end': True},
                      {'cue_id': 22, 'frame': 1, 'stop_on_end': 1}):
            with self.assertRaises(ValueError):
                compose_event(bson_fixture(True), ['figure'], socket='@root', duration_frames=120, audio=audio, sound_template_set='set1')

    def test_repeated_figures_use_native_set_clocks_and_zero_first_hit(self):
        original = bson_fixture()
        raw, proof = compose_event(original, ['impact', 'smoke', 'impact', 'smoke', 'impact', 'smoke'],
            socket='@effect', duration_frames=145, figure_start_frames=[0, 0, 15, 15, 25, 25], hit_frames=[0, 15, 25])
        doc = EffectDocument.from_bytes(raw)
        self.assertEqual([doc.value((s.name, 'common', 'so')) for s in doc.sets], [0, 0, 15, 15, 25, 25])
        self.assertEqual(len({s.id for s in doc.sets}), 6)
        self.assertEqual([s.drawables for s in doc.sets], [('impact',), ('smoke',)] * 3)
        self.assertEqual([doc.value(('hf', f'hf{i}')) for i in range(8)], [0, 15, 25, 0, 0, 0, 0, 0])
        self.assertEqual(proof['figure_start_frames'], [0, 0, 15, 15, 25, 25])
        self.assertEqual(EffectDocument.from_bytes(original).value(('set1', 'common', 'so')), 0)
        # An explicit zero clock preserves the established event bytes.
        default, _ = compose_event(original, ['impact'], socket='@effect', duration_frames=120)
        explicit, _ = compose_event(original, ['impact'], socket='@effect', duration_frames=120, figure_start_frames=[0])
        self.assertEqual(default, explicit)

    def test_invalid_figure_clocks_rejected(self):
        for starts in ([0], [0, 0], [-1, 15], [0, 120], [False, 15], '0,15'):
            with self.assertRaises(ValueError):
                compose_event(bson_fixture(), ['impact', 'impact'], socket='@effect', duration_frames=120,
                              figure_start_frames=starts)

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
            recipe['native_bindings'].update(target_start_frames=[0, 15, 25], target_hit_frames=[0, 15, 25])
            repeated = inspect_recipe(recipe)
            self.assertTrue(repeated['buildable'], repeated['errors'])
            proof = repeated['compositions'][0]['provenance']
            self.assertEqual(proof['figure_start_frames'], [0, 15, 25])
            self.assertEqual(proof['hit_frames'], [0, 15, 25])
            self.assertEqual(proof['duration_frames'], 25 + report['compositions'][0]['provenance']['duration_frames'])
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
