"""Finite view-mesh geometry and unsupported-input regressions."""
import copy
import importlib.util
import math
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('view_mesh_profile', Path(__file__).resolve().parents[1] / 'src/skill_conversion/view_mesh_profile.py')
profile = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(profile)


def node():
    return {'prefabBasis': [1,0,0,0,1,0,0,0,1],
            'renderer': {'renderMode':'Mesh','alignment':0,'pivot':[0,0,0],'flip':[0,0,0]},
            'ps': {'renderMode':'Mesh','size3D':False,'scalingMode':0,'dynamics':{'simulationSpace':0}},
            'material_infos':[{'shader':'Honor/FX/Honor_KJniuqu','passStates':[
                {'srcBlend':5,'dstBlend':10,'srcBlendAlpha':5,'dstBlendAlpha':10,'blendOp':0,
                 'blendOpAlpha':0,'colorMask':15,'cull':0,'zTest':4,'zWrite':0}]}]}


def particle():
    return {'renderMode':'Mesh','alignment':0,'samples':[
        {'frame':1,'position':[1,2,3],'worldOffset':[0,-.5,0],'size':[2,2,2],
         'rotation':[0,1,0,0],'viewAligned':True,'flipbook':0,'color':[1,.5,.2,1],
         'customData':[0,0,0,0],'customData2':[0,0,0,0]}]}


def rotate(q, p):
    x,y,z,w=q; px,py,pz=p
    return [(1-2*y*y-2*z*z)*px+(2*x*y-2*z*w)*py+(2*x*z+2*y*w)*pz,
            (2*x*y+2*z*w)*px+(1-2*x*x-2*z*z)*py+(2*y*z-2*x*w)*pz,
            (2*x*z-2*y*w)*px+(2*y*z+2*x*w)*py+(1-2*x*x-2*y*y)*pz]


class ViewMeshTests(unittest.TestCase):
    def test_separates_world_offset_and_owner_from_view_rotation(self):
        original=particle(); before=copy.deepcopy(original)
        sample=profile.prepare_view_mesh_samples(node(),original)[0]
        self.assertEqual(original,before)
        self.assertEqual(sample['position'],[1,2.5,3])
        self.assertEqual(sample['rotationDegrees'],[0,0,0])
        self.assertEqual(sample['materialRotation'],[0,1,0,0])
        self.assertEqual(sample['size'],[2,2,2])

    def test_reflected_camera_basis_matches_unity_nonuniform_owner_order(self):
        # Unity world fixture: 37-degree Z, owner scale(1.3,.7,1.6),
        # particle size(3,2,1). For source vertex+X, measured world delta:
        # (3.1146788597,1.2638115883,0). Source mesh X is reflected.
        # Native camera basis F*identity*optical = diag(-1,1,-1).
        angle=math.radians(37); q=[-math.sin(angle/2),math.cos(angle/2),0,0]
        spun=rotate(q,[-3,0,0])
        native=[spun[0]*-1.3,spun[1]*.7,spun[2]*-1.6]
        expected=[-3.1146788597106934,1.2638115882873535,0]
        self.assertLess(max(abs(a-b) for a,b in zip(native,expected)),5e-7)
        # Incorrectly scaling input before rotation differs substantially.
        wrong=rotate(q,[-3*1.3,0,0]);wrong=[-wrong[0],wrong[1],-wrong[2]]
        self.assertGreater(abs(wrong[1]-native[1]),1)

    def test_quaternion_sign_continuity_preserves_rotation(self):
        p=particle(); second=copy.deepcopy(p['samples'][0]);second.update(frame=2,rotation=[0,-1,0,0]);p['samples'].append(second)
        got=profile.prepare_view_mesh_samples(node(),p)
        self.assertEqual(got[0]['materialRotation'],got[1]['materialRotation'])
        self.assertEqual(p['samples'][1]['rotation'],[0,-1,0,0])

    def test_rejects_size_shear_owner_and_material_profile_changes(self):
        changes=[lambda n:n['ps'].update(size3D=True),
                 lambda n:n['ps']['dynamics'].update(simulationSpace=1),
                 lambda n:n['ps']['dynamics'].update(size={'separate':True}),
                 lambda n:n['renderer'].update(alignment=2),
                 lambda n:n['renderer'].update(pivot=[1,0,0]),
                 lambda n:n.update(prefabBasis=[1,0,0,0,2,0,0,0,1]),
                 lambda n:n['material_infos'][0]['passStates'][0].update(srcBlendAlpha=1)]
        for change in changes:
            n=node();change(n)
            with self.assertRaises(ValueError):profile.validate_view_mesh(n)
        p=particle();p['samples'][0]['size']=[2,3,2]
        with self.assertRaises(ValueError):profile.prepare_view_mesh_samples(node(),p)
        p['samples'][0]['size']=[-2,-2,-2]
        with self.assertRaises(ValueError):profile.prepare_view_mesh_samples(node(),p)

    def test_rejects_invalid_sample_clock_and_unbound_streams(self):
        for updates in ({'frame':1.5},{'rotation':[0,0,0,0]}, {'rotation':[0,0,1]}, {'rotation':[0,0,math.nan,1]}, {'position':[0,math.nan,0]},
                        {'viewAligned':False},{'customData':[1,0,0,0]},{'flipbook':1}):
            p=particle();p['samples'][0].update(updates)
            with self.assertRaises(ValueError):profile.prepare_view_mesh_samples(node(),p)
        p=particle();p['samples'].append(copy.deepcopy(p['samples'][0]));p['samples'][1]['frame']=3
        with self.assertRaises(ValueError):profile.prepare_view_mesh_samples(node(),p)

    def test_runtime_owner_requires_positive_scale_and_no_shear(self):
        owner=[[0,-.7,0,2],[1.3,0,0,3],[0,0,1.6,-1],[0,0,0,1]]
        self.assertTrue(profile.validate_runtime_owner(owner,[1.3,.7,1.6]))
        for scale in ([-1.3,.7,1.6],[-1.3,-.7,1.6],[0,.7,1.6]):
            with self.assertRaises(ValueError):profile.validate_runtime_owner(owner,scale)
        reflected=copy.deepcopy(owner);reflected[2][2]*=-1
        with self.assertRaises(ValueError):profile.validate_runtime_owner(reflected,[1.3,.7,1.6])
        shear=copy.deepcopy(owner);shear[0][0]=.2
        with self.assertRaises(ValueError):profile.validate_runtime_owner(shear,[1.3,.7,1.6])


if __name__=='__main__':unittest.main()
