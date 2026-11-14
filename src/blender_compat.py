"""Feature-detected Blender action APIs (legacy and Blender 5.x slots)."""

from __future__ import annotations


def ensure_animation_data(obj):
    return obj.animation_data or obj.animation_data_create()


def scene_fps(scene):
    return float(scene.render.fps) / float(scene.render.fps_base or 1.0)


def action_slot_for(action, datablock=None, slot=None):
    """Resolve one owner's slot; never combine unrelated objects' channels."""
    slots = list(getattr(action, "slots", ()))
    if not slots:
        return None
    if slot is not None and slot in slots:
        return slot
    ad = getattr(datablock, "animation_data", None)
    if ad and ad.action == action and getattr(ad, "action_slot", None) in slots:
        return ad.action_slot
    if datablock is not None:
        identifier = "OB" + datablock.name if datablock.id_type == "OBJECT" else None
        matches = [s for s in slots if s.identifier == identifier]
        if len(matches) == 1:
            return matches[0]
        matches = [s for s in slots if s.target_id_type == datablock.id_type]
        if len(matches) == 1:
            return matches[0]
    if len(slots) == 1:
        return slots[0]
    raise ValueError(f"Action '{action.name}' has multiple slots; select its owner's NLA strip")


def create_action_fcurve(action, datablock, data_path, index=0, group_name=""):
    ad = ensure_animation_data(datablock)
    if hasattr(action, "fcurve_ensure_for_datablock"):
        if ad.action != action:
            ad.action = action
        return action.fcurve_ensure_for_datablock(
            datablock, data_path, index=index, group_name=group_name or "",
        )
    fc = action.fcurves.find(data_path, index=index)
    if fc is None:
        fc = action.fcurves.new(data_path=data_path, index=index,
                               action_group=group_name or "")
    return fc


def iter_action_fcurves(action, datablock=None, slot=None):
    if action is None:
        return
    if getattr(action, "layers", None):
        selected = action_slot_for(action, datablock, slot) if datablock is not None or slot is not None else None
        for layer in action.layers:
            for strip in layer.strips:
                for bag in getattr(strip, "channelbags", ()):
                    if selected is None or bag.slot_handle == selected.handle:
                        yield from bag.fcurves
    elif hasattr(action, "fcurves"):
        yield from action.fcurves


def create_nla_track(armature_obj, action, track_name, start_frame, scale=1.0, blend_type="COMBINE"):
    ad = ensure_animation_data(armature_obj)
    slot = action_slot_for(action, armature_obj)
    track = ad.nla_tracks.new()
    track.name = track_name
    track.mute = True
    strip = track.strips.new(action.name, int(start_frame), action)
    if slot is not None:
        strip.action_slot = slot
    strip.scale = scale
    strip.blend_type = blend_type
    if ad.action == action:
        ad.action = None
    return track, strip
