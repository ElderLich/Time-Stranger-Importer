import bpy
import numpy as np
from mathutils import Matrix, Vector
import math
import json
from . import dsts_formats
from .. import data

# ----------------------------------------------------------
# Optimization Helpers
# ----------------------------------------------------------

def get_np_dtype(dtype_str):
    """Maps the C++ string dtype to numpy dtype."""
    mapping = {
        "uByte": np.uint8,
        "sByte": np.int8,
        "uShort": np.uint16,
        "sShort": np.int16,
        "uInt": np.uint32,
        "sInt": np.int32,
        "float": np.float32,
        "float16": np.float16,
    }
    return mapping.get(dtype_str, np.float32)

def extract_attribute(raw_bytes, stride, num_verts, attr):
    """
    Extracts a specific attribute from the interleaved binary buffer.
    Uses attr.count to determine dimensionality (1, 2, 3, 4).
    """
    dtype = get_np_dtype(attr.dtype)
    
    # Calculate total size of this attribute (e.g., 3 floats * 4 bytes = 12 bytes)
    component_count = attr.count 
    attr_size = np.dtype(dtype).itemsize * component_count
    
    # Create a view of the whole buffer as uint8
    byte_view = np.frombuffer(raw_bytes, dtype=np.uint8)
    
    # Reshape to (num_verts, stride) to isolate vertices
    vertex_data = byte_view.reshape(num_verts, stride)
    
    # Slice the columns we need
    attr_bytes = vertex_data[:, attr.offset : attr.offset + attr_size]
    
    # Convert bytes to actual values
    values = np.frombuffer(attr_bytes.tobytes(), dtype=dtype)
    
    # Reshape to (Vertices, Components) if > 1 component
    if component_count > 1:
        values = values.reshape(num_verts, component_count)
        
    return values

