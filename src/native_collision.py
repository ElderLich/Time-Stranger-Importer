"""Existing native PHYS bodies/shapes as editable Blender collision geometry."""

import base64
import hashlib
import json
import math
from pathlib import Path
import uuid

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel
from bpy_extras.io_utils import ImportHelper, ExportHelper
from mathutils import Quaternion, Vector

_BASIS = Quaternion((1, 0, 0), math.pi / 2)
_SOURCE = 'DSTS_phys_source'
_A, _B = 'DSTS_phys_association_a', 'DSTS_phys_association_b'


def collection_for(obj):
    return next((col for col in obj.users_collection if _SOURCE in col), None) if obj else None


def _document(collection):
    from .physics import PhysicsDocument
    raw = base64.b64decode(collection[_SOURCE], validate=True)
    if hashlib.sha256(raw).hexdigest() != collection['DSTS_phys_sha256']:
        raise ValueError('Stored collision source hash mismatch; reimport the PHYS file')
    return PhysicsDocument.from_bytes(raw)


def _to_blender(vector):
    x, y, z = vector
    return x, -z, y


def _to_native(vector):
    x, y, z = vector
    return x, z, -y


def _primitive_geometry(mesh):
    kind = mesh['DSTS_phys_kind']
    if kind == 'box':
        x, y, z = mesh['DSTS_phys_half_extents']
        vertices = [_to_blender((a*x, b*y, c*z)) for a, b, c in
                    ((-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1),
                     (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1))]
        faces = [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)]
        return vertices, [], faces
    radius = mesh['DSTS_phys_radius']
    vertices, edges = [], []
    for axis in range(3):
        start = len(vertices)
        for step in range(32):
            a, b = radius * math.cos(step*math.tau/32), radius * math.sin(step*math.tau/32)
            vertices.append((0, a, b) if axis == 0 else (a, 0, b) if axis == 1 else (a, b, 0))
            edges.append((start+step, start+(step+1)%32))
    return vertices, edges, []


def refresh_primitive(mesh):
    if mesh['DSTS_phys_kind'] not in {'box', 'sphere'}:
        raise ValueError('Only primitive collision previews use this operation')
    if mesh.is_editmode:
        raise ValueError('Leave Edit Mode before rebuilding a primitive preview')
    vertices, edges, faces = _primitive_geometry(mesh)
    if not all(math.isfinite(value) for vertex in vertices for value in vertex):
        raise ValueError('Primitive dimensions must be finite')
    mesh.clear_geometry()
    mesh.from_pydata(vertices, edges, faces)
    mesh.update()
    # Store float32 Blender coordinates so harmless construction rounding does
    # not turn an unchanged preview into a native-file edit.
    mesh['DSTS_phys_preview_vertices'] = json.dumps([list(vertex.co) for vertex in mesh.vertices])
    mesh['DSTS_phys_preview_polygons'] = json.dumps([list(polygon.vertices) for polygon in mesh.polygons])
    mesh['DSTS_phys_preview_edges'] = json.dumps([list(edge.vertices) for edge in mesh.edges])
    mesh['DSTS_phys_preview_dimensions'] = json.dumps(list(mesh['DSTS_phys_half_extents']) if mesh['DSTS_phys_kind'] == 'box' else mesh['DSTS_phys_radius'])


