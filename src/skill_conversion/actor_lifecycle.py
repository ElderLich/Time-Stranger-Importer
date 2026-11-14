"""Bounded New Century actor lifecycle primitives, independent of Blender.

Role transforms are deliberately separate from skeletal samples. These functions
reproduce the captured ordinary single-target, linear MoveToEffectView branch;
they do not assert that Time Stranger has an equivalent dynamic placement route.
Controller transitions are loaded from a pinned real-Animator probe, never
replaced with a guessed blend between unrelated phase previews.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct


class ActorLifecycleError(ValueError):
    pass


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ActorLifecycleError(f"{label} must be finite")
    return float(value)


def _vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ActorLifecycleError(f"{label} must have three coordinates")
    return tuple(_number(x, label) for x in value)


def _float32(value):
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError as exc:
        raise ActorLifecycleError("Value exceeds Unity float range") from exc


def automatic_stand_schedule(clip_length, delta_times, *, scheduled_after_update=True):
    """Run the captured cached SetDelayCall queue for ``clip_length - .1``.

``delta_times`` are successive role-update deltas after Play. FixedUpdater runs
role children before battle skill dispatch, so ordinary skill events use the
default: the next role update merges the new timer and does not decrement it.
The returned tick is one-based. No callback within the supplied horizon is an
error, not an invented fallback. A zero-delay callback executes synchronously.
"""
    length = _number(clip_length, "clip_length")
    if length < 0:
        raise ActorLifecycleError("clip_length must not be negative")
    delay = max(0.0, length - 0.1)
    if delay == 0:
        return {"tick": 0, "elapsed": 0.0, "remaining": 0.0, "delay": delay}
    elapsed = 0.0
    for tick, raw_delta in enumerate(delta_times, 1):
        delta = _float32(_number(raw_delta, "delta_time"))
        if delta < 0:
            raise ActorLifecycleError("delta_time must not be negative")
        elapsed += delta
        if scheduled_after_update and tick == 1:
            continue
        delay -= delta
        if delay <= 0:
            return {"tick": tick, "elapsed": elapsed, "remaining": delay,
                    "delay": max(0.0, length - 0.1)}
    raise ActorLifecycleError("Supplied update horizon does not reach automatic stand")


def sample_linear_role_movement(config, start, target, *, kind="approach",
                                target_front_distance=0.0, initial_yaw=0.0,
                                parent_yaw=0.0):
    """Sample the source role path, including both endpoints, in Unity axes.

