"""Bounded native rest-scale adapter for unit-bind, uniform-scale leaf bones.

GEOM rest TRS and inverse bind matrices are separate authored data. A constant
NLA scale layer preserves that distinction without multiplying it into each
native animation delta. No handlers, drivers or native runtime changes.
"""
import json
import math
import struct

import bpy
from mathutils import Matrix, Quaternion, Vector

from .blender_compat import create_action_fcurve, create_nla_track

MARKER = 'DSTS_native_rest_scale'
TRACK_NAME = '[DSTS] Native rest scale (keep enabled)'


def is_rest_action(action):
    return bool(action and action.get(MARKER, False))


def is_rest_track(track):
    return any(is_rest_action(strip.action) for strip in track.strips)


def apply_profile(geom, owner, names=None):
    """Apply only independently recognizable positive uniform leaf scaling."""
    if MARKER in owner:
        return json.loads(owner[MARKER])
    snapshot = bytes(getattr(geom, 'bind_pose_snapshot', b''))
    if not snapshot:
        return None
    magic, version, count = struct.unpack_from('<III', snapshot)
    if magic != 0x53504244 or version != 1 or len(snapshot) != 12 + count * 104:
        raise ValueError('Unsupported native bind snapshot')
    bones = list(geom.skeleton.bones)
    if count != len(bones):
        raise ValueError('Native bind snapshot count differs from skeleton')
    names = names or {}
    worlds = {}
    def world(bone):
        if bone.name not in worlds:
            t = bone.transform
            q = t.quaternion
            local = Matrix.LocRotScale(Vector(t.position[:3]), Quaternion((q[3], *q[:3])), Vector(t.scale[:3]))
            worlds[bone.name] = world(bone.parent) @ local if bone.parent else local
        return worlds[bone.name]
    nonleaves = {bone.parent.name for bone in bones if bone.parent}
    scales, unhandled, handled = {}, [], []
    for index, bone in enumerate(bones):
        if bone.is_geometry:
            continue
        values = struct.unpack_from('<12f', snapshot, 12 + index * 104 + 56)
        inverse = Matrix((values[:4], values[4:8], values[8:12], (0, 0, 0, 1)))
        try:
            bind = inverse.inverted()
        except ValueError:
            unhandled.append(bone.name)
            continue
        rest = world(bone)
        if max(abs(bind[r][c] - rest[r][c]) for r in range(4) for c in range(4)) <= 1e-4:
            continue
        scale = rest.to_scale()
        factor = sum(scale) / 3
        normalized = rest.copy()
        if factor > 0:
            for r in range(3):
                for c in range(3):
                    normalized[r][c] /= factor
        # Leaf-only avoids changing descendant translations/inheritance. This
        # does not classify arbitrary bind/rest mismatches as supported.
        supported = (bone.name not in nonleaves and math.isfinite(factor) and factor > 0 and
                     max(abs(value - factor) for value in scale) < 1e-5 and
                     max(abs(value - 1) for value in bind.to_scale()) < 1e-4 and
                     max(abs(bind[r][c] - normalized[r][c]) for r in range(4) for c in range(4)) < 1e-4)
        if supported:
            scales[names.get(bone.name, bone.name)] = factor
            handled.append(bone.name)
        else:
            unhandled.append(bone.name)
    if not scales:
        return None
    profile = {'profile': 'unit-bind-uniform-leaf-rest-scale-v1', 'scales': scales,
               'source_bones': handled,
               'source_geometry': [bool(bone.is_geometry) for bone in bones],
               'unhandled_mismatches': unhandled,
               'note': 'Keep the Native rest scale track enabled. Use track mute for clip selection; if using Solo, also solo this rest track.'}
    owner[MARKER] = json.dumps(profile)
    owner['DSTS_native_rest_scale_note'] = profile['note']
    action = bpy.data.actions.new('Native authored rest scale')
    action[MARKER] = True
    for pose in owner.pose.bones:
        factor = scales.get(pose.name, 1)
        pose.rotation_mode = 'QUATERNION'
        pose.location = (0, 0, 0)
        pose.rotation_quaternion = (1, 0, 0, 0)
        pose.scale = (factor,) * 3
        # Blender retains previously evaluated channels when every clip that
        # wrote them is muted. An explicit neutral base also resets those
        # channels, so returning from a native clip restores the authored pose.
        for channel, values in (('location', (0, 0, 0)), ('rotation_quaternion', (1, 0, 0, 0)),
                                ('scale', (factor, factor, factor))):
            for axis, value in enumerate(values):
                curve = create_action_fcurve(action, owner, pose.path_from_id() + '.' + channel,
                                              index=axis, group_name=pose.name)
                curve.keyframe_points.add(1)
                curve.keyframe_points[0].co = (1, value)
                curve.keyframe_points[0].interpolation = 'CONSTANT'
    action.use_frame_range = True
    action.frame_start, action.frame_end = 1, 2
    track, strip = create_nla_track(owner, action, TRACK_NAME, 1, blend_type='COMBINE')
    track.mute, track.lock = False, True
    strip.extrapolation = 'HOLD'
    owner.animation_data.action_blend_type = 'COMBINE'
    bpy.context.view_layer.update()
    return profile


