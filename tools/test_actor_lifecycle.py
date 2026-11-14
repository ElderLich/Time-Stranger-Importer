"""Actor placement contracts and optional captured real-Animator comparisons."""
from pathlib import Path
import hashlib
import importlib.util
import json
import math
import tempfile
import unittest

MODULE=Path(__file__).resolve().parents[1]/"src/skill_conversion/actor_lifecycle.py"
spec=importlib.util.spec_from_file_location("actor_lifecycle",MODULE)
lifecycle=importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)
FIXTURE=Path(__file__).resolve().parents[2]/"Time Stranger/local/ordinary-skill-native-candidate/actors"


class ActorLifecycleTests(unittest.TestCase):
    def test_approach_uses_selected_target_front_distance(self):
        result=lifecycle.sample_linear_role_movement({"moveFrame":10,"offsetDistance":2},
            [1,0,-2],[10,0,10],target_front_distance=1)
        self.assertEqual(result["samples"][0]["position"],[1,0,-2])
        for actual,expected in zip(result["destination"],[8.2,0,7.6]):
            self.assertAlmostEqual(actual,expected)
        self.assertEqual(len(result["samples"]),10)
        self.assertAlmostEqual(result["samples"][5]["yaw"],math.atan2(9,12))
        other=lifecycle.sample_linear_role_movement({"moveFrame":10,"offsetDistance":2},
            [1,0,-2],[10,0,10],target_front_distance=4)
        self.assertNotEqual(result["destination"],other["destination"])

    def test_return_keeps_facing_until_parent_reset(self):
        result=lifecycle.sample_linear_role_movement({"moveFrame":10,"moveLookAtTarget":False},
            [8.2,0,7.6],[1,0,-2],kind="return",initial_yaw=.7,parent_yaw=1.2)
        self.assertTrue(all(p["yaw"]==.7 for p in result["samples"][:-1]))
        self.assertEqual(result["samples"][-1]["yaw"],1.2)
        self.assertEqual(result["samples"][-1]["position"],[1,0,-2])
        self.assertFalse(result["native_dynamic_placement_supported"])

    def test_short_approach_keeps_source_one_point_branch(self):
        result=lifecycle.sample_linear_role_movement({"moveFrame":10,"offsetDistance":2},
            [0,0,0],[0,0,2.05])
        self.assertEqual(result["sample_count"],1)
        self.assertEqual(result["samples"][0]["position"],[0,0,0])
        immediate=lifecycle.sample_linear_role_movement({"moveFrame":1,"offsetDistance":2},
            [0,0,0],[0,0,2.05])
        self.assertAlmostEqual(immediate["samples"][0]["position"][2],.05)

    def test_timers_are_seconds_based_and_cached(self):
        length=.9666666984558105
        self.assertEqual(lifecycle.automatic_stand_schedule(length,[1/30]*40)["tick"],27)
        self.assertEqual(lifecycle.automatic_stand_schedule(length,[1/60]*80)["tick"],53)
        self.assertEqual(lifecycle.automatic_stand_schedule(length,[1/30]*40,scheduled_after_update=False)["tick"],26)
        self.assertEqual(lifecycle.automatic_stand_schedule(.05,[])["tick"],0)
        with self.assertRaises(lifecycle.ActorLifecycleError):
            lifecycle.automatic_stand_schedule(length,[1/30])

    def test_unproven_motion_rejected(self):
        for extra in ({"moveCurve":[{},{}]},{"aoePostion":2},{"isMultHit":2},
                      {"moveBackEndTurnKeepFrame":5},{"step":-1},{"moveFrame":False}):
            with self.subTest(extra=extra),self.assertRaises(lifecycle.ActorLifecycleError):
                lifecycle.sample_linear_role_movement({"moveFrame":10,**extra},[0,0,0],[0,0,5])

    @unittest.skipUnless((FIXTURE/"ControllerProbe2022/controller-samples.json").is_file(),"private Unity fixture absent")
    def test_real_animator_transition_and_provenance(self):
        path=FIXTURE/"ControllerProbe2022/controller-samples.json"
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        loaded=lifecycle.load_controller_samples(path,digest,sequence="return_to_stand_30hz_cache1")
        samples=loaded["sequence"]["samples"]
        self.assertEqual(loaded["sequence"]["crossFadeFrame"],27)
        self.assertEqual(len(samples[0]["poses"]),136)
        self.assertEqual([s["frame"] for s in samples if s["transition"]],[27,28,29,30,31])
        self.assertAlmostEqual(samples[27]["nextTime"],0)
        self.assertAlmostEqual(samples[27]["transitionTime"],(1/30)/(.2*.9666666984558105),places=6)
        with self.assertRaises(lifecycle.ActorLifecycleError):
            lifecycle.load_controller_samples(path,"0"*64,sequence="return_to_stand_30hz_cache1")

    @unittest.skipUnless((FIXTURE/"ControllerProbe6000/controller-samples.json").is_file(),"private Unity fixture absent")
    def test_two_engine_transform_samples_agree(self):
        a=json.loads((FIXTURE/"ControllerProbe2022/controller-samples.json").read_text())
        b=json.loads((FIXTURE/"ControllerProbe6000/controller-samples.json").read_text())
        maximum=0
        for first,second in zip(a["sequences"],b["sequences"]):
            self.assertEqual(first["label"],second["label"])
            for f,g in zip(first["samples"],second["samples"]):
                self.assertEqual(f["frame"],g["frame"])
                for p,q in zip(f["poses"],g["poses"]):
                    self.assertEqual(p["path"],q["path"])
                    for key in ("position","rotation","scale"):
                        maximum=max(maximum,max(abs(v-w) for v,w in zip(p[key],q[key])))
        self.assertLess(maximum,2e-7)


if __name__=="__main__":
    unittest.main()
