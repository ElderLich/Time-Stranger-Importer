"""Inspect captured ReArise actions and their ordinary Time Stranger battle bindings."""
import csv
import filecmp
import json
from pathlib import Path
import re
import struct


def motion_track(code, clip):
    """Match the NLA track name produced by the ReArise importer, including shared clips."""
    match = re.search(r'_([bf][a-z]\d+)(?:_?rd)?$', clip, re.I)
    return f'{code}_{match.group(1).lower()}' if match else clip


def body_atlas(mat):
    if mat.get('alphaMode') == 'BLEND':
        return None
    name = mat['name']
    atlas = re.search(r'(a\d\d)(?:_rd)?$', name, re.I)
    if atlas:
        return atlas.group(1).lower()
    # Baby captures a008 and b009 use a_c00 for their single cutout body.
    if re.fullmatch(r'[ab]\d{3}a_c00', name, re.I):
        return 'a01'
    return None


def inspect_actor(path):
    path = Path(path).resolve()
    with path.open('rb') as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError('Truncated ReArise GLB')
        magic, version, size, length, kind = struct.unpack('<4sIII4s', header)
        if (magic, version, kind) != (b'glTF', 2, b'JSON') or size != path.stat().st_size or length > size - 20:
            raise ValueError('Expected a complete GLB with a JSON first chunk')
        doc = json.loads(stream.read(length))
    code = doc['asset']['extras']['nc']['model_id']
    library = path.parents[3]
    if path != library / 'Digimon_Library' / code / 'model/model.glb':
        raise ValueError(f'Select Digimon_Library/{code}/model/model.glb inside the captured ReArise library')
    manifest = library / 'MANIFEST/display_playback.json'
    clips = json.loads(manifest.read_bytes()).get(code, {}).get('clips', {})
    embedded = {a['name'] for a in doc.get('animations', []) if a.get('channels')}
    actions = {}
    for key, cue in clips.items():
        clip = cue.get('actorClip', key)
        events = []
        for event in cue.get('events', []):
            owner, slot = event.get('owner', code), event['slot']
            asset = library / 'vfx_assets' / owner / slot / (slot + '.json')
            events.append({**event, 'manifest': str(asset), 'captured': asset.is_file()})
        camera = cue.get('actionCamera')
        if camera:
            owner, slot = camera.get('owner', code), camera['slot']
            asset = library / 'vfx_assets' / owner / slot / (slot + '.json')
            camera = {**camera, 'manifest': str(asset), 'captured': asset.is_file()}
        sounds = []
        for sound in cue.get('sounds', []):
            wav = library / 'Global_Assets/skill_audio' / (sound['key'] + '.wav')
            sounds.append({**sound, 'wav': str(wav) if wav.is_file() else None})
        actions[key] = {'label': cue.get('actionLabel', key), 'master_skill_ids': cue.get('masterSkillIds', []),
                        'actor_clip': clip, 'export_track': motion_track(code, clip),
                        'embedded': clip in embedded, 'total_frames': cue.get('totalFrames'),
                        'common_action': cue.get('commonAction'), 'actor_transition': cue.get('actorTransition'), 'camera': camera,
                        'events': events, 'sounds': sounds}
    manual = []
    for node in doc['nodes']:
        if 'mesh' not in node:
            continue
        if 'skin' not in node:
            manual.append(f'Rigid attachment: {node["name"]}')
        for primitive in doc['meshes'][node['mesh']]['primitives']:
            mat = doc['materials'][primitive['material']]
            if not body_atlas(mat):
                manual.append(f'Native material mapping: {mat["name"]}')
    return {'source_model': code, 'glb': str(path), 'motion_tracks': sorted({motion_track(code, c) for c in embedded}),
            'manual_model_work': sorted(set(manual)), 'actions': actions}


