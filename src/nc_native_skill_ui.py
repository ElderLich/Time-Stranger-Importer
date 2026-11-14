"""Saved New Century skill recipes and reviewed native candidate composition."""

import copy
import json
from pathlib import Path
import textwrap

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy_extras.io_utils import ImportHelper

from . import nc_skill_ui
from . import skill_conversion as compiler


_PHASE_ITEMS = {}


def phase_items(self, context):
    path = getattr(self, 'filepath', '') or context.scene.dsts_nc_model_path
    try:
        phases = nc_skill_ui.catalog(bpy.path.abspath(path))['phases']
        try:
            primary = {p['id'] for p in nc_skill_ui.primary_phases({'phases': phases})}
        except ValueError:
            primary = set()
        phases = sorted(phases, key=lambda p: p['id'] not in primary)
        items = [(p['id'], nc_skill_ui.phase_label(p), 'Keep this captured phase and its source events')
                 for p in phases]
    except (ValueError, OSError, KeyError):
        items = [('__NONE__', 'Choose a captured model', 'Select model.glb in a New Century library')]
    _PHASE_ITEMS[str(path)] = items
    return items


class DSTS_PG_SkillSocket(PropertyGroup):
    side: EnumProperty(name='Owner', items=[('caster', 'Caster', 'Caster-owned effect'),
                                          ('target', 'Target', 'Separate target-owned effect')])
    source_socket: StringProperty(name='Source Socket')
    native_root: BoolProperty(name='Native Actor Root', description='Use this owner\'s actor root instead of a named bone')
    native_socket: StringProperty(name='Native Socket', description='Explicit native socket; blank remains unresolved')


class DSTS_PG_SkillCue(PropertyGroup):
    source_key: StringProperty(name='Source Sound')
    cue_id: IntProperty(name='Native Cue', min=0, description='Zero leaves the cue unassigned')
    component: StringProperty(name='Audio Component', description='Reviewed component supplying this native sound')


class DSTS_PG_SkillComponent(PropertyGroup):
    path: StringProperty(name='Component Manifest', subtype='FILE_PATH')
    pin_json: StringProperty(options={'HIDDEN'})


class DSTS_PG_NativeSkill(PropertyGroup):
    recipe_json: StringProperty(options={'HIDDEN'})
    report_json: StringProperty(options={'HIDDEN'})
    inspected_recipe_json: StringProperty(options={'HIDDEN'})
    model_stem: StringProperty(name='Native Actor', description='Destination character stem, for example chr050')
    motion_suffix: StringProperty(name='Native Motion', description='Explicit native motion suffix, for example bs02')
    animation_selector: IntProperty(name='Motion Selector', min=0, description='Zero leaves native routing unresolved')
    primary_effect_id: IntProperty(name='Caster Effect ID', min=0)
    target_effect_id: IntProperty(name='Target Effect ID', min=0)
    caster_donor: StringProperty(name='Caster BSON Donor', subtype='FILE_PATH')
    target_donor: StringProperty(name='Target BSON Donor', subtype='FILE_PATH')
    caster_template_set: StringProperty(name='Caster Figure Template', default='set1',
        description='Attached Figure set in the selected donor, inspected before composition')
    target_template_set: StringProperty(name='Target Figure Template', default='set1',
        description='Attached Figure set in the selected donor, inspected before composition')
    caster_socket_inventory: StringProperty(name='Caster Socket List', subtype='FILE_PATH',
        description='Native NLST to verify caster socket names')
    target_socket_inventory: StringProperty(name='Target Socket List', subtype='FILE_PATH',
        description='Native NLST to verify target socket names for this candidate')
    output: StringProperty(name='New Candidate Folder', subtype='DIR_PATH',
        description='Choose a new isolated folder; existing destinations are protected')
    sockets: CollectionProperty(type=DSTS_PG_SkillSocket)
    cues: CollectionProperty(type=DSTS_PG_SkillCue)
    components: CollectionProperty(type=DSTS_PG_SkillComponent)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _path(value):
    return str(Path(bpy.path.abspath(value)).resolve()) if value else ''


