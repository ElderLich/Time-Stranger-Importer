"""Digimon New Century .glb -> Time Stranger import pipeline.

One-click import of an nc-extractor model.glb into an export-ready DSTS scene:

1.  Import the .glb with Blender's glTF importer (scene fps is set to 30 first
    so the NC 30 fps sample keys land on integer frames).
2.  Keep only the skinned ``*_lod0`` body meshes and their armature; everything
    else (other submodels, empties, effect/LOD meshes) is deleted. Actions are
    kept.
3.  Re-add the missing root bone. NC glbs contain the full Bip001 rig but the
    top-level ``Bip001`` node is not in any skin's joint list, so the glTF
    importer drops it as a bone and folds its (animated) transform onto the
    armature OBJECT. We read the glb JSON chunk to find that node and create it
    as a bone. Source world poses are sampled before rebuilding the rig; every
    new pose basis is solved against the final rest hierarchy and game bone
    flags. Object motion is included in those poses, then its curves are removed.
4.  Re-seat everything in file-consistent space: the glTF importer leaves
    skinned vertices and bone rests in bind/armature space with a non-bind
    transform on the armature object. We derive the rigid axis rotation M that
    maps the imported bone rests onto the glb inverse-bind-matrix bind worlds
    (M is rotX(-90) for NC files, but it is fitted and verified numerically),
    bake M into mesh vertices and bone rests, and leave identity object
    transforms so the existing .geom exporter works unmodified.
5.  Borrow materials, per-mesh DSTS flags and Geom-collection metadata from a
    donor .geom (default chr050), rename the primary UV map to ``uv1``.
6.  Optionally build the ``outline`` mesh: a joined copy of the body meshes
    with the donor's MTR_line01 material and an all-white byte color attribute
    (the DSTS outline shader reads that color; zeros render the model as a
    solid black shell).

The math anchor points (validated against the chr110 fan port and the shipped
chr110_bn01.anim):

- TS .anim binds bones by index into the geom bone order and stores deltas vs
  the geom rest: ``local(t) = rest_local @ T(dp) @ R(dq) @ S(ds)``.
- Final bone rest (Blender space, identity object transform) = glb IBM bind
  world. Exporting applies rotX(-90), which lands the skeleton in the same
  Y-up file space as the glb node hierarchy.
- New root bone rest R = the armature object's static world transform with
  animation cleared (the importer's conversion of the root node's TRS).
- Animation conversion uses ``Bone.convert_local_to_pose(..., invert=True)``
  with each original world matrix and the original animated parent matrix;
  this preserves both root motion and changed child rest bases.
"""

import bpy
import json
import math
import os
import re
import struct
import tempfile

from mathutils import Euler, Matrix, Quaternion, Vector

from .geom import import_geom

# Object-level transform channel paths (as opposed to pose.bones paths).
OBJECT_TRANSFORM_PATHS = {
    "location",
    "rotation_quaternion",
    "rotation_euler",
    "rotation_axis_angle",
    "scale",
    "delta_location",
    "delta_rotation_euler",
    "delta_rotation_quaternion",
    "delta_scale",
}

_IDENTITY = Matrix.Identity(4)
_FLAG_KEYS = tuple(f"DSTS_flag_{i}" for i in range(8))
_GEOM_METADATA_KEYS = ("unknown_0x10", "unknown_0x30", "unknown_0x34", "CLUT")
_OUTLINE_MATERIAL = "MTR_line01"


def _log(msg):
    print(f"[DSTS NC] {msg}")


class NcImportError(RuntimeError):
    pass


def _matrix_is_identity(matrix, epsilon=1e-5):
    for row in range(4):
        for col in range(4):
            if abs(matrix[row][col] - _IDENTITY[row][col]) > epsilon:
                return False
    return True


def _base_name(name):
    return re.sub(r"\.[0-9]{3}$", "", name)


# ---------------------------------------------------------------------------
# glb JSON/BIN chunk reading (structure + inverse bind matrices only)
# ---------------------------------------------------------------------------

def read_glb_chunks(filepath):
    with open(filepath, "rb") as handle:
        header = handle.read(12)
        if len(header) < 12:
            raise NcImportError("File too short to be a .glb")
        magic, version, total = struct.unpack("<III", header)
        if magic != 0x46546C67 or version != 2:
            raise NcImportError("Expected a binary glTF 2 (.glb) file")
        if total != os.fstat(handle.fileno()).st_size:
            raise NcImportError("GLB declared length does not match the file")
        gltf = None
        binary = b""
        while handle.tell() < total:
            chunk_header = handle.read(8)
            if len(chunk_header) < 8:
                raise NcImportError("Truncated GLB chunk header")
            length, chunk_type = struct.unpack("<II", chunk_header)
            if length % 4 or handle.tell() + length > total:
                raise NcImportError("Invalid GLB chunk length")
            data = handle.read(length)
            if chunk_type == 0x4E4F534A:  # JSON
                if gltf is not None:
                    raise NcImportError("GLB contains more than one JSON chunk")
                gltf = json.loads(data)
            elif chunk_type == 0x004E4942:  # BIN
                binary = data
        if gltf is None:
            raise NcImportError("No JSON chunk found in .glb")
        return gltf, binary


def _pad4(data, pad):
    return data + (pad * ((4 - (len(data) % 4)) % 4))


def write_glb_chunks(filepath, gltf, binary):
    """Write a binary glTF v2 file from JSON + BIN chunks."""
    gltf.setdefault("buffers", [{"byteLength": 0}])
    gltf["buffers"][0]["byteLength"] = len(binary)
    json_chunk = _pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bin_chunk = _pad4(bytes(binary), b"\x00")
    total = 12 + 8 + len(json_chunk) + (8 + len(bin_chunk) if bin_chunk else 0)
    with open(filepath, "wb") as handle:
        handle.write(struct.pack("<III", 0x46546C67, 2, total))
        handle.write(struct.pack("<II", len(json_chunk), 0x4E4F534A))
        handle.write(json_chunk)
        if bin_chunk:
            handle.write(struct.pack("<II", len(bin_chunk), 0x004E4942))
            handle.write(bin_chunk)


def _glb_node_parents(gltf):
    parents = {}
    for index, node in enumerate(gltf.get("nodes", [])):
        for child in node.get("children", []):
            parents[child] = index
    return parents


def _glb_all_joints(gltf):
    joints = set()
    for skin in gltf.get("skins", []):
        joints.update(skin.get("joints", []))
    return joints


def find_missing_root_node(gltf, joints=None):
    """Return (node_index, name) of the missing skeleton root above the joints.

    Most NC models have one immediate non-joint parent above the skinned joint
    roots (``Bip001``). Some models also skin accessory roots directly under a
    shared Unity ``root`` node while the body starts below ``Bip001``; in those
    cases the lowest common non-joint ancestor is the missing export root.
    Returns (None, None) when no unambiguous common ancestor exists.
    """
    nodes = gltf.get("nodes", [])
    parents = _glb_node_parents(gltf)
    joints = set(_glb_all_joints(gltf) if joints is None else joints)
    if not joints:
        return None, None

    joint_roots = []
    for joint in joints:
        parent = parents.get(joint)
        if parent is not None and parent not in joints:
            joint_roots.append(joint)
    if not joint_roots:
        return None, None

    ancestor_chains = []
    for joint in sorted(joint_roots):
        chain = []
        parent = parents.get(joint)
        while parent is not None and parent not in joints:
            chain.append(parent)
            parent = parents.get(parent)
        if not chain:
            return None, None
        ancestor_chains.append(chain)

    shared_ancestors = set(ancestor_chains[0])
    for chain in ancestor_chains[1:]:
        shared_ancestors.intersection_update(chain)
    if not shared_ancestors:
        return None, None

    candidate = next(
        ancestor for ancestor in ancestor_chains[0]
        if ancestor in shared_ancestors
    )
    node = nodes[candidate]
    if "mesh" in node or "skin" in node:
        return None, None
    return candidate, node.get("name")


def _node_local_matrix(node):
    if "matrix" in node:
        c = node["matrix"]  # column-major
        return Matrix((
            (c[0], c[4], c[8], c[12]),
            (c[1], c[5], c[9], c[13]),
            (c[2], c[6], c[10], c[14]),
            (c[3], c[7], c[11], c[15]),
        ))
    translation = node.get("translation", (0.0, 0.0, 0.0))
    rotation = node.get("rotation", (0.0, 0.0, 0.0, 1.0))  # glTF xyzw
    scale = node.get("scale", (1.0, 1.0, 1.0))
    quat = Quaternion((rotation[3], rotation[0], rotation[1], rotation[2]))
    return (
        Matrix.Translation(Vector(translation))
        @ quat.to_matrix().to_4x4()
        @ Matrix.Diagonal((*scale, 1.0))
    )