def _table(patch, name):
    folder = patch / 'data' / (name + '.mbe')
    paths = sorted(folder.glob('000_*.ap.csv'))
    if len(paths) != 1:
        raise ValueError(f'Expected one staged first-sheet append CSV under {folder}')
    with paths[0].open(encoding='utf-8-sig', newline='') as stream:
        rows = csv.reader(stream)
        if not next(rows, None):
            raise ValueError(f'Missing CSV header in {paths[0]}')
        result = {}
        for row in rows:
            if not row:
                continue
            key = int(row[0])
            if key in result:
                raise ValueError(f'Duplicate {name} ID {key} in {paths[0]}')
            result[key] = row
        return result


def _group_damage_missing_contact(source, row, recipient):
    """Recognize the observed Wolkenapalm-to-Special failure without classifying other routes."""
    group_spell = source['common_action'] and any(
        event.get('anchor', {}).get('origin') == 'target' for event in source['events'])
    return bool(group_spell and row[10] in ('3', '4', '5') and row[20] == '0' and row[22] in ('1', '2')
                and row[33] == '5' and row[11] == '5' and int(row[14]) == 0
                and recipient and recipient['position_type'] == 4 and recipient['hit_frame'] == 0)


def inspect_pack(plan, pack, actor, bindings, resources=()):
    from .effect_bson import EffectDocument
    from .skill_conversion.camera_assets import camera_record

    patch = pack / 'dsts-loader/patch'
    errors = []
    review = []
    result = {'native_actor': actor, 'actions': [], 'errors': errors, 'review': review}
    if not re.fullmatch(r'chr[0-9a-z_]+', actor, re.I):
        raise ValueError(f'Invalid native model code {actor!r}')
    if not bindings:
        raise ValueError('Supply a captured timeline key and native skill ID with --bind')
    statuses = [s for s in _table(patch, 'digimon_status').values() if s[3].casefold() == actor.casefold()]
    if len(statuses) != 1:
        raise ValueError(f'Expected one staged digimon_status row for {actor}, found {len(statuses)}')
    skills, effects = _table(patch, 'battle_skill'), _table(patch, 'battle_effect')
    bank = pack / 'Ryo/Digimon Story Time Stranger/SE/se.acb'
    cues = {}
    for config in bank.glob('*.cue/config.yaml'):
        match = re.search(r'''(?m)^\s*cue_name:\s*['"]?([^'"\s#]+)''', config.read_text(encoding='utf-8-sig'))
        if match:
            cues.setdefault(match[1], []).append(config.parent / (config.parent.stem + '.hca'))

    def asset(name):
        if Path(name).name != name or ':' in name:
            raise ValueError(f'Unexpected resource basename {name!r}')
        for root in (patch, *resources):
            path = root / name
            if path.is_file() and path.stat().st_size:
                return path
        errors.append(f'Missing native asset {name}')
        return None

    for ext in ('.geom', '.anim', '.nlst'):
        asset(actor + ext)
    for key, skill_id in bindings:
        source = plan['actions'].get(key)
        if not source:
            errors.append(f'Unknown ReArise timeline {key}')
            continue
        if not source['embedded']:
            errors.append(f'{key} selects absent GLB motion {source["actor_clip"]}')
        row = skills.get(skill_id)
        if not row:
            errors.append(f'Missing staged battle_skill ID {skill_id}')
            continue
        slot = int(row[10]) - 2
        if slot not in (1, 2, 3):
            errors.append(f'Skill {skill_id} uses unsupported signature motion selector {row[10]}')
            continue
        assignments = {(int(statuses[0][i]), int(statuses[0][i+2])) for i in (72, 75, 78)
                       if statuses[0][i] and statuses[0][i+2]}
        if (skill_id, slot) not in assignments:
            errors.append(f'{actor} does not assign skill {skill_id} to signature slot {slot}')
        stem = f'{actor}_bs{slot:02d}'
        native_motion = asset(stem + '.anim')
        summary = {'timeline': key, 'skill_id': skill_id, 'actor_clip': source['actor_clip'],
                   'source_track': source['export_track'], 'native_track': stem, 'effects': {}, 'sounds': []}
        result['actions'].append(summary)
        original = patch / (motion_track(actor, source['actor_clip']) + '.anim')
        if native_motion and original != native_motion and original.is_file():
            summary['matches_source_export'] = filecmp.cmp(original, native_motion, shallow=False)
            if not summary['matches_source_export']:
                review.append(f'{skill_id}: compare {stem}.anim with {original.name} for intentional motion edits')
        if source['camera']:
            source_cam = Path(source['camera']['manifest'])
            capture = json.loads(source_cam.read_bytes()).get('actionCamera', {}) if source_cam.is_file() else {}
            summary['source_camera_samples'] = len(capture.get('frames', []))
            if not summary['source_camera_samples']:
                review.append(f'{key}: captured action camera frames unavailable in {source_cam}')
        for event in source['events']:
            if not event['captured']:
                review.append(f'{key}: source event manifest unavailable at {event["manifest"]}')
        for side in ('pc', 'em'):
            camera = 'cam_' + stem + '_' + side
            geo = asset(camera + '.geom')
            if geo:
                try:
                    camera_record(geo.read_bytes())
                except ValueError as exc:
                    errors.append(f'{geo.name}: {exc}')
            for ext in ('.anim', '.nlst'):
                asset(camera + ext)
        for side, col in [('caster', 12), ('group', 14), ('receiver', 16), ('extra', 18)]:
            effect_id = int(row[col])
            if effect_id <= 0:
                summary['effects'][side] = None
                continue
            if effect_id not in effects:
                errors.append(f'{skill_id} {side} effect {effect_id} is outside the staged battle_effect rows')
                continue
            effect = effects[effect_id]
            script = asset(effect[4] + '.bson')
            if not script:
                continue
            try:
                doc = EffectDocument.read(script)
            except (OSError, ValueError) as exc:
                errors.append(f'{script.name}: {exc}')
                continue
            figures = sorted({f for group in doc.sets for f in group.drawables})
            summary['effects'][side] = {'id': effect_id, 'script': script.name, 'figures': figures,
                                        'position_type': int(effect[5]), 'socket': effect[6],
                                        'map_hide_show': list(map(int, effect[11:13])),
                                        'hit_frame': doc.value(('hf', 'hf0')), 'lifetime_frames': doc.value(('mf',))}
            for fig in figures:
                for ext in ('.geom', '.anim', '.nlst'):
                    asset(fig + ext)
            for group in doc.sets:
                if not group.sound_name:
                    continue
                waveforms = cues.get(group.sound_name, [])
                summary['sounds'].append({'cue': group.sound_name, 'frame': group.sound_start_frame,
                                          'owner': side, 'stop_on_end': doc.value((group.name, 'common', 'se', 'se_stop')),
                                          'ryo_files': [str(p) for p in waveforms]})
                if len(waveforms) != 1 or not waveforms[0].is_file():
                    errors.append(f'{skill_id}: expected one staged Ryo cue and HCA for {group.sound_name}')
                else:
                    with waveforms[0].open('rb') as handle:
                        if bytes(b & 0x7f for b in handle.read(4)) != b'HCA\x00':
                            errors.append(f'{skill_id}: invalid HCA signature in {waveforms[0]}')
        if _group_damage_missing_contact(source, row, summary['effects'].get('receiver')):
            errors.append(f'{skill_id}: group-damage Special dispatches its unmarked group Figure as per-target contact. '
                          'Review the caster, group and per-enemy contact rows against a native group-damage Special.')
        if source['events'] and not any(e and e['figures'] for e in summary['effects'].values()):
            errors.append(f'{skill_id} has source visuals but no resolved native Figures')
        if source['sounds'] and not summary['sounds']:
            errors.append(f'{skill_id} has source sounds but no resolved native cues')
        else:
            caster_frames = {sound['frame'] for sound in summary['sounds'] if sound['owner'] == 'caster'}
            for sound in source['sounds']:
                if sound['frame'] not in caster_frames:
                    review.append(f'{skill_id}: compare source {sound["key"]} at frame {sound["frame"]} with native cue timing')
    return result
