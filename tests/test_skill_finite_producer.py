"""Finite producer source gates, native graph readback, and rollback checks."""
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
from skill_conversion import create_recipe, pin_file
from skill_conversion import finite_producer as producer
from spark import SparkDocument


def donor(kind, identity):
    header = bytearray(24)
    header[:4] = b'SPAK'
    struct.pack_into('<HH', header, 8, 140, 1)
    header[16] = 30
    payload = bytearray(656 if kind == 4 else 552)
    struct.pack_into('<H', payload, 0, kind)
    struct.pack_into('<I', payload, 4, identity)
    struct.pack_into('<I', payload, 520, 0xffffffff)
    struct.pack_into('<I', payload, 536, 0xffffffff)
    return bytes(header) + producer._chunk(b'NODE', payload) + producer._chunk(b'TERM', b'')


def carrier_header():
    # Independent bounded header fixture, sufficient for the pure inspector.
    # Actual full GEOM/ANIM serialization is covered in Blender acceptance.
    raw = bytearray(640)
    struct.pack_into('<I4H', raw, 0, 316, 1, 1, 0, 0)
    struct.pack_into('<Q', raw, 104, 512)
    struct.pack_into('<Q', raw, 152, 176)
    raw[176:180] = b'60SE'
    struct.pack_into('<HH', raw, 192, 1, 4)
    struct.pack_into('<I', raw, 200, 88)
    for field, offset in ((36, 28), (40, 40), (44, 52)):
        struct.pack_into('<I', raw, 176+field, offset)
    struct.pack_into('<4I', raw, 240, 301 << 4 | 2, 178 << 4, 178 << 4 | 1, 178 << 4 | 2)
    struct.pack_into('<4I', raw, 256, 123, 123, 123, 123)
    raw[272:276] = bytes([8] * 4)
    return bytes(raw)


class FiniteProducerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        library = self.root / 'library'
        model = library / 'Digimon_Library/Example_x901'
        (model / 'model').mkdir(parents=True)
        (library / 'MANIFEST').mkdir()
        (library / 'Vfx_assets/x901').mkdir(parents=True)
        write_glb(model / 'model/model.glb')
        (model / 'info.json').write_text(json.dumps({'code': 'x901', 'ids': ['9001']}))
        event = {'frame': 0, 'slot': 'hit', 'prefab': 'x901_hit', 'lifeFrames': 2,
                 'anchor': {'side': 'caster', 'name': 'foot'}}
        phase = {'kind': 'action', 'actorClip': 'cast', 'totalFrames': 2, 'events': [event], 'sounds': []}
        (library / 'MANIFEST/skill_timelines.json').write_text(json.dumps(
            {'9001': {'code': 'x901', 'equippedSkills': [{'slot': 2, 'name': 'Example', 'phases': [phase]}]}}))
        self.node = {'path': 'x901_hit/mesh', 'ps': {'renderMode': 'Mesh', 'dynamics': {'simulationSpace': 0}},
                     'renderer': {'alignment': 2}, 'material_infos': [{'shader': 'Honor/FX/CommonShader', 'floats': {}}]}
        self.asset = library / 'Vfx_assets/x901/hit.json'
        self.asset.write_text(json.dumps({'nodes': [self.node, {'path': 'x901_hit/unselected'}]}))
        self.samples = [{'frame': frame, 'viewAligned': False, 'position': [4+frame, 2, 3],
                         'worldOffset': [1, 0, 0], 'size': [1, 2, 3], 'rotationDegrees': [0, 0, 30],
                         'rotation': [0, 0, 0, 1], 'color': [1, .5, 0, 1], 'customData': [0]*4,
                         'customData2': [0]*4, 'flipbook': 0} for frame in range(2)]
        self.bake = {'format': 'nc-native-particle-bake-v1', 'source': str(self.asset), 'fps': 30,
                     'duration_frames': 2, 'local_mounted': True, 'coordinates': 'explicit native Y-up',
                     'nodes': [{'index': 0, 'path': self.node['path']}], 'particles': [
                         {'nodeIndex': 0, 'path': self.node['path'], 'materialIndex': 0, 'renderMode': 'Mesh',
                          'alignment': 2, 'sortingOrder': -2, 'samples': self.samples}]}
        self.bake_path = self.root / 'bake.json'
        self.bake_path.write_text(json.dumps(self.bake))
        self.carrier_path = self.root / 'carriers.json'
        geom = self.root / 'carrier.geom'; geom.write_bytes(carrier_header())
        proof = self.root / 'review.txt'; proof.write_text('Bounded reviewed carrier fixture')
        self.carriers = {'format': producer.CARRIER_FORMAT, 'profile': producer.PROFILE,
                         'source_asset': pin_file(self.asset), 'source_bake': pin_file(self.bake_path),
                         'nodes': [{'node_index': 0, 'node_path': self.node['path'],
                                    'source_node_sha256': producer.node_fingerprint(self.node),
                                    'source_material_sha256': producer.material_fingerprint(self.node),
                                    'geometry': pin_file(geom), 'dependencies': [], 'provenance': [pin_file(proof)]}]}
        group = self.root / 'group.sprk'; group.write_bytes(donor(0, 4))
        model_donor = self.root / 'model.sprk'; model_donor.write_bytes(donor(4, 7))
        self.job = {'format': producer.JOB_FORMAT, 'source': create_recipe(model, '2:0')['source'],
                    'event_order': 0, 'bake': pin_file(self.bake_path), 'bake_reference_root': str(self.root),
                    'donors': {'group_sprk': pin_file(group), 'group_node_id': 4,
                               'model_sprk': pin_file(model_donor), 'model_node_id': 7, 'wrapper_geom': pin_file(geom)},
                    'prefix': 'testmesh', 'node_indices': [0]}
        self.repin()

    def tearDown(self):
        self.temp.cleanup()

    def repin(self):
        self.bake_path.write_text(json.dumps(self.bake))
        self.job['bake'] = pin_file(self.bake_path)
        self.carriers['source_bake'] = self.job['bake']
        self.carrier_path.write_text(json.dumps(self.carriers))
        self.job['carriers'] = pin_file(self.carrier_path)

    def test_source_inspection_keeps_omitted_nodes_and_stable_plan(self):
        original = copy.deepcopy(self.job)
        report = producer.inspect_finite_job(self.job)
        self.assertTrue(report['buildable'], report['errors'])
        self.assertFalse(report['installable'])
        self.assertEqual(report['omitted_nodes'][0]['node_index'], 1)
        self.assertEqual(report['selected_nodes'][0]['particles'], 1)
        self.assertEqual(report['plan_sha256'], producer.inspect_finite_job(self.job)['plan_sha256'])
        self.assertEqual(self.job, original)

    def test_explicit_unsupported_selection_and_empty_particles_fail(self):
        self.job['node_indices'] = [0, 1]
        self.assertFalse(producer.inspect_finite_job(self.job)['buildable'])
        self.job['node_indices'] = [0]
        self.bake['particles'] = []
        self.repin()
        self.assertIn('no sampled particles', producer.inspect_finite_job(self.job)['errors'][0]['message'])

    def test_changed_source_is_not_silently_repinned(self):
        self.asset.write_text(self.asset.read_text() + ' ')
        self.assertIn('snapshot changed', producer.inspect_finite_job(self.job)['errors'][0]['message'])

    def test_stale_carrier_and_source_fingerprints_fail(self):
        self.carrier_path.write_text(self.carrier_path.read_text() + ' ')
        self.assertIn('Stale pinned', producer.inspect_finite_job(self.job)['errors'][0]['message'])
        self.carriers['nodes'][0]['source_node_sha256'] = '0'*64
        self.repin()
        self.assertIn('fingerprint differs', producer.inspect_finite_job(self.job)['errors'][0]['message'])

    def test_bake_identity_discontinuity_and_skipped_system_fail(self):
        self.bake['particles'][0]['path'] = 'other'
        self.repin()
        self.assertIn('identity differs', producer.inspect_finite_job(self.job)['errors'][0]['message'])
        self.bake['particles'][0]['path'] = self.node['path']
        self.samples[1]['frame'] = 3
        self.repin()
        self.assertIn('contiguous', producer.inspect_finite_job(self.job)['errors'][0]['message'])
        self.samples[1]['frame'] = 1
        self.bake['skipped'] = [{'nodeIndex': 0, 'reason': 'unknown emission'}]
        self.repin()
        self.assertIn('unresolved or skipped', producer.inspect_finite_job(self.job)['errors'][0]['message'])

    def test_bad_inputs_return_structured_errors(self):
        for value in (None, {'format': producer.JOB_FORMAT}, {**self.job, 'prefix': '../bad'}, {**self.job, 'extra': float('nan')}):
            report = producer.inspect_finite_job(value)
            self.assertFalse(report['buildable'])
            self.assertTrue(report['errors'][0]['message'])

    def test_dependency_case_collision_and_missing_declared_reference_fail(self):
        texture = self.root/'image.img'
        raw = bytearray(132); raw[:4] = b'DDS '
        struct.pack_into('<I', raw, 4, 124)
        struct.pack_into('<II', raw, 12, 1, 1)
        texture.write_bytes(raw)
        dependency = {**pin_file(texture), 'destination': 'images/example.img', 'references': ['images/missing.img']}
        self.carriers['nodes'][0]['dependencies'] = [dependency]
        self.repin()
        self.assertIn('closure is incomplete', producer.inspect_finite_job(self.job)['errors'][0]['message'])
        dependency['references'] = []
        self.carriers['nodes'][0]['dependencies'].append({**dependency, 'destination': 'images/EXAMPLE.img'})
        self.repin()
        self.assertIn('collision', producer.inspect_finite_job(self.job)['errors'][0]['message'])

    def test_plan_destination_and_source_guards_precede_native_loading(self):
        output = self.root / 'new'
        with patch.object(producer, '_native_modules', side_effect=AssertionError('must not load')):
            with self.assertRaisesRegex(ValueError, 'stale'):
                producer.produce_finite_components(self.job, output, expected_plan_sha256='0'*64)
            output.mkdir(); (output/'keep').write_text('unchanged')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                producer.produce_finite_components(self.job, output)
            self.assertEqual((output/'keep').read_text(), 'unchanged')
            with self.assertRaisesRegex(ValueError, 'outside the source library'):
                producer.produce_finite_components(self.job, Path(self.job['source']['library'])/'candidate')

    def test_generation_failure_cleans_staging_and_restores_backend_mode(self):
        class Backend:
            throwing = False
            errors = ['previous']
            class Geom:
                @staticmethod
                def from_bytes(raw):
                    raise RuntimeError('forced native serialization failure')
            @classmethod
            def get_throw_errors(cls): return cls.throwing
            @classmethod
            def set_throw_errors(cls, value): cls.throwing = value
            @classmethod
            def get_error_list(cls): return cls.errors
            @classmethod
            def set_error_list(cls, value): cls.errors = value
        before = sorted(p.name for p in self.root.iterdir())
        with patch.object(producer, '_native_modules', return_value=(Backend, None, None, None)), \
                patch.object(producer, '_runtime_provenance', return_value={'inputs': []}):
            with self.assertRaisesRegex(RuntimeError, 'forced native'):
                producer.produce_finite_components(self.job, self.root/'failed')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)
        self.assertFalse(Backend.throwing)
        self.assertEqual(Backend.errors, ['previous'])

    def test_native_graph_preserves_current_owner_delay_channels_and_separate_world(self):
        group = producer._template(donor(0, 4), 4, 0)
        model = producer._template(donor(4, 7), 7, 4)
        samples = producer.prepare_local_mesh_samples(self.node, self.bake['particles'][0])
        for sample in samples: sample['frame'] += 7
        raw = producer.author_current_owner_graph(group, model, 'example_s000',
            [{'stem': 'example_p00000', 'sorting_order': -2, 'samples': samples}], 12)
        document = SparkDocument.from_bytes(raw)
        self.assertEqual(document.to_bytes(), raw)
        self.assertEqual(document.nodes[0].children, (1,))
        child = document._payload(document._node_chunks[1])
        self.assertEqual(struct.unpack_from('<I', child, 460)[0], 0x79)
        self.assertEqual(struct.unpack_from('<6h', child, 468)[:4], (7, 7, 2, 2))
        channels = {channel.id: channel for channel in document.channels(0)}
        self.assertEqual(set(channels), set(range(1, 10)) | set(range(149, 153)))
        self.assertEqual([key.value for key in channels[1].keys], [3, 4])
        self.assertEqual([key.frame for key in channels[1].keys], [1, 2])
        self.assertEqual(self.samples[0]['position'], [4, 2, 3])


if __name__ == '__main__':
    unittest.main()