def _glb_node_worlds(gltf, indices):
    """{node index: static world matrix} composed from the glb node hierarchy."""
    nodes = gltf.get("nodes", [])
    parents = _glb_node_parents(gltf)
    cache = {}

    def world(i):
        if i in cache:
            return cache[i]
        matrix = _node_local_matrix(nodes[i])
        parent = parents.get(i)
        cache[i] = matrix if parent is None else world(parent) @ matrix
        return cache[i]

    return {i: world(i) for i in indices}


def _read_ibm_bind_worlds_by_skin(gltf, binary):
    """Per-skin bind worlds from inverseBindMatrices.

    Multi-root Unity GLBs can contain duplicate skeleton names under several
    skins. Keep each skin isolated so the importer can pick the bind pose that
    matches Blender's chosen skinned armature instead of overwriting by name.
    """
    nodes = gltf.get("nodes", [])
    accessors = gltf.get("accessors", [])
    views = gltf.get("bufferViews", [])
    results = []
    for skin_index, skin in enumerate(gltf.get("skins", [])):
        joints = skin.get("joints", [])
        accessor_index = skin.get("inverseBindMatrices")
        bind_worlds = {}
        if accessor_index is None:
            for joint in joints:
                bind_worlds[nodes[joint].get("name")] = Matrix.Identity(4)
        else:
            accessor = accessors[accessor_index]
            if accessor.get("componentType") != 5126 or accessor.get("type") != "MAT4":
                raise NcImportError("Unsupported inverseBindMatrices accessor format")
            view = views[accessor["bufferView"]]
            offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
            count = accessor["count"]
            floats = struct.unpack_from(f"<{count * 16}f", binary, offset)
            for j, joint in enumerate(joints):
                columns = floats[j * 16:(j + 1) * 16]
                # glTF matrices are column-major.
                ibm = Matrix((
                    (columns[0], columns[4], columns[8], columns[12]),
                    (columns[1], columns[5], columns[9], columns[13]),
                    (columns[2], columns[6], columns[10], columns[14]),
                    (columns[3], columns[7], columns[11], columns[15]),
                ))
                bind_worlds[nodes[joint].get("name")] = ibm.inverted()
        results.append({
            "index": skin_index,
            "joints": set(joints),
            "bind_worlds": bind_worlds,
        })
    return results


def _read_ibm_bind_worlds(gltf, binary):
    """{joint node name: 4x4 bind world Matrix} from skin inverseBindMatrices."""
    bind_worlds = {}
    for entry in _read_ibm_bind_worlds_by_skin(gltf, binary):
        bind_worlds.update(entry["bind_worlds"])
    return bind_worlds


# ---------------------------------------------------------------------------
# Blender-safe glTF animation pre-bake
# ---------------------------------------------------------------------------

_GLTF_FLOAT = 5126
_ACCESSOR_COMPONENTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT4": 16,
}


def _buffer_bytearray(gltf, binary):
    byte_length = len(binary)
    if gltf.get("buffers"):
        byte_length = min(byte_length, int(gltf["buffers"][0].get("byteLength", byte_length)))
    return bytearray(binary[:byte_length])


def _read_float_accessor(gltf, binary, accessor_index):
    accessors = gltf.get("accessors", [])
    views = gltf.get("bufferViews", [])
    accessor = accessors[accessor_index]
    if accessor.get("sparse"):
        raise NcImportError("Sparse animation accessors are not supported")
    if accessor.get("componentType") != _GLTF_FLOAT:
        raise NcImportError("Only FLOAT animation accessors are supported")
    accessor_type = accessor.get("type")
    components = _ACCESSOR_COMPONENTS.get(accessor_type)
    if components is None:
        raise NcImportError(f"Unsupported accessor type: {accessor_type}")
    view = views[accessor["bufferView"]]
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", components * 4)
    rows = []
    for index in range(accessor["count"]):
        values = struct.unpack_from(
            f"<{components}f", binary, offset + index * stride
        )
        rows.append(values[0] if components == 1 else list(values))
    return rows


def _append_float_accessor(gltf, binary, values, accessor_type):
    components = _ACCESSOR_COMPONENTS[accessor_type]
    while len(binary) % 4:
        binary.append(0)
    byte_offset = len(binary)
    if components == 1:
        for value in values:
            binary.extend(struct.pack("<f", float(value)))
    else:
        for row in values:
            if len(row) != components:
                raise NcImportError(
                    f"Expected {components} components for {accessor_type}, got {len(row)}"
                )
            binary.extend(struct.pack(f"<{components}f", *[float(v) for v in row]))

    gltf.setdefault("bufferViews", []).append({
        "buffer": 0,
        "byteOffset": byte_offset,
        "byteLength": len(binary) - byte_offset,
    })
    accessor = {
        "bufferView": len(gltf["bufferViews"]) - 1,
        "componentType": _GLTF_FLOAT,
        "count": len(values),
        "type": accessor_type,
    }
    if values:
        if components == 1:
            flat = [float(v) for v in values]
            accessor["min"] = [min(flat)]
            accessor["max"] = [max(flat)]
        else:
            cols = list(zip(*values))
            accessor["min"] = [float(min(col)) for col in cols]
            accessor["max"] = [float(max(col)) for col in cols]
    gltf.setdefault("accessors", []).append(accessor)
    gltf.setdefault("buffers", [{"byteLength": 0}])
    gltf["buffers"][0]["byteLength"] = len(binary)
    return len(gltf["accessors"]) - 1


def _normalize_quat_xyzw(values):
    mag = math.sqrt(sum(float(v) * float(v) for v in values))
    if mag <= 1e-12:
        return [0.0, 0.0, 0.0, 1.0]
    return [float(v) / mag for v in values]


def _slerp_xyzw(a, b, t):
    a = _normalize_quat_xyzw(a)
    b = _normalize_quat_xyzw(b)
    dot = sum(a[i] * b[i] for i in range(4))
    if dot < 0.0:
        b = [-v for v in b]
        dot = -dot
    if dot > 0.9995:
        return _normalize_quat_xyzw([a[i] + t * (b[i] - a[i]) for i in range(4)])
    theta0 = math.acos(max(-1.0, min(1.0, dot)))
    theta = theta0 * t
    sin_theta = math.sin(theta)
    sin_theta0 = math.sin(theta0)
    s0 = math.cos(theta) - dot * sin_theta / sin_theta0
    s1 = sin_theta / sin_theta0
    return [s0 * a[i] + s1 * b[i] for i in range(4)]


def _sample_gltf_track(times, values, interpolation, target_path, time):
    if not times:
        raise NcImportError("Animation sampler has no input keys")
    ts = [float(v) for v in times]
    if time <= ts[0]:
        return list(values[1] if interpolation == "CUBICSPLINE" else values[0])
    if time >= ts[-1]:
        last = len(ts) - 1
        return list(values[last * 3 + 1] if interpolation == "CUBICSPLINE" else values[last])

    hi = 1
    while hi < len(ts) and ts[hi] < time:
        hi += 1
    lo = hi - 1
    t0, t1 = ts[lo], ts[hi]
    dt = t1 - t0
    u = (time - t0) / dt if dt > 0.0 else 0.0

    if interpolation == "CUBICSPLINE":
        if len(values) != len(times) * 3:
            raise NcImportError("CUBICSPLINE output count does not equal input_count * 3")
        v0 = [float(v) for v in values[lo * 3 + 1]]
        out0 = [float(v) for v in values[lo * 3 + 2]]
        in1 = [float(v) for v in values[hi * 3]]
        v1 = [float(v) for v in values[hi * 3 + 1]]
        u2 = u * u
        u3 = u2 * u
        out = [
            (2.0 * u3 - 3.0 * u2 + 1.0) * v0[i]
            + (u3 - 2.0 * u2 + u) * dt * out0[i]
            + (-2.0 * u3 + 3.0 * u2) * v1[i]
            + (u3 - u2) * dt * in1[i]
            for i in range(len(v0))
        ]
        return _normalize_quat_xyzw(out) if target_path == "rotation" else out

    a = [float(v) for v in values[lo]]
    b = [float(v) for v in values[hi]]
    if target_path == "rotation":
        return _slerp_xyzw(a, b, u)
    return [a[i] + u * (b[i] - a[i]) for i in range(len(a))]