# ----------------------------------------------------------
# Convert Mesh → Blender Mesh (Optimized)
# ----------------------------------------------------------
def import_mesh_object(bl_mesh: dsts_formats.Mesh, armature_obj, materials_dict, collection, coord_transform=Matrix.Rotation(math.radians(90), 4, 'X'), bone_name_map=None):
    
    attributes, stride, packed = bl_mesh.pack_vertices()
    num_verts = len(packed) // stride
    attr_map = {a.atype: a for a in attributes}

    # --- GEOMETRY ---
    positions = extract_attribute(packed, stride, num_verts, attr_map["position"])
    if positions.shape[1] == 4:
        positions = positions[:, :3]
    
    if coord_transform:
        if isinstance(coord_transform, Matrix):
            mat = np.array(coord_transform)
            positions = positions @ mat[:3, :3].T + mat[:3, 3]
        else:
            vecs = [coord_transform(Vector(p)) for p in positions]
            positions = np.array(vecs, dtype=np.float32)

    flat_indices = bl_mesh.get_indices()
    faces = np.array(flat_indices, dtype=np.int32).reshape(-1, 3)

    mesh_data = bpy.data.meshes.new(bl_mesh.name)
    mesh_data.from_pydata(positions, [], faces)
    
    mesh_data.update(calc_edges=True)

    # --- UVs ---
    if faces.size > 0:
        loop_v_idxs = np.zeros(len(mesh_data.loops), dtype=np.int32)
        mesh_data.loops.foreach_get("vertex_index", loop_v_idxs)
        
        for uv_name in ["uv1", "uv2", "uv3"]:
            if uv_name in attr_map:
                uv_data = extract_attribute(packed, stride, num_verts, attr_map[uv_name])
                uv_layer = mesh_data.uv_layers.new(name=uv_name)
                uv_layer.data.foreach_set("uv", uv_data[loop_v_idxs].reshape(-1))

    # --- VERTEX COLORS (Color Attributes) ---
    if faces.size > 0:
        # The loop_v_idxs array is already retrieved above for UVs, 
        # but we check again for robustness in case UVs section is skipped.
        if 'loop_v_idxs' not in locals():
             loop_v_idxs = np.zeros(len(mesh_data.loops), dtype=np.int32)
             mesh_data.loops.foreach_get("vertex_index", loop_v_idxs)

        # Assuming the vertex color attribute is named 'color' in the source data.
        if "color" in attr_map:
            color_attr = attr_map["color"]
            color_data = extract_attribute(packed, stride, num_verts, color_attr)
            
            # Ensure color data has 4 components (RGBA) for Blender, padding with alpha=1.0 if necessary
            # Color attributes in Blender are typically 4-component (RGBA) float or byte.
            if color_attr.count == 3:
                # Pad with 1.0 alpha
                alpha_channel = np.ones((num_verts, 1), dtype=color_data.dtype)
                color_data = np.hstack((color_data, alpha_channel))
            elif color_attr.count < 3:
                print(f"Warning: Vertex color attribute 'color' has less than 3 components ({color_attr.count}), skipping.")
                color_data = None

            # Normalize uByte/uShort data to float if necessary for Blender's color layer
            if color_data is not None:
                if color_data.dtype == np.uint8:
                    color_data = color_data.astype(np.float32) / 255.0
                elif color_data.dtype == np.uint16:
                    color_data = color_data.astype(np.float32) / 65535.0
                
                # Create a new color attribute on the mesh (Blender 3.x API)
                # Setting 'color' as the attribute name. 
                # type='FLOAT_COLOR' for float data (which we normalized to)
                color_layer = mesh_data.color_attributes.new(
                    name='Color', 
                    type='FLOAT_COLOR', 
                    domain='CORNER' # Color is typically per-face-corner (loop)
                )
                
                # The data is per vertex, but the color layer data is per loop (face corner).
                # We map the per-vertex data to the per-loop indices.
                # Blender expects a flattened array of RGBA values.
                loop_colors = color_data[loop_v_idxs].reshape(-1)

                # Set the data for the new color attribute
                color_layer.data.foreach_set("color", loop_colors)

    # --- NORMALS ---
    if "normal" in attr_map:
        normals = extract_attribute(packed, stride, num_verts, attr_map["normal"])
        
        if coord_transform and isinstance(coord_transform, Matrix):
            mat_rot = np.array(coord_transform)[:3, :3]
            normals = normals @ mat_rot.T
            norms = np.linalg.norm(normals, axis=1, keepdims=True)
            norms[norms == 0] = 1 
            normals /= norms
            
        # 1. MANDATORY: Set all faces to Smooth.
        # Without this, Blender treats faces as Flat, fighting the custom normals during deformation.
        mesh_data.polygons.foreach_set("use_smooth", [True] * len(mesh_data.polygons))
        
        # 2. Set the custom normals (These are loop/corner normals)
        mesh_data.normals_split_custom_set_from_vertices(normals)
        
        # 3. Validate the mesh
        # This ensures the custom normal data block is properly locked and index-matched.
        mesh_data.validate(clean_customdata=False)
        
        # 4. Optional: If you rely on Normal Maps, calculate tangents now
        if mesh_data.uv_layers:
            mesh_data.calc_tangents(uvmap=mesh_data.uv_layers[0].name)
    
    # --- OBJECT ---
    obj = bpy.data.objects.new(bl_mesh.name, mesh_data)
    collection.objects.link(obj)

    if bl_mesh.material and bl_mesh.material.name in materials_dict:
        obj.data.materials.append(materials_dict[bl_mesh.material.name])

    # --- WEIGHTS (Fixed for single-bone cases) ---
    if "index" in attr_map and "weight" in attr_map and armature_obj:
        # 1. Map Bones
        palette_map = {}
        missing_bones = []
        if bl_mesh.matrix_palette:
            for idx, bone_data in enumerate(bl_mesh.matrix_palette):
                original_name = bone_data.name
                blender_name = bone_name_map.get(original_name, original_name) if bone_name_map else original_name

                if blender_name in armature_obj.data.bones:
                    palette_map[idx] = blender_name
                    if blender_name not in obj.vertex_groups:
                        obj.vertex_groups.new(name=blender_name)
                elif original_name in armature_obj.data.bones:
                    palette_map[idx] = original_name
                    if original_name not in obj.vertex_groups:
                        obj.vertex_groups.new(name=original_name)
                else:
                    missing_bones.append(f"{original_name} (mapped to {blender_name})")

        if missing_bones:
            suffix = "..." if len(missing_bones) > 5 else ""
            print(f"[DSTS] Mesh '{bl_mesh.name}' - Missing bones: {missing_bones[:5]}{suffix}")

        # 2. Extract Data
        idx_data = extract_attribute(packed, stride, num_verts, attr_map["index"])
        wgt_data = extract_attribute(packed, stride, num_verts, attr_map["weight"])
        
        # Normalize if uByte
        if wgt_data.dtype == np.uint8:
            wgt_data = wgt_data.astype(np.float32) / 255.0

        # --- FIX: Ensure data is always 2D (N, Comp) ---
        # If count was 1, extract_attribute returned (N,), which causes the error
        if idx_data.ndim == 1:
            idx_data = idx_data.reshape(-1, 1)
        if wgt_data.ndim == 1:
            wgt_data = wgt_data.reshape(-1, 1)

        # 3. Flatten
        flat_weights = wgt_data.flatten()
        flat_indices = idx_data.flatten()
        
        # Now shape[1] is guaranteed to exist
        comp_count = idx_data.shape[1] 
        flat_v_ids = np.repeat(np.arange(num_verts), comp_count)

        # 4. Filter & Assign
        mask = flat_weights > 0.001
        
        valid_weights = flat_weights[mask]
        valid_bone_idxs = flat_indices[mask]
        valid_v_ids = flat_v_ids[mask]

        for i in range(len(valid_weights)):
            palette_idx = valid_bone_idxs[i]
            if palette_idx in palette_map:
                bone_name = palette_map[palette_idx]
                obj.vertex_groups[bone_name].add([int(valid_v_ids[i])], float(valid_weights[i]), 'ADD')
    
    # --- PARENTING ---
    if armature_obj:
        obj.parent = armature_obj
        mod = obj.modifiers.new(name="Armature", type='ARMATURE')
        mod.object = armature_obj

    # --- FLAGS ---
    mesh_data["DSTS_flag_0"] = bl_mesh.flag_0
    mesh_data["DSTS_flag_1"] = bl_mesh.flag_1
    mesh_data["DSTS_flag_2"] = bl_mesh.flag_2
    mesh_data["DSTS_flag_3"] = bl_mesh.flag_3
    mesh_data["DSTS_flag_4"] = bl_mesh.flag_4
    mesh_data["DSTS_flag_5"] = bl_mesh.flag_5
    mesh_data["DSTS_flag_6"] = bl_mesh.flag_6
    mesh_data["DSTS_flag_7"] = bl_mesh.flag_7

    return obj