For approach, ``target`` is the selected target's position and its own front
distance. For return, ``target`` is the caster parent position. Yaw is in
radians, separate from the rig. Arbitrary curves, AOE/multihit and turn easing
remain rejected until independently sampled. Output is preview/evidence data,
not a native root-motion animation.
"""
    if kind not in {"approach", "return"}:
        raise ActorLifecycleError("kind must be approach or return")
    if not isinstance(config, dict) or config.get("bEnable", True) is not True:
        raise ActorLifecycleError("Movement must be an enabled source record")
    if config.get("aoePostion", 0) not in (None, 0):
        raise ActorLifecycleError("AOE placement is not supported by this single-target profile")
    if config.get("isMultHit", 1) != 1:
        raise ActorLifecycleError("Multihit placement requires its separate source branch")
    curve = config.get("moveCurve")
    if curve and (not isinstance(curve, list) or len(curve) > 1):
        raise ActorLifecycleError("Authored movement curves require real-Unity curve samples")
    if config.get("moveBackEndTurnKeepFrame", 0) or config.get("moveBackEndTurnFrame", 0):
        raise ActorLifecycleError("Return turn easing requires a separate sampled profile")
    frames = config.get("moveFrame")
    if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= 100000:
        raise ActorLifecycleError("moveFrame must be an integer from 1 to 100000")
    start = _vector(start, "start")
    target = _vector(target, "target")
    yaw = _number(initial_yaw, "initial_yaw")
    parent_yaw = _number(parent_yaw, "parent_yaw")
    front = _number(target_front_distance, "target_front_distance")
    offset = _number(config.get("offsetDistance", 0), "offsetDistance")
    step = _number(config.get("step", 0), "step")
    if step < 0:
        raise ActorLifecycleError("Negative movement step is outside this profile")
    delta = tuple(b-a for a,b in zip(start,target))
    target_distance = math.sqrt(sum(x*x for x in delta))
    if kind == "approach":
        distance = max(0.0, target_distance-offset-front)
        end = tuple(a+(v/target_distance)*distance for a,v in zip(start,delta)) if target_distance > 1e-5 else start
    else:
        end = target
    distance = math.sqrt(sum((b-a)**2 for a,b in zip(start,end)))
    actual_frames = math.floor(distance/step)+1 if step > 0 else frames
    if kind == "approach" and step == 0 and distance < 0.1:
        actual_frames = 1
    if actual_frames > 100000:
        raise ActorLifecycleError("Movement sampling exceeds bounded frame limit")
    # The source checks authored moveFrame for its immediate branch, not the
    # shortened step count. A shortened one-point path therefore retains start.
    if frames <= 1:
        points = [end]
    elif actual_frames == 1:
        points = [start]
    else:
        points = [tuple(a+(b-a)*(i/(actual_frames-1)) for a,b in zip(start,end))
                  for i in range(actual_frames)]
    look = config.get("moveLookAtTarget", True)
    if not isinstance(look, bool):
        raise ActorLifecycleError("moveLookAtTarget must be boolean")
    samples=[]
    for index,position in enumerate(points):
        if look:
            direction = (target[0]-position[0], target[2]-position[2])
            if math.hypot(*direction) > 1e-5:
                yaw = math.atan2(direction[0],direction[1])
        if index == len(points)-1:
            if kind == "return":
                yaw = parent_yaw
            else:
                # MoveToEnd calls LookAt(mEndPos); a zero direction preserves yaw.
                direction=(end[0]-position[0],end[2]-position[2])
                if math.hypot(*direction)>1e-5:
                    yaw=math.atan2(direction[0],direction[1])
        samples.append({"index":index,"position":list(position),"yaw":yaw})
    return {"profile":"nc-single-target-linear-role-v1", "space":"unity-world",
            "kind":kind,"start":list(start),"destination":list(end),
            "target":list(target),"distance":distance,"authored_frames":frames,
            "sample_count":len(samples),"samples":samples,
            "native_dynamic_placement_supported":False}


def load_controller_samples(path, expected_sha256, *, sequence):
    """Read an exact sequence of real Animator poses after provenance checks."""
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:
        raise ActorLifecycleError("Controller sample hash differs from reviewed evidence")
    try:
        data=json.loads(raw)
        matches=[item for item in data["sequences"] if item["label"]==sequence]
        if len(matches)!=1:
            raise ActorLifecycleError("Expected one named controller sequence")
        item=matches[0]
        step=_number(item["step"],"sample step")
        if step<=0 or not item["samples"]:
            raise ActorLifecycleError("Controller sequence has no positive sample interval")
        paths=None
        for frame,sample in enumerate(item["samples"]):
            if sample["frame"]!=frame:
                raise ActorLifecycleError("Controller sample frames are not contiguous")
            current=[pose["path"] for pose in sample["poses"]]
            if len(current)!=len(set(current)) or (paths is not None and current!=paths):
                raise ActorLifecycleError("Controller transform identity/order changed")
            paths=current
            for pose in sample["poses"]:
                _vector(pose["position"],"pose position")
                _vector(pose["scale"],"pose scale")
                rotation=pose["rotation"]
                if len(rotation)!=4 or abs(sum(_number(x,"rotation")**2 for x in rotation)-1)>0.002:
                    raise ActorLifecycleError("Controller sample quaternion is not normalized")
        return {"unity_version":data["unity"],"controller":data["controller"],
                "prefab":data["prefab"],"sequence":item,"sha256":expected_sha256}
    except (KeyError,TypeError,json.JSONDecodeError) as exc:
        raise ActorLifecycleError("Malformed controller sample report") from exc


def animation_from_controller_samples(geometry, samples, bone_paths, *, coordinate_profile):
    """Convert reviewed sampled local poses through the maintained ANIM writer.