def import_collision(context, filepath):
    from .physics import PhysicsDocument
    from .scene_state import snapshot, rollback
    path = Path(filepath).resolve()
    raw = path.read_bytes()
    doc = PhysicsDocument.from_bytes(raw)
    before = snapshot()
    try:
        collection = bpy.data.collections.new(f'Collision · {path.stem}')
        context.scene.collection.children.link(collection)
        identity = uuid.uuid4().hex
        collection[_SOURCE] = base64.b64encode(raw).decode('ascii')
        collection['DSTS_phys_sha256'] = hashlib.sha256(raw).hexdigest()
        collection['DSTS_phys_path'] = str(path)
        collection['DSTS_phys_identity'] = identity
        meshes = []
        for index, shape in enumerate(doc.shapes):
            mesh = bpy.data.meshes.new(f'{path.stem} · shape {index} · {shape.kind}')
            mesh['DSTS_phys_document'] = identity
            mesh['DSTS_phys_shape_index'] = index
            mesh['DSTS_phys_kind'] = shape.kind
            mesh.use_fake_user = True
            if shape.kind in {'box', 'sphere'}:
                if shape.kind == 'box':
                    mesh['DSTS_phys_half_extents'] = list(shape.half_extents)
                else:
                    mesh['DSTS_phys_radius'] = shape.radius
                mesh[_A], mesh[_B] = shape.table_a_index, shape.table_b_index
                refresh_primitive(mesh)
            else:
                mesh.from_pydata([_to_blender(vertex) for vertex in shape.vertices], [], shape.triangles)
                for key, values in ((_A, shape.table_a_indices), (_B, shape.table_b_indices)):
                    attribute = mesh.attributes.new(key, 'INT', 'FACE')
                    for item, value in zip(attribute.data, values):
                        item.value = value
                mesh.update()
            meshes.append(mesh)
        for index, body in enumerate(doc.bodies):
            obj = bpy.data.objects.new(body.name or f'Body {index}', meshes[body.shape_index])
            collection.objects.link(obj)
            obj['DSTS_phys_body_index'] = index
            obj['DSTS_phys_body_name'] = body.name
            obj['DSTS_phys_mass'] = body.mass
            obj['DSTS_phys_shape_index'] = body.shape_index
            obj.location = _to_blender(body.position)
            x, y, z, w = body.rotation_xyzw
            obj.rotation_mode = 'QUATERNION'
            obj.rotation_quaternion = _BASIS @ Quaternion((w, x, y, z)).normalized() @ _BASIS.conjugated()
            obj['DSTS_phys_transform_snapshot'] = json.dumps(dict(location=list(obj.location), rotation=list(obj.rotation_quaternion)))
            obj.lock_scale = (True,) * 3
            obj.display_type = 'WIRE'
            obj.show_in_front = True
            obj.color = (.1, .7, 1, 1)
        return collection
    except Exception:
        rollback(before)
        raise


