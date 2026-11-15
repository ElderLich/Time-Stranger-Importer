import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'src/skill_conversion/dissolve_profile.py'
spec = importlib.util.spec_from_file_location('dissolve_profile', path)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def fixture():
    return {'material_infos': [{'shader': profile.FAMILY, 'floats': {
        '_Spread': 0, '_Softness': .5, '_DissRimSize': 1}, 'passStates': [{
        'srcBlend': 5, 'dstBlend': 10, 'srcBlendAlpha': 1, 'dstBlendAlpha': 10,
        'blendOp': 0, 'blendOpAlpha': 0, 'colorMask': 15, 'cull': 2,
        'zTest': 4, 'zWrite': 0}]}]}


class DissolveProfileTests(unittest.TestCase):
    def test_captured_domain(self):
        self.assertEqual(profile.validate_material(fixture()), profile.PROFILE)

    def test_singular_variants_are_not_silently_admitted(self):
        for key, value in (('_Spread', .1), ('_Softness', .4), ('_DissRimSize', 0), ('_RoleClip', 1)):
            node = fixture(); node['material_infos'][0]['floats'][key] = value
            with self.assertRaises(ValueError): profile.validate_material(node)

    def test_depth_and_blend_state_gate(self):
        for key, value in (('zWrite', 1), ('srcBlendAlpha', 5), ('colorMask', 14), ('blendOpAlpha', 1)):
            node = fixture(); node['material_infos'][0]['passStates'][0][key] = value
            with self.assertRaises(ValueError): profile.validate_material(node)

    def test_hdr_color_is_not_clamped_or_premultiplied_twice(self):
        for actual,expected in zip(profile.blend_layers([4,-2,1,.25],[.2,.4,.8,.6]), [1.15,-.2,.85,.7]):
            self.assertAlmostEqual(actual,expected)

    def test_channel_interleaving_preserves_overlapping_particle_order(self):
        colors = ([4, .3, -.2, .2], [.1, 1, 2, .8], [1, 0, .5, 0])
        initial = [.4, .2, .7, .3]
        expected = initial
        for source in colors: expected = profile.blend_layers(source, expected)
        rgb = initial[:3]
        for source in colors: rgb = [source[i] * source[3] + rgb[i] * (1-source[3]) for i in range(3)]
        alpha = initial[3]
        for source in colors: alpha = source[3] + alpha * (1-source[3])
        self.assertEqual(expected, rgb + [alpha])

    def test_masks_and_factors_are_disjoint(self):
        rgb, alpha = profile.LAYERS
        self.assertEqual([a+b for a,b in zip(rgb['write_mask'], alpha['write_mask'])], [1]*4)
        self.assertEqual(rgb['blend'], [770,771])
        self.assertEqual(alpha['blend'], [1,771])


if __name__ == '__main__': unittest.main()