``bone_paths`` maps every native bone name to an explicit probe transform path.
Only the established Unity-to-native X reflection is admitted. Parent identity
must match, including identity ancestors above root bones. No role movement is
baked into the skeleton and no native motion slot is chosen here. The returned
AnimFileHundredLine can be serialized by the existing atomic ``to_file`` API.
"""
    from ..anim.InterfaceHundredLine import AnimFileHundredLine
    if coordinate_profile != "unity-reflect-x-v1":
        raise ActorLifecycleError("Unsupported controller coordinate profile")
    sequence=samples["sequence"]
    bones=list(geometry.skeleton.bones)
    if set(bone_paths)!={bone.name for bone in bones}:
        raise ActorLifecycleError("Explicit path mapping must cover every native bone exactly")
    if not 1 <= len(sequence["samples"]) <= 65535:
        raise ActorLifecycleError("Controller sequence exceeds native sample bounds")
    result=AnimFileHundredLine()
    result.playback_rate=1.0/sequence["step"]
    # Probe steps are Unity float32. Preserve standard exact playback rates.
    if abs(result.playback_rate-round(result.playback_rate))<1e-4:
        result.playback_rate=float(round(result.playback_rate))
    result.frame_count=len(sequence["samples"])
    result.bone_count=len(bones)
    result.bone_blend_factors=[255]*len(bones)
    result.float_channel_count=0
    result.float_channel_blend_factors=[]

    def multiply(a,b):
        x,y,z,w=a; X,Y,Z,W=b
        return [w*X+x*W+y*Z-z*Y,w*Y-x*Z+y*W+z*X,w*Z+x*Y-y*X+z*W,w*W-x*X-y*Y-z*Z]

    for bone_index,bone in enumerate(bones):
        path=bone_paths[bone.name]
        result.positions[bone_index]={}
        result.rotations[bone_index]={}
        result.scales[bone_index]={}
        previous=None
        for frame,sample in enumerate(sequence["samples"]):
            poses={p["path"]:p for p in sample["poses"]}
            if path not in poses:
                raise ActorLifecycleError(f"Mapped source bone is absent: {path}")
            parent_path=path.rpartition("/")[0]
            if bone.parent:
                if parent_path!=bone_paths[bone.parent.name]:
                    raise ActorLifecycleError(f"Native/source parent mismatch: {bone.name}")
            else:
                ancestor=parent_path
                while True:
                    pose=poses.get(ancestor)
                    if pose is None:
                        raise ActorLifecycleError("Root ancestor missing from sampled hierarchy")
                    if (max(abs(v) for v in pose["position"])>1e-6 or
                            max(abs(v-1) for v in pose["scale"])>1e-6 or
                            max(abs(v) for v in pose["rotation"][:3])>1e-6):
                        raise ActorLifecycleError("Nonidentity root ancestors require an explicit coordinate profile")
                    if not ancestor:
                        break
                    ancestor=ancestor.rpartition("/")[0]
            pose=poses[path]
            position=[-pose["position"][0],*pose["position"][1:]]
            x,y,z,w=pose["rotation"]
            rotation=[x,-y,-z,w]
            rest=bone.transform
            rest_rotation=list(map(float,rest.quaternion))
            norm=math.sqrt(sum(x*x for x in rest_rotation))
            if norm<1e-8:
                raise ActorLifecycleError("Native rest quaternion is singular")
            rest_rotation=[x/norm for x in rest_rotation]
            delta=multiply([-rest_rotation[0],-rest_rotation[1],-rest_rotation[2],rest_rotation[3]],rotation)
            norm=math.sqrt(sum(x*x for x in delta))
            delta=[x/norm for x in delta]
            if previous is not None and sum(x*y for x,y in zip(previous,delta))<0:
                delta=[-x for x in delta]
            previous=delta
            if any(abs(float(x))<1e-8 for x in list(rest.scale)[:3]):
                raise ActorLifecycleError("Native rest scale is singular")
            result.positions[bone_index][frame]=[x-float(y) for x,y in zip(position,rest.position)]
            result.rotations[bone_index][frame]=delta
            result.scales[bone_index][frame]=[x/float(y) for x,y in zip(pose["scale"],rest.scale)]
    return result
