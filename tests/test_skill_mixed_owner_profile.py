"""Independent algebra and rejection tests for the bounded native root profile."""
import copy
import importlib.util
import math
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('mixed_owner', Path(__file__).resolve().parents[1] / 'src/skill_conversion/mixed_owner_profile.py')
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


def node():
    return {'prefabPos':[0,.27,0], 'renderer':{'alignment':2,'pivot':[0,0,0]},
            'ps':{'renderMode':'Mesh','dynamics':{'simulationSpace':1,
                  'startSpeed':{'minMaxState':0,'scalar':0},'gravityModifier':{'minMaxState':0,'scalar':0}}}}


def particle():
    return {'renderMode':'Mesh','alignment':2,'samples':[
        {'frame':i,'position':[0,.27,0],'worldOffset':[0,0,0],
         'localVelocity':[0,0,0],'worldVelocity':[0,0,0]} for i in range(3)]}


def owner(yaw, position=None, scale=None):
    return {'position':position or [2,3,4],'scale':scale or [1.7]*3,
            'rotation':[0,math.sin(yaw/2),0,math.cos(yaw/2)]}


class MixedOwnerProfileTests(unittest.TestCase):
    def test_source_is_not_mutated_and_original_space_is_reported(self):
        n,p=node(),particle();before=copy.deepcopy((n,p))
        report=profile.validate_samples(n,p)
        self.assertEqual((n,p),before)
        self.assertEqual(report['source_simulation_space'],1)
        self.assertFalse(report['assumptions']['in_game_verified'])

    def test_independent_birth_and_current_yaw_centers_are_equal(self):
        center=(0,.27,0);point=(.6,-.2,.4);scale=1.7;position=(2,3,4)
        def rotate(yaw,p):
            c,s=math.cos(yaw),math.sin(yaw)
            return (c*p[0]+s*p[2],p[1],-s*p[0]+c*p[2])
        for birth in (-2.7,-.1,.5,2.8):
            for current in (-3.1,-1.1,0,1.2,3.0):
                profile.validate_owner_samples([owner(birth),owner(current)])
                frozen_center=rotate(birth,center)
                current_surface=rotate(current,point)
                expected=[p+scale*(a+b) for p,a,b in zip(position,frozen_center,current_surface)]
                combined=rotate(current,tuple(a+b for a,b in zip(center,point)))
                actual=[p+scale*v for p,v in zip(position,combined)]
                for a,b in zip(actual,expected):self.assertAlmostEqual(a,b,places=12)

    def test_reject_moving_root(self):
        with self.assertRaisesRegex(ValueError,'stationary'):
            profile.validate_owner_samples([owner(0),owner(1,[2.1,3,4])])

    def test_reject_scale_change_and_nonuniform_scale(self):
        for scale in ([1.8]*3,[1,2,1]):
            with self.assertRaises(ValueError):profile.validate_owner_samples([owner(0),owner(1,scale=scale)])

    def test_reject_tilt(self):
        value=owner(0);value['rotation']=[math.sin(.1),0,0,math.cos(.1)]
        with self.assertRaisesRegex(ValueError,'upright'):profile.validate_owner_samples([value])

    def test_reject_off_axis_and_motion(self):
        n=node();n['prefabPos'][0]=.1
        with self.assertRaisesRegex(ValueError,'yaw axis'):profile.validate_node(n)
        for field in ('position','worldOffset','localVelocity','worldVelocity'):
            p=particle();p['samples'][1][field][0]=.1
            with self.assertRaises(ValueError):profile.validate_samples(node(),p)

    def test_reject_missing_samples_and_nonfinite_input(self):
        p=particle();p['samples']=[]
        with self.assertRaises(ValueError):profile.validate_samples(node(),p)
        p=particle();p['samples'][0]['position'][0]=math.nan
        with self.assertRaises(ValueError):profile.validate_samples(node(),p)

    def test_mesh_profile_requires_explicit_bounded_selection(self):
        spec=importlib.util.spec_from_file_location('mesh_profile',Path(__file__).resolve().parents[1]/'src/skill_conversion/mesh_profile.py')
        mesh=importlib.util.module_from_spec(spec);spec.loader.exec_module(mesh)
        n=node();n['material_infos']=[{'shader':'Honor/FX/CommonShader'}]
        with self.assertRaisesRegex(ValueError,'separate current-owner'):
            mesh.validate_local_mesh(n)
        mesh.validate_local_mesh(n,ownership_profile=profile.PROFILE)
        p=particle()
        for sample in p['samples']:
            sample.update(viewAligned=False,size=[1,1,1],rotationDegrees=[0,0,0],rotation=[0,0,0,1],
                          color=[1]*4,customData=[0]*4,customData2=[0]*4,flipbook=0)
        self.assertEqual(len(mesh.prepare_local_mesh_samples(n,p,ownership_profile=profile.PROFILE)),3)
        p['samples'][1]['position'][0]=.1
        with self.assertRaisesRegex(ValueError,'stationary'):
            mesh.prepare_local_mesh_samples(n,p,ownership_profile=profile.PROFILE)


if __name__=='__main__':unittest.main()