def load_recipe(scene, recipe):
    """Replace controls only after a recipe was parsed successfully."""
    recipe = copy.deepcopy(recipe)
    if recipe.get('format') != 'nc-native-skill-recipe-v1':
        raise ValueError('Expected a New Century native skill recipe')
    source, target = recipe['source'], recipe['target']
    if not isinstance(source['model_path'], str) or not isinstance(source['phase_id'], str):
        raise ValueError('Recipe source model and phase must be strings')
    if not isinstance(nc_skill_ui.phase_label(source['phase']), str):
        raise ValueError('Recipe source phase must have a display label')
    for field in ('model_stem', 'motion_suffix'):
        if not isinstance(target.get(field) or '', str):
            raise ValueError('Native actor and motion must be strings')
    for field in ('animation_selector', 'primary_effect_id', 'target_effect_id'):
        value = target.get(field)
        if value is not None and (type(value) is not int or not 0 <= value <= 2147483647):
            raise ValueError('Native identifiers must be nonnegative 32-bit integers')
    donors = recipe.get('donor_bson') or {}
    templates = recipe.get('donor_template_set') or {}
    if not isinstance(templates, dict) or any(not isinstance(templates.get(side, 'set1'), str)
                                            for side in ('caster', 'target')):
        raise ValueError('Donor template sets must be strings')
    for mapping in (donors, target.get('socket_inventory') or {}):
        for side in ('caster', 'target'):
            pin = mapping.get(side)
            if pin is not None and not isinstance(pin.get('path'), str):
                raise ValueError('Expected a pinned native donor or socket list')
    for row in recipe.get('socket_mappings', []):
        if (row.get('side') not in ('caster', 'target') or not isinstance(row.get('source_socket'), str)
                or not isinstance(row.get('native_socket') or '', str)):
            raise ValueError('Invalid source/native socket mapping')
    for row in recipe.get('cue_mappings', []):
        if not isinstance(row.get('source_key'), str) or not isinstance(row.get('component') or '', str):
            raise ValueError('Invalid source/native sound mapping')
        value = row.get('cue_id')
        if value is not None and (type(value) is not int or not 0 <= value <= 2147483647):
            raise ValueError('Native cue must be a nonnegative 32-bit integer')
    for pin in recipe.get('components', []):
        if not isinstance(pin.get('path'), str):
            raise ValueError('Expected a pinned component manifest')
    recipe['donor_bson'] = donors
    recipe['donor_template_set'] = templates
    target['socket_inventory'] = target.get('socket_inventory') or {}
    state = scene.dsts_native_skill
    state.recipe_json = _json(recipe)
    state.report_json = state.inspected_recipe_json = ''
    state.output = ''
    scene.dsts_nc_model_path = source['model_path']
    for field in ('model_stem', 'motion_suffix'):
        setattr(state, field, target.get(field) or '')
    for field in ('animation_selector', 'primary_effect_id', 'target_effect_id'):
        setattr(state, field, target.get(field) or 0)
    for side in ('caster', 'target'):
        setattr(state, side + '_donor', (donors.get(side) or {}).get('path', ''))
        setattr(state, side + '_template_set', templates.get(side, 'set1'))
        setattr(state, side + '_socket_inventory', ((target.get('socket_inventory') or {}).get(side) or {}).get('path', ''))
    state.sockets.clear()
    for row in recipe.get('socket_mappings', []):
        item = state.sockets.add()
        item.side, item.source_socket = row['side'], row['source_socket']
        item.native_root = row.get('native_socket') == '@root'
        item.native_socket = '' if item.native_root else row.get('native_socket') or ''
    state.cues.clear()
    for row in recipe.get('cue_mappings', []):
        item = state.cues.add()
        item.source_key = row['source_key']
        item.cue_id, item.component = row.get('cue_id') or 0, row.get('component') or ''
    state.components.clear()
    for pin in recipe.get('components', []):
        item = state.components.add()
        item.path, item.pin_json = pin['path'], _json(pin)


