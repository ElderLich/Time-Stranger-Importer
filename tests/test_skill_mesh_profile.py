"""Regression checks for separated owner/world motion and rejected mesh modes."""
import copy
import importlib.util
import math
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('skill_mesh_profile', Path(__file__).resolve().parents[1] / 'src/skill_conversion/mesh_profile.py')
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


def source():
    return {'renderer': {'alignment': 2, 'pivot': [0, 0, -.13], 'flip': [0, 0, 0]},
            'ps': {'renderMode': 'Mesh', 'dynamics': {'simulationSpace': 0}, 'tilesX': 4, 'tilesY': 4},
            'material_infos': [{'shader': 'Honor/FX/CommonShader', 'floats': {}}]}


def particle():
    return {'renderMode': 'Mesh', 'alignment': 2, 'samples': [
        {'frame': frame, 'position': [2, 3 - frame, 4], 'worldOffset': [0, -frame, 0],
         'size': [2, 1, 3], 'rotation': [0, 0, 0, 1], 'rotationDegrees': [0, 0, 0],
         'viewAligned': False, 'color': [1, 1, 1, 1], 'customData': [0, 0, 0, 0],
         'customData2': [0, 0, 0, frame / 10], 'flipbook': frame}
        for frame in (2, 3, 4)]}


class MeshProfileTests(unittest.TestCase):
    def test_world_gravity_stays_outside_moving_scaled_owner(self):
        original = particle(); frozen = copy.deepcopy(original)
        samples = profile.prepare_local_mesh_samples(source(), original)
        self.assertEqual(original, frozen)
        for sample in samples:
            self.assertEqual(sample['position'], [2, 3, 4])
            # A90degree rotation and nonuniform owner scale distinguish the
            # separate gravity from mistakenly rotating/scaling total position.
            x, y, z = sample['position']; wx, wy, wz = sample['worldOffset']
            actual = [-.5 * y + 10 + wx, 2 * x + wy, 3 * z + wz]
            self.assertEqual(actual, [8.5, 4 - sample['frame'], 12])
        samples[0]['color'][0] = .1
        self.assertEqual(original, frozen)

    def test_frozen_position_current_orientation_is_not_admitted(self):
        node = source(); node['ps']['dynamics']['simulationSpace'] = 1
        with self.assertRaisesRegex(ValueError, 'current-owner orientation'):
            profile.prepare_local_mesh_samples(node, particle())

    def test_camera_alignment_fresnel_and_trails_are_explicit(self):
        changes = [('alignment', lambda n: n['renderer'].update(alignment=0)),
                   ('Fresnel', lambda n: n['material_infos'][0]['floats'].update(_FresnelToggle=1)),
                   ('trails', lambda n: n['ps']['dynamics'].update(trail=True))]
        for label, change in changes:
            with self.subTest(label=label):
                node = source(); change(node)
                with self.assertRaises(ValueError):
                    profile.validate_local_mesh(node)

    def test_invalid_clock_vectors_and_negative_scale_fail_before_conversion(self):
        changes = [lambda s: s.update(frame=2.5), lambda s: s.update(worldOffset=[0, math.nan, 0]),
                   lambda s: s.update(size=[-1, 1, 1]), lambda s: s.update(rotation=[0, 0, 0, 2]),
                   lambda s: s.update(viewAligned=True), lambda s: s.update(flipbook=16)]
        for change in changes:
            with self.subTest(change=change):
                value = particle(); change(value['samples'][0])
                with self.assertRaises(ValueError):
                    profile.prepare_local_mesh_samples(source(), value)
        value = particle(); value['samples'][1]['frame'] = 7
        with self.assertRaises(ValueError):
            profile.prepare_local_mesh_samples(source(), value)

    def test_atlas_dimensions_are_captured_and_bounded(self):
        node = source(); shader = profile.vertex_source(node)
        self.assertNotIn('NC_COLUMNS', shader)
        self.assertIn('float2(4,4)', shader)
        for invalid in (0, -1, 257, 4.5, True):
            node = source(); node['ps']['tilesX'] = invalid
            with self.assertRaises(ValueError):
                profile.vertex_source(node)

    def test_mesh_basis_and_custom_streams_are_not_billboard_dimensions(self):
        value = particle()
        for sample in value['samples']:
            sample['renderSize'] = [200, 300, 400]
        converted = profile.prepare_local_mesh_samples(source(), value)
        for actual, expected in zip(converted, value['samples']):
            self.assertEqual(actual['size'], [2, 1, 3])
            self.assertEqual(actual['customData2'], expected['customData2'])


if __name__ == '__main__':
    unittest.main()