def document_from_collection(collection):
    doc = _document(collection)
    identity = collection['DSTS_phys_identity']
    shapes = [mesh for mesh in bpy.data.meshes if mesh.get('DSTS_phys_document') == identity]
    if sorted(mesh.get('DSTS_phys_shape_index') for mesh in shapes) != list(range(len(doc.shapes))):
        raise ValueError('Collision shape creation/deletion/duplication is not supported yet')
    objects = list(collection.objects)
    if sorted(obj.get('DSTS_phys_body_index', -1) for obj in objects) != list(range(len(doc.bodies))):
        raise ValueError('Collision body creation/deletion/duplication is not supported yet')
    for mesh in shapes:
        index, kind = mesh['DSTS_phys_shape_index'], mesh['DSTS_phys_kind']
        source = doc.shapes[index]
        if kind != source.kind:
            raise ValueError('Changing collision shape type is not supported yet')
        if mesh.shape_keys is not None:
            raise ValueError('Shape keys cannot be represented by a static native collision shape')
        if mesh.is_editmode:
            raise ValueError('Leave Edit Mode before collision export')
        if kind in {'box', 'sphere'}:
            dimensions = list(mesh['DSTS_phys_half_extents']) if kind == 'box' else mesh['DSTS_phys_radius']
            if dimensions != json.loads(mesh['DSTS_phys_preview_dimensions']):
                raise ValueError('Primitive dimensions changed; use Refresh Primitive Preview before export')
            if ([list(vertex.co) for vertex in mesh.vertices] != json.loads(mesh['DSTS_phys_preview_vertices']) or
                    [list(poly.vertices) for poly in mesh.polygons] != json.loads(mesh['DSTS_phys_preview_polygons']) or
                    [list(edge.vertices) for edge in mesh.edges] != json.loads(mesh['DSTS_phys_preview_edges'])):
                raise ValueError('Primitive mesh edits cannot change its native shape; edit half-extents/radius and refresh')
            if kind == 'box' and tuple(dimensions) != source.half_extents:
                doc.set_box(index, half_extents=dimensions)
            if kind == 'sphere' and dimensions != source.radius:
                doc.set_sphere(index, radius=dimensions)
            if (mesh[_A], mesh[_B]) != (source.table_a_index, source.table_b_index):
                doc.set_shape_associations(index, table_a_index=mesh[_A], table_b_index=mesh[_B])
        else:
            if len(mesh.vertices) != len(source.vertices) or len(mesh.polygons) != len(source.triangles):
                raise ValueError('Native collision meshes require the existing vertex and triangle counts')
            triangle_edges = {tuple(sorted((polygon.vertices[edge], polygon.vertices[(edge+1) % len(polygon.vertices)])))
                              for polygon in mesh.polygons for edge in range(len(polygon.vertices))}
            if {tuple(sorted(edge.vertices)) for edge in mesh.edges} != triangle_edges:
                raise ValueError('Loose collision edges cannot be serialized as native triangles')
            vertices = tuple(_to_native(vertex.co) for vertex in mesh.vertices)
            triangles, associations_a, associations_b = [], [], []
            for triangle_index, (polygon, original) in enumerate(zip(mesh.polygons, source.triangles)):
                value = tuple(polygon.vertices)
                if len(value) != 3:
                    raise ValueError('Native collision faces must remain triangles')
                triangles.append(value)
                values = []
                for key in (_A, _B):
                    attribute = mesh.attributes.get(key)
                    if attribute is None or attribute.domain != 'FACE' or attribute.data_type != 'INT':
                        raise ValueError('Collision face associations were removed or changed type')
                    values.append(attribute.data[triangle_index].value)
                associations_a.append(values[0])
                associations_b.append(values[1])
            edits = {name: value for name, value, original in (
                ('vertices', vertices, source.vertices), ('triangles', tuple(triangles), source.triangles),
                ('table_a_indices', tuple(associations_a), source.table_a_indices),
                ('table_b_indices', tuple(associations_b), source.table_b_indices)) if value != original}
            if edits:
                doc.set_mesh(index, **edits)
    for obj in objects:
        index = obj['DSTS_phys_body_index']
        body = doc.bodies[index]
        if (obj.type != 'MESH' or obj.data.get('DSTS_phys_document') != identity or
                obj.data.get('DSTS_phys_shape_index') != body.shape_index or obj.get('DSTS_phys_shape_index') != body.shape_index):
            raise ValueError('Reassigning body shapes is not supported by this editor yet')
        if (obj.parent or obj.constraints or obj.modifiers or obj.animation_data or obj.vertex_groups or tuple(obj.scale) != (1,1,1) or
                tuple(obj.delta_location) != (0,0,0) or tuple(obj.delta_rotation_euler) != (0,0,0) or
                tuple(obj.delta_rotation_quaternion) != (1,0,0,0) or tuple(obj.delta_scale) != (1,1,1)):
            raise ValueError('Collision bodies support rigid location/rotation edits; scales, modifiers, parenting and animation must not be added')
        snapshot = json.loads(obj['DSTS_phys_transform_snapshot'])
        edits = {}
        if list(obj.location) != snapshot['location']:
            edits['position'] = _to_native(obj.location)
        if obj.rotation_mode != 'QUATERNION' or list(obj.rotation_quaternion) != snapshot['rotation']:
            native = _BASIS.conjugated() @ obj.matrix_basis.to_quaternion() @ _BASIS
            native.normalize()
            edits['rotation_xyzw'] = (native.x, native.y, native.z, native.w)
        if obj['DSTS_phys_mass'] != body.mass:
            edits['mass'] = obj['DSTS_phys_mass']
        if obj['DSTS_phys_body_name'] != body.name:
            edits['name'] = obj['DSTS_phys_body_name']
        if edits:
            doc.set_body(index, **edits)
    return doc


def export_collision(collection, filepath, *, overwrite=False):
    from .source_edit import publish_document
    return publish_document(document_from_collection(collection), collection, filepath,
        source_path=collection['DSTS_phys_path'], source_hash=collection['DSTS_phys_sha256'], overwrite=overwrite)


class DSTS_OT_CollisionImport(Operator, ImportHelper):
    bl_idname = 'import_scene.dsts_collision'
    bl_label = 'Native Object Collisions (.phys)'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.phys'
    filter_glob: StringProperty(default='*.phys', options={'HIDDEN'})

    def execute(self, context):
        try:
            collection = import_collision(context, self.filepath)
            if collection.objects:
                bpy.ops.object.select_all(action='DESELECT')
                obj = next(iter(collection.objects))
                obj.select_set(True)
                context.view_layer.objects.active = obj
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Imported {len(collection.objects)} native collision bodies; runtime interaction still requires game validation')
        return {'FINISHED'}


