"""Inspect the local meshes of a rigid effect independently of its animated rig."""

import bpy
from mathutils import Vector

SOURCE_COLLECTION = 'DSTS_component_source_collection'
SOURCE_SCENE = 'DSTS_component_source_scene'


def is_rigid_effect(collection):
    return collection is not None and any(
        obj.type == 'ARMATURE' and obj.get('DSTS_rigid_scene') for obj in collection.objects)


def _inspection_material(source):
    from ..material import find_data_group
    # Show the color plate, including RGB where alpha or an animated tint would
    # make it invisible. Never attach this shader to the native mesh/material.
    group = find_data_group(source) if source else None
    sampler = next((node for node in group.node_tree.nodes if node.type == 'TEX_IMAGE'
                    and node.get('DSTS_parameter_id') == 24), None) if group else None
    mat = bpy.data.materials.new((source.name if source else "Untextured") + " • Components")
    mat.use_backface_culling = True
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    output = tree.nodes.new('ShaderNodeOutputMaterial')
    emission = tree.nodes.new('ShaderNodeEmission')
    tree.links.new(emission.outputs[0], output.inputs['Surface'])
    if sampler and sampler.image:
        texture = tree.nodes.new('ShaderNodeTexImage')
        texture.image = sampler.image
        texture.extension = sampler.extension
        texture.interpolation = sampler.interpolation
        tree.links.new(texture.outputs['Color'], emission.inputs['Color'])
        tree.nodes.active = texture
    return mat


def _build_scene(source_scene, collection, meshes):
    from ..scene_state import snapshot, rollback
    before = snapshot()
    name = collection.get('DSTS_rigid_effect_name', collection.name)
    preview = bpy.data.scenes.new(name + " • Rigid Effect Components")
    try:
        preview[SOURCE_COLLECTION] = collection
        preview[SOURCE_SCENE] = source_scene
        materials = {}
        for obj in meshes:
            mesh = obj.data.copy()
            label = obj.data.get('DSTS_external_mesh_name') or obj.parent_bone
            part = bpy.data.objects.new(label + " • Component", mesh)
            preview.collection.objects.link(part)
            mesh.materials.clear()
            source = obj.active_material
            if source not in materials:
                materials[source] = _inspection_material(source)
            mesh.materials.append(materials[source])
        return preview
    except Exception:
        bpy.data.scenes.remove(preview)
        rollback(before)
        raise


def _view_space(context, window):
    area = context.area
    if not area or area.type != 'VIEW_3D' or area not in window.screen.areas[:]:
        area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'),
                   key=lambda a: a.width * a.height, default=None)
    return area.spaces.active if area else None


# View orientation and shading belong to the window's workspace, shared by scenes.
# Keep the caller's view for the Return button. Blender invalidates these pointers
# when reopening a file, so restoration is limited to the same live space.
_previous_views = {}


def _frame_components(space, preview):
    corners = [corner for obj in preview.objects if obj.type == 'MESH' for corner in obj.bound_box]
    if not corners:
        return
    low = Vector(tuple(min(point[i] for point in corners) for i in range(3)))
    high = Vector(tuple(max(point[i] for point in corners) for i in range(3)))
    view = space.region_3d
    view.view_perspective = 'PERSP'
    view.view_location = (low + high) / 2
    view.view_rotation = Vector((-.60, 1, -.36)).to_track_quat('-Z', 'Y')
    view.view_distance = max(.1, (high - low).length * 1.25 * space.lens / 50)
    space.shading.type = 'MATERIAL'


def open_components(context, collection):
    if not is_rigid_effect(collection):
        raise ValueError("Select an imported rigid effect to inspect its components")
    window = context.window
    if window is None:
        raise ValueError("Open the component view from a Blender window")
    source = window.scene
    preview = next((s for s in bpy.data.scenes if s.get(SOURCE_COLLECTION) == collection
                    and s.get(SOURCE_SCENE) == source), None)
    if preview is None:
        meshes = [obj for obj in collection.objects if obj.type == 'MESH' and
                  obj.get('DSTS_rigid_attachment') and obj.parent and obj.parent.get('DSTS_rigid_scene')]
        if not meshes:
            raise ValueError("This effect has no rigid mesh attachments")
        preview = _build_scene(source, collection, meshes)
    space = _view_space(context, window)
    window.scene = preview
    if space:
        view = space.region_3d
        _previous_views[window.as_pointer()] = (space.as_pointer(), source.session_uid, view.view_location.copy(),
            view.view_rotation.copy(), view.view_distance, view.view_perspective, space.shading.type)
        _frame_components(space, preview)
    return preview


class DSTS_OT_InspectRigidComponents(bpy.types.Operator):
    bl_idname = 'scene.dsts_inspect_rigid_components'
    bl_label = 'Inspect Rigid Effect Components'
    bl_description = 'Open a static copy of the local meshes with their color textures, including hidden components'
    bl_options = {'REGISTER', 'UNDO'}

    collection_name: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        from .context import _active_geom_collection
        collection = bpy.data.collections.get(self.collection_name) if self.collection_name else _active_geom_collection(context)
        if collection and collection not in context.scene.collection.children_recursive:
            collection = None
        try:
            open_components(context, collection)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_ReturnToEffect(bpy.types.Operator):
    bl_idname = 'scene.dsts_return_to_effect'
    bl_label = 'Return to Native Effect'
    bl_description = 'Return to the scene containing the animated export rig'

    def execute(self, context):
        source = context.scene.get(SOURCE_SCENE)
        if source is None:
            self.report({'WARNING'}, "The original effect scene is unavailable")
            return {'CANCELLED'}
        window = context.window
        window.scene = source
        state = _previous_views.pop(window.as_pointer(), None)
        if state and state[1] == source.session_uid:
            space = next((a.spaces.active for a in window.screen.areas if a.type == 'VIEW_3D'
                          and a.spaces.active.as_pointer() == state[0]), None)
            if space is None:
                return {'FINISHED'}
            _, _, location, rotation, distance, perspective, shading = state
            view = space.region_3d
            view.view_location, view.view_rotation = location, rotation
            view.view_distance, view.view_perspective = distance, perspective
            space.shading.type = shading
        return {'FINISHED'}