def export_mesh_object(mesh_obj, skeleton = None, coord_transform = Matrix.Rotation(math.radians(-90), 4, 'X'),
                       *, split_palettes=False):
    mesh = mesh_obj.data
    if any(mod.type != 'ARMATURE' and (mod.show_viewport or mod.show_render) for mod in mesh_obj.modifiers):
        raise ValueError(f"Mesh '{mesh.name}' has unapplied modifiers; apply them before export")
    if mesh.shape_keys and any(abs(key.value) > 1e-7 for key in mesh.shape_keys.key_blocks):
        raise ValueError(f"Mesh '{mesh.name}' has active shape keys; bake them before export")
    if not mesh.vertices or not mesh.polygons:
        raise ValueError(f"Mesh '{mesh.name}' has no surface geometry")
    if any(p.material_index != 0 for p in mesh.polygons):
        raise ValueError(f"Mesh '{mesh.name}' uses several materials; split by material before export")
    mesh.calc_loop_triangles()
    loop_vertex = np.array([loop.vertex_index for loop in mesh.loops], dtype=np.int32)
    normals = np.array([normal.vector[:] for normal in mesh.corner_normals], dtype=np.float32)
    uv_arrays = {n: np.array([item.uv[:] for item in mesh.uv_layers[f"uv{n}"].data], dtype=np.float32)
                 for n in range(1, 4) if f"uv{n}" in mesh.uv_layers}
    colors = None
    if "Color" in mesh.color_attributes:
        attribute = mesh.color_attributes["Color"]
        raw_colors = np.array([item.color[:] for item in attribute.data], dtype=np.float32)
        colors = raw_colors[loop_vertex] if attribute.domain == 'POINT' else raw_colors
    tangents = None
    if 1 in uv_arrays:
        if any(len(p.vertices) > 4 for p in mesh.polygons):
            raise ValueError(f"Mesh '{mesh.name}' has n-gons; triangulate them before export")
        mesh.calc_tangents(uvmap="uv1")
        tangents = np.array([(*loop.tangent, loop.bitangent_sign) for loop in mesh.loops], dtype=np.float32)

    # GEOM has a single vertex index for every attribute. Blender has separate
    # corner normals/UVs/colors: split vertices only where those corners differ.
    columns = [loop_vertex[:, None], normals]
    columns.extend(uv_arrays.values())
    if colors is not None:
        columns.append(colors)
    if tangents is not None:
        columns.append(tangents)
    _, representatives, remap = np.unique(np.concatenate(columns, axis=1), axis=0,
                                           return_index=True, return_inverse=True)
    source_vertices = loop_vertex[representatives]
    vertex_count = len(representatives)
    if vertex_count > 0xFFFF:
        raise ValueError(f"Mesh '{mesh.name}' needs {vertex_count} seam-split vertices (limit 65535)")
    transform = np.array(coord_transform, dtype=np.float32)
    positions = np.array([v.co[:] for v in mesh.vertices], dtype=np.float32)[source_vertices]
    positions = positions @ transform[:3, :3].T + transform[:3, 3]

    def transform_directions(values):
        values = values @ transform[:3, :3].T
        lengths = np.linalg.norm(values, axis=1, keepdims=True)
        return values / np.maximum(lengths, 1e-20)

    mesh_out = dsts_formats.Mesh()
    mesh_out.set_vertex_count(vertex_count)
    mesh_out.set_position(positions.astype(np.float32))
    mesh_out.indices = remap[np.array([loop for tri in mesh.loop_triangles for loop in tri.loops])].astype(np.int32)
    mesh_out.primitive = 1
    mesh_out.set_normal(transform_directions(normals[representatives]).astype(np.float16))
    present_uv_channels = set(uv_arrays)
    uv1_coords = uv_arrays[1][representatives] if 1 in uv_arrays else None
    for n, values in uv_arrays.items():
        mesh_out.set_uv(n, values[representatives].astype(np.float32))
    if colors is not None:
        mesh_out.set_color(np.rint(np.clip(colors[representatives], 0, 1) * 255).astype(np.uint8))
    if tangents is not None:
        selected = tangents[representatives]
        selected[:, :3] = transform_directions(selected[:, :3])
        mesh_out.set_tangent(selected.astype(np.float16))

    vert_indices = None
    if skeleton is not None:
        mesh_out.matrix_palette = [b for b in skeleton.bones if b.name in mesh_obj.vertex_groups]
        name_to_id = {b.name: i for i, b in enumerate(mesh_out.matrix_palette)}
        group_to_id = {g.index: name_to_id[g.name] for g in mesh_obj.vertex_groups if g.name in name_to_id}
        weights_by_vertex = []
        for v in mesh.vertices:
            # Ignore non-bone groups BEFORE selecting the four strongest influences.
            weights = sorted(((group_to_id[g.group], g.weight) for g in v.groups
                              if g.group in group_to_id and g.weight > 0),
                             key=lambda item: -item[1])[:4]
            weights_by_vertex.append(weights)
        groups_per_vertex = max((len(weights_by_vertex[i]) for i in source_vertices), default=0)
        if not groups_per_vertex:
            raise ValueError(f"Mesh '{mesh.name}' has no weights referencing its armature")
        groups_per_vertex = max(groups_per_vertex, int(mesh.get("DSTS_min_skin_influences", 0)))
        vert_indices = np.zeros((vertex_count, groups_per_vertex), dtype=np.int32)
        vert_weights = np.zeros((vertex_count, groups_per_vertex), dtype=np.float32)
        for vi, source in enumerate(source_vertices):
            weights = weights_by_vertex[source]
            if not weights:
                raise ValueError(f"Mesh '{mesh.name}' vertex {source} has no bone weights")
            total = sum(weight for _, weight in weights)
            for gi, (index, weight) in enumerate(weights):
                vert_indices[vi, gi] = index
                vert_weights[vi, gi] = weight / total
        mesh_out.set_weight(vert_weights.astype(np.float16))

    # -- ATTRIBUTES --
    if not mesh.materials or mesh.materials[0] is None:
        raise ValueError(
            f"Mesh '{mesh.name}' has no material assigned. "
            "Use Borrow Material Setup to give it a donor material before exporting."
        )
    mat = mesh.materials[0]
    if not mat.node_tree:
        raise ValueError(
            f"Material '{mat.name}' has no node tree. "
            "Use Borrow Material Setup before exporting."
        )
    from .material import find_data_group
    g_node = find_data_group(mat)
    if g_node is None:
        raise ValueError(
            f"Material '{mat.name}' has no DSTS_Data node group. "
            "Use Borrow Material Setup before exporting."
        )
    data_node = next(
        (n for n in g_node.node_tree.nodes if type(n) == data.material_nodes.ShaderDataNode),
        None,
    )
    if data_node is None or data_node.bytes_per_vertex <= 0 or len(data_node.attributes) == 0:
        raise ValueError(
            f"Material '{mat.name}' has no DSTS vertex buffer layout. "
            "Use Borrow Material Setup again before exporting."
        )
    attrs = []
    for at in data_node.attributes:
        attribute = dsts_formats.MeshAttribute()
        attribute.count = at.count
        attribute.offset = at.offset
        attribute.atype = at.atype
        attribute.dtype = at.dtype
        attrs.append(attribute)

    mesh_out.mesh_attributes = attrs
    mesh_out.bytes_per_vertex = data_node.bytes_per_vertex

    # -- AUTO-UV: fill declared-but-missing uv channels from uv1 --
    # Overlay materials (e.g. chr050_f01/f02) declare a vertex layout with
    # uv1+uv2+uv3 (bytes_per_vertex 52), but custom claw/shield meshes often
    # carry ONLY uv1. Any uv channel the layout declares but the mesh never set
    # is packed as garbage in the vertex buffer, so the overlay shader samples
    # noise and the mesh loses its texture. Copy uv1's coordinates into every
    # declared-but-missing channel so the overlay samples a valid UV. This is
    # idempotent: channels the mesh already provides are left untouched.
    if uv1_coords is not None:
        for at in data_node.attributes:
            atype = str(at.atype)
            if not atype.startswith("uv"):
                continue
            try:
                n = int(atype[2:])
            except ValueError:
                continue
            if 1 <= n <= 3 and n not in present_uv_channels:
                mesh_out.set_uv(n, uv1_coords)
                present_uv_channels.add(n)

    # -- FLAGS --
    mesh_out.flag_0 = mesh.get("DSTS_flag_0", True)
    mesh_out.flag_1 = mesh.get("DSTS_flag_1", False)
    mesh_out.flag_2 = mesh.get("DSTS_flag_2", False)
    mesh_out.flag_3 = mesh.get("DSTS_flag_3", False)
    mesh_out.flag_4 = mesh.get("DSTS_flag_4", False)
    mesh_out.flag_5 = mesh.get("DSTS_flag_5", False)
    mesh_out.flag_6 = mesh.get("DSTS_flag_6", False)
    mesh_out.flag_7 = mesh.get("DSTS_flag_7", False)

    mesh_out.name = mesh.name

    from .skin_palette import split_skin_palette
    draws = split_skin_palette(mesh_out, vert_indices)
    if split_palettes:
        return draws
    if len(draws) != 1:
        raise ValueError(f"'{mesh.name}' needs multiple native skin palettes; request split_palettes=True")
    return draws[0]
