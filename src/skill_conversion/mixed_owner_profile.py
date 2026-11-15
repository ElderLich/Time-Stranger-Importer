"""Finite world-birth/local-surface conversion for stationary upright roots.

This is not a general split-transform carrier. With fixed translation/scale,
arbitrary yaw leaves a vertical particle center unchanged while its surface
uses the current owner. Native current-owner Model4 therefore represents this
bounded asset profile without another runtime or a second matrix provider.
"""
from __future__ import annotations

import math

PROFILE = "stationary-root-yaw-v1"
ASSUMPTIONS = {
    "native_binding": "@root",
    "translation": "fixed from particle birth through particle death",
    "scale": "fixed positive uniform root scale",
    "rotation": "upright root; yaw may vary independently every frame",
    "source_center": "constant point on the root Y axis",
    "native_carrier": "ordinary current-owner Model4; no split matrix claim",
    "in_game_verified": False,
    "remaining_visual_acceptance": "Verify native root translation/scale stay fixed during these effects; moving-root source parity is not implemented by this profile.",
}


def _vector(value, count, label):
    if (not isinstance(value, (list, tuple)) or len(value) != count
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError(f"{label} requires {count} finite values")
    return value


def validate_node(node):
    ps, renderer = node.get("ps", {}), node.get("renderer", {})
    dynamics = ps.get("dynamics", {})
    if (ps.get("renderMode") not in ("Billboard", "Mesh")
            or renderer.get("alignment") != 2
            or dynamics.get("simulationSpace") != 1):
        raise ValueError("Stationary-root profile requires world-birth local-aligned surfaces")
    if node.get("actionPose") is not None or node.get("actionRenderer"):
        raise ValueError("Stationary-root profile does not admit animated renderer transforms")
    if ps.get("rotation3D") or any(renderer.get("pivot", [])) or any(renderer.get("flip", [])):
        raise ValueError("Stationary-root profile requires centered unflipped surfaces without 3D start rotation")
    for key in ("startSpeed", "gravityModifier"):
        curve = dynamics.get(key, {})
        if curve.get("minMaxState") != 0 or curve.get("scalar") != 0:
            raise ValueError("Stationary-root particles require constant zero speed and gravity")
    if any(dynamics.get(key) for key in ("velocity", "force", "noise", "trail", "orbital", "limitVelocity", "inheritVelocity")):
        raise ValueError("Additional particle motion is outside stationary-root profile")
    position = _vector(node.get("prefabPos"), 3, "prefab position")
    if abs(position[0]) > 1e-9 or abs(position[2]) > 1e-9:
        raise ValueError("Stationary-root center must lie on the yaw axis")
    return PROFILE


def validate_samples(node, particle):
    validate_node(node)
    if particle.get("renderMode") != node["ps"]["renderMode"] or particle.get("alignment") != 2:
        raise ValueError("Particle geometry differs from stationary-root source")
    samples = particle.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("Stationary-root particle requires captured samples")
    expected = node["prefabPos"]
    previous = None
    for sample in samples:
        frame = sample.get("frame")
        if (type(frame) not in (int, float) or not math.isfinite(frame) or int(frame) != frame
                or frame < 0 or frame >= 32766 or previous is not None and frame != previous + 1):
            raise ValueError("Stationary-root samples require contiguous native-range integer frames")
        previous = frame
        for field in ("position", "renderOrigin"):
            if field == "renderOrigin" and particle["renderMode"] == "Mesh":
                continue
            value = _vector(sample.get(field), 3, field)
            if any(abs(a-b) > 1e-7 for a, b in zip(value, expected)):
                raise ValueError("Captured particle center is not stationary on the root yaw axis")
        for field in ("worldOffset", "localVelocity", "worldVelocity"):
            if any(abs(v) > 1e-9 for v in _vector(sample.get(field), 3, field)):
                raise ValueError("Stationary-root particle has nonzero captured motion")
    return {"profile": PROFILE, "assumptions": dict(ASSUMPTIONS),
            "source_simulation_space": 1, "native_owner": "current",
            "center": list(expected), "first_frame": samples[0]["frame"],
            "last_frame": samples[-1]["frame"], "samples": len(samples)}


def validate_owner_samples(samples):
    """Validate an optional sampled root track; never infer one from the asset."""
    if not isinstance(samples, list) or not samples:
        raise ValueError("A nonempty root track is required")
    reference = None
    for sample in samples:
        position = _vector(sample.get("position"), 3, "root position")
        scale = _vector(sample.get("scale"), 3, "root scale")
        rotation = _vector(sample.get("rotation"), 4, "root quaternion")
        if min(scale) <= 0 or max(scale)-min(scale) > 1e-7:
            raise ValueError("Root scale must be positive and uniform")
        if abs(rotation[0])+abs(rotation[2]) > 1e-7 or abs(sum(v*v for v in rotation)-1) > 1e-6:
            raise ValueError("Root rotation must be a normalized upright yaw quaternion")
        if reference is not None and any(abs(a-b) > 1e-7 for a,b in zip(list(position)+list(scale), reference)):
            raise ValueError("Root translation and scale must remain stationary")
        reference = list(position)+list(scale)
    return PROFILE
