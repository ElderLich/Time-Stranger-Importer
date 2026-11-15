"""Finite captured-source billboard boundaries and independent geometry checks."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("sampled_particle_profile", Path(__file__).resolve().parents[1] / "src/skill_conversion/sampled_particle_profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def xyz(x,y,z):
    return dict(x=x,y=y,z=z)


def fixture():
    node = {"path":"fixture/quad", "ps":{"renderMode":"Billboard", "scalingMode":0,
        "dynamics":{"simulationSpace":1}, "tilesX":2, "tilesY":2},
        "renderer":{"alignment":0, "pivot":[0,0,0], "flip":[0,0,0]}}
    color = dict(r=255,g=128,b=0,a=64)
    # Source getter size1, owner scale2, clockwise90 degrees: the atlas cell is1.
    particle = dict(seed=42,lifetime=.1,remaining=.08,rotation=90.,position=xyz(3,4,5),
                    velocity=xyz(0,0,0),size=xyz(1,1,1),color=color)
    empty = dict(particles=[],vertices=[],uv=[],colors=[])
    frame = dict(frame=1,time=1/30,particles=[particle],vertices=[xyz(4,5,5),xyz(4,3,5),xyz(2,3,5),xyz(2,5,5)],
                 uv=[dict(x=.5,y=1),dict(x=1,y=1),dict(x=1,y=.5),dict(x=.5,y=.5)],colors=[dict(color) for _ in range(4)])
    capture = dict(format="nc-unity-particle-samples-v1",unity="fixture",prefab="fixture",nodePath="quad",
        fps=30,frames=2,simulationSpace=1,alignment=0,renderMode=0,scalingMode=0,
        ownerScale=xyz(2,2,2),pivot=xyz(0,0,0),samples=[dict(empty,frame=0,time=0),frame,dict(empty,frame=2,time=2/30)])
    return node,capture


class CapturedParticleTests(unittest.TestCase):
    def test_reflection_rotation_atlas_color_and_original_inputs(self):
        node,capture=fixture(); original=copy.deepcopy(capture)
        particles,audit=profile.convert_samples(node,capture,7)
        sample=particles[0]["samples"][0]
        self.assertEqual(sample["position"],[-3,4,5])
        self.assertEqual(sample["renderSize"],[2,2,1])
        self.assertEqual(sample["flipbook"],1)
        self.assertEqual(sample["color"],[1,128/255,0,64/255])
        self.assertEqual(audit["vertex_checks"],4)
        self.assertLess(audit["maximum_vertex_error"],1e-12)
        self.assertEqual(capture,original)

    def test_rejects_source_geometry_uv_and_color_disagreement(self):
        for field,key,value in (("vertices","x",4.1),("uv","x",.51),("colors","a",63)):
            node,capture=fixture(); capture["samples"][1][field][0][key]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                profile.convert_samples(node,capture,0)

    def test_rejects_incomplete_clock_and_truncated_lifetime(self):
        node,capture=fixture()
        capture["samples"][0]["frame"]=1
        with self.assertRaisesRegex(ValueError,"clock"):
            profile.convert_samples(node,capture,0)
        node,capture=fixture();capture["frames"]=1;capture["samples"].pop()
        with self.assertRaisesRegex(ValueError,"died"):
            profile.convert_samples(node,capture,0)

    def test_rejects_seed_reuse_and_duplicate_identities(self):
        node,capture=fixture(); capture["frames"]=4
        capture["samples"].extend([dict(copy.deepcopy(capture["samples"][1]),frame=3),dict(copy.deepcopy(capture["samples"][0]),frame=4)])
        with self.assertRaisesRegex(ValueError,"reused"):
            profile.convert_samples(node,capture,0)
        node,capture=fixture(); row=capture["samples"][1]
        for field in ("particles","vertices","uv","colors"):row[field]*=2
        with self.assertRaisesRegex(ValueError,"unique"):
            profile.convert_samples(node,capture,0)

    def test_rejects_unsupported_owner_velocity_and_streams(self):
        edits=[lambda n,c:c.update(ownerScale=xyz(-2,2,2)),
               lambda n,c:c.update(ownerScale=xyz(1,2,2)),
               lambda n,c:c["samples"][1]["particles"][0].update(velocity=xyz(1,0,0)),
               lambda n,c:n["ps"].update(customData={"vector0_0":1}),
               lambda n,c:n["renderer"].update(alignment=2)]
        for edit in edits:
            node,capture=fixture();edit(node,capture)
            with self.assertRaises(ValueError):profile.convert_samples(node,capture,0)

    def test_rejects_nonfinite_and_wrong_source(self):
        node,capture=fixture();capture["samples"][1]["particles"][0]["rotation"]=float("nan")
        with self.assertRaises(ValueError):profile.convert_samples(node,capture,0)
        node,capture=fixture();capture["nodePath"]="other"
        with self.assertRaisesRegex(ValueError,"identity"):profile.convert_samples(node,capture,0)


if __name__=="__main__":
    unittest.main()