def scene_recipe(scene):
    """Apply explicit UI mappings while retaining the original source pins."""
    state = scene.dsts_native_skill
    if not state.recipe_json:
        raise ValueError('Create or load a source recipe first')
    recipe = json.loads(state.recipe_json)
    if _path(scene.dsts_nc_model_path) != _path(recipe['source']['model_path']):
        raise ValueError('Source model changed; create a new recipe to capture its source identity')
    target = recipe['target']
    for field in ('model_stem', 'motion_suffix'):
        target[field] = getattr(state, field).strip()
    for field in ('animation_selector', 'primary_effect_id', 'target_effect_id'):
        target[field] = getattr(state, field) or None
    donors = recipe.setdefault('donor_bson', {})
    for side in ('caster', 'target'):
        recipe.setdefault('donor_template_set', {})[side] = getattr(state, side + '_template_set')
        path = _path(getattr(state, side + '_donor'))
        old = donors.get(side)
        donors[side] = (old if old and _path(old['path']) == path else compiler.pin_file(path)) if path else None
        inventory = target.setdefault('socket_inventory', {})
        path = _path(getattr(state, side + '_socket_inventory'))
        old = inventory.get(side)
        inventory[side] = (old if old and _path(old['path']) == path else compiler.pin_file(path)) if path else None
    recipe['socket_mappings'] = [dict(side=r.side, source_socket=r.source_socket,
                                    native_socket='@root' if r.native_root else r.native_socket) for r in state.sockets]
    recipe['cue_mappings'] = [dict(source_key=r.source_key, cue_id=r.cue_id or None,
                                 component=r.component) for r in state.cues]
    recipe['components'] = []
    for item in state.components:
        pin = json.loads(item.pin_json)
        if _path(item.path) != _path(pin['path']):
            raise ValueError('Component path changed; remove it and choose its new manifest to pin it')
        recipe['components'].append(pin)
    return recipe


class DSTS_OT_NativeSkillCreate(Operator, ImportHelper):
    bl_idname = 'import_scene.dsts_native_skill_recipe'
    bl_label = 'Create Native Skill Recipe'
    bl_description = 'Choose a captured model and skill phase, pin its source files and start an editable native conversion recipe'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.glb'
    filter_glob: StringProperty(default='*.glb', options={'HIDDEN'})
    phase: EnumProperty(name='Captured Skill Phase', items=phase_items)

    def invoke(self, context, event):
        self.filepath = context.scene.dsts_nc_model_path
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        self.layout.prop(self, 'phase')
        self.layout.label(text='Select the ordinary cast or approach for a battle skill.')

    def execute(self, context):
        try:
            recipe = compiler.create_recipe(_path(self.filepath), self.phase)
            load_recipe(context.scene, recipe)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Source recipe saved in this scene; review native mappings next')
        return {'FINISHED'}


class DSTS_OT_NativeSkillLoad(Operator, ImportHelper):
    bl_idname = 'import_scene.dsts_native_skill_recipe_json'
    bl_label = 'Load Skill Recipe'
    bl_description = 'Load a pinned native skill recipe and its explicit mappings into the current scene'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            recipe = json.loads(Path(_path(self.filepath)).read_text(encoding='utf-8'))
            # Validation is read-only. Unsupported conversion requirements stay visible.
            compiler.inspect_recipe(recipe)
            load_recipe(context.scene, recipe)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_NativeSkillComponent(Operator, ImportHelper):
    bl_idname = 'import_scene.dsts_native_skill_component'
    bl_label = 'Add Reviewed Component'
    bl_description = 'Pin an exact native component manifest and its dependency list for this skill recipe'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            pin = compiler.pin_file(_path(self.filepath))
            document = json.loads(Path(pin['path']).read_text(encoding='utf-8'))
            if document.get('format') != 'nc-native-skill-component-v1':
                raise ValueError('Choose an nc-native-skill-component-v1 manifest')
            recipe = scene_recipe(context.scene)
            if any(_path(p['path']) == _path(pin['path']) for p in recipe['components']):
                raise ValueError('This component is already in the recipe')
            item = context.scene.dsts_native_skill.components.add()
            item.path, item.pin_json = pin['path'], _json(pin)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class DSTS_OT_NativeSkillRemove(Operator):
    bl_idname = 'dsts.native_skill_remove_component'
    bl_label = 'Remove Component'
    bl_description = 'Remove this reviewed component from the scene recipe without changing its source files'
    bl_options = {'REGISTER', 'UNDO'}
    index: IntProperty(min=0)

    def execute(self, context):
        items = context.scene.dsts_native_skill.components
        if self.index >= len(items):
            return {'CANCELLED'}
        items.remove(self.index)
        return {'FINISHED'}


