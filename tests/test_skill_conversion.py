"""Meaningful recipe, native composition and publication failure checks."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
from test_nc_preview_source import write_glb
from effect_bson import EffectDocument, END
from skill_conversion import create_recipe, inspect_recipe, build_candidate, pin_file
from skill_conversion.bson import compose_event


def bson_fixture():
    pool = bytearray(b'\0')
    indices = {'': 0}

    def text(value):
        if value not in indices:
            indices[value] = len(pool)
            pool.extend(value.encode('utf8') + b'\0')
        return indices[value]

    def encode(name, value):
        key = text(name)
        if isinstance(value, dict):
            kind, payload = 1, struct.pack('<I', len(value)) + b''.join(encode(k, v) for k, v in value.items()) + END
        elif type(value) is int:
            kind, payload = 0x10, struct.pack('<i', value)
        elif type(value) is float:
            kind, payload = 0x11, struct.pack('<f', value)
        else:
            kind, payload = 0x14, struct.pack('<I', text(value))
        high, low = divmod(len(payload) + 8, 65535)
        return struct.pack('<BBHI', kind, high, low, key) + payload

    # Native neutral emitter fixture is written independently of the composer.
    dae = {'lbt': 0, 'cl': 0, 'osa': 0.0, 'pt': 0, 'osa_random_distributed': 0,
           'pc': {'pc_calc': 0, 'pc_random': 0}, 'lr': {'lr_begin': -1, 'lr_end': -1, 'lr_cancel': -1},
           'osa_list': '', 'zsort': 0, 'dl': 1, 'ff': 0, 'l': -1, 'sf': {'sf_random': 0, 'sf_frame': 0}}
    for prefix, default in [('os', 1.0), ('or', 0.0), ('ot', 0.0)]:
        for axis in 'xyz':
            key = prefix + axis
            dae[key] = {key + '_' + axis: default, key + '_random': 0.0,
                        key + '_random_list': '', key + '_random_distributed': 0}
    emitter = {'dn': 'old_figure', 'el': {'el_type': 0, 'el_random': 0, 'el_life': 0},
               'daectrl': dae, 'ei': {'ei_interval': 0, 'ei_random': 0}, 'ec': {'ec_count': 1, 'ec_random': 0}}
    common = {'id': 'donor', 'ca': {'ca_ref': 1, 'ca_sca': 1, 'ca_rot': 1, 'ca_pos': 1, 'ca_frame': -1},
              'so': 0, 'bt': {'bt_id': 'donor_owner', 'bt_pos': 'old_socket', 'bt_rot': '', 'bt_sca': ''},
              'emitter0': emitter, 'movie': {'movie_name': 'omit_donor_movie', 'movie_offset': 1}}
    root = encode('', {'mf': 100, 'hf': {f'hf{i}': i for i in range(8)}, 'bs': 0.0, 'sg': 0,
                       'set1': {'common': common}})
    return b'BSON\x0a\0\0\0' + root + struct.pack('<BBH', 0x20, 0, len(pool)) + pool + END


class SkillConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'library'
        self.model = self.library / 'Digimon_Library/Example_x901'
        (self.model / 'model').mkdir(parents=True)
        (self.library / 'MANIFEST').mkdir()
        (self.library / 'Vfx_assets/x901').mkdir(parents=True)
        write_glb(self.model / 'model/model.glb')
        (self.model / 'info.json').write_text(json.dumps({'code': 'x901', 'ids': ['9001']}))
        self.event = {'frame': 7, 'slot': 'hit', 'prefab': 'x901_hit', 'lifeFrames': 120,
                      'anchor': {'side': 'target', 'name': 'foot'}}
        phase = {'kind': 'action', 'actorClip': 'cast', 'totalFrames': 1, 'events': [self.event], 'sounds': []}
        (self.library / 'MANIFEST/skill_timelines.json').write_text(json.dumps(
            {'9001': {'code': 'x901', 'equippedSkills': [{'slot': 2, 'name': 'Example', 'phases': [phase]}]}}))
        self.asset = self.library / 'Vfx_assets/x901/hit.json'
        self.asset.write_text(json.dumps({'nodes': [{'path': 'hit/sprite'}]}))
        (self.root / 'donors').mkdir()
        self.donor = self.root / 'donors/donor.bson'
        self.donor.write_bytes(bson_fixture())
        self.component_path = self.root / 'producer/component.json'
        self.component_path.parent.mkdir()
        self.dependencies = []
        for suffix in ('.geom', '.anim', '.nlst', '.sprk'):
            path = self.component_path.parent / ('figure' + suffix)
            if suffix == '.sprk':
                header = bytearray(24)
                header[:4] = b'SPAK'
                struct.pack_into('<H', header, 8, 140)
                header[16] = 30
                path.write_bytes(header + struct.pack('<4sIII', b'END ', 16, 0, 0))
            else:
                path.write_bytes(('producer resource ' + suffix).encode())
            self.dependencies.append({**pin_file(path), 'path': path.name, 'destination': path.name})
        self.component = {'format': 'nc-native-skill-component-v1', 'id': 'example_hit', 'kind': 'effect',
                          'source': {'model_code': 'x901', 'role': '9001', 'phase_id': '2:0',
                                     'asset': pin_file(self.asset), 'node_index': 0, 'node_path': 'hit/sprite',
                                     'event_order': 0, 'source_event': self.event},
                          'profiles': {'coordinate': 'native-reflected-v1', 'render': 'finite-local', 'ownership': 'current-owner'},
                          'native': {'figure_stem': 'figure'}, 'sampling': {'fps': 30, 'duration_frames': 120},
                          'dependencies': self.dependencies, 'provenance': [], 'blockers': ['Game parity pending']}
        self.save_component()
        self.recipe = create_recipe(self.model / 'model/model.glb', '2:0', 'chr9001', 'bs02', {'target': self.donor})
        self.recipe['components'] = [pin_file(self.component_path)]
        self.recipe['socket_mappings'][0]['native_socket'] = '@root'

    def tearDown(self):
        self.temp.cleanup()

    def save_component(self):
        self.component_path.write_text(json.dumps(self.component))

    def repin_component(self):
        self.save_component()
        self.recipe['components'] = [pin_file(self.component_path)]

    def test_source_identity_events_and_independent_native_mappings(self):
        recipe = create_recipe(self.model, '2:0')
        self.assertEqual(recipe['source']['phase']['source_phase']['events'], [self.event])
        self.assertEqual(recipe['socket_mappings'], [{'side': 'target', 'source_socket': 'foot', 'native_socket': ''}])
        self.assertIsNone(recipe['target']['animation_selector'])
        self.assertFalse(inspect_recipe(recipe)['buildable'])

    def test_long_lexical_source_path_is_normalized_before_file_lookup(self):
        (self.root / 'dot').mkdir()
        lexical = self.root.joinpath(*(['dot', '..'] * 36), 'donors', self.donor.name)
        self.assertGreater(len(str(lexical)), 260)
        self.assertEqual(pin_file(lexical), pin_file(self.donor))

    def test_real_bson_composition_preserves_donor_and_source_event(self):
        before = self.donor.read_bytes()
        report = inspect_recipe(self.recipe)
        self.assertEqual(report['errors'], [])
        self.assertTrue(report['buildable'])
        self.assertFalse(report['installable'])
        output = self.root / 'candidate'
        result = build_candidate(self.recipe, output, expected_plan_sha256=report['plan_sha256'])
        parsed = EffectDocument.read(output / 'resources/patch/nc_target_event_0000.bson')
        self.assertEqual(parsed.sets[0].drawables, ('figure',))
        self.assertEqual(parsed.sets[0].attachment_owner, 'CompositeModel')
        self.assertEqual(parsed.sets[0].position_socket, '')
        self.assertEqual(parsed.value(('mf',)), 120)
        self.assertTrue(all(parsed.value(('hf', f'hf{i}')) == 0 for i in range(8)))
        self.assertEqual(result['compositions'][0]['source_event']['frame'], 7)
        self.assertEqual(self.donor.read_bytes(), before)
        self.assertFalse((output / 'dsts-loader').exists())
        self.assertEqual(len(result['files']), 5)

    def test_unknown_files_are_not_globbed_and_same_hash_is_deterministic(self):
        report = inspect_recipe(self.recipe)
        (self.component_path.parent / 'unlisted.geom').write_bytes(b'not a dependency')
        self.assertEqual(inspect_recipe(copy.deepcopy(self.recipe))['plan_sha256'], report['plan_sha256'])
        result = build_candidate(self.recipe, self.root / 'candidate')
        self.assertNotIn('unlisted.geom', str(result['files']))

    def test_source_edit_and_component_event_substitution_fail(self):
        self.component['source']['source_event']['frame'] = 8
        self.repin_component()
        self.assertFalse(inspect_recipe(self.recipe)['buildable'])
        self.asset.write_text('{}')
        self.assertTrue(any(row['code'] == 'source-stale' for row in inspect_recipe(self.recipe)['errors']))

    def test_dependency_staleness_collision_and_traversal_fail_before_writes(self):
        original = copy.deepcopy(self.component)
        for mutation in ('stale', 'collision', 'traversal', 'closure'):
            with self.subTest(mutation=mutation):
                self.component = copy.deepcopy(original)
                if mutation == 'stale':
                    self.component['dependencies'][0]['sha256'] = '0' * 64
                elif mutation == 'collision':
                    self.component['dependencies'][1]['destination'] = 'FIGURE.geom'
                elif mutation == 'traversal':
                    self.component['dependencies'][0]['destination'] = '../outside.geom'
                else:
                    self.component['dependencies'][0]['references'] = ['missing.img']
                self.repin_component()
                output = self.root / mutation
                with self.assertRaises(ValueError):
                    build_candidate(self.recipe, output)
                self.assertFalse(output.exists())

    def test_stale_review_existing_output_and_publication_failure_preserve_data(self):
        report = inspect_recipe(self.recipe)
        self.recipe['target']['primary_effect_id'] = 123
        with self.assertRaisesRegex(ValueError, 'stale'):
            build_candidate(self.recipe, self.root / 'candidate', expected_plan_sha256=report['plan_sha256'])
        output = self.root / 'existing'
        output.mkdir()
        sentinel = output / 'keep.txt'
        sentinel.write_bytes(b'user data')
        with self.assertRaises(ValueError):
            build_candidate(self.recipe, output)
        self.assertEqual(sentinel.read_bytes(), b'user data')
        with patch('skill_conversion.compiler.os.rename', side_effect=OSError('injected publication failure')):
            with self.assertRaises(OSError):
                build_candidate(self.recipe, self.root / 'failed')
        self.assertFalse((self.root / 'failed').exists())
        self.assertEqual(list(self.root.glob('.failed.*')), [])

    def test_mid_build_source_change_rolls_back(self):
        import skill_conversion.compiler as compiler
        original_write = compiler._write
        changed = False
        def changed_write(path, raw):
            nonlocal changed
            original_write(path, raw)
            if not changed:
                changed = True
                self.asset.write_text('{}')
        with patch.object(compiler, '_write', side_effect=changed_write):
            with self.assertRaisesRegex(ValueError, 'changed'):
                build_candidate(self.recipe, self.root / 'candidate')
        self.assertFalse((self.root / 'candidate').exists())
        self.assertEqual(list(self.root.glob('.candidate.*')), [])

    def test_non_neutral_donor_and_unknown_root_rejected(self):
        raw = bytearray(self.donor.read_bytes())
        doc = EffectDocument.from_bytes(raw)
        node = doc._get(('set1', 'common', 'emitter0', 'ec', 'ec_count'))
        struct.pack_into('<i', raw, node.start + 8, 2)
        with self.assertRaisesRegex(ValueError, 'neutral'):
            compose_event(raw, ['figure'], socket='@root', duration_frames=120)

    def test_socket_inventory_rejects_missing_socket_and_target_alias(self):
        inventory = self.root / 'actor.nlst'
        inventory.write_text('real_socket, 1, null, joint\n')
        self.recipe['target']['socket_inventory']['target'] = pin_file(inventory)
        self.recipe['socket_mappings'][0]['native_socket'] = 'invented'
        report = inspect_recipe(self.recipe)
        self.assertFalse(report['buildable'])
        self.assertTrue(any(row['code'] == 'socket-inventory' for row in report['errors']))

    def test_source_tree_output_and_prefix_collision_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'source library'):
            build_candidate(self.recipe, self.model / 'candidate')
        self.component['dependencies'][1]['destination'] = 'figure.geom/child.anim'
        self.repin_component()
        self.assertTrue(any('file/directory collision' in row['message'] for row in inspect_recipe(self.recipe)['errors']))

    def test_audio_mod_root_and_cross_root_collision(self):
        timeline = self.library / 'MANIFEST/skill_timelines.json'
        data = json.loads(timeline.read_text())
        sound = {'key': 'cast', 'frame': 3}
        data['9001']['equippedSkills'][0]['phases'][0]['sounds'] = [sound]
        timeline.write_text(json.dumps(data))
        (self.model / 'audio').mkdir()
        wav = self.model / 'audio/cast__test.wav'
        wav.write_bytes(b'captured audio fixture')
        self.recipe = create_recipe(self.model, '2:0', 'chr9001', 'bs02', {'target': self.donor})
        self.recipe['socket_mappings'][0]['native_socket'] = '@root'
        self.recipe['components'] = [pin_file(self.component_path)]
        hca = self.component_path.parent / 'native.hca'
        hca.write_bytes(b'encoded audio fixture')
        audio = {'format': 'nc-native-skill-component-v1', 'id': 'cast_audio', 'kind': 'audio', 'destination_root': 'mod',
                 'source': {'model_code': 'x901', 'role': '9001', 'phase_id': '2:0', 'asset': pin_file(wav),
                            'source_key': 'cast', 'source_event': sound},
                 'native': {'cue_id': 123, 'raw_cue': 23, 'bank': 'se'},
                 'dependencies': [{**pin_file(hca), 'path': hca.name, 'destination': 'Ryo/bank/23.hca'}],
                 'provenance': [], 'blockers': ['Audio acceptance pending']}
        audio_path = self.component_path.parent / 'audio.json'
        audio_path.write_text(json.dumps(audio))
        self.recipe['components'].append(pin_file(audio_path))
        self.recipe['cue_mappings'][0].update(cue_id=123, component='cast_audio')
        report = inspect_recipe(self.recipe)
        self.assertTrue(report['buildable'], report['errors'])
        self.assertFalse(any(b['code'] == 'audio-component-missing' for b in report['blockers']))
        output = self.root / 'audio-candidate'
        build_candidate(self.recipe, output)
        self.assertTrue((output / 'resources/mod/Ryo/bank/23.hca').is_file())
        self.assertFalse((output / 'resources/patch/Ryo').exists())
        audio['dependencies'][0]['destination'] = 'dsts-loader/patch/figure.geom'
        audio_path.write_text(json.dumps(audio))
        self.recipe['components'][1] = pin_file(audio_path)
        self.assertFalse(inspect_recipe(self.recipe)['buildable'])

    def test_omitted_node_identity_and_symlink_input_are_rejected(self):
        del self.component['source']['node_path']
        self.repin_component()
        self.assertFalse(inspect_recipe(self.recipe)['buildable'])
        alias = self.root / 'alias.json'
        try:
            alias.symlink_to(self.component_path)
        except OSError:
            self.skipTest('Windows session lacks symbolic-link permission')
        with self.assertRaisesRegex(ValueError, 'Symbolic-link'):
            pin_file(alias)


if __name__ == '__main__':
    unittest.main()