class DSTS_OT_CollisionExport(Operator, ExportHelper):
    bl_idname = 'export_scene.dsts_collision'
    bl_label = 'Export Native Object Collisions'
    filename_ext = '.phys'
    filter_glob: StringProperty(default='*.phys', options={'HIDDEN'})
    overwrite: BoolProperty(name='Replace Existing File', default=False)

    def execute(self, context):
        try:
            collection = collection_for(context.object)
            if collection is None:
                raise ValueError('Select a body in an imported collision collection')
            export_collision(collection, self.filepath, overwrite=self.overwrite)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Exported native PHYS; object associations and game collision need runtime validation')
        return {'FINISHED'}


class DSTS_OT_CollisionRefresh(Operator):
    bl_idname = 'dsts.collision_refresh'
    bl_label = 'Refresh Primitive Preview'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            refresh_primitive(context.object.data)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_CollisionAssociations(Operator):
    bl_idname = 'dsts.collision_associations'
    bl_label = 'Set Selected Face Associations'
    bl_options = {'REGISTER', 'UNDO'}
    table_a_index: IntProperty(name='Native association A', min=0)
    table_b_index: IntProperty(name='Native association B', min=0)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        doc = _document(collection_for(context.object))
        for table, prop in ((doc.table_a, 'table_a_index'), (doc.table_b, 'table_b_index')):
            self.layout.prop(self, prop)
            index = getattr(self, prop)
            self.layout.label(text=table[index] if 0 <= index < len(table) else 'Invalid association index')

    def execute(self, context):
        try:
            obj = context.object
            doc = _document(collection_for(obj))
            if not 0 <= self.table_a_index < len(doc.table_a) or not 0 <= self.table_b_index < len(doc.table_b):
                raise ValueError('Choose existing native association entries')
            mesh = obj.data
            if mesh['DSTS_phys_kind'] != 'triangle_mesh':
                raise ValueError('Select faces on a native triangle collision mesh')
            if obj.mode == 'EDIT':
                import bmesh
                bm = bmesh.from_edit_mesh(mesh)
                layers = [bm.faces.layers.int.get(name) for name in (_A, _B)]
                if any(layer is None for layer in layers):
                    raise ValueError('Native face association layers are missing')
                selected = [face for face in bm.faces if face.select]
                for face in selected:
                    face[layers[0]], face[layers[1]] = self.table_a_index, self.table_b_index
                bmesh.update_edit_mesh(mesh)
            else:
                selected = [face for face in mesh.polygons if face.select]
                for face in selected:
                    mesh.attributes[_A].data[face.index].value = self.table_a_index
                    mesh.attributes[_B].data[face.index].value = self.table_b_index
            if not selected:
                raise ValueError('Select one or more collision faces first')
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_PT_Collision(Panel):
    bl_idname = 'DSTS_PT_collision'
    bl_label = 'Native Object Collision'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'DSTS'

    @classmethod
    def poll(cls, context):
        return collection_for(context.object) is not None

    def draw(self, context):
        obj, layout = context.object, self.layout
        mesh = obj.data
        layout.label(text=f'Native shape: {mesh["DSTS_phys_kind"]}')
        layout.prop(obj, '["DSTS_phys_body_name"]', text='Native body name')
        layout.prop(obj, '["DSTS_phys_mass"]', text='Mass')
        layout.label(text='Move/rotate the body; native scale stays fixed.')
        if mesh['DSTS_phys_kind'] in {'box', 'sphere'}:
            key = 'DSTS_phys_half_extents' if mesh['DSTS_phys_kind'] == 'box' else 'DSTS_phys_radius'
            layout.prop(mesh, f'["{key}"]', text='Half-extents (native XYZ)' if mesh['DSTS_phys_kind'] == 'box' else 'Radius')
            layout.operator(DSTS_OT_CollisionRefresh.bl_idname)
            doc = _document(collection_for(obj))
            for key, values in ((_A, doc.table_a), (_B, doc.table_b)):
                layout.prop(mesh, f'["{key}"]', text='Native association ' + ('A' if key == _A else 'B'))
                index = mesh[key]
                layout.label(text=values[index] if 0 <= index < len(values) else 'Invalid association index')
        else:
            layout.label(text='Edit existing vertices/triangles in Edit Mode.')
            layout.operator(DSTS_OT_CollisionAssociations.bl_idname)
        layout.label(text='Shape preview; no runtime simulation.', icon='INFO')
        layout.operator(DSTS_OT_CollisionExport.bl_idname, icon='EXPORT')


CLASSES = (DSTS_OT_CollisionImport, DSTS_OT_CollisionExport, DSTS_OT_CollisionRefresh,
           DSTS_OT_CollisionAssociations, DSTS_PT_Collision)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
