"""Hash-pinned, Blender-independent native skill candidate compiler."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import tempfile

try:
    from .. import nc_preview_source
    from ..spark import SparkDocument
except ImportError:
    import nc_preview_source
    from spark import SparkDocument

from .bson import compose_event
from .coverage import inspect_coverage

RECIPE_FORMAT = 'nc-native-skill-recipe-v1'
COMPONENT_FORMAT = 'nc-native-skill-component-v1'
API_VERSION = 1


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode('utf8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _file(path):
    path = Path(path).absolute()
    _require(not any(part.is_symlink() for part in (path, *path.parents)), 'Symbolic-link inputs are not supported')
    # Manifest references can contain many .. segments before a long shader
    # basename. Normalize before is_file(): CPython 3.13 on Windows can reject
    # the long lexical spelling although the canonical native path exists.
    # Inspect the original chain above so normalization cannot hide a link.
    path = path.resolve()
    _require(not any(part.is_symlink() for part in (path, *path.parents)), 'Symbolic-link inputs are not supported')
    _require(path.is_file(), f'Input file is missing: {path}')
    return path


def pin_file(path):
    """Return a JSON-safe absolute path, SHA-256 and byte count."""
    path = _file(path)
    raw = path.read_bytes()
    return {'path': str(path), 'sha256': _sha(raw), 'bytes': len(raw)}


def _check_pin(pin, base=None):
    _require(isinstance(pin, dict), 'Expected a pinned file object')
    _require(isinstance(pin.get('path'), str) and pin['path'], 'Pinned file path is missing')
    _require(isinstance(pin.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', pin['sha256']), 'Invalid file SHA-256')
    _require(type(pin.get('bytes')) is int and pin['bytes'] >= 0, 'Invalid pinned file byte count')
    path = Path(pin['path'])
    if not path.is_absolute():
        _require(base is not None, 'Pinned source paths must be absolute')
        path = Path(base) / path
    path = _file(path)
    raw = path.read_bytes()
    _require(len(raw) == pin['bytes'] and _sha(raw) == pin['sha256'], f'Stale pinned input: {path}')
    return path, raw


def _destination(value):
    _require(isinstance(value, str) and value and '\\' not in value, 'Dependency destination must use relative forward slashes')
    parts = PurePosixPath(value).parts
    _require(not PurePosixPath(value).is_absolute() and parts and all(p not in ('.', '..') for p in parts), 'Unsafe dependency destination')
    _require('/'.join(parts) == value, 'Dependency destination must be canonical')
    for part in parts:
        _require(not any(c in part for c in ':*?"<>|') and not any(ord(c) < 32 for c in part)
                 and part.rstrip(' .') == part, 'Unsafe dependency destination')
        _require(part.split('.')[0].upper() not in {'CON', 'PRN', 'AUX', 'NUL',
                 *('COM' + str(i) for i in range(1, 10)), *('LPT' + str(i) for i in range(1, 10))},
                 'Reserved dependency destination')
    return value


def _source_snapshot(model_path, phase_id, role=None):
    catalog = nc_preview_source.catalog_model(model_path, role)
    phases = [phase for phase in catalog['phases'] if phase['id'] == phase_id]
    _require(len(phases) == 1, f'Captured phase does not exist: {phase_id}')
    phase = phases[0]
    nc_preview_source._validate_phase(phase['source_phase'])
    model, library = Path(catalog['model_dir']), Path(catalog['library'])
    files = {str(model / 'model/model.glb'), str(library / 'MANIFEST/skill_timelines.json')}
    if (model / 'info.json').is_file():
        files.add(str(model / 'info.json'))
    effects, missing = [], []
    for order, event in enumerate(phase['source_phase'].get('events', [])):
        owner, slot = event.get('owner', catalog['model_code']), event['slot']
        _require(all(isinstance(value, str) and value and Path(value).name == value
                     and not any(c in value for c in '/\\:') for value in (owner, slot)), 'Unsafe captured effect path')
        path = library / 'Vfx_assets' / owner / (slot + '.json')
        effects.append({'event_order': order, 'path': str(path), 'source_event': event})
        if path.is_file():
            files.add(str(path))
        else:
            missing.append({'code': 'source-effect-missing', 'message': f'Captured effect JSON is missing: {path}'})
    for sound in phase['source_phase'].get('sounds', []):
        key = sound['key']
        _require(isinstance(key, str) and key and not any(c in key for c in '/\\:*?[]'), 'Unsafe source audio key')
        matches = list((model / 'audio').glob(key + '__*.wav'))
        if len(matches) == 1:
            files.add(str(matches[0]))
        else:
            missing.append({'code': 'source-audio-missing', 'message': f'No unique captured WAV for {key}'})
    return {'model_path': catalog['actor_path'], 'model_dir': str(model), 'library': str(library),
            'model_code': catalog['model_code'], 'role': catalog['role'],
            'phase_id': phase_id, 'phase': copy.deepcopy(phase), 'files': [pin_file(path) for path in sorted(files)],
            'effects': effects, 'missing': missing}


def create_recipe(model_path, phase_id, target_chr='', motion_suffix='', donor_bson=None):
    """Select one captured phase and retain all source ordering and identities.

    Unchosen native IDs, sockets, cues and producers remain explicit. The
    caller may edit the returned JSON object and persist it with a .blend.
    """
    source = _source_snapshot(model_path, phase_id)
    sockets = []
    for effect in source['effects']:
        anchor = effect['source_event']['anchor']
        row = {'side': anchor['side'], 'source_socket': anchor['name'], 'native_socket': ''}
        if row not in sockets:
            sockets.append(row)
    donors = {'caster': None, 'target': None}
    if donor_bson is not None:
        values = donor_bson if isinstance(donor_bson, dict) and 'path' not in donor_bson else {'caster': donor_bson}
        for side, value in values.items():
            _require(side in donors, 'Donor side must be caster or target')
            if value is not None:
                donors[side] = copy.deepcopy(value) if isinstance(value, dict) else pin_file(value)
                _check_pin(donors[side])
    return {'format': RECIPE_FORMAT, 'source': source,
            'target': {'model_stem': target_chr, 'motion_suffix': motion_suffix, 'animation_selector': None,
                       'primary_effect_id': None, 'target_effect_id': None, 'socket_inventory': {}},
            'donor_bson': donors, 'donor_template_set': {'caster': 'set1', 'target': 'set1'},
            'socket_mappings': sockets,
            'cue_mappings': [{'source_key': sound['key'], 'cue_id': None, 'component': ''}
                             for sound in source['phase']['source_phase'].get('sounds', [])],
            'components': []}


def validate_native_bindings(value):
    _require(isinstance(value, dict), 'Native bindings must be an object')
    _require(set(value) == {'caster_basename', 'target_basename', 'hit_frames', 'include_audio',
                            'sound_template_set', 'stop_audio_on_end'}, 'Unexpected native binding fields')
    for key in ('caster_basename', 'target_basename', 'sound_template_set'):
        _require(isinstance(value[key], str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,30}', value[key]),
                 'Invalid native binding ' + key)
    _require(value['caster_basename'].casefold() != value['target_basename'].casefold(), 'Caster and target effect names must differ')
    frames = value['hit_frames']
    _require(isinstance(frames, list) and len(frames) <= 8 and all(type(x) is int and 0 < x <= 216000 for x in frames)
             and frames == sorted(set(frames)), 'Native hit frames must be distinct increasing positive integers')
    _require(type(value['include_audio']) is bool and type(value['stop_audio_on_end']) is bool, 'Native audio choices must be boolean')
    return value


def _collect(recipe):
    """Validate and resolve a plan in memory, with no output mutations."""
    _require(isinstance(recipe, dict) and recipe.get('format') == RECIPE_FORMAT, 'Unsupported skill recipe format')
    _json(recipe)  # Reject nonfinite or non-JSON state before using it.
    source = recipe['source']
    errors, blockers, components, files, payloads, generated, compositions = [], [], [], {}, {}, {}, []

    def block(code, message, **fields):
        blockers.append({'code': code, 'message': message, **fields})

    def error(code, exc):
        errors.append({'code': code, 'message': str(exc)})

    try:
        for pin in source['files']:
            _check_pin(pin)
        current = _source_snapshot(source['model_path'], source['phase_id'], source['role'])
        _require(current == source, 'Captured source selection has changed; create a new recipe')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        error('source-stale', exc)
    blockers.extend(source.get('missing', []))
    target = recipe.get('target', {})
    binding = None
    if recipe.get('native_bindings') is not None:
        try:
            binding = validate_native_bindings(recipe['native_bindings'])
        except (ValueError, TypeError, KeyError) as exc:
            error('native-binding', exc)
    for name in ('model_stem', 'motion_suffix'):
        value = target.get(name)
        if not value:
            block('mapping-missing', 'Select native ' + name.replace('_', ' '))
        elif not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,30}', value):
            error('invalid-target', 'Invalid native ' + name)
    for name in ('animation_selector', 'primary_effect_id', 'target_effect_id'):
        value = target.get(name)
        if value is None or value == 0:
            block('mapping-missing', 'Select native ' + name.replace('_', ' '))
        elif type(value) is not int or not 0 < value <= 2147483647:
            error('invalid-target', 'Invalid native ' + name)
    sockets = {}
    for row in recipe.get('socket_mappings', []):
        key = (row.get('side'), row.get('source_socket'))
        if key in sockets or key[0] not in ('caster', 'target') or not isinstance(key[1], str) or not key[1]:
            error('socket-mapping', 'Duplicate or invalid source socket mapping')
        sockets[key] = row.get('native_socket', '')
    ids, node_keys = set(), set()
    coverage_pins = copy.deepcopy(recipe.get('coverage_evidence', []))
    _require(isinstance(coverage_pins, list), 'Coverage evidence must be a list of pinned manifests')
    for pin in recipe.get('components', []):
        try:
            path, raw = _check_pin(pin)
            component = json.loads(raw)
            _require(component.get('format') == COMPONENT_FORMAT, 'Unsupported component format')
            identity = component.get('id')
            _require(isinstance(identity, str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}', identity), 'Invalid component ID')
            _require(identity not in ids, 'Duplicate component ID: ' + identity)
            ids.add(identity)
            _require(component.get('kind') in ('effect', 'actor', 'audio', 'table'), 'Unsupported component kind')
            destination_root = component.get('destination_root', 'patch')
            _require(destination_root in ('patch', 'mod'), 'Unsupported component destination root')
            _require(component['kind'] not in ('effect', 'actor') or destination_root == 'patch',
                     'Effect and actor resources must use the native patch root')
            origin = component['source']
            for key in ('model_code', 'role', 'phase_id'):
                _require(str(origin[key]) == str(source[key]), 'Component belongs to a different model/role/phase')
            origin_path, _ = _check_pin(origin['asset'], path.parent)
            for provenance in component.get('provenance', []):
                _check_pin(provenance, path.parent)
            deps = component['dependencies']
            _require(isinstance(deps, list) and deps, 'Component has no dependencies')
            destinations = set()
            for dependency in deps:
                destination = _destination(dependency['destination'])
                logical = ('dsts-loader/patch/' if destination_root == 'patch' else '') + destination
                resource_path = destination_root + '/' + destination
                folded = logical.casefold()
                _require(destination.casefold() not in destinations, 'Duplicate component dependency destination')
                destinations.add(destination.casefold())
                file_path, file_raw = _check_pin(dependency, path.parent)
                existing = files.get(folded)
                _require(existing is None or (existing['resource_path'] == resource_path and existing['sha256'] == dependency['sha256']),
                         'Dependency destination collision: ' + destination)
                _require(not any(key.startswith(folded + '/') or folded.startswith(key + '/') for key in files),
                         'Dependency file/directory collision: ' + destination)
                files[folded] = {'destination': destination, 'destination_root': destination_root,
                                 'resource_path': resource_path, **pin_file(file_path)}
                payloads[resource_path] = file_raw
            for dependency in deps:
                for reference in dependency.get('references', []):
                    _require(_destination(reference).casefold() in destinations, 'Component dependency closure is missing: ' + reference)
                if dependency['destination'].endswith('.sprk'):
                    spark = SparkDocument.from_bytes(payloads[destination_root + '/' + dependency['destination']])
                    # Known native reference fields, from the maintained codec's
                    # bounded node spans. Unknown node families remain a release
                    # blocker rather than a false complete-closure claim.
                    for chunk in spark._node_chunks.values():
                        node = spark._payload(chunk)
                        kind = struct.unpack_from('<H', node)[0]
                        if kind == 4:
                            _require(len(node) >= 616, 'Truncated Model4 resource fields')
                            figure = bytes(node[552:584]).split(b'\0', 1)[0].decode('ascii')
                            motion = bytes(node[584:616]).split(b'\0', 1)[0].decode('ascii')
                            for suffix in ('.geom', '.anim', '.nlst'):
                                _require((figure + suffix).casefold() in destinations,
                                         'SPRK Figure dependency is missing: ' + figure + suffix)
                            if motion:
                                _require((motion + '.anim').casefold() in destinations, 'SPRK motion dependency is missing: ' + motion)
                        elif kind not in (0,):
                            block('native-closure-review', f'SPRK node kind {kind} requires additional native dependency audit', component=identity)
            if component['kind'] == 'effect':
                order = origin['event_order']
                _require(type(order) is int and 0 <= order < len(source['effects']), 'Invalid source event order')
                event = source['effects'][order]
                _require(origin['source_event'] == event['source_event'], 'Component source event differs from captured event')
                _require(origin_path == Path(event['path']).resolve(), 'Component source asset differs from captured event')
                index = origin['node_index']
                node_key = (order, index)
                _require(type(index) is int and index >= 0 and node_key not in node_keys, 'Duplicate or invalid source node')
                node_keys.add(node_key)
                node_doc = json.loads(origin_path.read_bytes())
                _require(index < len(node_doc.get('nodes', [])) and origin['node_path'] == node_doc['nodes'][index]['path'],
                         'Source node identity differs from pinned asset')
                _require(all(isinstance(component.get('profiles', {}).get(key), str) and component['profiles'][key]
                             for key in ('coordinate', 'render', 'ownership')), 'Effect profiles must be explicit')
                sampling = component['sampling']
                _require(sampling.get('fps') == 30 and type(sampling.get('duration_frames')) is int
                         and 0 < sampling['duration_frames'] <= 216000, 'Unsupported component sampling clock')
                stem = component['native']['figure_stem']
                _require(isinstance(stem, str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,30}', stem), 'Invalid component Figure stem')
                for suffix in ('.geom', '.anim', '.nlst', '.sprk'):
                    _require((stem + suffix).casefold() in destinations, 'Figure companion missing: ' + stem + suffix)
            elif component['kind'] == 'actor':
                native = component.get('native', {})
                _require(origin_path == Path(source['model_path']).resolve()
                         and origin.get('actor_clip') == source['phase']['actor_clip'],
                         'Actor component source model/clip differs from the captured phase')
                _require(all(isinstance(native.get(key), str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,30}', native[key])
                             for key in ('model_stem', 'motion_suffix')), 'Actor component must identify its native model and motion')
                expected_motion = native['model_stem'] + '_' + native['motion_suffix'] + '.anim'
                _require(expected_motion.casefold() in destinations, 'Actor component is missing its named motion')
                block('actor-binding-review', 'Native motion slot and animation appearance require package review and in-game acceptance', component=identity)
                if native['model_stem'] != target.get('model_stem') or native['motion_suffix'] != target.get('motion_suffix'):
                    block('actor-fragment', 'Actor component does not provide the selected native motion slot', component=identity)
            elif component['kind'] == 'audio':
                native = component.get('native', {})
                _require(all(type(native.get(key)) is int and 0 < native[key] <= 2147483647 for key in ('cue_id', 'raw_cue')),
                         'Audio component must identify encoded and raw native cue IDs')
                _require(isinstance(native.get('bank'), str) and re.fullmatch(r'[A-Za-z0-9_.-]+', native['bank']),
                         'Audio component must identify its native bank')
                key = origin['source_key']
                sounds = source['phase']['source_phase'].get('sounds', [])
                _require(origin['source_event'] in sounds and origin['source_event']['key'] == key,
                         'Audio component event differs from captured sound')
                _require(origin_path.suffix.lower() == '.wav' and origin_path.name.startswith(key + '__')
                         and any(Path(pin['path']).resolve() == origin_path for pin in source['files']),
                         'Audio source is not the captured phase WAV')
                mappings = [row for row in recipe.get('cue_mappings', []) if row.get('component') == identity]
                _require(all(row.get('source_key') == key and row.get('cue_id') == native['cue_id'] for row in mappings),
                         'Audio component does not match selected source/native cue mapping')
                block('audio-lifecycle-review', 'Encoded audio requires bank collision, routing and source lifetime acceptance', component=identity)
            for reason in component.get('blockers', []):
                block('component-incomplete', str(reason), component=identity)
            for evidence in component.get('coverage_evidence', []):
                evidence = copy.deepcopy(evidence)
                evidence_path = Path(evidence['path'])
                if not evidence_path.is_absolute():
                    evidence['path'] = str(path.parent / evidence_path)
                coverage_pins.append(evidence)
            components.append({'pin': pin, 'manifest': component})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            error('component-invalid', exc)

    coverage = inspect_coverage(source, [item['manifest'] for item in components], producer_evidence=coverage_pins)
    errors.extend(coverage['errors'])
    for missing in coverage['uncovered']:
        reason = '; '.join(missing['reasons'])
        block('source-system-uncovered',
              f"{missing['side']} event {missing['event_order']} node {missing['node_index']} "
              f"{missing['node_path']} ({missing['render_mode']}): {reason}", **missing)
    for event in coverage.get('events', []):
        for other in event['other_records']:
            block('source-record-uncovered', other['reason'], event_order=event['event_order'], **{key: value for key, value in other.items() if key != 'reason'})

    # BSON is event-local: source side/order/frame stays in the route plan.
    # No source phase is reinterpreted as a complete ordinary skill lifecycle.
    for event in source.get('effects', []):
        order, source_event = event['event_order'], event['source_event']
        selected = [item['manifest'] for item in components if item['manifest']['kind'] == 'effect'
                    and item['manifest']['source']['event_order'] == order]
        anchor = source_event['anchor']
        socket = sockets.get((anchor['side'], anchor['name']))
        if not selected:
            block('effect-components-missing', f'No converted components for source event {order}', event_order=order)
            continue
        block('native-effect-acceptance', f'Converted event {order} still requires native GEOM/material dependency and render/lifecycle acceptance', event_order=order)
        if not socket:
            block('socket-missing', f'Map {anchor["side"]} socket {anchor["name"]}', event_order=order)
            continue
        if any(c.get('profiles', {}).get('ownership') == 'stationary-root-yaw-v1' for c in selected) and socket != '@root':
            error('owner-profile-binding', 'Stationary-root visual assets require the explicit native actor root binding')
            continue
        if socket not in ('@root', '@effect'):
            inventory_pin = target.get('socket_inventory', {}).get(anchor['side'])
            if inventory_pin:
                try:
                    _, inventory_raw = _check_pin(inventory_pin)
                    names = {line.split(',')[0].strip() for line in inventory_raw.decode('utf-8-sig').splitlines() if line.strip()}
                    _require(socket in names, 'Mapped socket is absent from selected NLST: ' + socket)
                except (OSError, ValueError, TypeError) as exc:
                    error('socket-inventory', exc)
            else:
                block('socket-unverified', f'Native socket {socket} requires a pinned owner NLST', event_order=order)
        donor_pin = recipe.get('donor_bson', {}).get(anchor['side'])
        if not donor_pin:
            block('donor-missing', f'Select {anchor["side"]} BSON template', event_order=order)
            continue
        try:
            _, donor_raw = _check_pin(donor_pin)
            figures = [c['native']['figure_stem'] for c in sorted(selected, key=lambda c: c['source']['node_index'])]
            duration = max(c['sampling']['duration_frames'] for c in selected)
            native_options = {}
            name = f'nc_{anchor["side"]}_event_{order:04d}.bson'
            if binding is not None:
                name = binding[anchor['side'] + '_basename'] + '.bson'
                _require('patch/' + name not in generated, 'Named native bindings support one event per owner; use separate recipes for additional events')
                if anchor['side'] == 'caster':
                    native_options['hit_frames'] = binding['hit_frames']
                    if binding['include_audio']:
                        sounds = source['phase']['source_phase'].get('sounds', [])
                        _require(len(sounds) == 1, 'Named native audio currently requires one captured sound event')
                        sound = sounds[0]
                        mappings = [row for row in recipe.get('cue_mappings', []) if row['source_key'] == sound['key']]
                        _require(len(mappings) == 1, 'Choose the captured sound cue mapping')
                        mapping = mappings[0]
                        _require(any(item['manifest']['kind'] == 'audio' and item['manifest']['id'] == mapping['component']
                                     for item in components), 'Named sound binding requires its converted audio component')
                        native_options['audio'] = {'cue_id': mapping['cue_id'], 'frame': sound['frame'] - source_event['frame'],
                                                   'stop_on_end': binding['stop_audio_on_end']}
                        native_options['sound_template_set'] = binding['sound_template_set']
            result, provenance = compose_event(donor_raw, figures, socket=socket, duration_frames=duration,
                         template_set=recipe.get('donor_template_set', {}).get(anchor['side'], 'set1'), **native_options)
            _require(('dsts-loader/patch/' + name).casefold() not in files, 'Generated BSON collides with component resource: ' + name)
            generated['patch/' + name] = result
            compositions.append({'path': name, 'event_order': order, 'source_event': source_event,
                                 'donor': donor_pin, 'provenance': provenance, 'sha256': _sha(result), 'bytes': len(result)})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            error('bson-template', exc)
    if binding is not None and (binding['hit_frames'] or binding['include_audio']):
        if not any(row['source_event']['anchor']['side'] == 'caster' for row in compositions):
            error('native-binding-caster-missing', 'Authored hit frames or source sound require a composed caster event')
    if not any(item['manifest']['kind'] == 'actor' for item in components):
        block('actor-missing', 'Native actor animation component is required')
    for sound in source.get('phase', {}).get('source_phase', {}).get('sounds', []):
        matches = [row for row in recipe.get('cue_mappings', []) if row.get('source_key') == sound['key']]
        if len(matches) != 1 or type(matches[0].get('cue_id')) is not int or not 0 < matches[0]['cue_id'] <= 2147483647:
            block('cue-missing', 'Map native audio cue for ' + sound['key'])
        else:
            block('audio-route-review', f'Cue {matches[0]["cue_id"]} needs bank allocation, source timing and lifetime acceptance')
        audio_id = matches[0].get('component') if len(matches) == 1 else None
        if not audio_id or not any(item['manifest']['id'] == audio_id and item['manifest']['kind'] == 'audio' for item in components):
            block('audio-component-missing', 'Select a converted audio component for ' + sound['key'])
    block('native-routing', 'Native skill rows, caster/target asset bindings and authored effect timing require package review')
    block('game-acceptance', 'Normal/accelerated speed, repeat/abort and arbitrary-target game acceptance remain required')
    if not components:
        block('components-missing', 'Select hash-pinned converted component manifests')
    report = {'format': 'nc-native-skill-inspection-v1', 'source': copy.deepcopy(source),
              'effects': copy.deepcopy(source.get('effects', [])),
              'requirements': ['actor animation assets', 'all source effect systems', 'caster/target socket owners',
                               'BSON event scheduling', 'source audio and cue banks', 'native table routes',
                               'complete dependency closure', 'game acceptance'],
              'errors': errors, 'blockers': blockers, 'coverage': coverage, 'installable': False,
              'buildable': bool(components) and not errors,
              'components': [{'id': item['manifest']['id'], 'kind': item['manifest']['kind'], 'pin': item['pin']} for item in components],
              'files': sorted(files.values(), key=lambda item: item['resource_path']), 'compositions': compositions}
    report['plan_sha256'] = _sha(_json({'recipe': recipe, 'inspection': report}))
    return report, payloads, generated


def inspect_recipe(recipe):
    """Resolve exact inputs, diagnostics and a deterministic review-plan hash."""
    return _collect(copy.deepcopy(recipe))[0]


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def build_candidate(recipe, output, *, expected_plan_sha256=None):
    """Atomically publish a new review directory; never overwrite or install.

    Exact dependencies and actual composed BSON live under resources/patch;
    mod-root audio/table components retain their layout under resources/mod.
    The result is deliberately not a dsts-loader payload. A failed build
    removes its own temporary directory and preserves every existing output.
    """
    recipe = copy.deepcopy(recipe)
    report, payloads, generated = _collect(recipe)
    _require(report['buildable'], 'Candidate is not buildable: ' + '; '.join(row['message'] for row in report['errors'] + report['blockers'][:1]))
    _require(expected_plan_sha256 is None or expected_plan_sha256 == report['plan_sha256'], 'Reviewed plan is stale; inspect again')
    destination = Path(output).absolute()
    _require(destination.name and not destination.exists() and not destination.is_symlink(), 'Candidate destination already exists')
    _require(destination.parent.is_dir() and not any(p.is_symlink() for p in destination.parents), 'Select an existing regular output parent')
    # Normalize Windows short/long names before containment comparisons.
    destination = destination.parent.resolve() / destination.name
    # Cataloged libraries are source captures, never output destinations.
    _require(not destination.is_relative_to(Path(recipe['source']['library']).resolve()),
             'Candidate destination must be outside the captured source library')
    for pin in recipe.get('components', []) + [pin for pin in recipe.get('donor_bson', {}).values() if pin]:
        source_path, _ = _check_pin(pin)
        _require(not destination.is_relative_to(source_path.parent), 'Candidate destination must be outside selected producer/template directories')
    # Do not replace a parent containing any selected input.
    for pin in recipe['source']['files'] + recipe.get('components', []):
        source_path, _ = _check_pin(pin)
        _require(not source_path.is_relative_to(destination), 'Candidate would contain a selected input')
    temporary = Path(tempfile.mkdtemp(prefix='.' + destination.name + '.', dir=destination.parent))
    try:
        for name, raw in sorted({**payloads, **generated}.items()):
            _write(temporary / 'resources' / name, raw)
        _write(temporary / 'recipe.json', _json(recipe))
        _write(temporary / 'inspection.json', _json(report))
        # Pin manifest snapshots alongside the exact native resource set.
        for index, pin in enumerate(recipe.get('components', [])):
            _, raw = _check_pin(pin)
            _write(temporary / 'provenance' / f'component-{index:04d}.json', raw)
        for index, pin in enumerate(report['coverage'].get('evidence', [])):
            _, raw = _check_pin(pin)
            _write(temporary / 'provenance' / f'coverage-{index:04d}.json', raw)
        verified = inspect_recipe(recipe)
        _require(verified['plan_sha256'] == report['plan_sha256'], 'Inputs changed while preparing candidate')
        _require(not destination.exists() and not destination.is_symlink(), 'Candidate destination appeared during build')
        result = {'format': 'nc-native-skill-candidate-v1', 'installable': False, 'output': str(destination),
                  'plan_sha256': report['plan_sha256'], 'blockers': report['blockers'],
                  'files': [{'path': 'resources/' + name, 'sha256': _sha(raw), 'bytes': len(raw)}
                            for name, raw in sorted({**payloads, **generated}.items())],
                  'compositions': report['compositions'],
                  'coverage': report['coverage'],
                  'status': 'Native component review assets; full skill/package/game acceptance incomplete'}
        _write(temporary / 'candidate.json', _json(result))
        # Same-volume Windows rename fails if destination exists. On POSIX,
        # refuse existing paths above; this is a cooperative publication guard.
        os.rename(temporary, destination)
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
