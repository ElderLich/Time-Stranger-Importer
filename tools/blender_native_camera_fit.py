"""Fit constant native cameras to complete actor clips from a pinned JSON job."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Quaternion, Vector

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--addon-root', type=Path, required=True)
parser.add_argument('--job', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])

def checked_output(path):
    path = path.absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path,*path.parents)):
        raise ValueError('Redirected camera output is unsupported')
    return path.resolve()


root, output = args.addon_root.resolve(), checked_output(args.output)
if output.exists() or not output.parent.is_dir():
    raise ValueError('Choose a new output directory with an existing parent')
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', root/'__init__.py', submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec);sys.modules[spec.name] = addon;spec.loader.exec_module(addon);addon.register()
from Time_Stranger_Importer.src.skill_conversion.compiler import _check_pin, pin_file
from Time_Stranger_Importer.src.skill_conversion.camera_assets import camera_record, rename_camera_bindings
from Time_Stranger_Importer.src.geom import import_geom
from Time_Stranger_Importer.src.backend import load_format_modules
from Time_Stranger_Importer.src.anim import AnimFileHundredLine

job_raw = args.job.read_bytes()
job_pin = {'path':str(args.job.resolve()), 'sha256':hashlib.sha256(job_raw).hexdigest(),'bytes':len(job_raw)}
job = json.loads(job_raw)
if job.get('format') != 'native-camera-fit-job-v1':raise ValueError('Unsupported camera fit job')
inputs = [job_pin, job['actor_geom'], job['actor_nlst'], job['actor_base_anim']]
actor_geom_path = Path(job['actor_geom']['path']).resolve()
for key,suffix in [('actor_nlst','.nlst'),('actor_base_anim','.anim')]:
    if Path(job[key]['path']).resolve()!=actor_geom_path.with_suffix(suffix):raise ValueError('Actor companion pin differs from imported sibling')
aspects, margin = job['aspect_ratios'], job['margin']
if not aspects or not all(type(x) in (int,float) and math.isfinite(x) and .5<x<4 for x in aspects):raise ValueError('Invalid aspect ratios')
if type(margin) not in (int,float) or not .05 <= margin <= .3:raise ValueError('Invalid framing margin')
if not job['opaque_mesh_prefixes'] or not job['cameras']:raise ValueError('Select explicit opaque meshes and cameras')
stems = set()
for entry in job['cameras']:
    stem=entry['stem']
    if not re.fullmatch(r'cam_[a-z0-9_]{1,50}',stem) or stem.casefold() in stems:raise ValueError('Invalid or duplicate camera stem')
    stems.add(stem.casefold())
    if entry['name'].lower() != stem:raise ValueError('Camera name and filename differ')
    motion = Path(entry['actor_motion']['path']).resolve()
    if motion.parent!=actor_geom_path.parent or not motion.stem.startswith(actor_geom_path.stem+'_'):
        raise ValueError('Pinned actor motion must be the imported geometry sibling')
    inputs.extend(entry[key] for key in ('actor_motion','donor_geom','donor_anim','donor_nlst'))
for value in inputs:
    path,_ = _check_pin(value)
    if output.is_relative_to(path.parent) or path.is_relative_to(output):raise ValueError('Camera output overlaps source tree')
pending=output.parent/('.'+output.name+'.pending-'+uuid.uuid4().hex);pending.mkdir(mode=0o777)
patch=pending/'dsts-loader/patch';patch.mkdir(parents=True)
reviews=pending/'reviews';reviews.mkdir()
fmt=load_format_modules()[0];fmt.set_throw_errors(False)
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
collection=import_geom(bpy.context,job['actor_geom']['path'])
rig=next(o for o in collection.objects if o.type=='ARMATURE')
meshes=[o for o in collection.objects if o.type=='MESH']
fit=[o for o in meshes if o.name.startswith(tuple(job['opaque_mesh_prefixes']))]
if len(fit) != job['expected_mesh_count']:raise ValueError('Opaque mesh selection changed')
scene=bpy.context.scene;scene.render.fps=30
axes=Matrix.Rotation(math.pi/2,4,'X');native_axes=axes.inverted()
for track in rig.animation_data.nla_tracks:track.mute=True
for obj in meshes:
    if obj not in fit:obj.hide_render=True
for mat in bpy.data.materials:
    if not mat.use_nodes:continue
    groups=[n.node_tree for n in mat.node_tree.nodes if n.type=='GROUP' and n.node_tree]
    texture=next((n.image for group in groups for n in group.nodes if n.type=='TEX_IMAGE' and n.get('DSTS_parameter_id')==24),None)
    if texture:
        node=mat.node_tree.nodes.new('ShaderNodeTexImage');node.image=texture;mat.node_tree.nodes.active=node
scene.render.engine='BLENDER_WORKBENCH';scene.display.shading.light='STUDIO'
scene.display.shading.color_type='TEXTURE';scene.display.shading.show_shadows=True
scene.display.shading.background_type='WORLD';scene.world.color=(.025,.035,.045)
scene.render.resolution_x=960;scene.render.resolution_y=540;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'
samples_cache={};records=[];files=[]
for entry in job['cameras']:
    motion_path=Path(entry['actor_motion']['path']);actor=addon._read_anim_file(str(motion_path))
    if abs(actor.playback_rate-30)>1e-4:raise ValueError('Actor fit requires verified30Hz clip')
    if actor.bone_count!=len(rig.data.bones):raise ValueError('Actor motion/rig bone count differs')
    tracks=[t for t in rig.animation_data.nla_tracks if t.name==motion_path.stem]
    if len(tracks)!=1:raise ValueError('Actor motion must be imported beside its geometry')
    clip=tracks[0].strips[0];rig.animation_data.action=clip.action;rig.animation_data.action_slot=clip.action_slot
    if str(motion_path) not in samples_cache:
        samples=[]
        for frame in range(actor.frame_count):
            scene.frame_set(frame+1);bpy.context.view_layer.update();graph=bpy.context.evaluated_depsgraph_get();parts=[]
            for obj in fit:
                evaluated=obj.evaluated_get(graph);vertices=np.empty(len(evaluated.data.vertices)*3,dtype=np.float32)
                evaluated.data.vertices.foreach_get('co',vertices);transform=np.array(native_axes@evaluated.matrix_world,dtype=np.float64)
                parts.append(vertices.reshape(-1,3)@transform[:3,:3].T+transform[:3,3])
            points=np.concatenate(parts)
            if not np.isfinite(points).all():raise ValueError('Nonfinite actor geometry')
            samples.append(points)
        samples_cache[str(motion_path)]=samples
    samples=samples_cache[str(motion_path)]
    raw=Path(entry['donor_geom']['path']).read_bytes();_,record=camera_record(raw)
    geom=fmt.Geom.from_file(entry['donor_geom']['path']);donor=addon._read_anim_file(entry['donor_anim']['path'])
    indices=[i for i,b in enumerate(geom.skeleton.bones) if b.name_hash==record[0]]
    if len(indices)!=1 or donor.bone_count!=len(geom.skeleton.bones):raise ValueError('Donor camera rig differs')
    ci=indices[0]
    if geom.skeleton.bones[ci].parent is not None:raise ValueError('Parented camera fitting is unsupported')
    rotation=donor.rotations.get(ci,{})
    value=rotation.get(0,list(geom.skeleton.bones[ci].transform.quaternion))
    q=Quaternion((value[3],*value[:3])).normalized();fov=float(record[1])
    if donor.float_channel_count:raise ValueError('Choose a static-lens ordinary camera donor')
    if not 10<fov<100 or not 0<record[3]<record[4]:raise ValueError('Invalid donor lens')
    inverse=np.array(q.inverted().to_matrix()).T
    projected=[points@inverse for points in samples]
    low=np.min([p.min(axis=0) for p in projected],axis=0);high=np.max([p.max(axis=0) for p in projected],axis=0)
    center=(low+high)*.5;tan=math.tan(math.radians(fov)/2);limit=1-2*margin
    distance=max(max(float(np.maximum((p-center)[:,2]+np.abs((p-center)[:,1])/(tan*limit),
                    (p-center)[:,2]+np.abs((p-center)[:,0])/(tan*min(aspects)*limit)).max()),
                    float((p-center)[:,2].max())+record[3]*2) for p in projected)+.05
    position=(center+np.array([0,0,distance]))@np.array(q.to_matrix()).T
    raw,nlst=rename_camera_bindings(raw,Path(entry['donor_nlst']['path']).read_bytes().decode('ascii'),entry['name'])
    destination=patch/(entry['stem']+'.geom');destination.write_bytes(raw);destination.with_suffix('.nlst').write_bytes(nlst.encode('ascii'))
    anim=AnimFileHundredLine();anim.bone_count=donor.bone_count;anim.bone_blend_factors=donor.bone_blend_factors
    anim.float_channel_count=donor.float_channel_count;anim.float_channel_blend_factors=donor.float_channel_blend_factors
    anim.unknown_section=donor.unknown_section;anim.frame_count=actor.frame_count;anim.playback_rate=actor.playback_rate
    anim.positions={ci:{0:position.tolist()}};anim.rotations={ci:{0:[q.x,q.y,q.z,q.w]}}
    anim.to_file(str(destination.with_suffix('.anim')))
    restored=addon._read_anim_file(str(destination.with_suffix('.anim')));reread=fmt.Geom.from_file(str(destination))
    if reread.skeleton.bones[ci].name!=entry['name'] or restored.frame_count!=actor.frame_count:raise ValueError('Camera readback differs')
    rp=np.array(restored.positions[ci][0]);rq=restored.rotations[ci][0];rq=Quaternion((rq[3],*rq[:3])).normalized()
    data=bpy.data.cameras.new(entry['name']);camera=bpy.data.objects.new(entry['name'],data);scene.collection.objects.link(camera)
    camera.matrix_world=axes@Matrix.LocRotScale(Vector(rp),rq,Vector((1,1,1)));scene.camera=camera
    data.sensor_fit='VERTICAL';data.sensor_height=24;data.lens=24/(2*tan);data.clip_start=record[3];data.clip_end=record[4]
    bounds=[];checks=0
    for frame,points in enumerate(samples):
        local=(points-rp)@np.array(rq.inverted().to_matrix()).T;depth=-local[:,2]
        if not ((depth>record[3]).all() and (depth<record[4]).all()):raise ValueError('Camera clips actor depth')
        scene.frame_set(frame+1);bpy.context.view_layer.update()
        for aspect in aspects:
            screen=np.column_stack((.5+.5*local[:,0]/(depth*tan*aspect),.5+.5*local[:,1]/(depth*tan)))
            if not ((screen>margin-.002).all() and (screen<1-margin+.002).all()):raise ValueError('Serialized camera clips actor framing')
            scene.render.resolution_x=round(540*aspect)
            # Independent Blender projection at all extrema, every frame/aspect.
            for index in {int(screen[:,axis].argmin()) for axis in (0,1)}|{int(screen[:,axis].argmax()) for axis in (0,1)}:
                v=world_to_camera_view(scene,camera,axes@Vector(points[index]))
                if max(abs(v[k]-screen[index,k]) for k in (0,1))>.001:raise ValueError('Blender and native projection differ')
                checks+=1
            bounds.append({'frame':frame,'aspect':aspect,'min':screen.min(axis=0).tolist(),'max':screen.max(axis=0).tolist()})
    scene.render.resolution_x=960;scene.frame_start=1;scene.frame_end=actor.frame_count
    render_paths=[]
    for frame in sorted({1,actor.frame_count//2,actor.frame_count}):
        scene.frame_set(frame);scene.render.filepath=str(reviews/f'{entry["stem"]}-{frame:03}.png');bpy.ops.render.render(write_still=True)
        render_paths.append(str((output/'reviews'/Path(scene.render.filepath).name)))
    scene.frame_set(1);bpy.ops.wm.save_as_mainfile(filepath=str(reviews/(entry['stem']+'.blend')))
    for suffix in ('.geom','.anim','.nlst'):
        p=destination.with_suffix(suffix);files.append({'path':p.relative_to(pending).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
    records.append({'stem':entry['stem'],'name':entry['name'],'camera_index':ci,'frames':actor.frame_count,
                    'position':rp.tolist(),'rotation_xyzw':[rq.x,rq.y,rq.z,rq.w],'fov':fov,'near':record[3],'far':record[4],
                    'projection_checks':checks,'bounds':bounds,'renders':render_paths})
for value in inputs:_check_pin(value)
report={'format':'native-camera-fit-candidate-v1','status':'Native fixed-framing assets; in-game visual acceptance pending',
        'addon_root':str(root),'addon_version':list(addon.bl_info['version']),'job':job_pin,'inputs':inputs,
        'producer':pin_file(__file__),'files':files,'cameras':records,'game_acceptance':False,
        'scope':'Whole-clip opaque actor fit under native actor-origin/world-scale camera binding; no battle controller or source camera parity claim.'}
(pending/'camera-fit-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
(pending/'camera-fit-job.json').write_bytes(job_raw)
if checked_output(args.output)!=output or output.exists():raise FileExistsError(output)
os.rename(pending,output)
print('NATIVE CAMERA FIT PASS',json.dumps({'output':str(output),'files':len(files),'cameras':[{k:r[k] for k in ('stem','frames','position','projection_checks')} for r in records]}))
addon.unregister()