class DSTS_OT_NativeSkillInspect(Operator):
    bl_idname = 'dsts.native_skill_inspect'
    bl_label = 'Inspect Conversion'
    bl_description = 'Validate source and dependency hashes, mappings and unsupported features before building this candidate'

    def execute(self, context):
        state = context.scene.dsts_native_skill
        try:
            recipe = scene_recipe(context.scene)
            report = compiler.inspect_recipe(recipe)
            state.report_json, state.inspected_recipe_json = _json(report), _json(recipe)
            # Newly chosen donor pins become the source of truth for subsequent checks.
            state.recipe_json = _json(recipe)
        except Exception as exc:
            state.report_json = state.inspected_recipe_json = ''
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Inspection recorded: {len(report.get('errors', []))} errors, "
                    f"{len(report.get('blockers', []))} unresolved requirements")
        return {'FINISHED'}


class DSTS_OT_NativeSkillReport(Operator):
    bl_idname = 'dsts.native_skill_report'
    bl_label = 'Open Full Review'
    bl_description = 'Show the complete conversion review, source provenance and unresolved requirements in a text editor'

    def execute(self, context):
        report = context.scene.dsts_native_skill.report_json
        if not report:
            return {'CANCELLED'}
        text = bpy.data.texts.new('Native Skill Review')
        text.write(json.dumps(json.loads(report), indent=2, ensure_ascii=False))
        if context.area:
            context.area.type = 'TEXT_EDITOR'
            context.area.spaces.active.text = text
        return {'FINISHED'}


class DSTS_OT_NativeSkillPreview(Operator):
    bl_idname = 'dsts.native_skill_preview'
    bl_label = 'Preview Captured Phase'
    bl_description = 'Import the captured actor, audio and effect references into a separate scene while retaining this conversion recipe'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            recipe = scene_recipe(context.scene)
            scenes = nc_skill_ui.import_related_skills(context, recipe['source']['model_path'], recipe['source']['phase_id'])
            for scene in scenes:
                load_recipe(scene, copy.deepcopy(recipe))
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Captured phase preview created; particle references are not rendered effects')
        return {'FINISHED'}


class DSTS_OT_NativeSkillBuild(Operator):
    bl_idname = 'export_scene.dsts_native_skill_candidate'
    bl_label = 'Build Review Candidate'
    bl_description = 'Compose the inspected native assets into a new isolated review folder with exact dependencies and source provenance'

    def execute(self, context):
        state = context.scene.dsts_native_skill
        try:
            recipe = scene_recipe(context.scene)
            if not state.report_json or state.inspected_recipe_json != _json(recipe):
                raise ValueError('Mappings changed or have not been inspected; inspect the conversion before building')
            if not state.output:
                raise ValueError('Choose a new candidate folder')
            inspection = json.loads(state.report_json)
            report = compiler.build_candidate(recipe, _path(state.output),
                                               expected_plan_sha256=inspection['plan_sha256'])
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Review candidate built; in-game skill acceptance remains required')
        return {'FINISHED'}


