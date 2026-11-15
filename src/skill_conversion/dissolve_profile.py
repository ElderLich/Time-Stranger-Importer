"""Finite captured singular-dissolve equations and native color-mask layers.

The source expression is kept verbatim by the shader translator, including its
zero denominators. Desktop GLES and D3D11 probe agreement bounds admission;
this profile does not claim Android GPU or in-game visual acceptance.
"""
from __future__ import annotations

import math

PROFILE = "honor-singular-dissolve-layered-v1"
FAMILY = "Honor/FX/Honor_Dissolve_Noise_Mask##"
LAYERS = (
    {"name": "rgb", "blend": [770, 771], "write_mask": [1, 1, 1, 0]},
    {"name": "alpha", "blend": [1, 771], "write_mask": [0, 0, 0, 1]},
)


def validate_material(node):
    """Admit the captured singular domain and exact channel-independent blend."""
    materials = node.get("material_infos", [])
    if len(materials) != 1 or materials[0].get("shader") != FAMILY:
        raise ValueError("Singular dissolve profile requires one Honor dissolve material")
    material = materials[0]
    floats = material.get("floats", {})
    for name, expected in (("_Spread", 0), ("_Softness", .5), ("_DissRimSize", 1)):
        if floats.get(name) != expected:
            raise ValueError("Singular dissolve profile requires captured Spread=0, Softness=.5, RimSize=1")
    if floats.get("_RoleClip") or floats.get("_GroundFadeToggle") or material.get("keywords"):
        raise ValueError("Singular dissolve renderer visibility/fade variants are not mapped")
    states = material.get("passStates", [])
    expected = {"srcBlend": 5, "dstBlend": 10, "srcBlendAlpha": 1,
                "dstBlendAlpha": 10, "blendOp": 0, "blendOpAlpha": 0,
                "colorMask": 15, "cull": 2, "zTest": 4, "zWrite": 0}
    if len(states) != 1 or any(states[0].get(key) != value for key, value in expected.items()):
        raise ValueError("Singular dissolve requires captured separate-alpha, CullBack, no-depth-write state")
    if not all(type(floats.get(key, default)) in (int, float) and math.isfinite(floats.get(key, default))
               for key, default in (("_DissolveValue", 0), ("_MaskInt", 1), ("_WarpInt", .5), ("_NoiseInt", .5))):
        raise ValueError("Singular dissolve constants must be finite")
    return PROFILE


def blend_layers(source, destination):
    """Reference only: no premultiply/clamp rewrite; two disjoint write masks."""
    if len(source) != 4 or len(destination) != 4 or not all(
            type(x) in (int, float) and math.isfinite(x) for x in (*source, *destination)):
        raise ValueError("Blend colors require four finite channels")
    alpha = source[3]
    return [source[i] * alpha + destination[i] * (1 - alpha) for i in range(3)] + [
        alpha + destination[3] * (1 - alpha)]
