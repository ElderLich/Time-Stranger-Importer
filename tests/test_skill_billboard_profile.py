"""Source rotation/scene projection regressions; no Blender dependency."""
import copy
import importlib.util
import math
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('skill_billboard_profile', Path(__file__).resolve().parents[1] / 'src/skill_conversion/billboard_profile.py')
profile = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(profile)


def node():
    return {'renderer': {'alignment': 2}, 'ps': {'renderMode': 'Billboard', 'rotation3D': True,
            'dynamics': {'simulationSpace': 0}, 'startRotationAxes': [
                {'minMaxState': 3, 'scalar': math.pi / 2, 'minScalar': math.pi / 2},
                {'minMaxState': 0, 'scalar': 0}, {'minMaxState': 3, 'scalar': math.tau, 'minScalar': 0}]}}


def rotate(q, point):
    x, y, z, w = q; px, py, pz = point
    # Independent quaternion matrix expansion, not the producer multiplication.
    return [(1-2*y*y-2*z*z)*px+(2*x*y-2*z*w)*py+(2*x*z+2*y*w)*pz,
            (2*x*y+2*z*w)*px+(1-2*x*x-2*z*z)*py+(2*y*z-2*x*w)*pz,
            (2*x*z-2*y*w)*px+(2*y*z+2*x*w)*py+(1-2*x*x-2*y*y)*pz]


class BillboardProfileTests(unittest.TestCase):
    def test_local3d_matches_measured_unity_corner_with_reflection(self):
        # Unity BakeMesh local3D rotation(90,0,37), size(3,2), UV(0,1):
        # local corner(-.5961382389,0,-1.7013579607), reflected X below.
        z = math.radians(37); sample = {'rotation': [0, 0, math.sin(-z/2), math.cos(-z/2)], 'angle': -z}
        before = copy.deepcopy(sample)
        q = profile.local_billboard_quaternion(node(), sample)
        actual = rotate(q, [1.5, 1, 0])
        expected = [.5961382389068604, 0, -1.7013579607009888]
        self.assertLess(max(abs(a-b) for a,b in zip(actual, expected)), 5e-7)
        self.assertEqual(sample, before)

    def test_source_z_spin_is_not_applied_twice(self):
        source = node(); source['ps']['startRotationAxes'][0] = {'scalar': 0}
        z = .7; sample = {'rotation': [0, 0, math.sin(-z/2), math.cos(-z/2)], 'angle': -z}
        actual = rotate(profile.local_billboard_quaternion(source, sample), [1, 0, 0])
        self.assertAlmostEqual(actual[0], math.cos(z)); self.assertAlmostEqual(actual[1], math.sin(z))

    def test_unknown_xy_lifetime_and_mixed_owner_clocks_reject(self):
        for change in [lambda n: n['ps']['startRotationAxes'][0].update(minScalar=0),
                       lambda n: n['ps']['dynamics'].update(angularVelocity={'separate': True}),
                       lambda n: n['ps']['dynamics'].update(simulationSpace=1),
                       lambda n: n['renderer'].update(alignment=4)]:
            source = node(); change(source)
            with self.assertRaises(ValueError):profile.validate_local_3d(source)

    def test_native_screen_uv_orders_perspective_offset_flip_and_resolution(self):
        # Off-center clip position, w2, source distortion.05, nonunit resolution.
        self.assertEqual(profile.native_scene_uv([.5, 1, .2, 2], .05, [.75, .8]), [.5062500000000001, .16000000000000003])
        edge = profile.native_scene_uv([2, -2, .2, 2], .05, [1, 1])
        self.assertEqual(edge, [1.05, .95])  # Sampler owns clamping, not source math.

    def test_nonfinite_quaternion_and_projection_inputs_reject(self):
        for sample in ({'rotation': [0, 0, 0, 2], 'angle': 0}, {'rotation': [0, 0, 0, 1], 'angle': math.nan}):
            with self.assertRaises(ValueError):profile.local_billboard_quaternion(node(), sample)
        with self.assertRaises(ValueError):profile.native_scene_uv([0, 0, 0, math.inf], 0, [1, 1])
        with self.assertRaises(ValueError):profile.native_scene_uv([0, 0, 0, 1], 0, [0, 1])

    def test_projection_preserves_small_positive_w_and_rejects_undefined_domain(self):
        self.assertEqual(profile.native_scene_uv([5e-11, 1e-10, 0, 2e-10], .05, [.75, .8]),
                         profile.native_scene_uv([.5, 1, 0, 2], .05, [.75, .8]))
        for w in (0, -2):
            with self.assertRaises(ValueError):profile.native_scene_uv([.5, 1, 0, w], 0, [1, 1])
        with self.assertRaises(ValueError):profile.native_scene_uv([1e308, 0, 0, 1e-308], 0, [1, 1])
        self.assertEqual(profile.REFRACTION_SCREEN_HLSL, 'projected.xy/projected.w*.5+.5')


if __name__ == '__main__':unittest.main()
