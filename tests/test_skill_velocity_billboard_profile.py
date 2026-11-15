"""Measured velocity geometry and bounded source-carrier validation."""
import copy
import importlib.util
import math
from pathlib import Path
import unittest

SPEC=importlib.util.spec_from_file_location('velocity_profile',Path(__file__).resolve().parents[1]/'src/skill_conversion/velocity_billboard_profile.py')
p=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(p)


def node():
    return {'prefabBasis':[1,0,0,0,1,0,0,0,1],'prefabScale':[1,1,1],
            'renderer':{'renderMode':'Billboard','alignment':4,'pivot':[.2,-.3,.4],'flip':[0,0,0]},
            'ps':{'renderMode':'Billboard','scalingMode':0,'rotation3D':True,
                  'startRotationAxes':[{'minMaxState':0,'scalar':math.radians(v)} for v in (13,27,41)],
                  'dynamics':{'simulationSpace':0}},
            'material_infos':[{'shader':'Legacy Shaders/Particles/Additive','passStates':[
                {'srcBlend':5,'dstBlend':1,'srcBlendAlpha':5,'dstBlendAlpha':1,'blendOp':0,
                 'blendOpAlpha':0,'colorMask':14,'cull':0,'zTest':4,'zWrite':0}]}]}


def particle():
    return {'renderMode':'Billboard','alignment':4,'samples':[
        {'frame':5,'renderOrigin':[1,2,3],'position':[99,99,99],'worldOffset':[0,-.5,0],
         'renderSize':[3,2,1],'localVelocity':[-.3,.4,.5],'worldVelocity':[0,0,0],
         'color':[1,.5,.2,1],'customData':[0,0,0,0],'customData2':[0,0,0,0],'flipbook':0}]}


class VelocityBillboardTests(unittest.TestCase):
    def test_separate_dimensions_and_unshifted_origin(self):
        original=particle();before=copy.deepcopy(original)
        s=p.prepare_velocity_samples(node(),original)[0]
        self.assertEqual(original,before)
        self.assertEqual(s['position'],[1,2.5,3])
        self.assertEqual(s['dimensions'],[3,2])
        self.assertEqual(s['size'],[1,1,1])
        self.assertEqual(s['rotationDegrees'],[0,0,0])

    def test_measured_unity_3d_rotation_and_depth_pivot(self):
        c=p.profile_constants(node());v=[-.3,.4,.5]
        normal=[x/math.sqrt(.5) for x in v]
        tangent=p._apply(c['tangent'],v);length=math.sqrt(sum(x*x for x in tangent));tangent=[x/length for x in tangent]
        up=p._cross(tangent,normal)
        point=p._apply(c['spin'],[-.3*3,.2*2,.4*3]) # UV(0,1), pivotZ uses width.
        got=[sum(axis[i]*value for axis,value in zip((tangent,up,normal),point)) for i in range(3)]
        expected=[-(1.4513198137283325-1.0000300407409668),1.2499595880508423-2.000040054321289,4.282125473022461-3.0000500679016113]
        self.assertLess(max(abs(a-b) for a,b in zip(got,expected)),5e-7)

    def test_projected_hierarchy_scale_is_not_column_norm(self):
        n=node();a=math.radians(35);s=math.sin(a);c=math.cos(a)
        n['prefabBasis']=[c,-s,0,s,c,0,0,0,1]
        constants=p.profile_constants(n)
        scale=p._apply(constants['scale_weights'],[1.3,.7,1.6])
        self.assertAlmostEqual(scale[0],1.3*c*c+.7*s*s)
        self.assertGreater(abs(scale[0]-math.hypot(1.3*c,.7*s)),.02)

    def test_rejects_unproved_source_geometry_and_material(self):
        changes=[lambda n:n['renderer'].update(alignment=0),lambda n:n['renderer'].update(flip=[1,0,0]),
                 lambda n:n['ps']['dynamics'].update(simulationSpace=1),lambda n:n['ps']['dynamics'].update(angularVelocity={ 'separate':True }),
                 lambda n:n['ps']['startRotationAxes'][0].update(minMaxState=3,minScalar=1),
                 lambda n:n.update(prefabScale=[-1,-1,1]),lambda n:n.update(prefabBasis=[1,.2,0,0,1,0,0,0,1]),
                 lambda n:n.update(prefabScale=[1,2,1]),lambda n:n['material_infos'][0]['passStates'][0].update(srcBlendAlpha=1)]
        for change in changes:
            n=node();change(n)
            with self.assertRaises(ValueError):p.validate_velocity_billboard(n)

    def test_rejects_unbound_or_degenerate_sample_inputs(self):
        for values in ({'frame':5.5},{'localVelocity':[0,0,1]},{'localVelocity':[0,0,0]},
                       {'localVelocity':[math.nan,0,0]},{'worldVelocity':[0,1,0]},
                       {'renderSize':[-1,2,1]},{'renderSize':[1,2]},
                       {'customData':[1,0,0,0]},{'renderOrigin':[1,2,math.inf]},{'flipbook':1}):
            value=particle();value['samples'][0].update(values)
            with self.assertRaises(ValueError):p.prepare_velocity_samples(node(),value)
        value=particle();second=copy.deepcopy(value['samples'][0]);second['frame']=7;value['samples'].append(second)
        with self.assertRaises(ValueError):p.prepare_velocity_samples(node(),value)

    def test_shader_uses_separate_tracks_without_camera_or_fallback(self):
        source=p.vertex_source(node())
        self.assertNotIn('ViewInverse',source)
        self.assertNotIn('max(',source)
        self.assertIn('float3 up=cross(tangent,normal)',source)
        self.assertEqual(p.FLOAT_BINDINGS,((301,1),(301,2),(36,0),(36,1),(36,2),(178,0),(178,1),(178,2)))


if __name__=='__main__':unittest.main()
