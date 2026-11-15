"""Phase-source workflow checks with synthetic glTF, timelines and audio."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import wave

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nc_preview_source import Glb, catalog_model, export_preview


def write_glb(path, doc=None, binary=None):
    if binary is None:
        binary = struct.pack('<8f', 0, 4.5, 0, 0, 0, 1, 2, 3)
    if doc is None:
        doc = dict(asset={'version': '2.0'}, nodes=[{'name': 'root'}], buffers=[{'byteLength': len(binary)}],
                   bufferViews=[{'buffer': 0, 'byteOffset': 0, 'byteLength': 8},
                                {'buffer': 0, 'byteOffset': 8, 'byteLength': 24}],
                   accessors=[{'bufferView': 0, 'componentType': 5126, 'count': 2, 'type': 'SCALAR'},
                              {'bufferView': 1, 'componentType': 5126, 'count': 2, 'type': 'VEC3'}],
                   animations=[{'name': 'cast', 'samplers': [{'input': 0, 'output': 1}],
                                'channels': [{'sampler': 0, 'target': {'node': 0, 'path': 'translation'}}]}])
    encoded = json.dumps(doc).encode()
    encoded += b' ' * (-len(encoded) % 4)
    raw = struct.pack('<III', 0x46546C67, 2, 28+len(encoded)+len(binary))
    raw += struct.pack('<II', len(encoded), 0x4E4F534A)+encoded
    raw += struct.pack('<II', len(binary), 0x004E4942)+binary
    path.write_bytes(raw)
    return doc


class PhaseSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root/'Digimon_Library/Example_x901'
        (self.model/'model').mkdir(parents=True)
        (self.model/'audio').mkdir()
        (self.root/'MANIFEST').mkdir()
        (self.root/'Vfx_assets/x901').mkdir(parents=True)
        self.glb = self.model/'model/model.glb'
        self.doc = write_glb(self.glb)
        (self.model/'info.json').write_text(json.dumps({'code': 'x901', 'ids': ['9001']}))
        phase = dict(kind='action', actorClip='cast', totalFrames=1,
                     events=[dict(frame=1, slot='hit', prefab='x901_hit', lifeFrames=120,
                                  anchor={'side': 'target', 'name': 'foot'})], sounds=[{'frame': 0, 'key': 'cast'}])
        self.timelines = {'9001': {'code': 'x901', 'equippedSkills': [{'slot': 2, 'name': 'Test Cast', 'phases': [phase]}]}}
        self.timeline_path = self.root/'MANIFEST/skill_timelines.json'
        self.save_timeline()
        (self.root/'Vfx_assets/x901/hit.json').write_text(json.dumps({'nodes': [], 'effect_nodes': []}))
        with wave.open(str(self.model/'audio/cast__test.wav'), 'wb') as sound:
            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(30); sound.writeframes(bytes(360))

    def tearDown(self):
        self.temp.cleanup()

    def save_timeline(self):
        self.timeline_path.write_text(json.dumps(self.timelines))

    def entry(self):
        return catalog_model(self.model)['phases'][0]

    def test_camera_optional_cast_retains_separate_clocks(self):
        result = export_preview(self.model, self.entry(), self.root/'cast.json')
        self.assertIsNone(result['camera'])
        self.assertEqual(result['actor_duration_frames'], 135)
        self.assertEqual(result['source_phase']['totalFrames'], 1)
        self.assertEqual(result['duration_frames'], 180)
        self.assertEqual(result['effects'][0]['anchor'], {'side': 'target', 'name': 'foot'})
        self.assertEqual(result['audio'][0]['source_key'], 'cast')

    def duplicate_socket_fixture(self, nodes, animated, skins=()):
        doc = copy.deepcopy(self.doc)
        doc['nodes'] = nodes
        doc['skins'] = list(skins)
        doc['animations'][0]['channels'] = [
            {'sampler': 0, 'target': {'node': i, 'path': 'translation'}} for i in animated]
        write_glb(self.glb, doc)
        return Glb(self.glb, 'cast')

    def test_static_socket_inherits_animation_from_its_parent(self):
        nodes = [{'name': 'camera anchor'}, {'children': [2]}, {'name': 'camera anchor'}]
        glb = self.duplicate_socket_fixture(nodes, [1])
        self.assertEqual(glb.socket('camera anchor'), 2)
        self.assertEqual(glb.node_path(2), (1, 2))
        self.assertTrue((glb.world(2, 4.5)[:3, 3] == [1, 2, 3]).all())

    def test_animated_skin_owns_socket_even_when_another_prefab_keys_its_copy(self):
        nodes = [{'name': 'anchor'}, {'children': [2, 3]}, {'name': 'joint'},
                 {'name': 'anchor'}, {'mesh': 0, 'skin': 0}]
        glb = self.duplicate_socket_fixture(nodes, [0, 2], [{'joints': [2]}])
        self.assertEqual(glb.socket('anchor'), 3)

    def test_equally_animated_sibling_sockets_report_both_paths(self):
        nodes = [{'name': 'rig', 'children': [1, 2]}, {'name': 'anchor'}, {'name': 'anchor'}]
        glb = self.duplicate_socket_fixture(nodes, [0])
        with self.assertRaisesRegex(ValueError, r'Ambiguous socket.*rig\[0\]/anchor\[1\].*rig\[0\]/anchor\[2\]'):
            glb.socket('anchor')
        with self.assertRaisesRegex(ValueError, 'Missing socket'):
            glb.socket('missing')

    def test_actor_root_motion_does_not_mask_keyed_socket_parent(self):
        nodes = [{'name': 'rig', 'children': [1, 2]}, {'name': 'anchor'},
                 {'name': 'camera controller', 'children': [3]}, {'name': 'anchor'}]
        glb = self.duplicate_socket_fixture(nodes, [0, 2])
        self.assertEqual(glb.socket('anchor'), 3)

    def test_existing_output_is_protected(self):
        out = self.root/'cast.json'; out.write_bytes(b'user output')
        with self.assertRaises(FileExistsError):
            export_preview(self.model, self.entry(), out)
        self.assertEqual(out.read_bytes(), b'user output')

    def test_failed_publish_keeps_existing_output(self):
        out = self.root/'cast.json'; out.write_bytes(b'user output')
        with patch('nc_preview_source.os.replace', side_effect=OSError('injected disk failure')):
            with self.assertRaises(OSError):
                export_preview(self.model, self.entry(), out, overwrite=True)
        self.assertEqual(out.read_bytes(), b'user output')
        self.assertEqual(list(self.root.glob('.cast.json.*.tmp')), [])

    def test_cross_drive_reference_uses_absolute_source(self):
        with patch('nc_preview_source.os.path.relpath', side_effect=ValueError('different drives')):
            result = export_preview(self.model, self.entry(), self.root/'out.json')
        self.assertEqual(result['actor_path'], self.glb.resolve().as_posix())
        self.assertTrue(Path(result['effects'][0]['path']).is_absolute())
        self.assertTrue(Path(result['audio'][0]['path']).is_absolute())

    def test_stale_phase_fails_before_output(self):
        entry = self.entry(); entry['source_phase']['totalFrames'] = 5
        with self.assertRaisesRegex(ValueError, 'stale'):
            export_preview(self.model, entry, self.root/'out.json')
        self.assertFalse((self.root/'out.json').exists())

    def test_negative_event_rejected(self):
        self.timelines['9001']['equippedSkills'][0]['phases'][0]['events'][0]['frame'] = -1
        self.save_timeline()
        with self.assertRaises(ValueError):
            export_preview(self.model, self.entry(), self.root/'out.json')
        self.assertFalse((self.root/'out.json').exists())

    def test_held_pose_and_delay_retained(self):
        phase = self.timelines['9001']['equippedSkills'][0]['phases'][0]
        phase['keepActorPose'] = True; phase['actorDelayFrames'] = 5
        self.save_timeline()
        result = export_preview(self.model, self.entry(), self.root/'out.json')
        self.assertTrue(result['actor_keep_pose'])
        self.assertEqual(result['actor_delay_frames'], 5)

    def test_foreign_role_rejected(self):
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            catalog_model(self.model, '9012')

    def test_accessor_bounds_rejected(self):
        doc = copy.deepcopy(self.doc); doc['accessors'][1]['count'] = 500
        write_glb(self.glb, doc)
        with self.assertRaisesRegex(ValueError, 'Out-of-bounds'):
            Glb(self.glb, 'cast')

    def test_chunk_bounds_rejected(self):
        raw = bytearray(self.glb.read_bytes()); struct.pack_into('<I', raw, 12, len(raw)*2)
        self.glb.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'chunk'):
            Glb(self.glb, 'cast')

    def test_cyclic_hierarchy_rejected(self):
        doc = copy.deepcopy(self.doc); doc['nodes'] = [{'children': [1]}, {'children': [0]}]
        write_glb(self.glb, doc)
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            Glb(self.glb, 'cast')

    def test_nonfinite_samples_rejected(self):
        write_glb(self.glb, binary=struct.pack('<8f', 0, 4.5, 0, 0, 0, float('nan'), 2, 3))
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            Glb(self.glb, 'cast')


if __name__ == '__main__':
    unittest.main()
