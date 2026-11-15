"""Discover captured New Century skill phases beside a selected source model."""

from functools import lru_cache
import json
from pathlib import Path
import tempfile

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator, Panel
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import nc_preview_source
from .cinematic import import_preview


_ENUM_ITEMS = {}
_NAMES = json.loads((Path(__file__).parent / 'nc_skill_names.json').read_text(encoding='utf8'))


@lru_cache(maxsize=8)
def _cached_catalog(model_dir, stamps):
    return nc_preview_source.catalog_model(model_dir)


def catalog(filepath):
    model = nc_preview_source._model_dir(filepath)
    source = model.parent.parent / 'MANIFEST' / 'skill_timelines.json'
    stamps = tuple((p.stat().st_mtime_ns, p.stat().st_size)
                   for p in (source, source.with_name('skill_presentations.json')) if p.is_file())
    return _cached_catalog(str(model), stamps)


def phase_label(phase):
    label = phase['label']
    name = phase['skill_name']
    return label.replace(name, _NAMES.get(name, name))


def phase_items(self, context):
    path = getattr(self, 'filepath', '') or getattr(context.scene, 'dsts_nc_model_path', '')
    try:
        data = catalog(bpy.path.abspath(path))
        items = [('__PRIMARY__', 'Primary attack phases', 'Import each skill cast or approach in its own scene')]
        items += [(item['id'], phase_label(item),
                   'Preserves this source phase separately; no cinematic/cast concatenation') for item in data['phases']]
    except (ValueError, OSError, KeyError):
        items = [('__PRIMARY__', 'Choose a captured model.glb', 'Model must be in an extracted New Century library')]
    # Blender keeps pointers to enum strings; retain their owner for the UI lifetime.
    key = str(path)
    _ENUM_ITEMS[key] = items
    return _ENUM_ITEMS[key]


def primary_phases(data):
    groups = {}
    for phase in data['phases']:
        source = phase['source_phase']
        if source.get('keepActorPose') or source.get('triggered') or phase['phase_kind'] not in {'action', 'approach'}:
            continue
        groups.setdefault(phase['slot'], []).append(phase)
    result = []
    for phases in groups.values():
        # A genuine cast takes precedence over a separate cinematic showcase.
        selected = [phase for phase in phases if phase['phase_kind'] == 'action'] or phases
        if len(selected) != 1:
            raise ValueError('This skill has several attack branches. Select one explicit phase to import')
        result.append(selected[0])
    if not result:
        raise ValueError('No unambiguous animated attack phases are captured; choose an explicit phase')
    return result


def import_related_skills(context, filepath, selection='__PRIMARY__', *, import_actor=True,
                          import_camera=True, import_audio=True, import_effect_references=True,
                          reset_actor=True, show_source_helpers=False):
    data = catalog(filepath)
    phases = primary_phases(data) if selection == '__PRIMARY__' else [
        phase for phase in data['phases'] if phase['id'] == selection]
    if not phases:
        raise ValueError('The selected source phase no longer exists; choose it again')
    if import_actor and any(phase['source_phase'].get('keepActorPose') for phase in phases):
        raise ValueError('This phase keeps its preceding actor pose. Disable Source Actor to inspect its '
                         'references independently; no stand animation is substituted')
    from .scene_state import snapshot, rollback
    before, old_scene, old_scenes = snapshot(), context.window.scene, set(bpy.data.scenes)
    created = []
    try:
        with tempfile.TemporaryDirectory(prefix='dsts_skill_preview_') as folder:
            for index, phase in enumerate(phases):
                manifest = Path(folder) / f'phase-{index}.json'
                nc_preview_source.export_preview(data['model_dir'], phase, manifest)
                scene = import_preview(context, manifest, import_actor, import_camera=import_camera,
                                       import_audio=import_audio, import_effect_references=import_effect_references,
                                       reset_actor=reset_actor, show_source_helpers=show_source_helpers)
                scene.name = phase_label(phase)
                scene.dsts_nc_model_path = str(data['actor_path'])
                scene['DSTS_preview_source_model'] = str(data['actor_path'])
                scene['DSTS_preview_phase_id'] = phase['id']
                scene['DSTS_preview_manifest'] = 'Embedded source JSON; generated from captured library'
                created.append(scene)
        return created
    except Exception:
        context.window.scene = old_scene
        for scene in list(bpy.data.scenes):
            if scene not in old_scenes:
                bpy.data.scenes.remove(scene)
        rollback(before)
        raise