def _bake_cubicspline_samplers_for_blender(gltf, binary, fps=30.0):
    """Bake glTF CUBICSPLINE transform tracks to exact 30 fps LINEAR keys.

    Blender imports glTF cubic quaternion tracks as native Bezier F-Curves. That
    is close, but not exact enough for the tiny foot-lock corrections used by
    Unity-origin NC/Next Order GLBs. Baking before Blender sees the file keeps
    Blender's later integer-frame resample faithful to the glTF evaluator.
    """
    if fps <= 0.0:
        raise NcImportError("Animation bake FPS must be positive")
    binary = _buffer_bytearray(gltf, binary)
    baked = 0
    for anim in gltf.get("animations", []) or []:
        samplers = anim.get("samplers", []) or []
        sampler_paths = {}
        for channel in anim.get("channels", []) or []:
            target = channel.get("target", {}) or {}
            path = target.get("path")
            if path in {"translation", "rotation", "scale"}:
                sampler_paths.setdefault(channel.get("sampler"), path)

        for sampler_index, sampler in enumerate(samplers):
            if sampler.get("interpolation", "LINEAR") != "CUBICSPLINE":
                continue
            target_path = sampler_paths.get(sampler_index)
            if target_path not in {"translation", "rotation", "scale"}:
                continue
            times = _read_float_accessor(gltf, binary, sampler["input"])
            values = _read_float_accessor(gltf, binary, sampler["output"])
            if not times:
                continue
            end_time = float(times[-1])
            frame_count = int(math.floor(end_time * fps + 0.5)) + 1
            frame_count = max(frame_count, 1)
            baked_times = [min(end_time, frame / fps) for frame in range(frame_count)]
            baked_values = []
            previous_quat = None
            for time in baked_times:
                value = _sample_gltf_track(times, values, "CUBICSPLINE", target_path, time)
                if target_path == "rotation":
                    value = _normalize_quat_xyzw(value)
                    if previous_quat is not None and sum(previous_quat[i] * value[i] for i in range(4)) < 0.0:
                        value = [-v for v in value]
                    previous_quat = value
                baked_values.append(value)

            sampler["input"] = _append_float_accessor(gltf, binary, baked_times, "SCALAR")
            sampler["output"] = _append_float_accessor(
                gltf,
                binary,
                baked_values,
                "VEC4" if target_path == "rotation" else "VEC3",
            )
            sampler["interpolation"] = "LINEAR"
            baked += 1

    if baked:
        extras = gltf.setdefault("asset", {}).setdefault("extras", {})
        extras.setdefault("dsts_blender_import", {})["cubicspline_bake"] = {
            "fps": fps,
            "samplers": baked,
        }
    return gltf, binary, baked


# ---------------------------------------------------------------------------
# Rigid alignment fitting
# ---------------------------------------------------------------------------

_STRICT_ALIGNMENT_TOLERANCE = 1e-3
_ALIGNMENT_TOLERANCE = 1e-2


