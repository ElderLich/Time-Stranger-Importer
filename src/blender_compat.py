"""Compatibility helpers for Blender 4.x legacy actions and Blender 5.1 layers."""

from __future__ import annotations


def ensure_animation_data(obj):
    if not obj.animation_data:
        obj.animation_data_create()
    return obj.animation_data


def create_action_fcurve(action, datablock, data_path, index=0, group_name=""):
    """Return an F-Curve for ``action`` across Blender 4.x and 5.x APIs."""
    ensure_animation_data(datablock)

    if hasattr(action, "fcurve_ensure_for_datablock"):
        if datablock.animation_data.action != action:
            datablock.animation_data.action = action
        return action.fcurve_ensure_for_datablock(
            datablock,
            data_path,
            index=index,
            group_name=group_name or "",
        )

    fcurve = action.fcurves.new(data_path=data_path, index=index)
    if group_name and hasattr(action, "groups") and hasattr(fcurve, "group"):
        group = action.groups.get(group_name) or action.groups.new(group_name)
        fcurve.group = group
    return fcurve


def iter_action_fcurves(action):
    """Yield all F-Curves from legacy or layered Blender actions."""
    if hasattr(action, "fcurves"):
        yield from action.fcurves
        return

    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for channelbag in getattr(strip, "channelbags", ()):
                yield from getattr(channelbag, "fcurves", ())


def create_nla_track(armature_obj, action, track_name, start_frame, scale=1.0, blend_type="COMBINE"):
    """Create an NLA strip and wire the Blender 5.x action slot when present."""
    anim_data = ensure_animation_data(armature_obj)
    slot = getattr(anim_data, "action_slot", None)

    track = anim_data.nla_tracks.new()
    track.name = track_name
    track.mute = True

    strip = track.strips.new(action.name, start_frame, action)
    strip.scale = scale
    strip.blend_type = blend_type
    if slot is not None and hasattr(strip, "action_slot"):
        try:
            strip.action_slot = slot
        except RuntimeError:
            pass

    if anim_data.action == action:
        anim_data.action = None
    return track, strip
