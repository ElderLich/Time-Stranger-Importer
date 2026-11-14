"""Bounded native Model4 profile for captured, locally simulated mesh particles.

The external owner matrix is refreshed by the direct Model4 graph. World-axis
motion is carried independently in material uniform 178 and applied after that
matrix. This module does not establish external socket, event or cleanup routes.
It consumes captured samples; it does not invent a Unity particle schedule.
"""
from __future__ import annotations

import copy
import math


PROFILE = "local-mesh-current-owner-world-offset-v1"
WORLD_OFFSET_UNIFORM = 178

# The original effect donor's output register order, UV convention and projected
# coordinate equation are retained. The only position addition is the separate
# world vector, after the native three-row palette multiplication.
VERTEX_HLSL = """
cbuffer Constants : register(b0) {
  column_major float4x4 ViewProj;
  float2 OffsetSet1;
  float2 OffsetSet2;
  float3 SpecularColorRgb;
  float3 EmissiveColor;
};
cbuffer BoneConstants : register(b1) { float4 MatrixPalette[750]; };
struct VertexOutput {
  float4 color:COLOR0;
  float2 uv:TEXCOORD0;
  float4 uv2:TEXCOORD1;
  float4 projected:TEXCOORD2;
  float4 position:SV_Position;
};
VertexOutput main(float3 position:POSITION0,float2 uv:TEXCOORD0,
                  float2 uv2:TEXCOORD1,float4 color:COLOR0) {
  VertexOutput o;
  float4 localPosition=float4(position,1);
  float3 world=float3(dot(MatrixPalette[0],localPosition),
                      dot(MatrixPalette[1],localPosition),
                      dot(MatrixPalette[2],localPosition))+SpecularColorRgb;
  float4 clip=mul(ViewProj,float4(world,1));
  o.color=color;
  uint frame=(uint)max(round(EmissiveColor.z),0.0)%(NC_COLUMNS*NC_ROWS);
  float2 sourceUV=(uv+OffsetSet1+float2(frame%NC_COLUMNS,NC_ROWS-1-frame/NC_COLUMNS))/float2(NC_COLUMNS,NC_ROWS);
  o.uv=float2(sourceUV.x,1-sourceUV.y);
  o.uv2=float4(uv2.x+OffsetSet2.x,1-uv2.y-OffsetSet2.y,0,0);
  o.projected=float4((clip.x+clip.w)*.5,(clip.w-clip.y)*.5,clip.z,clip.w);
  o.position=clip;
  return o;
}
"""


def _vector(value, length, field):
    if (not isinstance(value, (list, tuple)) or len(value) != length
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError(f"{field} requires {length} finite numbers")
    return value


def validate_local_mesh(node):
    """Reject every owner/render geometry contract outside this finite profile."""
    ps = node.get("ps", {})
    renderer = node.get("renderer", {})
    if ps.get("renderMode") != "Mesh" or renderer.get("alignment") != 2:
        raise ValueError("Only local-aligned source meshes are supported")
    space = ps.get("dynamics", {}).get("simulationSpace")
    if type(space) is not int or space != 0:
        raise ValueError("World-birth meshes require separate current-owner orientation and frozen-owner position")
    _vector(renderer.get("pivot", [0, 0, 0]), 3, "mesh pivot")
    if any(renderer.get("flip", [])) or ps.get("dynamics", {}).get("trail"):
        raise ValueError("Mesh flip and trails require separate geometry profiles")
    if node.get("actionPose") is not None or node.get("actionRenderer"):
        raise ValueError("Animated renderer overrides are not mapped")
    materials = node.get("material_infos", [])
    if len(materials) != 1:
        raise ValueError("Expected exactly one source mesh material")
    material = materials[0]
    if material.get("floats", {}).get("_FresnelToggle"):
        raise ValueError("Fresnel requires the separate normal/view vertex profile")
    if material.get("shader") not in (
        "Honor/FX/CommonShader", "Legacy Shaders/Particles/Additive",
        "Legacy Shaders/Particles/Alpha Blended",
    ):
        raise ValueError("Mesh shader family is outside the verified vertex profile")
    for field in ("tilesX", "tilesY"):
        value = ps.get(field, 1)
        if type(value) is not int or not 1 <= value <= 256:
            raise ValueError("Mesh texture sheet dimensions must be integers from1to256")
    return PROFILE


def vertex_source(node):
    """Specialize the admitted source atlas dimensions without changing timing."""
    validate_local_mesh(node)
    ps = node["ps"]
    return (VERTEX_HLSL.replace("NC_COLUMNS", str(ps.get("tilesX", 1)))
            .replace("NC_ROWS", str(ps.get("tilesY", 1))))


def prepare_local_mesh_samples(node, particle):
    """Return a deep copy with owner-relative positions and separate world offsets.

    Sizes and quaternions already include the source prefab basis. Their native
    ZYX Euler samples are retained, not replaced with billboard dimensions.
    Every sample must be an integer 30 Hz frame in a contiguous interval.
    """
    validate_local_mesh(node)
    if particle.get("renderMode") != "Mesh" or particle.get("alignment") != 2:
        raise ValueError("Particle geometry differs from its source profile")
    samples = particle.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("Mesh particle has no source samples")
    result = copy.deepcopy(samples)
    previous = None
    for sample in result:
        frame = sample.get("frame")
        if (type(frame) not in (int, float) or not math.isfinite(frame)
                or int(frame) != frame or not 0 <= frame < 32766
                or previous is not None and frame != previous + 1):
            raise ValueError("Mesh source samples need contiguous native-range integer frames")
        previous = frame
        if sample.get("viewAligned") is not False:
            raise ValueError("View-aligned mesh requires a live-camera profile")
        position = _vector(sample.get("position"), 3, "mesh position")
        world = _vector(sample.get("worldOffset"), 3, "world-axis offset")
        sizes = _vector(sample.get("size"), 3, "mesh scale")
        if any(v < 0 for v in sizes):
            raise ValueError("Native primary scale clamps negative values")
        _vector(sample.get("rotationDegrees"), 3, "native ZYX rotation")
        quaternion = _vector(sample.get("rotation"), 4, "source rotation")
        if abs(sum(v * v for v in quaternion) - 1) > 1e-4:
            raise ValueError("Mesh source quaternion is not normalized")
        _vector(sample.get("color"), 4, "mesh color")
        _vector(sample.get("customData"), 4, "Custom1")
        _vector(sample.get("customData2"), 4, "Custom2")
        flipbook = sample.get("flipbook")
        count = node["ps"].get("tilesX", 1) * node["ps"].get("tilesY", 1)
        if type(flipbook) is not int or not 0 <= flipbook < count:
            raise ValueError("Mesh sheet sample is outside its captured atlas")
        sample["position"] = [p - w for p, w in zip(position, world)]
    return result