def _axis_rotation_candidates():
    axes = (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
    candidates = []
    for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        for signs in ((sx, sy, sz) for sx in (1, -1) for sy in (1, -1) for sz in (1, -1)):
            matrix = Matrix.Identity(4)
            for col in range(3):
                vec = axes[perm[col]] * signs[col]
                matrix[0][col], matrix[1][col], matrix[2][col] = vec.x, vec.y, vec.z
            if matrix.to_3x3().determinant() > 0.5:  # proper rotations only
                candidates.append(matrix)
    return candidates


def fit_alignment_rotation(source_points, target_points):
    """Best axis-aligned proper rotation M with M @ source ~= target.

    Returns (matrix, max_error). Both dicts are keyed by bone name.
    """
    shared = sorted(set(source_points) & set(target_points))
    if not shared:
        return None, float("inf")
    best_matrix, best_error = None, float("inf")
    for candidate in _axis_rotation_candidates():
        error = max(
            ((candidate @ source_points[name].to_4d()).to_3d() - target_points[name]).length
            for name in shared
        )
        if error < best_error:
            best_matrix, best_error = candidate, error
    return best_matrix, best_error


def _select_bind_worlds_for_armature(gltf, binary, current_heads):
    """Pick the skin bind pose that matches Blender's selected armature."""
    skin_entries = _read_ibm_bind_worlds_by_skin(gltf, binary)
    if not skin_entries:
        return {}, set(), {
            "skin": None,
            "shared": 0,
            "error": float("inf"),
            "skins": 0,
        }

    min_shared = max(3, len(current_heads) // 2)
    scored = []
    for entry in skin_entries:
        bind_worlds = entry["bind_worlds"]
        shared = sorted(set(current_heads) & set(bind_worlds))
        if shared:
            _matrix, error = fit_alignment_rotation(
                current_heads,
                {name: bind_worlds[name].translation.copy() for name in shared},
            )
        else:
            error = float("inf")
        scored.append({
            **entry,
            "shared": len(shared),
            "error": error,
        })

    eligible = [entry for entry in scored if entry["shared"] >= min_shared]
    pool = eligible or scored
    best = min(pool, key=lambda entry: (entry["error"], -entry["shared"], entry["index"]))
    return best["bind_worlds"], best["joints"], {
        "skin": best["index"],
        "shared": best["shared"],
        "error": best["error"],
        "skins": len(skin_entries),
        "eligible": len(eligible),
    }


def _node_bind_alignment_fit(gltf, joint_indices, bind_heads):
    """Fit raw glTF node worlds to bind heads for retry diagnostics."""
    nodes = gltf.get("nodes", [])
    node_worlds = _glb_node_worlds(gltf, set(joint_indices))
    node_heads = {}
    for joint in joint_indices:
        name = nodes[joint].get("name") if 0 <= joint < len(nodes) else None
        if name in bind_heads:
            node_heads[name] = node_worlds[joint].translation.copy()
    _matrix, error = fit_alignment_rotation(node_heads, bind_heads)
    return len(node_heads), error


# ---------------------------------------------------------------------------
# Action helpers (Blender 4.x legacy F-Curves and 5.x slotted actions)
# ---------------------------------------------------------------------------

def _action_channelbags(action):
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            yield from getattr(strip, "channelbags", ())


def _pose_slot_and_fcurves(action):
    """Return (slot, fcurve_list) of the channelbag holding pose-bone curves.

    For legacy actions the slot is None and the flat fcurve list is returned.
    """
    if getattr(action, "layers", None):
        for bag in _action_channelbags(action):
            fcurves = list(getattr(bag, "fcurves", ()))
            if any(fc.data_path.startswith("pose.bones") for fc in fcurves):
                slot = None
                bag_handle = getattr(bag, "slot_handle", None)
                if bag_handle is not None:
                    slot = next(
                        (s for s in action.slots if getattr(s, "handle", None) == bag_handle),
                        None,
                    )
                return slot, fcurves
        return None, []
    fcurves = list(getattr(action, "fcurves", ()))
    if any(fc.data_path.startswith("pose.bones") for fc in fcurves):
        return None, fcurves
    return None, []


def remove_object_transform_fcurves(action):
    """Delete every object-level transform F-Curve from the action (all slots)."""
    removed = 0
    if getattr(action, "layers", None):
        for bag in _action_channelbags(action):
            doomed = [fc for fc in bag.fcurves if fc.data_path in OBJECT_TRANSFORM_PATHS]
            for fc in doomed:
                bag.fcurves.remove(fc)
                removed += 1
        return removed
    doomed = [fc for fc in action.fcurves if fc.data_path in OBJECT_TRANSFORM_PATHS]
    for fc in doomed:
        action.fcurves.remove(fc)
        removed += 1
    return removed


def _channel_defaults(obj):
    return {
        ("location", 0): obj.location[0],
        ("location", 1): obj.location[1],
        ("location", 2): obj.location[2],
        ("rotation_quaternion", 0): obj.rotation_quaternion[0],
        ("rotation_quaternion", 1): obj.rotation_quaternion[1],
        ("rotation_quaternion", 2): obj.rotation_quaternion[2],
        ("rotation_quaternion", 3): obj.rotation_quaternion[3],
        ("rotation_euler", 0): obj.rotation_euler[0],
        ("rotation_euler", 1): obj.rotation_euler[1],
        ("rotation_euler", 2): obj.rotation_euler[2],
        ("scale", 0): obj.scale[0],
        ("scale", 1): obj.scale[1],
        ("scale", 2): obj.scale[2],
    }


def object_basis_matrix_from_fcurves(obj_defaults, rotation_mode, fcurve_map, frame):
    """Compose the object's local basis matrix at ``frame`` from its F-Curves.

    ``fcurve_map`` maps (data_path, array_index) -> fcurve; channels without a
    curve fall back to the authored values captured in ``obj_defaults``.
    """
    def value(path, index):
        fc = fcurve_map.get((path, index))
        if fc is not None:
            return fc.evaluate(frame)
        return obj_defaults.get((path, index), 1.0 if path == "scale" else 0.0)

    loc = Vector((value("location", 0), value("location", 1), value("location", 2)))
    scale = Vector((value("scale", 0), value("scale", 1), value("scale", 2)))
    if rotation_mode == "QUATERNION":
        quat = Quaternion((
            value("rotation_quaternion", 0),
            value("rotation_quaternion", 1),
            value("rotation_quaternion", 2),
            value("rotation_quaternion", 3),
        ))
        if quat.magnitude > 1e-8:
            quat.normalize()
        else:
            quat = Quaternion((1.0, 0.0, 0.0, 0.0))
        rot = quat.to_matrix().to_4x4()
    else:
        rot = Euler((
            value("rotation_euler", 0),
            value("rotation_euler", 1),
            value("rotation_euler", 2),
        ), rotation_mode if rotation_mode in {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"} else "XYZ").to_matrix().to_4x4()
    return Matrix.Translation(loc) @ rot @ Matrix.Diagonal((*scale, 1.0))


def _action_frame_span(action):
    frame_range = action.frame_range
    start = int(math.floor(frame_range[0] + 0.5))
    end = int(math.floor(frame_range[1] + 0.5))
    return start, max(start, end)


def _ensure_root_fcurve(action, armature_obj, data_path, index, group_name):
    if hasattr(action, "fcurve_ensure_for_datablock"):
        return action.fcurve_ensure_for_datablock(
            armature_obj, data_path, index=index, group_name=group_name
        )
    fcurve = action.fcurves.find(data_path, index=index)
    if fcurve is None:
        fcurve = action.fcurves.new(data_path=data_path, index=index)
        if group_name and hasattr(fcurve, "group"):
            group = action.groups.get(group_name) or action.groups.new(group_name)
            fcurve.group = group
    return fcurve


def resample_pose_fcurves(action, frame_start, frame_end):
    """Resample every pose-bone F-Curve onto the integer frame grid.

    NC clips are Unity keyframe-reduced: the glb (and hence the imported
    F-Curves) key at fractional frame times. The TS .anim format stores keys
    on an integer frame grid, so exporting raw keyframes truncates their
    timing (up to a full frame of drift mid-clip). Baking one key per integer
    frame keeps the exported values exactly equal to Blender's evaluated
    animation. Returns the number of resampled F-Curves.
    """
    frames = list(range(int(frame_start), int(frame_end) + 1))
    if not frames:
        return 0
    resampled = 0

    def _resample(fcurve):
        nonlocal resampled
        if not str(fcurve.data_path).startswith("pose.bones"):
            return
        values = [fcurve.evaluate(float(f)) for f in frames]
        _clear_fcurve_keys(fcurve)
        keyframes = fcurve.keyframe_points
        keyframes.add(len(frames))
        flat = [0.0] * (len(frames) * 2)
        for i, f in enumerate(frames):
            flat[i * 2] = float(f)
            flat[i * 2 + 1] = values[i]
        keyframes.foreach_set("co", flat)
        try:
            keyframes.foreach_set("interpolation", [1] * len(frames))  # LINEAR
        except Exception:
            for keyframe in keyframes:
                keyframe.interpolation = 'LINEAR'
        fcurve.update()
        resampled += 1

    if getattr(action, "layers", None):
        for bag in _action_channelbags(action):
            for fcurve in list(bag.fcurves):
                _resample(fcurve)
    else:
        for fcurve in list(action.fcurves):
            _resample(fcurve)
    return resampled


def _clear_fcurve_keys(fcurve):
    keyframes = fcurve.keyframe_points
    try:
        keyframes.clear()
    except AttributeError:
        for keyframe in reversed(list(keyframes)):
            keyframes.remove(keyframe)


def write_pose_matrix_keys(action, armature_obj, pose_slot, tracks):
    """(Re)key pose bones from per-frame basis matrices.

    ``tracks`` maps bone name -> {frame: basis Matrix}. Any existing keys on
    the affected channels are replaced.
    """
    anim_data = armature_obj.animation_data or armature_obj.animation_data_create()
    previous_action = anim_data.action
    anim_data.action = action
    if pose_slot is not None and hasattr(anim_data, "action_slot"):
        try:
            anim_data.action_slot = pose_slot
        except Exception as exc:  # pragma: no cover - defensive
            _log(f"could not assign action slot for {action.name}: {exc}")

    for bone_name, deltas in tracks.items():
        base_path = f'pose.bones["{bone_name}"]'
        loc_fcs = [_ensure_root_fcurve(action, armature_obj, f"{base_path}.location", i, bone_name) for i in range(3)]
        rot_fcs = [_ensure_root_fcurve(action, armature_obj, f"{base_path}.rotation_quaternion", i, bone_name) for i in range(4)]
        scl_fcs = [_ensure_root_fcurve(action, armature_obj, f"{base_path}.scale", i, bone_name) for i in range(3)]
        for fc in (*loc_fcs, *rot_fcs, *scl_fcs):
            _clear_fcurve_keys(fc)

        previous_quat = None
        for frame in sorted(deltas):
            loc, quat, scale = deltas[frame].decompose()
            if previous_quat is not None and previous_quat.dot(quat) < 0.0:
                quat = -quat
            previous_quat = quat
            for i in range(3):
                key = loc_fcs[i].keyframe_points.insert(float(frame), loc[i], options={'FAST'})
                key.interpolation = 'LINEAR'
            for i in range(4):
                key = rot_fcs[i].keyframe_points.insert(float(frame), quat[i], options={'FAST'})
                key.interpolation = 'LINEAR'
            for i in range(3):
                key = scl_fcs[i].keyframe_points.insert(float(frame), scale[i], options={'FAST'})
                key.interpolation = 'LINEAR'
        for fc in (*loc_fcs, *rot_fcs, *scl_fcs):
            fc.update()

    anim_data.action = previous_action


def _assign_action_slot(anim_data, pose_slot):
    if pose_slot is not None and hasattr(anim_data, "action_slot"):
        try:
            anim_data.action_slot = pose_slot
        except Exception as exc:  # pragma: no cover - defensive
            _log(f"could not assign action slot: {exc}")


def _set_pose_to_rest(armature_obj):
    for pose_bone in armature_obj.pose.bones:
        pose_bone.location = (0.0, 0.0, 0.0)
        pose_bone.rotation_mode = "QUATERNION"
        pose_bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pose_bone.rotation_euler = (0.0, 0.0, 0.0)
        try:
            pose_bone.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        except Exception:
            pass
        pose_bone.scale = (1.0, 1.0, 1.0)


def _bone_depth_groups(armature_obj):
    depth_groups = []
    for bone in armature_obj.data.bones:
        depth = 0
        parent = bone.parent
        while parent is not None:
            depth += 1
            parent = parent.parent
        while len(depth_groups) <= depth:
            depth_groups.append([])
        depth_groups[depth].append(bone)
    return depth_groups


def _capture_source_world_poses(context, armature_obj, actions, root_name, static_basis):
    """Capture the glTF scene before changing its bind space or parenting."""
    ad = armature_obj.animation_data or armature_obj.animation_data_create()
    previous_frame = context.scene.frame_current
    samples = {}
    for track in ad.nla_tracks:
        track.mute = True
    for action in actions:
        slot, curves = _pose_slot_and_fcurves(action)
        if not curves:
            continue
        ad.action = action
        _assign_action_slot(ad, slot)
        armature_obj.matrix_basis = static_basis.copy()
        _set_pose_to_rest(armature_obj)
        start, end = _action_frame_span(action)
        frames = {}
        for frame in range(start, end + 1):
            context.scene.frame_set(frame)
            context.view_layer.update()
            world = armature_obj.matrix_world.copy()
            poses = {b.name: world @ b.matrix for b in armature_obj.pose.bones}
            if root_name:
                poses[root_name] = world
            frames[frame] = poses
        samples[action] = (slot, frames)
    ad.action = None
    armature_obj.matrix_basis = static_basis.copy()
    _set_pose_to_rest(armature_obj)
    context.scene.frame_set(previous_frame)
    return samples


def _bake_source_world_poses(context, armature_obj, samples):
    """Solve source world poses against the final rest rig and game bone flags.

    Reparenting a glTF root under a new root changes its rest basis. Copying
    old child quaternions silently rotates the whole animated model. Solving
    every target basis from the source world matrix preserves the pose and
    handles both the root change and use_local_location=False in one step.
    """
    for bone in armature_obj.data.bones:
        bone.use_local_location = False
    maximum_error = 0.0
    for action, (slot, frames) in samples.items():
        tracks = {b.name: {} for b in armature_obj.data.bones}
        for frame, poses in frames.items():
            for bone in armature_obj.data.bones:
                kwargs = {}
                if bone.parent:
                    kwargs = {'parent_matrix': poses[bone.parent.name],
                              'parent_matrix_local': bone.parent.matrix_local}
                basis = bone.convert_local_to_pose(poses[bone.name], bone.matrix_local,
                                                   invert=True, **kwargs)
                loc, rot, scale = basis.decompose()
                reconstructed = bone.convert_local_to_pose(Matrix.LocRotScale(loc, rot, scale),
                                                            bone.matrix_local, **kwargs)
                error = max(abs(reconstructed[r][c] - poses[bone.name][r][c])
                            for r in range(4) for c in range(4))
                maximum_error = max(error, maximum_error)
                if error > .001:
                    raise NcImportError(f'{action.name}: bone {bone.name} needs unsupported shear at frame {frame} ({error:.6f})')
                tracks[bone.name][frame] = basis
        write_pose_matrix_keys(action, armature_obj, slot, tracks)
        remove_object_transform_fcurves(action)
    armature_obj.animation_data.action = None
    _set_pose_to_rest(armature_obj)
    context.view_layer.update()
    _log(f'Baked {len(samples)} source world-pose clips; maximum matrix error {maximum_error:.7f}')
    return maximum_error


def _restore_bone_flags(armature_obj, flags, *, force_game_location=False):
    for bone in armature_obj.data.bones:
        local_location, inherit_rotation, inherit_scale = flags[bone.name]
        bone.use_local_location = False if force_game_location else local_location
        bone.use_inherit_rotation = inherit_rotation
        bone.inherit_scale = inherit_scale


def _sample_action_pose_matrices(context, armature_obj, action, pose_slot, frames, flags):
    anim_data = armature_obj.animation_data or armature_obj.animation_data_create()
    _restore_bone_flags(armature_obj, flags)
    anim_data.action = action
    _assign_action_slot(anim_data, pose_slot)
    context.view_layer.update()

    samples = {}
    for frame in frames:
        _set_pose_to_rest(armature_obj)
        context.scene.frame_set(frame)
        context.view_layer.update()
        samples[frame] = {
            pose_bone.name: pose_bone.matrix.copy()
            for pose_bone in armature_obj.pose.bones
        }
    return samples


def _max_game_semantics_translation_error(context, armature_obj, action, pose_slot, frames, flags, source_samples):
    anim_data = armature_obj.animation_data or armature_obj.animation_data_create()
    _restore_bone_flags(armature_obj, flags, force_game_location=True)
    anim_data.action = action
    _assign_action_slot(anim_data, pose_slot)
    context.view_layer.update()

    max_error = 0.0
    max_bone = None
    max_frame = None
    for frame in frames:
        _set_pose_to_rest(armature_obj)
        context.scene.frame_set(frame)
        context.view_layer.update()
        frame_samples = source_samples[frame]
        for pose_bone in armature_obj.pose.bones:
            source_matrix = frame_samples.get(pose_bone.name)
            if source_matrix is None:
                continue
            error = (pose_bone.matrix.translation - source_matrix.translation).length
            if error > max_error:
                max_error = error
                max_bone = pose_bone.name
                max_frame = frame
    return max_error, max_bone, max_frame


def _basis_tracks_for_game_semantics(context, armature_obj, frames, flags, source_samples, depth_groups):
    _restore_bone_flags(armature_obj, flags, force_game_location=True)
    anim_data = armature_obj.animation_data or armature_obj.animation_data_create()
    anim_data.action = None
    context.view_layer.update()

    tracks = {pose_bone.name: {} for pose_bone in armature_obj.pose.bones}
    for frame in frames:
        _set_pose_to_rest(armature_obj)
        context.view_layer.update()
        frame_samples = source_samples[frame]
        for depth_group in depth_groups:
            assigned = []
            for bone in depth_group:
                pose_bone = armature_obj.pose.bones.get(bone.name)
                if pose_bone is None or bone.name not in frame_samples:
                    continue
                pose_bone.matrix = frame_samples[bone.name]
                assigned.append(pose_bone)
            context.view_layer.update()
            for pose_bone in assigned:
                tracks[pose_bone.name][frame] = pose_bone.matrix_basis.copy()
    return tracks


def _bake_actions_to_game_location_semantics(context, armature_obj, actions):
    """Bake only NC actions that diverge under DSTS bone-location semantics.

    Blender's glTF importer authors actions for ``use_local_location=True``.
    Native DSTS armatures evaluate pose locations with that flag disabled. Most
    NC clips survive that difference, but models such as x133 visibly drift or
    misplace weapons when the raw action is exported. For each action, sample the
    correct visible pose, probe how far the raw action moves under DSTS flags,
    and rewrite only clips whose bone translations meaningfully diverge.
    """
    bones = list(armature_obj.data.bones)
    if not any(bone.use_local_location for bone in bones):
        return 0, 0

    anim_data = armature_obj.animation_data or armature_obj.animation_data_create()
    previous_action = anim_data.action
    previous_frame = context.scene.frame_current
    previous_slot = getattr(anim_data, "action_slot", None)
    original_flags = {
        bone.name: (
            bone.use_local_location,
            bone.use_inherit_rotation,
            bone.inherit_scale,
        )
        for bone in bones
    }
    depth_groups = _bone_depth_groups(armature_obj)
    threshold = 0.005

    checked = 0
    converted = 0
    try:
        for action in actions:
            pose_slot, pose_fcurves = _pose_slot_and_fcurves(action)
            if not pose_fcurves:
                continue

            frame_start, frame_end = _action_frame_span(action)
            frames = list(range(frame_start, frame_end + 1))
            if not frames:
                continue

            checked += 1
            source_samples = _sample_action_pose_matrices(
                context, armature_obj, action, pose_slot, frames, original_flags,
            )
            max_error, max_bone, max_frame = _max_game_semantics_translation_error(
                context, armature_obj, action, pose_slot, frames, original_flags, source_samples,
            )
            if max_error <= threshold:
                _log(
                    f"  {action.name}: DSTS location probe OK "
                    f"(max {max_error:.6f})"
                )
                continue

            tracks = _basis_tracks_for_game_semantics(
                context, armature_obj, frames, original_flags, source_samples, depth_groups,
            )
            write_pose_matrix_keys(action, armature_obj, pose_slot, tracks)
            converted += 1
            _log(
                f"  {action.name}: baked DSTS location semantics "
                f"(max raw drift {max_error:.6f} on {max_bone} frame {max_frame})"
            )

        _restore_bone_flags(armature_obj, original_flags, force_game_location=True)
        _set_pose_to_rest(armature_obj)
        context.view_layer.update()
    finally:
        anim_data.action = previous_action
        if previous_slot is not None and hasattr(anim_data, "action_slot"):
            try:
                anim_data.action_slot = previous_slot
            except Exception:
                pass
        context.scene.frame_set(previous_frame)
        context.view_layer.update()

    return converted, checked


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _select_only(context, objs, active=None):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objs:
        obj.select_set(True)
    context.view_layer.objects.active = active or (objs[0] if objs else None)


def _find_target_armature(new_objects):
    armatures = [o for o in new_objects if o.type == 'ARMATURE']
    best = None
    best_meshes = []
    best_used_fallback = False

    def skinned_meshes(armature):
        meshes = []
        for obj in new_objects:
            if obj.type != 'MESH':
                continue
            if any(m.type == 'ARMATURE' and m.object == armature for m in obj.modifiers):
                meshes.append(obj)
        return meshes

    for armature in armatures:
        skinned = skinned_meshes(armature)
        lod0 = [obj for obj in skinned if _base_name(obj.name).lower().endswith("_lod0")]
        body = [obj for obj in skinned if _base_name(obj.name).lower().startswith("body")]
        candidates = lod0 or body or skinned
        if not candidates:
            continue
        used_fallback = not bool(lod0)
        score = (
            0 if used_fallback else 1,
            len(candidates),
            sum(len(obj.data.vertices) for obj in candidates),
            len(armature.data.bones),
        )
        best_score = (
            -1 if best is None or best_used_fallback else 0,
            len(best_meshes),
            sum(len(obj.data.vertices) for obj in best_meshes),
            len(best.data.bones) if best else 0,
        )
        if best is None or score > best_score:
            best, best_meshes = armature, candidates
            best_used_fallback = used_fallback
    if best is None:
        raise NcImportError(
            "No armature with skinned body meshes found in the glb "
            "(is this an nc-extractor model.glb?)"
        )
    if best_used_fallback:
        _log(
            "no skinned *_lod0 meshes found; using skinned body mesh fallback "
            f"({', '.join(obj.name for obj in best_meshes)})"
        )
    best_meshes.sort(key=lambda o: _base_name(o.name))
    return best, best_meshes


def _import_donor(context, donor_path):
    """Import the donor .geom, harvest materials/flags/metadata, delete its objects.

    Returns (collection, body_infos, outline_info) where each info is a dict
    with 'material' and 'flags'.
    """
    pre_actions = set(bpy.data.actions)
    donor_collection = import_geom(context, donor_path, None)
    donor_meshes = [o for o in donor_collection.objects if o.type == 'MESH']
    if not donor_meshes:
        raise NcImportError(f"Donor geom has no meshes: {donor_path}")

    def mesh_info(obj):
        material = obj.data.materials[0] if obj.data.materials else None
        flags = {key: bool(obj.data.get(key, key == "DSTS_flag_0")) for key in _FLAG_KEYS}
        return {"material": material, "flags": flags, "name": obj.name}

    body_infos = []
    outline_info = None
    for obj in donor_meshes:
        info = mesh_info(obj)
        material_name = info["material"].name if info["material"] else ""
        if material_name.startswith(_OUTLINE_MATERIAL) or _base_name(obj.name) == "outline":
            if outline_info is None:
                outline_info = info
        else:
            body_infos.append(info)

    if not body_infos:
        raise NcImportError(f"Donor geom has no body materials: {donor_path}")

    # Materials must survive donor mesh deletion until they are assigned.
    for info in body_infos + ([outline_info] if outline_info else []):
        if info["material"]:
            info["material"].use_fake_user = True

    # Delete donor objects (meshes + armature); keep the collection + metadata.
    for obj in list(donor_collection.objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Armature):
            bpy.data.armatures.remove(data)

    # Donor import auto-loads the donor's .anim clips; those actions target the
    # (now deleted) donor armature and must not leak into the NC scene.
    for action in [a for a in bpy.data.actions if a not in pre_actions]:
        bpy.data.actions.remove(action)

    return donor_collection, body_infos, outline_info


def _build_new_armature(context, old_armature, root_name, root_rest, alignment):
    """Create the export armature: root bone first, then the NC bones re-seated.

    Bone order is root + the old armature's bone order (stable topological sort
    so parents always precede children); rests are ``alignment @ old rest`` and
    the root gets ``root_rest``. Returns the new armature object.
    """
    old_bones = list(old_armature.data.bones)
    by_name = {b.name: b for b in old_bones}

    ordered = []
    seen = set()

    def visit(bone):
        if bone.name in seen:
            return
        if bone.parent is not None:
            visit(bone.parent)
        seen.add(bone.name)
        ordered.append(bone)

    for bone in old_bones:
        visit(bone)

    lengths = [b.length for b in old_bones if b.length > 1e-6]
    median_length = sorted(lengths)[len(lengths) // 2] if lengths else 0.1

    armature_data = bpy.data.armatures.new("Skeleton")
    armature_obj = bpy.data.objects.new("Geom", armature_data)
    context.scene.collection.objects.link(armature_obj)
    _select_only(context, [armature_obj], armature_obj)
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature_data.edit_bones
    root_edit = None
    if root_name:
        root_edit = edit_bones.new(root_name)
        root_edit.head = (0.0, 0.0, 0.0)
        root_edit.tail = (0.0, 0.0, median_length)
        root_edit.matrix = root_rest.copy()

    created = {}
    for bone in ordered:
        edit_bone = edit_bones.new(bone.name)
        edit_bone.head = (0.0, 0.0, 0.0)
        edit_bone.tail = (0.0, 0.0, max(bone.length, 1e-3))
        edit_bone.matrix = alignment @ bone.matrix_local
        if bone.parent is not None:
            edit_bone.parent = created[bone.parent.name]
        elif root_edit is not None:
            edit_bone.parent = root_edit
        created[bone.name] = edit_bone

    bpy.ops.object.mode_set(mode='OBJECT')

    # Preserve the pose-channel semantics the glTF F-Curves were authored for.
    for bone in ordered:
        new_bone = armature_data.bones.get(bone.name)
        old_bone = by_name[bone.name]
        if new_bone is None:
            continue
        new_bone.use_local_location = old_bone.use_local_location
        new_bone.use_inherit_rotation = old_bone.use_inherit_rotation
        new_bone.inherit_scale = old_bone.inherit_scale
        new_bone.use_deform = old_bone.use_deform

    return armature_obj


def _bake_mesh_to_world(context, obj, alignment):
    """Bake ``alignment`` into the mesh vertices, leaving an identity transform."""
    for modifier in list(obj.modifiers):
        obj.modifiers.remove(modifier)
    if obj.parent is not None:
        obj.parent = None
    obj.matrix_world = alignment.copy()
    if obj.data.shape_keys:
        _log(f"{obj.name}: has shape keys; using Mesh.transform fallback")
        obj.data.transform(alignment, shape_keys=True)
        obj.matrix_world = Matrix.Identity(4)
    else:
        _select_only(context, [obj], obj)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if not _matrix_is_identity(obj.matrix_world):
        raise NcImportError(f"{obj.name}: object transform not identity after bake")


def _ensure_white_color_attribute(mesh_data, name="Color"):
    """Create (or overwrite) a per-corner byte color attribute, all white.

    The .geom exporter reads ``mesh.color_attributes['Color']`` per loop; the
    DSTS outline shader uses this color, and zeros make the outline shell
    swallow the model (the in-game black-model bug).
    """
    existing = mesh_data.color_attributes.get(name)
    if existing is not None:
        mesh_data.color_attributes.remove(existing)
    layer = mesh_data.color_attributes.new(name=name, type='BYTE_COLOR', domain='CORNER')
    layer.data.foreach_set("color", [1.0] * (len(mesh_data.loops) * 4))
    return layer


def _build_outline_mesh(context, body_objects, armature_obj, outline_info, collection):
    duplicates = []
    for source in body_objects:
        duplicate = source.copy()
        duplicate.data = source.data.copy()
        collection.objects.link(duplicate)
        duplicates.append(duplicate)
    _select_only(context, duplicates, duplicates[0])
    if len(duplicates) > 1:
        bpy.ops.object.join()
    outline = duplicates[0]
    outline.name = "outline"
    outline.data.name = "outline"

    outline.data.materials.clear()
    outline.data.materials.append(outline_info["material"])
    for key, value in outline_info["flags"].items():
        outline.data[key] = value

    outline.parent = armature_obj
    outline.matrix_parent_inverse = Matrix.Identity(4)
    for modifier in list(outline.modifiers):
        outline.modifiers.remove(modifier)
    modifier = outline.modifiers.new(name="Armature", type='ARMATURE')
    modifier.object = armature_obj

    _ensure_white_color_attribute(outline.data)
    return outline


def import_nc_model(context, filepath, donor_geom_path, build_outline=True):
    from .scene_state import snapshot, rollback
    before = snapshot()
    fps, fps_base = context.scene.render.fps, context.scene.render.fps_base
    active = context.view_layer.objects.active
    selected = list(context.selected_objects)
    try:
        return _import_nc_model(context, filepath, donor_geom_path, build_outline, before)
    except Exception:
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        rollback(before)
        context.scene.render.fps, context.scene.render.fps_base = fps, fps_base
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
        raise


def _import_nc_model(context, filepath, donor_geom_path, build_outline, before):
    """Import an NC model.glb into an export-ready Geom collection.

    Returns (collection, armature_object, notes) where notes is a list of
    warning strings for the operator to report.
    """
    from .scene_state import purge_new_orphans
    notes = []
    if not os.path.isfile(filepath):
        raise NcImportError(f"File not found: {filepath}")
    if not donor_geom_path or not os.path.isfile(donor_geom_path):
        raise NcImportError(f"Donor .geom not found: {donor_geom_path}")

    if context.object and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    context.view_layer.active_layer_collection = context.view_layer.layer_collection

    # NC clips are sampled at 30 fps; align scene fps so glb keys land on
    # integer frames (the .anim exporter truncates key frames to ints).
    if context.scene.render.fps != 30:
        context.scene.render.fps = 30
        context.scene.render.fps_base = 1.0
        notes.append("Scene FPS set to 30 to match NC animation sampling")

    gltf, binary = read_glb_chunks(filepath)
    gltf, binary, baked_samplers = _bake_cubicspline_samplers_for_blender(
        gltf, binary, float(context.scene.render.fps or 30)
    )
    if baked_samplers:
        _log(f"baked {baked_samplers} glTF CUBICSPLINE sampler(s) for Blender")

    _log(f"=== Import glb: {filepath}")

    def _run_gltf_import(guess_original_bind_pose=None):
        import_filepath = filepath
        temp_glb_path = None
        if baked_samplers:
            handle = tempfile.NamedTemporaryFile(
                prefix="dsts_nc_blender_", suffix=".glb", delete=False
            )
            temp_glb_path = handle.name
            handle.close()
            write_glb_chunks(temp_glb_path, gltf, binary)
            import_filepath = temp_glb_path
        pre_objects = set(bpy.data.objects)
        pre_actions = set(bpy.data.actions)
        try:
            kwargs = {"filepath": import_filepath}
            if guess_original_bind_pose is not None:
                kwargs["guess_original_bind_pose"] = guess_original_bind_pose
            result = bpy.ops.import_scene.gltf(**kwargs)
            if result != {'FINISHED'}:
                raise NcImportError(f"glTF import returned {result}")
        finally:
            if temp_glb_path:
                try:
                    os.remove(temp_glb_path)
                except OSError:
                    pass
        return (
            [o for o in bpy.data.objects if o not in pre_objects],
            [a for a in bpy.data.actions if a not in pre_actions],
        )

    def _discard_gltf_import(objects, actions):
        for obj in list(objects):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass
        for action in list(actions):
            try:
                bpy.data.actions.remove(action)
            except (ReferenceError, RuntimeError):
                pass
        purge_new_orphans(before, skip_actions=True)

    new_objects, imported_actions = _run_gltf_import()

    def _select_import_state(objects, actions):
        old_armature, body_objects = _find_target_armature(objects)
        bone_names = [b.name for b in old_armature.data.bones]
        _log(f"armature '{old_armature.name}': {len(bone_names)} bones, "
             f"{len(body_objects)} lod0 body meshes, {len(actions)} actions")

        current_heads = {b.name: b.matrix_local.translation.copy() for b in old_armature.data.bones}
        bind_worlds, selected_joint_indices, bind_selection = _select_bind_worlds_for_armature(
            gltf, binary, current_heads
        )
        if not bind_worlds:
            raise NcImportError("glb has no skinned joints (no inverse bind matrices)")
        _log(
            "selected skin "
            f"{bind_selection['skin']} for bind pose "
            f"({bind_selection['shared']} shared bone(s), "
            f"fit {bind_selection['error']:.6f}, "
            f"{bind_selection['skins']} skin(s) in glb)"
        )

        missing_root_index, missing_root = find_missing_root_node(gltf, selected_joint_indices)

        if missing_root and missing_root in old_armature.data.bones:
            _log(f"root node '{missing_root}' already imported as a bone")
            missing_root_index, missing_root = None, None
        return (
            old_armature,
            body_objects,
            current_heads,
            bind_worlds,
            selected_joint_indices,
            missing_root_index,
            missing_root,
        )

    (
        old_armature,
        body_objects,
        current_heads,
        bind_worlds,
        selected_joint_indices,
        missing_root_index,
        missing_root,
    ) = _select_import_state(new_objects, imported_actions)

    if missing_root is None:
        notes.append("No missing root node detected; skeleton imported as-is")

    # --- rigid alignment: imported rests -> glb bind worlds --------------
    bind_heads = {name: m.translation.copy() for name, m in bind_worlds.items()
                  if name in current_heads}
    if len(bind_heads) < max(3, len(current_heads) // 2):
        raise NcImportError(
            f"Only {len(bind_heads)} of {len(current_heads)} bones have glb bind "
            "matrices; cannot derive the alignment rotation"
        )
    alignment, fit_error = fit_alignment_rotation(current_heads, bind_heads)
    _log(f"alignment fit over {len(bind_heads)} joints: max head error {fit_error:.6f}")
    if alignment is None or fit_error > _ALIGNMENT_TOLERANCE:
        node_fit_count, node_fit_error = _node_bind_alignment_fit(
            gltf, selected_joint_indices, bind_heads
        )
        _log(
            f"glb node-world -> bind-world retry fit over {node_fit_count} joints: "
            f"max error {node_fit_error:.6f}"
        )
        if node_fit_error <= _ALIGNMENT_TOLERANCE:
            _log("retrying glTF import with guess_original_bind_pose=False")
            _discard_gltf_import(new_objects, imported_actions)
            new_objects, imported_actions = _run_gltf_import(guess_original_bind_pose=False)
            (
                old_armature,
                body_objects,
                current_heads,
                bind_worlds,
                selected_joint_indices,
                missing_root_index,
                missing_root,
            ) = _select_import_state(new_objects, imported_actions)
            if missing_root is None and "No missing root node detected; skeleton imported as-is" not in notes:
                notes.append("No missing root node detected; skeleton imported as-is")
            bind_heads = {name: m.translation.copy() for name, m in bind_worlds.items()
                          if name in current_heads}
            alignment, fit_error = fit_alignment_rotation(current_heads, bind_heads)
            _log(f"retry alignment fit over {len(bind_heads)} joints: max head error {fit_error:.6f}")
            if alignment is not None and fit_error <= _ALIGNMENT_TOLERANCE:
                notes.append(
                    "Retried glb import with Blender original-bind guessing disabled "
                    "because helper bones distorted the first rest pose"
                )
        if alignment is None or fit_error > _ALIGNMENT_TOLERANCE:
            raise NcImportError(
                f"No axis-aligned rotation maps the imported rest pose onto the glb "
                f"bind pose (best error {fit_error:.6f})"
            )
    elif fit_error > _STRICT_ALIGNMENT_TOLERANCE:
        _log(
            f"accepted small bind-pose alignment discrepancy "
            f"({fit_error:.6f} <= {_ALIGNMENT_TOLERANCE:.6f})"
        )
    # Scene hygiene: stop the importer's last-assigned action from evaluating.
    old_anim_data = old_armature.animation_data
    if old_anim_data:
        old_anim_data.action = None
        for track in old_anim_data.nla_tracks:
            track.mute = True
    context.view_layer.update()

    # --- root rest, derived from the glb node TRS ------------------------
    # The imported bone rests equal C @ node_bind_world for a constant axis
    # rotation the importer applied; the FINAL rests are the IBM bind worlds.
    # Fit the node-world -> bind-world conversion over the joints and apply it
    # to the missing root node's static world. (The armature OBJECT transform
    # cannot be used here: Blender leaves it at the last evaluated frame.)
    root_rest = None
    node_conversion = None
    if missing_root is not None:
        joint_indices = set(selected_joint_indices)
        node_worlds = _glb_node_worlds(gltf, joint_indices | {missing_root_index})
        glb_nodes = gltf.get("nodes", [])
        node_heads = {}
        for joint in joint_indices:
            name = glb_nodes[joint].get("name")
            if name in bind_heads:
                node_heads[name] = node_worlds[joint].translation.copy()
        node_conversion, node_fit_error = fit_alignment_rotation(node_heads, bind_heads)
        _log(f"node-world -> bind-world fit over {len(node_heads)} joints: "
             f"max error {node_fit_error:.6f}")
        if node_conversion is None or node_fit_error > _ALIGNMENT_TOLERANCE:
            raise NcImportError(
                f"glb node hierarchy does not coincide with the bind pose "
                f"(best fit error {node_fit_error:.6f}); cannot place the root bone"
            )
        # The glTF importer's object-transform conversion is the fixed Y-up ->
        # Z-up rotation; the node-derived root rest is only valid when the glb
        # node hierarchy uses the same convention (true for nc-extractor
        # files). Otherwise fall back to a rest equal to the alignment
        # rotation (origin-anchored), which is always self-consistent.
        yup_to_zup = Matrix.Rotation(math.pi / 2.0, 4, 'X')
        conversion_diff = max(
            abs(node_conversion[r][c] - yup_to_zup[r][c])
            for r in range(4) for c in range(4)
        )
        if conversion_diff <= 1e-3:
            root_rest = node_conversion @ node_worlds[missing_root_index]
        else:
            root_rest = alignment.copy()
            notes.append(
                "glb node convention differs from the glTF importer's Y-up "
                "conversion; root bone rest anchored at the origin instead of "
                "the root node TRS"
            )
        if abs(root_rest.to_3x3().determinant() - 1.0) > 1e-3:
            notes.append("Root node transform is not a pure rotation; "
                         "root rest may be skewed")
        _log(f"root rest translation: {tuple(round(v, 5) for v in root_rest.translation)}")

    # Preserve source world poses before changing the bind basis or hierarchy.
    parent_world = old_armature.parent.matrix_world.copy() if old_armature.parent else Matrix.Identity(4)
    parent_inverse = old_armature.matrix_parent_inverse.copy()
    if missing_root:
        source_static_world = Matrix.Rotation(math.pi / 2, 4, 'X') @ node_worlds[missing_root_index]
        source_static_basis = (parent_world @ parent_inverse).inverted() @ source_static_world
    else:
        source_static_basis = old_armature.matrix_basis.copy()
    source_world_poses = _capture_source_world_poses(
        context, old_armature, imported_actions, missing_root, source_static_basis)

    # --- donor materials / flags / metadata ------------------------------
    _log(f"=== Import donor geom: {donor_geom_path}")
    collection, body_infos, outline_info = _import_donor(context, donor_geom_path)
    missing_keys = [k for k in _GEOM_METADATA_KEYS if k not in collection]
    if missing_keys:
        raise NcImportError(f"Donor collection missing metadata keys: {missing_keys}")
    if build_outline and outline_info is None:
        notes.append(f"Donor geom has no {_OUTLINE_MATERIAL} material; outline skipped")

    # --- build export armature -------------------------------------------
    new_armature = _build_new_armature(context, old_armature, missing_root, root_rest, alignment)
    new_bone_names = [b.name for b in new_armature.data.bones]
    _log(f"new armature: {len(new_bone_names)} bones"
         + (f", root '{new_bone_names[0]}'" if missing_root else ""))

    # Rebuild all local pose curves from the original scene's world poses.
    _bake_source_world_poses(context, new_armature, source_world_poses)

    # --- rebuild NLA tracks on the new armature --------------------------
    from .blender_compat import create_nla_track
    anim_data = new_armature.animation_data or new_armature.animation_data_create()
    source_clip_names = {item.get('name', '') for item in gltf.get('animations', [])}
    for action in imported_actions:
        pose_slot, pose_fcurves = _pose_slot_and_fcurves(action)
        if not pose_fcurves:
            continue
        anim_data.action = action
        if pose_slot is not None and hasattr(anim_data, "action_slot"):
            try:
                anim_data.action_slot = pose_slot
            except Exception:
                pass
        start, _end = _action_frame_span(action)
        clip_name = action.name if action.name in source_clip_names else _base_name(action.name)
        action['DSTS_source_clip'] = clip_name
        _track, strip = create_nla_track(new_armature, action, clip_name, max(start, 1),
                                         scale=1.0, blend_type='REPLACE')
        try:
            strip.extrapolation = 'NOTHING'
        except Exception:
            pass
    anim_data.action = None

    # --- bake meshes to file-consistent space and rebind ------------------
    for obj in body_objects:
        _bake_mesh_to_world(context, obj, alignment)

    min_z = min(v.co.z for obj in body_objects for v in obj.data.vertices)
    max_z = max(v.co.z for obj in body_objects for v in obj.data.vertices)
    _log(f"baked mesh Z range: [{min_z:.4f}, {max_z:.4f}] (expect grounded ~0)")
    if not (-0.25 < min_z < 0.25):
        notes.append(f"Baked meshes are not grounded (min Z {min_z:.3f}); "
                     "check the model's bind pose")

    material_use_counts = {}
    for index, obj in enumerate(body_objects):
        info = body_infos[min(index, len(body_infos) - 1)]
        preferred_atlas = f"a{index + 1:02d}"
        material_obj = info["material"]
        if material_obj is not None:
            material_key = material_obj.name
            material_use_count = material_use_counts.get(material_key, 0)
            material_use_counts[material_key] = material_use_count + 1
            if material_use_count:
                info = dict(info)
                material_obj = material_obj.copy()
                material_obj.name = f"{material_key}_{preferred_atlas}"
                info["material"] = material_obj
            material_obj["DSTS_nc_atlas"] = preferred_atlas

        if not obj.data.uv_layers:
            raise NcImportError(f"{obj.name}: mesh has no UV layers")
        if obj.data.uv_layers[0].name != "uv1":
            obj.data.uv_layers[0].name = "uv1"

        obj.data.materials.clear()
        obj.data.materials.append(info["material"])
        obj["DSTS_nc_atlas"] = preferred_atlas
        obj.data["DSTS_nc_atlas"] = preferred_atlas
        for key, value in info["flags"].items():
            obj.data[key] = value

        unknown_groups = {vg.name for vg in obj.vertex_groups} - set(new_bone_names)
        if unknown_groups:
            raise NcImportError(
                f"{obj.name}: vertex groups missing from skeleton: "
                f"{sorted(unknown_groups)[:8]}"
            )

        obj.parent = new_armature
        obj.matrix_parent_inverse = Matrix.Identity(4)
        modifier = obj.modifiers.new(name="Armature", type='ARMATURE')
        modifier.object = new_armature

        for coll in list(obj.users_collection):
            coll.objects.unlink(obj)
        collection.objects.link(obj)
        set_flags = [i for i in range(8) if info["flags"][f"DSTS_flag_{i}"]]
        _log(f"  {obj.name}: material={info['material'].name} atlas={preferred_atlas} flags_set={set_flags} "
             f"verts={len(obj.data.vertices)}")

    # --- outline ----------------------------------------------------------
    if build_outline and outline_info is not None:
        outline = _build_outline_mesh(context, body_objects, new_armature, outline_info, collection)
        _log(f"  outline: material={outline_info['material'].name} "
             f"verts={len(outline.data.vertices)} (white vertex color)")

    # --- clean up the rest of the glb import ------------------------------
    keep = set(body_objects) | {new_armature}
    for obj in new_objects:
        if obj in keep:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass
    purge_new_orphans(before)

    # --- final placement + bookkeeping -------------------------------------
    for coll in list(new_armature.users_collection):
        if coll is not collection:
            coll.objects.unlink(new_armature)
    if new_armature.name not in collection.objects:
        collection.objects.link(new_armature)

    collection["DSTS_material_donor_geom"] = bpy.path.abspath(donor_geom_path)
    collection["DSTS_source_nc_glb"] = bpy.path.abspath(filepath)

    # release the fake users taken to protect donor materials during surgery,
    # then purge again so unassigned donor materials (e.g. eyes) do not linger
    for info in body_infos + ([outline_info] if outline_info else []):
        if info["material"]:
            info["material"].use_fake_user = False
    purge_new_orphans(before)

    # sanity: exporter preconditions
    for obj in collection.objects:
        if obj.type in {'MESH', 'ARMATURE'} and not _matrix_is_identity(obj.matrix_basis):
            raise NcImportError(f"{obj.name}: non-identity object transform after import")
    posed = [pb.name for pb in new_armature.pose.bones if not _matrix_is_identity(pb.matrix_basis)]
    if posed:
        raise NcImportError(f"pose bones not at rest after import: {posed[:8]}")

    _log("=== NC import complete")
    return collection, new_armature, notes
