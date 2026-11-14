"""Exercise the installed sampled-actor converter against a private probe audit.

blender --background --factory-startup --python this.py -- --addon-root PATH
  --evidence sampled-actor-audit.json --output NEW_DIRECTORY
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addon-root",type=Path,required=True)
    parser.add_argument("--evidence",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if args.output.exists():
        raise FileExistsError(args.output)
    root=args.addon_root.resolve()
    spec=importlib.util.spec_from_file_location("Time_Stranger_Importer",root/"__init__.py",submodule_search_locations=[str(root)])
    addon=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=addon
    spec.loader.exec_module(addon)
    from Time_Stranger_Importer.src.backend import load_format_modules
    from Time_Stranger_Importer.src.skill_conversion import actor_lifecycle
    evidence=json.loads(args.evidence.read_text(encoding="utf-8"))
    geom_path=Path(evidence["geometry"]["path"])
    assert hashlib.sha256(geom_path.read_bytes()).hexdigest()==evidence["geometry"]["sha256"]
    loaded=actor_lifecycle.load_controller_samples(evidence["source"]["path"],evidence["source"]["sha256"],sequence=evidence["source"]["sequence"])
    geometry=load_format_modules()[0].Geom.from_file(str(geom_path))
    mapping=evidence["bone_paths"]
    animation=actor_lifecycle.animation_from_controller_samples(geometry,loaded,mapping,coordinate_profile="unity-reflect-x-v1")
    args.output.mkdir(parents=True)
    path=args.output/"sampled-actor.anim"
    animation.to_file(str(path))
    read=addon._read_anim_file(str(path))
    assert hashlib.sha256(path.read_bytes()).hexdigest()==evidence["output"]["sha256"]
    reflection=Matrix.Diagonal((-1,1,1,1))
    max_error=0.0
    for frame,sample in enumerate(loaded["sequence"]["samples"]):
        source_worlds={}
        for pose in sample["poses"]:
            q=pose["rotation"]
            local=Matrix.LocRotScale(Vector(pose["position"]),Quaternion((q[3],*q[:3])),Vector(pose["scale"]))
            source_worlds[pose["path"]]=(source_worlds[pose["path"].rpartition("/")[0]]@local if pose["path"] else local)
        native_worlds={}
        for index,bone in enumerate(geometry.skeleton.bones):
            def channel(channels,default):
                values=channels.get(index,{})
                return values[max(f for f in values if f<=frame)] if values else default
            rest=bone.transform
            q=rest.quaternion; dq=channel(read.rotations,[0,0,0,1])
            position=Vector(rest.position[:3])+Vector(channel(read.positions,[0,0,0]))
            rotation=Quaternion((q[3],*q[:3]))@Quaternion((dq[3],*dq[:3]))
            scale=Vector([a*b for a,b in zip(rest.scale[:3],channel(read.scales,[1,1,1]))])
            local=Matrix.LocRotScale(position,rotation,scale)
            actual=native_worlds[bone.parent.name]@local if bone.parent else local
            native_worlds[bone.name]=actual
            expected=reflection@source_worlds[mapping[bone.name]]@reflection
            max_error=max(max_error,max(abs(actual[i][j]-expected[i][j]) for i in range(4) for j in range(4)))
    assert max_error<.002,max_error
    invalid=dict(mapping);invalid.pop(next(iter(invalid)))
    try:
        actor_lifecycle.animation_from_controller_samples(geometry,loaded,invalid,coordinate_profile="unity-reflect-x-v1")
    except actor_lifecycle.ActorLifecycleError:
        pass
    else:
        raise AssertionError("Missing bone mapping accepted")
    try:
        actor_lifecycle.load_controller_samples(evidence["source"]["path"],"0"*64,sequence=evidence["source"]["sequence"])
    except actor_lifecycle.ActorLifecycleError:
        pass
    else:
        raise AssertionError("Changed source accepted")
    assert hashlib.sha256(path.read_bytes()).hexdigest()==evidence["output"]["sha256"]
    result={"blender":bpy.app.version_string,"addon_root":str(root),"module":actor_lifecycle.__file__,
            "module_sha256":hashlib.sha256(Path(actor_lifecycle.__file__).read_bytes()).hexdigest(),
            "frames":read.frame_count,"bones":read.bone_count,"maximum_matrix_error":max_error,
            "output_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "checks":["source hash pin","native actor mapping","atomic ANIM serialization and byte equality",
                      "every bone/frame world matrix","missing mapping rejection","stale source rejection"]}
    (args.output/"actor-lifecycle-check.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
