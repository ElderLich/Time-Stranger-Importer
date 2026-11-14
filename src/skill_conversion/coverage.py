"""Inspect exact captured-system coverage without inferring emission semantics.

Coverage means that a selected, already validated component represents a source
node. It never means runtime parity. Producer rejection reasons are accepted
only with matching source/event/node identity and current provenance hashes.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re

EVIDENCE_FORMAT = 'nc-native-skill-coverage-evidence-v1'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _curve(value):
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    result = {key: copy.deepcopy(value[key]) for key in ('minMaxState', 'scalar', 'minScalar') if key in value}
    for key in ('minCurve', 'maxCurve'):
        if isinstance(value.get(key), dict):
            curve = value[key]
            result[key] = {'key_count': len(curve.get('m_Curve', [])),
                           'pre_infinity': curve.get('m_PreInfinity'), 'post_infinity': curve.get('m_PostInfinity')}
    return result


def _features(node):
    ps = node.get('ps') if isinstance(node.get('ps'), dict) else {}
    renderer = node.get('renderer') if isinstance(node.get('renderer'), dict) else {}
    dynamics = ps.get('dynamics') if isinstance(ps.get('dynamics'), dict) else {}
    emission = ps.get('emission') if isinstance(ps.get('emission'), dict) else {}
    return {'has_particle_record': isinstance(node.get('ps'), dict),
            'active': node.get('active'), 'renderer_enabled': node.get('rendererEnabled', renderer.get('enabled')),
            'render_mode': ps.get('renderMode', renderer.get('renderMode')),
            'alignment': renderer.get('alignment'), 'simulation_space': dynamics.get('simulationSpace'),
            'scaling_mode': ps.get('scalingMode'), 'rotation_3d': ps.get('rotation3D'), 'size_3d': ps.get('size3D'),
            'loop': ps.get('loop'), 'start_delay': ps.get('startDelay'), 'max_particles': ps.get('maxParticles'),
            'flipbook': ps.get('flipbook'), 'mesh_file': node.get('meshFile'),
            'pivot': copy.deepcopy(renderer.get('pivot')), 'stretch': copy.deepcopy(renderer.get('stretch')),
            'min_particle_size': renderer.get('minParticleSize'), 'max_particle_size': renderer.get('maxParticleSize'),
            'vertex_streams': copy.deepcopy(renderer.get('vertexStreams')),
            'use_custom_vertex_streams': renderer.get('useCustomVertexStreams'),
            'dynamics_modules': sorted(dynamics), 'export_summary_emits': ps.get('emits'),
            'emission': {'captured': isinstance(ps.get('emission'), dict), 'enabled': emission.get('enabled'),
                         'rate_over_time': _curve(emission.get('rateOverTime')),
                         'rate_over_distance': _curve(emission.get('rateOverDistance')),
                         'bursts': [{**{key: copy.deepcopy(burst[key]) for key in ('time', 'cycleCount', 'repeatInterval', 'probability') if key in burst},
                                     'count': _curve(burst.get('countCurve'))}
                                    for burst in emission.get('bursts', [])]},
            'materials': [{'name': material.get('name'), 'shader': material.get('shader'),
                           'keywords': copy.deepcopy(material.get('keywords', [])),
                           'pass_states': copy.deepcopy(material.get('passStates', []))}
                          for material in node.get('material_infos', [])]}


def inspect_coverage(source, components, *, producer_evidence=()):
    """Return event-ordered source systems, selected coverage and exact gaps.

    ``source`` is the recipe's pinned source snapshot; ``components`` are loaded
    manifests that the compiler has validated. Optional producer evidence is a
    sequence of file pins using nc-native-skill-coverage-evidence-v1. This
    function checks their hashes and identities independently. It never drops
    a node for a zero rate, disabled renderer, inactive flag or empty bake.
    """
    errors, events, evidence_pins, cache = [], [], [], {}

    def error(code, exc, **fields):
        errors.append({'code': code, 'message': str(exc), **fields})

    def read_pin(pin, base=None):
        _require(isinstance(pin, dict) and isinstance(pin.get('path'), str), 'Expected a coverage file pin')
        _require(isinstance(pin.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', pin['sha256']), 'Invalid coverage SHA-256')
        _require(type(pin.get('bytes')) is int and pin['bytes'] >= 0, 'Invalid coverage byte count')
        path = Path(pin['path'])
        if not path.is_absolute():
            _require(base is not None, 'Coverage pins require absolute paths or a manifest base')
            path = base / path
        path = path.absolute()
        _require(not any(part.is_symlink() for part in (path, *path.parents)), 'Symbolic-link coverage input rejected')
        path = path.resolve()
        key = str(path), pin['sha256'], pin['bytes']
        if key not in cache:
            raw = path.read_bytes()
            _require(len(raw) == pin['bytes'] and hashlib.sha256(raw).hexdigest() == pin['sha256'],
                     'Stale coverage input: ' + str(path))
            cache[key] = raw
        return path, cache[key]

    try:
        _require(isinstance(source, dict) and isinstance(source.get('effects'), list), 'Source snapshot has no ordered events')
        source_files = {str(Path(pin['path']).resolve()): pin for pin in source['files']}
        identity = {key: str(source[key]) for key in ('model_code', 'role', 'phase_id')}
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return {'format': 'nc-native-skill-coverage-v1', 'events': [], 'errors': [{'code': 'coverage-source', 'message': str(exc)}],
                'systems': 0, 'represented': 0, 'uncovered': [], 'coverage_complete': False, 'game_acceptance': False}

    event_map = {}
    for entry in source['effects']:
        order = entry.get('event_order')
        try:
            _require(type(order) is int and order >= 0 and order not in event_map, 'Duplicate or invalid source event order')
            path = Path(entry['path']).resolve()
            _require(str(path) in source_files, 'Event asset is absent from pinned source files')
            pin = source_files[str(path)]
            _, raw = read_pin(pin)
            document = json.loads(raw)
            _require(isinstance(document.get('nodes'), list), 'Captured event has no node array')
            source_event = entry['source_event']
            anchor, prefab = source_event['anchor'], source_event['prefab']
            _require(anchor.get('side') in ('caster', 'target') and isinstance(anchor.get('name'), str), 'Invalid captured event owner')
            _require(isinstance(prefab, str) and prefab, 'Captured event prefab is missing')
            prefab_key = prefab.casefold()
            effect_nodes = document.get('effect_nodes', [])
            prefab_paths = {}
            for node in effect_nodes:
                if isinstance(node.get('prefab'), str):
                    prefab_paths.setdefault(node.get('path'), set()).add(node['prefab'].casefold())
            declared_prefabs = document.get('prefabs', [])
            rows, outside = [], []
            for index, node in enumerate(document['nodes']):
                node_path = node.get('path')
                _require(isinstance(node_path, str) and node_path, f'Source node {index} lacks a path')
                membership = prefab_paths.get(node_path, set())
                if not membership:
                    membership = {name.casefold() for name in declared_prefabs
                                  if node_path.casefold() == name.casefold() or node_path.casefold().startswith(name.casefold() + '/')}
                if not membership and (node_path.casefold() == prefab_key or node_path.casefold().startswith(prefab_key + '/')):
                    membership = {prefab_key}
                if membership and prefab_key not in membership:
                    outside.append(index)
                    continue
                features = _features(node)
                rows.append({'event_order': order, 'node_index': index, 'node_path': node_path,
                             'side': anchor['side'], 'source_socket': anchor['name'], 'source_asset': copy.deepcopy(pin),
                             'features': features, 'status': 'missing', 'component_ids': [], 'profiles': [], 'evidence': [],
                             'reasons': ([] if membership == {prefab_key} else ['Source prefab membership is unresolved'])})
            particle_paths = {node['path'] for node in document['nodes']}
            other_records = []
            for index, node in enumerate(effect_nodes):
                if not isinstance(node.get('prefab'), str) or node['prefab'].casefold() != prefab_key:
                    continue
                extra_components = set(node.get('components', [])) - {'Transform', 'ParticleSystem', 'ParticleSystemRenderer'}
                if node.get('path') not in particle_paths or extra_components:
                    other_records.append({'effect_node_index': index, 'node_path': node.get('path'),
                                          'components': copy.deepcopy(node.get('components', [])),
                                          'reason': 'Additional captured scene/controller records require separate coverage'})
            event = {'event_order': order, 'source_event': copy.deepcopy(source_event), 'source_asset': copy.deepcopy(pin),
                     'systems': rows, 'other_prefab_node_indices': outside, 'other_records': other_records}
            events.append(event)
            event_map[order] = event
        except (KeyError, TypeError, ValueError, OSError) as exc:
            error('coverage-source', exc, event_order=order)

    def source_node(origin, index, node_path, base=None):
        _require(all(str(origin.get(key)) == value for key, value in identity.items()), 'Coverage source model/role/phase differs')
        order = origin.get('event_order')
        _require(type(order) is int and order in event_map, 'Coverage event order is absent')
        event = event_map[order]
        _require(origin.get('source_event') == event['source_event'], 'Coverage event differs from captured event')
        asset = origin.get('asset', {})
        expected = event['source_asset']
        _require(asset.get('sha256') == expected['sha256'] and asset.get('bytes') == expected['bytes'], 'Coverage asset fingerprint differs')
        # Components were already resolved by the compiler; evidence resolves
        # its own relative asset pin against the evidence document directory.
        if base is not None or Path(asset.get('path', '')).is_absolute():
            path, _ = read_pin(asset, base)
            _require(path == Path(expected['path']).resolve(), 'Coverage asset path differs')
        _require(type(index) is int, 'Coverage node index must be an integer')
        rows = [row for row in event['systems'] if row['node_index'] == index]
        _require(len(rows) == 1 and rows[0]['node_path'] == node_path, 'Coverage node identity is absent or differs')
        return rows[0]

    for component in components:
        if component.get('kind') != 'effect':
            continue
        try:
            origin = component['source']
            row = source_node(origin, origin.get('node_index'), origin.get('node_path'))
            component_id = component.get('id')
            _require(isinstance(component_id, str) and component_id and not row['component_ids'], 'Duplicate or invalid component coverage')
            row['component_ids'].append(component_id)
            row['profiles'].append(copy.deepcopy(component.get('profiles', {})))
            row['status'] = 'represented'
        except (KeyError, TypeError, ValueError, OSError) as exc:
            error('coverage-component', exc, component=component.get('id'))

    seen_evidence = set()
    for pin in producer_evidence:
        try:
            path, raw = read_pin(pin)
            if str(path) in seen_evidence:
                continue
            seen_evidence.add(str(path))
            document = json.loads(raw)
            _require(document.get('format') == EVIDENCE_FORMAT and isinstance(document.get('records'), list), 'Unsupported coverage evidence format')
            evidence_pin = {'path': str(path), 'sha256': pin['sha256'], 'bytes': pin['bytes']}
            evidence_pins.append(evidence_pin)
            for record in document['records']:
                origin = record['source']
                if any(str(origin.get(key)) != value for key, value in identity.items()):
                    continue
                row = source_node(origin, record.get('node_index'), record.get('node_path'), path.parent)
                status, reason = record.get('status'), record.get('reason')
                _require(status in ('covered', 'rejected', 'unsupported', 'inactive'), 'Unknown producer coverage status')
                _require(isinstance(reason, str) and reason, 'Producer coverage reason is missing')
                provenance = record.get('provenance')
                _require(isinstance(provenance, list) and provenance, 'Producer coverage requires pinned provenance')
                for proof in provenance:
                    read_pin(proof, path.parent)
                row['evidence'].append({'manifest': evidence_pin, 'status': status, 'reason': reason,
                                        'provenance': copy.deepcopy(provenance)})
        except (KeyError, TypeError, ValueError, OSError) as exc:
            error('coverage-evidence', exc)

    uncovered, systems, covered = [], 0, 0
    for event in events:
        for row in event['systems']:
            systems += 1
            negative = [item for item in row['evidence'] if item['status'] != 'covered']
            if row['component_ids']:
                if negative:
                    row['status'] = 'conflict'
                    row['reasons'].append('Selected component conflicts with producer rejection/inactivity evidence')
                    error('coverage-conflict', row['reasons'][-1], event_order=row['event_order'], node_index=row['node_index'])
                elif row['reasons']:
                    row['status'] = 'unresolved'
                elif not row['reasons']:
                    covered += 1
            else:
                row['status'] = negative[0]['status'] if negative else 'missing'
                row['reasons'].append('No converted component selected for this captured system')
            row['reasons'].extend(item['reason'] for item in row['evidence'])
            row['reasons'] = list(dict.fromkeys(row['reasons']))
            if row['status'] != 'represented' or not row['component_ids']:
                uncovered.append({'event_order': row['event_order'], 'node_index': row['node_index'], 'node_path': row['node_path'],
                                  'source_sha256': row['source_asset']['sha256'], 'side': row['side'], 'source_socket': row['source_socket'],
                                  'render_mode': row['features']['render_mode'], 'status': row['status'], 'reasons': row['reasons']})
        event['represented'] = sum(row['status'] == 'represented' and bool(row['component_ids']) for row in event['systems'])
        event['uncovered'] = len(event['systems']) - event['represented']
    return {'format': 'nc-native-skill-coverage-v1', 'events': events, 'systems': systems, 'represented': covered,
            'uncovered': uncovered, 'evidence': evidence_pins, 'errors': errors,
            'coverage_complete': bool(events) and not errors and covered == systems and not any(event['other_records'] for event in events),
            'game_acceptance': False,
            'meaning': 'Selected component coverage only; zero emission/inactive flags never silently omit source systems'}