class DSTS_OT_NcSkillImport(Operator, ImportHelper):
    """Import a captured skill cast, approach, return or cinematic from the model library"""
    bl_idname = 'import_scene.dsts_nc_skill'
    bl_label = 'New Century Skill Phases'
    bl_description = 'Choose a captured cast, approach, return or cinematic phase and import its source motion, available camera, audio and effect references'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.glb'
    filter_glob: StringProperty(default='*.glb', options={'HIDDEN'})
    phase: EnumProperty(name='Skill phase', items=phase_items)
    import_actor: BoolProperty(name='Source Actor and Animation', default=True)
    import_camera: BoolProperty(name='Captured Camera (when present)', default=True)
    import_audio: BoolProperty(name='Captured Audio', default=True)
    import_effect_references: BoolProperty(name='Effect Timeline References', default=True,
        description='Keep source effects and timing as inspection records; Unity particles are not rendered')
    reset_actor: BoolProperty(name='Reset to Source Idle', default=True,
        description='Play the captured stand/idle after this isolated motion so the preview does not fall back to the bind pose')
    show_source_helpers: BoolProperty(name='Show Source Helper Hierarchy', default=False,
        description='Show original GLB empty/socket trees; these include static duplicate prefab guides')

    def invoke(self, context, event):
        if context.scene.dsts_nc_model_path:
            self.filepath = context.scene.dsts_nc_model_path
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'phase')
        layout.prop(self, 'import_actor')
        layout.prop(self, 'import_camera')
        layout.prop(self, 'import_audio')
        layout.prop(self, 'import_effect_references')
        layout.prop(self, 'reset_actor')
        layout.prop(self, 'show_source_helpers')
        layout.label(text='Cast and cinematic remain separate phases.', icon='INFO')
        layout.label(text='Unity particles/shaders are not yet translated.')

    def execute(self, context):
        try:
            scenes = import_related_skills(context, self.filepath, self.phase,
                import_actor=self.import_actor, import_camera=self.import_camera,
                import_audio=self.import_audio, import_effect_references=self.import_effect_references,
                reset_actor=self.reset_actor, show_source_helpers=self.show_source_helpers)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Imported {len(scenes)} source phase scenes; effects remain inspection references')
        return {'FINISHED'}


class DSTS_OT_SavePhaseManifest(Operator, ExportHelper):
    """Save the selected captured phase with paths relative to the chosen file"""
    bl_idname = 'export_scene.dsts_nc_phase_manifest'
    bl_label = 'Save Phase Manifest'
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return bool(scene.get('DSTS_preview_source_model') and scene.get('DSTS_preview_phase_id'))

    def invoke(self, context, event):
        phase_id = context.scene['DSTS_preview_phase_id'].replace(':', '-')
        self.filepath = f'phase-{phase_id}.json'
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            scene = context.scene
            data = catalog(scene['DSTS_preview_source_model'])
            phase = next((p for p in data['phases'] if p['id'] == scene['DSTS_preview_phase_id']), None)
            if phase is None:
                raise ValueError('Select the phase again from the current source catalog')
            nc_preview_source.export_preview(data['model_dir'], phase, Path(self.filepath))
        except (OSError, ValueError, KeyError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Saved captured phase and its source references')
        return {'FINISHED'}


class DSTS_PT_NcSkill(Panel):
    bl_idname = 'DSTS_PT_nc_skill'
    bl_label = 'New Century Skills'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'DSTS'

    def draw(self, context):
        layout, scene = self.layout, context.scene
        layout.prop(scene, 'dsts_nc_model_path', text='Source Model')
        layout.operator(DSTS_OT_NcSkillImport.bl_idname, icon='IMPORT')
        if DSTS_OT_SavePhaseManifest.poll(context):
            layout.operator(DSTS_OT_SavePhaseManifest.bl_idname, icon='EXPORT')
        if scene.get('DSTS_preview_actor_clip'):
            layout.label(text=f'Clip: {scene["DSTS_preview_actor_clip"]}')
            layout.label(text=f'Phase: {scene.get("DSTS_preview_kind", "showcase")}')
            layout.label(text='Camera: captured' if scene.camera else 'No captured camera in this phase')
            layout.label(text='Effect references are not rendered.', icon='INFO')
            if scene.get('DSTS_preview_reset_actor'):
                layout.label(text='After motion: captured stand/idle reset.')


CLASSES = (DSTS_OT_NcSkillImport, DSTS_OT_SavePhaseManifest, DSTS_PT_NcSkill)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.dsts_nc_model_path = StringProperty(name='New Century Source Model', subtype='FILE_PATH')


def unregister():
    del bpy.types.Scene.dsts_nc_model_path
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _ENUM_ITEMS.clear()
    _cached_catalog.cache_clear()
