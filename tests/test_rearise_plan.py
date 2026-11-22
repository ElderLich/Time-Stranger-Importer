"""Regression fixtures for shared ReArise clips and the group-damage Special route."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rearise_plan import _group_damage_missing_contact, inspect_actor


class ReArisePlanTests(unittest.TestCase):
    def test_shared_clip_and_authored_bs02_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / 'Digimon_Library/chr123/model/model.glb'
            model.parent.mkdir(parents=True)
            gltf = {'asset': {'extras': {'nc': {'model_id': 'chr123'}}}, 'nodes': [],
                    'animations': [{'name': name, 'channels': [{'target': {}}]}
                                   for name in ('chr009_ba02_rd', 'chr123_bs02_rd')]}
            chunk = json.dumps(gltf).encode()
            chunk += b' ' * (-len(chunk) % 4)
            model.write_bytes(struct.pack('<4sIII4s', b'glTF', 2, 20 + len(chunk), len(chunk), b'JSON') + chunk)
            (root / 'MANIFEST').mkdir()
            captured = {'chr123': {'clips': {'shared_action': {'actorClip': 'chr009_ba02_rd'},
                                            'chr123_bs02_rd': {}, 'missing': {'actorClip': 'chr009_bs03_rd'}}}}
            (root / 'MANIFEST/display_playback.json').write_text(json.dumps(captured))
            actions = inspect_actor(model)['actions']
            self.assertEqual(actions['shared_action']['export_track'], 'chr123_ba02')
            self.assertTrue(actions['shared_action']['embedded'])
            self.assertEqual(actions['chr123_bs02_rd']['export_track'], 'chr123_bs02')
            self.assertFalse(actions['missing']['embedded'])

    def test_unmarked_group_only_damage_and_valid_alternatives(self):
        source = {'common_action': 'group_fire', 'events': [{'anchor': {'origin': 'target'}}]}
        row = ['0'] * 34
        row[10], row[11], row[22], row[33] = '4', '5', '1', '5'
        centroid = {'position_type': 4, 'hit_frame': 0}
        self.assertTrue(_group_damage_missing_contact(source, row, centroid))
        for column, value in [(10, '1'), (11, '2'), (11, '6'), (14, '21104'), (20, '1'), (22, '0'), (33, '1')]:
            changed = row.copy()
            changed[column] = value
            with self.subTest(column=column, value=value):
                self.assertFalse(_group_damage_missing_contact(source, changed, centroid))
        self.assertFalse(_group_damage_missing_contact(source, row, dict(centroid, hit_frame=30)))
        self.assertFalse(_group_damage_missing_contact(source, row, dict(centroid, position_type=2)))
        self.assertFalse(_group_damage_missing_contact(dict(source, events=[]), row, centroid))