class DSTS_PT_NativeSkill(Panel):
    bl_idname = 'DSTS_PT_native_skill_conversion'
    bl_label = 'New Century Native Skill'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'DSTS'

    def draw(self, context):
        layout, scene = self.layout, context.scene
        state = scene.dsts_native_skill
        layout.prop(scene, 'dsts_nc_model_path', text='Captured Model')
        layout.operator(DSTS_OT_NativeSkillCreate.bl_idname, icon='ADD')
        layout.operator(DSTS_OT_NativeSkillLoad.bl_idname, icon='FILE_FOLDER')
        if not state.recipe_json:
            return
        recipe = json.loads(state.recipe_json)
        layout.label(text=nc_skill_ui.phase_label(recipe['source']['phase']))
        layout.operator(DSTS_OT_NativeSkillPreview.bl_idname, icon='PLAY')
        layout.label(text='Preview particles remain source references.', icon='INFO')
        for field in ('model_stem', 'motion_suffix', 'animation_selector', 'primary_effect_id', 'target_effect_id',
                      'caster_donor', 'caster_template_set', 'target_donor', 'target_template_set',
                      'caster_socket_inventory', 'target_socket_inventory'):
            layout.prop(state, field)
        if state.sockets:
            box = layout.box()
            box.label(text='Socket mappings')
            for item in state.sockets:
                box.label(text=f'{item.side.title()}: {item.source_socket}')
                box.prop(item, 'native_root')
                if not item.native_root:
                    box.prop(item, 'native_socket')
        if state.cues:
            box = layout.box()
            box.label(text='Sound mappings')
            for item in state.cues:
                box.label(text=item.source_key)
                box.prop(item, 'cue_id')
                box.prop(item, 'component')
        box = layout.box()
        box.label(text='Reviewed native components')
        for index, item in enumerate(state.components):
            row = box.row()
            row.label(text=Path(item.path).name)
            row.operator(DSTS_OT_NativeSkillRemove.bl_idname, text='', icon='X').index = index
        box.operator(DSTS_OT_NativeSkillComponent.bl_idname, icon='ADD')
        layout.operator(DSTS_OT_NativeSkillInspect.bl_idname, icon='VIEWZOOM')
        if state.report_json:
            report = json.loads(state.report_json)
            coverage = report.get('coverage')
            if coverage:
                box = layout.box()
                box.label(text=f"Source systems: {coverage['represented']} / {coverage['systems']} represented")
                for item in coverage.get('uncovered', [])[:8]:
                    box.label(text=f"Node {item['node_index']}: {item['node_path'].rsplit('/', 1)[-1]}", icon='ERROR')
                    reasons = item.get('reasons') or [item['status']]
                    for line in textwrap.wrap(str(reasons[0]), width=48):
                        box.label(text=line)
            messages = dict.fromkeys(row['message'] for row in report.get('errors', []) + report.get('blockers', []))
            for message in list(messages)[:6]:
                layout.label(text=message, icon='ERROR')
            layout.operator(DSTS_OT_NativeSkillReport.bl_idname, icon='TEXT')
        layout.prop(state, 'output')
        layout.operator(DSTS_OT_NativeSkillBuild.bl_idname, icon='EXPORT')
        layout.label(text='Review assets are not an installed skill.', icon='INFO')


CLASSES = (DSTS_PG_SkillSocket, DSTS_PG_SkillCue, DSTS_PG_SkillComponent, DSTS_PG_NativeSkill,
           DSTS_OT_NativeSkillCreate, DSTS_OT_NativeSkillLoad, DSTS_OT_NativeSkillComponent,
           DSTS_OT_NativeSkillRemove, DSTS_OT_NativeSkillInspect, DSTS_OT_NativeSkillReport,
           DSTS_OT_NativeSkillPreview, DSTS_OT_NativeSkillBuild, DSTS_PT_NativeSkill)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.dsts_native_skill = PointerProperty(type=DSTS_PG_NativeSkill)


def unregister():
    del bpy.types.Scene.dsts_native_skill
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _PHASE_ITEMS.clear()
