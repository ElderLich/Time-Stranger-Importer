"""Loss-aware Time Stranger animation interchange, independent of operators."""

import json
import math

import bpy
from mathutils import Euler, Quaternion

from .anim.InterfaceHundredLine import AnimFileHundredLine
from .blender_compat import create_action_fcurve, iter_action_fcurves, scene_fps


def _write_curve(action, owner, path, index, values, group):
    curve = create_action_fcurve(action, owner, path, index=index, group_name=group)
    # At a sequence cut the following phase owns the shared endpoint.
    values = sorted(dict(values).items())
    curve.keyframe_points.add(len(values))
    curve.keyframe_points.foreach_set("co", [v for pair in values for v in pair])
    for key in curve.keyframe_points:
        key.interpolation = 'LINEAR'
    curve.update()


def build_action(owner, name, source, bone_names=None):
    before = set(bpy.data.actions)
    ad = owner.animation_data or owner.animation_data_create()
    previous_action, previous_slot = ad.action, getattr(ad, 'action_slot', None)
    previous_properties = set(owner.keys())
    previous_floats = {key: owner[key] for key in owner.keys() if key.startswith('DSTS_float_')}
    modes = {bone.name: bone.rotation_mode for bone in owner.pose.bones}
    try:
        return _build_action(owner, name, source, bone_names)
    except Exception:
        ad.action = previous_action
        if previous_slot is not None:
            ad.action_slot = previous_slot
        for action in list(bpy.data.actions):
            if action not in before and action.users == 0:
                bpy.data.actions.remove(action)
        for key in set(owner.keys()) - previous_properties:
            if key.startswith('DSTS_float_'):
                del owner[key]
        for key, value in previous_floats.items():
            owner[key] = value
        for bone in owner.pose.bones:
            bone.rotation_mode = modes[bone.name]
        raise


def _build_action(owner, name, source, bone_names=None):
    bone_names = bone_names or [b.name for b in owner.data.bones]
    if source.bone_count > len(bone_names):
        raise ValueError(f"Animation has {source.bone_count} bones, but rig has {len(bone_names)}")
    action = bpy.data.actions.new(name)
    action["DSTS_animation_metadata"] = json.dumps({
        "playback_rate": source.playback_rate,
        "bone_names": bone_names,
        "bone_masks": list(source.bone_blend_factors),
        "float_masks": list(source.float_channel_blend_factors),
        "float_count": source.float_channel_count,
        "unknown_section": list(source.unknown_section),
    })
    for index, name in enumerate(bone_names[:source.bone_count]):
        bone = owner.pose.bones[name]
        bone.rotation_mode = 'QUATERNION'
        prefix = bone.path_from_id()
        for channel, data, count in (("location", source.positions, 3),
                                      ("scale", source.scales, 3)):
            values = data.get(index, {})
            if values:
                for component in range(count):
                    _write_curve(action, owner, prefix + "." + channel, component,
                                 [(f + 1, v[component]) for f, v in values.items()], name)
        values = source.rotations.get(index, {})
        if values:
            # Bake source spherical interpolation, avoiding Bezier overshoot and
            # antipodal quaternion flips in Blender's component F-curves.
            keys = sorted(values)
            samples = []
            previous = None
            segment = 0
            for frame in range(keys[0], keys[-1] + 1):
                while segment + 1 < len(keys) and frame > keys[segment + 1]:
                    segment += 1
                a = keys[segment]
                b = keys[min(segment + 1, len(keys) - 1)]
                qa = Quaternion((values[a][3], *values[a][:3])).normalized()
                qb = Quaternion((values[b][3], *values[b][:3])).normalized()
                if qa.dot(qb) < 0:
                    qb.negate()
                q = qa.slerp(qb, (frame - a) / (b - a)) if b != a else qa
                if previous is not None and previous.dot(q) < 0:
                    q.negate()
                samples.append((frame + 1, tuple(q)))
                previous = q
            for component in range(4):
                _write_curve(action, owner, prefix + ".rotation_quaternion", component,
                             [(f, q[component]) for f, q in samples], name)
    for index, values in source.float_channels.items():
        if not values:
            continue
        prop = f"DSTS_float_{index}"
        owner[prop] = float(next(iter(values.values())))
        _write_curve(action, owner, '["' + prop + '"]', 0,
                     [(f + 1, value) for f, value in values.items()], "DSTS Float Channels")
    # Static-only clips still have a duration in the native header.
    action.use_frame_range = True
    action.frame_start = 1
    keys_end = max((k.co.x for f in iter_action_fcurves(action, owner)
                    for k in f.keyframe_points), default=1)
    action.frame_end = max(keys_end, getattr(source, "frame_count", 1))
    return action


def source_rate_from_strip(strip, scene):
    if strip.scale <= 0:
        raise ValueError("Animation strip scale must be positive")
    return scene_fps(scene) / strip.scale


