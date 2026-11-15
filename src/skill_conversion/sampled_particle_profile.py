"""Convert finite Unity particle samples into the existing native bake contract.

This bounded profile consumes actual source-engine particle getters and checks
them against independently baked quads. It never approximates or edits an
emission curve. World-simulation, stationary, camera-facing quads are supported;
source-engine identity remains provenance, not a claim of engine-version parity.
"""
from __future__ import annotations
import math

PROFILE = "sampled-world-view-billboard-v1"


def _number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _vector(value, names="xyz"):
    if not isinstance(value, dict):
        raise ValueError("Sample vector must be a named object")
    return [_number(value.get(key), f"vector.{key}") for key in names]


def _color(value):
    result = _vector(value, "rgba")
    if any(v != int(v) or not 0 <= v <= 255 for v in result):
        raise ValueError("Captured color must contain four bytes")
    return [v / 255 for v in result]


def convert_samples(node, capture, node_index):
    """Return native particle samples plus a complete source-geometry audit.

    The caller pins the capture, source node and material resource inputs. All
    30Hz frames, including empty frames, must be supplied so births, gaps and
    deaths cannot disappear when serializing the native finite resource.
    """
    if capture.get("format") != "nc-unity-particle-samples-v1":
        raise ValueError("Unsupported captured particle sample format")
    if type(node_index) is not int or node_index < 0:
        raise ValueError("Source node index must be a nonnegative integer")
    ps, renderer = node["ps"], node["renderer"]
    if (capture.get("fps") != 30 or capture.get("simulationSpace") != 1
            or capture.get("alignment") != 0 or capture.get("renderMode") != 0
            or capture.get("scalingMode") != 0 or ps.get("renderMode") != "Billboard"
            or renderer.get("alignment") != 0 or ps.get("dynamics", {}).get("simulationSpace") != 1
            or ps.get("scalingMode") != 0):
        raise ValueError("Profile requires 30Hz world-simulation view billboards")
    if (any(renderer.get("pivot", [0, 0, 0])) or any(renderer.get("flip", []))
            or any(_vector(capture["pivot"])) or ps.get("rotation3D")
            or ps.get("customData") or ps.get("dynamics", {}).get("trail")
            or node.get("prefabAnimation") or node.get("actionPose") is not None or node.get("actionRenderer")):
        raise ValueError("Unsupported pivot, custom stream, trail or animated renderer")
    if capture.get("prefab") + "/" + capture.get("nodePath") != node.get("path"):
        raise ValueError("Captured node identity differs from source")
    scale = _vector(capture["ownerScale"])
    if min(scale) <= 0 or max(scale) - min(scale) > 1e-6:
        raise ValueError("Profile requires a positive uniform source owner scale")
    columns, rows = ps.get("tilesX", 1), ps.get("tilesY", 1)
    if any(type(v) is not int or v <= 0 for v in (columns, rows)):
        raise ValueError("Invalid source atlas dimensions")
    frames = capture.get("frames")
    records = capture.get("samples")
    if type(frames) is not int or frames <= 0 or not isinstance(records, list) or len(records) != frames + 1:
        raise ValueError("Complete finite source frames are required")
    particles, seeds, alive = [], {}, set()
    maximum_vertex_error = maximum_uv_error = 0.
    vertex_checks = sample_count = 0
    for frame, record in enumerate(records):
        if record.get("frame") != frame:
            raise ValueError("Source frame clock must be contiguous and ordered")
        current = set()
        source_particles = record["particles"]
        if any(len(record[field]) != 4 * len(source_particles) for field in ("vertices", "uv", "colors")):
            raise ValueError("Baked quad stream size differs from particle count")
        for index, particle in enumerate(source_particles):
            seed = particle["seed"]
            if type(seed) is not int or not 0 <= seed <= 0xffffffff or seed in current:
                raise ValueError("Particle identities must be unique unsigned seeds")
            current.add(seed)
            if seed in seeds and seed not in alive:
                raise ValueError("A particle seed was reused after death or omitted frames")
            position = _vector(particle["position"])
            if any(_vector(particle["velocity"])):
                raise ValueError("World velocity separation requires a different sample profile")
            size = [v * s for v, s in zip(_vector(particle["size"]), scale)]
            if min(size[:2]) <= 0:
                raise ValueError("Collapsed or negative sampled quads are unsupported")
            angle = -math.radians(_number(particle["rotation"], "particle rotation"))
            color = _color(particle["color"])
            remaining = _number(particle["remaining"], "remaining lifetime")
            lifetime = _number(particle["lifetime"], "start lifetime")
            if not 0 < remaining <= lifetime:
                raise ValueError("Particle lifetime is invalid")
            uv = [_vector(v, "xy") for v in record["uv"][index * 4:index * 4 + 4]]
            column = round(min(v[0] for v in uv) * columns)
            row = round((1 - max(v[1] for v in uv)) * rows)
            if not 0 <= column < columns or not 0 <= row < rows:
                raise ValueError("Captured atlas cell is outside the source sheet")
            c, s = math.cos(angle), math.sin(angle)
            for corner, (u, v) in enumerate(((0, 1), (1, 1), (1, 0), (0, 0))):
                expected_uv = [(column + u) / columns, (rows - 1 - row + v) / rows]
                maximum_uv_error = max(maximum_uv_error, *(abs(a-b) for a, b in zip(uv[corner], expected_uv)))
                x, y = (u - .5) * size[0], (v - .5) * size[1]
                expected = [position[0] + c*x - s*y, position[1] + s*x + c*y, position[2]]
                measured = _vector(record["vertices"][index * 4 + corner])
                maximum_vertex_error = max(maximum_vertex_error, *(abs(a-b) for a, b in zip(expected, measured)))
                if _color(record["colors"][index * 4 + corner]) != color:
                    raise ValueError("Baked vertex color differs from source particle color")
                vertex_checks += 1
            if maximum_vertex_error > 2e-5 or maximum_uv_error > 1e-6:
                raise ValueError("Source getters disagree with independently baked quad geometry/UV")
            if seed not in seeds:
                ordinal = len(particles)
                result = {"nodeIndex":node_index, "path":node["path"], "ordinal":ordinal,
                          "source_seed":seed, "birth":frame / 30 - (lifetime - remaining), "lifetime":lifetime,
                          "renderMode":"Billboard", "alignment":0, "pivot":[0, 0, 0], "stretch":None,
                          "sortingOrder":renderer.get("sortingOrder", 0), "texture":node.get("texture"),
                          "mesh":None, "tiles":[columns, rows], "materialIndex":0, "anchor":None, "samples":[]}
                seeds[seed] = result
                particles.append(result)
            result = seeds[seed]
            if result["samples"]:
                previous = result["samples"][-1]
                if max(abs(a-b) for a, b in zip(previous["position"], [-position[0], position[1], position[2]])) > 1e-7:
                    raise ValueError("Stationary world-particle profile received moving positions")
            size = [size[0], size[1], 1.]
            native_position = [-position[0], position[1], position[2]]
            result["samples"].append({"frame":frame, "position":native_position, "renderOrigin":native_position[:],
                "worldOffset":[0., 0., 0.], "localVelocity":[0., 0., 0.], "worldVelocity":[0., 0., 0.],
                "renderSize":size, "size":size[:], "angle":angle, "color":color,
                "rotation":[0., 0., math.sin(angle / 2), math.cos(angle / 2)], "rotationDegrees":[0., 0., math.degrees(angle)],
                "customData":[0., 0., 0., 0.], "customData2":[0., 0., 0., 0.], "flipbook":row * columns + column})
            sample_count += 1
        alive = current
    if alive:
        raise ValueError("Finite capture ends before all particles have died")
    if not particles:
        raise ValueError("No visible source particles were captured")
    return particles, {"profile":PROFILE, "unity":capture["unity"], "systems":1, "particles":len(particles),
        "samples":sample_count, "vertex_checks":vertex_checks, "uv_checks":vertex_checks,
        "maximum_vertex_error":maximum_vertex_error, "maximum_uv_error":maximum_uv_error,
        "clock":"Complete source-engine 30Hz samples; native activation uses first observed source frame",
        "scope":"Actual captured prefab simulated by the identified editor; captured game-engine parity is not implied"}