def partition_diagnostics(messages, profile):
    corrected = {'Inverse bind matrix differs from computed pose: ' + name
                 for name in (profile or {}).get('source_bones', [])}
    return ([message for message in messages if message in corrected],
            [message for message in messages if message not in corrected])


def expected_basis(owner, bone):
    """Only an unchanged native rest layout may exempt the authored base pose."""
    profile = json.loads(owner.get(MARKER, '{}'))
    factor = profile.get('scales', {}).get(bone.name)
    if factor is None:
        return Matrix.Identity(4)
    # Backend snapshot reuse is whole-skeleton, not per bone. An unrelated
    # bind edit invalidates it too; never permit the authored base pose while
    # export would regenerate its independent inverse bind matrices.
    geometry = profile.get('source_geometry', [])
    if len(owner.data.bones) != len(geometry):
        return Matrix.Identity(4)
    for candidate in owner.data.bones:
        original = json.loads(candidate.get('DSTS_source_rest', '{}'))
        index = candidate.get('DSTS_source_bone_index', -1)
        parent = candidate.parent.get('DSTS_source_bone_index', -2) if candidate.parent else -1
        if (not 0 <= index < len(geometry) or bool(candidate.get('DSTS_geometry', False)) != geometry[index] or
                [v for row in candidate.matrix_local for v in row] != original.get('matrix') or
                parent != original.get('parent')):
            return Matrix.Identity(4)
    source = json.loads(bone.bone.get('DSTS_source_rest', '{}'))
    current = [v for row in bone.bone.matrix_local for v in row]
    if current != source.get('matrix'):
        return Matrix.Identity(4)
    parent_index = bone.bone.parent.get('DSTS_source_bone_index', -2) if bone.bone.parent else -1
    if parent_index != source.get('parent'):
        return Matrix.Identity(4)
    if bone.bone.parent:
        parent = bone.bone.parent
        parent_source = json.loads(parent.get('DSTS_source_rest', '{}'))
        if [v for row in parent.matrix_local for v in row] != parent_source.get('matrix'):
            return Matrix.Identity(4)
    return Matrix.Diagonal((factor, factor, factor, 1))


def validate_preview_layer(owner):
    if MARKER not in owner:
        return
    ad = owner.animation_data
    tracks = [track for track in ad.nla_tracks if is_rest_track(track)] if ad else []
    if len(tracks) != 1 or tracks[0].mute:
        raise ValueError('Keep the Native rest scale track enabled for this imported rig')
    if any(track.is_solo for track in ad.nla_tracks) and not tracks[0].is_solo:
        raise ValueError('Also solo the Native rest scale track, or select clips with mute instead of Solo')