def build_from_action(owner, action, playback_rate=30.0, slot=None, frame_range=None):
    """Sample complete vectors, including unkeyed components and Euler curves."""
    if not math.isfinite(playback_rate) or playback_rate <= 0:
        raise ValueError("Animation playback rate must be finite and positive")
    constraints = list(owner.constraints) + [c for bone in owner.pose.bones for c in bone.constraints]
    if any(not c.mute and getattr(c, 'influence', 1) != 0 for c in constraints):
        raise ValueError('Bake active object/bone constraints to visual keyframes before animation export')
    if owner.animation_data and owner.animation_data.drivers:
        raise ValueError('Bake animation drivers to keyframes before export')
    curves = list(iter_action_fcurves(action, owner, slot))
    by_key = {(f.data_path, f.array_index): f for f in curves}
    metadata = json.loads(action.get("DSTS_animation_metadata", "{}"))
    result = AnimFileHundredLine()
    bones = list(owner.data.bones)
    result.playback_rate = playback_rate
    result.bone_count = len(bones)
    original_names = metadata.get("bone_names", [])
    original_masks = metadata.get("bone_masks", [])
    masks = dict(zip(original_names, original_masks))
    result.bone_blend_factors = [masks.get(b.name, 255) for b in bones]
    result.float_channel_count = metadata.get("float_count", 0)
    if "unknown_section" in metadata:
        result.unknown_section = metadata["unknown_section"]
    start, end = (float(v) for v in (frame_range or action.frame_range))
    if not all(math.isfinite(v) for v in (start, end)) or end < start:
        raise ValueError('Invalid animation frame range')
    count = max(1, int(math.ceil(end - start)) + 1)
    result.frame_count = count
    frames = [(i, min(start + i, end)) for i in range(count)]

    def vector(prefix, channel, defaults, frame):
        return [float(by_key[(prefix + channel, i)].evaluate(frame))
                if (prefix + channel, i) in by_key else defaults[i]
                for i in range(len(defaults))]

    def has(prefix, channel):
        return any(path == prefix + channel for path, _ in by_key)

    for index, bone in enumerate(bones):
        prefix = owner.pose.bones[bone.name].path_from_id() + "."
        result.positions[index] = {}
        result.rotations[index] = {}
        result.scales[index] = {}
        for channel, target, defaults in (("location", result.positions, [0, 0, 0]),
                                           ("scale", result.scales, [1, 1, 1])):
            if has(prefix, channel):
                target[index] = {i: vector(prefix, channel, defaults, f) for i, f in frames}
        previous = None
        rotation = next((c for c in ("rotation_quaternion", "rotation_euler", "rotation_axis_angle")
                         if has(prefix, c)), None)
        if rotation:
            for i, frame in frames:
                if rotation == "rotation_quaternion":
                    q = Quaternion(vector(prefix, rotation, [1, 0, 0, 0], frame))
                elif rotation == "rotation_euler":
                    mode = owner.pose.bones[bone.name].rotation_mode
                    mode = mode if len(mode) == 3 else 'XYZ'
                    q = Euler(vector(prefix, rotation, [0, 0, 0], frame), mode).to_quaternion()
                else:
                    angle, *axis = vector(prefix, rotation, [0, 0, 1, 0], frame)
                    q = Quaternion(axis, angle)
                q.normalize()
                if previous is not None and previous.dot(q) < 0:
                    q.negate()
                result.rotations[index][i] = [q.x, q.y, q.z, q.w]
                previous = q
    for fc in curves:
        prefix = '["DSTS_float_'
        if fc.data_path.startswith(prefix) and fc.data_path.endswith('"]'):
            index = int(fc.data_path[len(prefix):-2])
            result.float_channel_count = max(result.float_channel_count, index + 1)
            result.float_channels[index] = {i: float(fc.evaluate(f)) for i, f in frames}
    original = metadata.get("float_masks", [])
    result.float_channel_blend_factors = [original[i] if i < len(original) else 255
                                         for i in range(result.float_channel_count)]
    return result


def validate_strip(strip):
    if strip.repeat != 1 or strip.use_reverse or strip.use_animated_time:
        raise ValueError('Bake repeated, reversed or time-remapped NLA strips to an action before export')
    if len(strip.fcurves) or len(strip.modifiers):
        raise ValueError('Bake NLA strip influence/time modifiers before export')
    if not strip.action:
        raise ValueError('NLA strip has no action')
    return strip.action


def single_strip(track):
    if len(track.strips) != 1:
        raise ValueError(f"Track '{track.name}' has {len(track.strips)} strips; bake to one strip before export")
    strip = track.strips[0]
    validate_strip(strip)
    return strip


def animation_path(directory, name):
    from pathlib import Path
    if not name or name in {'.', '..'} or any(c in name for c in '/\\:*?"<>|'):
        raise ValueError(f'Animation track name cannot be used as a filename: {name!r}')
    return str(Path(directory) / (name + '.anim'))
