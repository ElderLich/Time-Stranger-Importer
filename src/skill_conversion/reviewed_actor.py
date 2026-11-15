"""Reuse an audited native motion with an explicit destination motion binding.

This authors an asset component only. Existing animation bytes are preserved;
the game remains responsible for movement and battle execution.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .compiler import _check_pin, _json, _require, _sha, _source_snapshot, pin_file

JOB_FORMAT = 'nc-reviewed-actor-job-v1'


def create_actor_job(recipe, audit_path):
    audit_pin = pin_file(audit_path)
    audit = json.loads(Path(audit_pin['path']).read_bytes())
    source = recipe['source']
    clips = [row for row in audit.get('clips', []) if row.get('source_clip') == source['phase']['actor_clip']]
    _require(len(clips) == 1, 'Choose an audit with exactly one motion for the captured phase clip')
    return {'format': JOB_FORMAT, 'source': copy.deepcopy(source), 'audit': audit_pin,
            'motion_file': clips[0]['file'], 'target': {
                key: recipe['target'].get(key) for key in ('model_stem', 'motion_suffix')}}


def _inspect(job):
    errors, inputs = [], []
    report = {'format': 'nc-reviewed-actor-plan-v1', 'kind': 'actor', 'errors': errors,
              'buildable': False, 'installable': False, 'inputs': inputs,
              'blockers': ['Native motion binding and in-game animation appearance require package review']}
    state = {}
    try:
        _require(job.get('format') == JOB_FORMAT, 'Unsupported reviewed actor job')
        source = job['source']
        _require(_source_snapshot(source['model_path'], source['phase_id'], source['role']) == source,
                 'Captured phase changed; recreate the actor job')
        for pin in source['files']:
            _check_pin(pin)
            inputs.append(pin)
        audit_path, raw = _check_pin(job['audit'])
        inputs.append(job['audit'])
        audit = json.loads(raw)
        _require(audit.get('format') == 'ordinary-attack-actor-fragments-v1', 'Unsupported native motion audit format')
        matches = [row for row in audit['clips'] if row['file'] == job['motion_file']]
        _require(len(matches) == 1, 'Expected one audited native motion')
        motion = matches[0]
        _require(motion['source_clip'] == source['phase']['actor_clip'], 'Audited motion belongs to a different source clip')
        _require(Path(motion['file']).name == motion['file'] and '/' not in motion['file'] and '\\' not in motion['file'],
                 'Audited motion must be a local filename')
        motion_path = audit_path.parent / motion['file']
        motion_pin = pin_file(motion_path)
        _require(motion_pin['sha256'] == motion['sha256'], 'Audited native motion changed')
        inputs.append(motion_pin)
        _require(motion['playback_rate'] == 30 and type(motion['frames']) is int and motion['frames'] > 0
                 and motion['checked_frames'] == motion['frames'] and motion['checked_source_bones'] > 0,
                 'Motion requires a complete 30 Hz source-pose audit')
        for key in ('maximum_position_error', 'maximum_matrix_error'):
            _require(type(motion[key]) in (int, float) and 0 <= motion[key] <= 0.002,
                     'Motion exceeds the established source-pose tolerance')
        target = job['target']
        for key in ('model_stem', 'motion_suffix'):
            _require(isinstance(target.get(key), str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,30}', target[key]),
                     'Choose a valid native actor and motion suffix')
        _require(motion['file'].startswith(target['model_stem'] + '_'),
                 'Audited animation belongs to a different native actor')
        audited_sources = {}
        for name, expected in audit['source_files'].items():
            pin = pin_file(name)
            _require(pin['sha256'] == expected, 'Audited source changed: ' + name)
            audited_sources[str(Path(pin['path']).resolve())] = pin
            inputs.append(pin)
        _require(str(Path(source['model_path']).resolve()) in audited_sources,
                 'Captured source model is absent from the motion audit')
        _require(any(Path(name).name == target['model_stem'] + '.geom' for name in audited_sources),
                 'Native actor geometry is absent from the motion audit')
        inputs.append(pin_file(__file__))
        # Stable de-duplication keeps all original evidence pins without copying
        # the source geometry or changing any animation channel.
        report['inputs'] = list({pin['path']: pin for pin in inputs}.values())
        destination = target['model_stem'] + '_' + target['motion_suffix'] + '.anim'
        report.update(buildable=True, source_clip=motion['source_clip'], source_motion=motion['file'],
                      destination_motion=destination, frames=motion['frames'], bones=motion['bone_count'])
        state.update(motion_path=motion_path, motion_pin=motion_pin, motion=motion)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append({'code': 'actor-job', 'message': str(exc)})
    report['plan_sha256'] = _sha(_json({'job': job, 'inspection': report}))
    return report, state


def inspect_actor_job(job):
    return _inspect(copy.deepcopy(job))[0]


def produce_actor_component(job, output, *, expected_plan_sha256=None):
    job = copy.deepcopy(job)
    report, state = _inspect(job)
    _require(report['buildable'], '; '.join(row['message'] for row in report['errors']))
    _require(expected_plan_sha256 is None or expected_plan_sha256 == report['plan_sha256'],
             'Reviewed actor plan is stale; inspect again')
    destination = Path(output).absolute()
    _require(not any(p.is_symlink() for p in (destination, *destination.parents)), 'Redirected actor output is unsupported')
    destination = destination.resolve()
    _require(not destination.exists(), 'Choose a new actor component folder')
    protected = [Path(job['source']['library']).resolve()]
    protected.extend(Path(pin['path']).resolve().parent for pin in report['inputs'])
    _require(not any(destination == p or destination.is_relative_to(p) or p.is_relative_to(destination) for p in protected),
             'Actor output overlaps an input tree')
    _require(destination.parent.is_dir(), 'Actor output parent must exist')
    pending = destination.parent / ('.' + destination.name + '.pending-' + uuid.uuid4().hex)
    pending.mkdir(mode=0o777)
    try:
        relative = 'resources/patch/' + report['destination_motion']
        path = pending / relative
        path.parent.mkdir(parents=True)
        raw = state['motion_path'].read_bytes()
        _require(_sha(raw) == state['motion_pin']['sha256'], 'Native motion changed during generation')
        path.write_bytes(raw)
        source = job['source']
        component = {'format': 'nc-native-skill-component-v1',
            'id': 'actor.' + source['model_code'] + '.' + source['phase_id'].replace(':', '-') + '.' + job['target']['motion_suffix'],
            'kind': 'actor', 'source': {**{key: source[key] for key in ('model_code', 'role', 'phase_id')},
                'asset': pin_file(source['model_path']), 'actor_clip': report['source_clip']},
            'native': job['target'], 'sampling': {'fps': 30, 'frames': report['frames'], 'bones': report['bones']},
            'dependencies': [{'path': relative, 'destination': report['destination_motion'],
                              'sha256': _sha(raw), 'bytes': len(raw)}],
            'provenance': report['inputs'], 'blockers': report['blockers'],
            'motion_binding': {'source_file': report['source_motion'], 'destination_file': report['destination_motion'],
                               'byte_identical': True, 'battle_execution': 'native game'}}
        manifest = _json(component)
        (pending / 'component.json').write_bytes(manifest)
        (pending / 'job.json').write_bytes(_json(job))
        (pending / 'inspection.json').write_bytes(_json(report))
        _require(inspect_actor_job(job)['plan_sha256'] == report['plan_sha256'], 'Actor inputs changed during generation')
        _require(not destination.exists(), 'Actor destination appeared during generation')
        os.rename(pending, destination)
        return {'format': 'nc-reviewed-actor-production-v1', 'installable': False,
                'plan_sha256': report['plan_sha256'], 'output': str(destination),
                'components': [{'path': str(destination / 'component.json'), 'sha256': _sha(manifest), 'bytes': len(manifest)}],
                'destination_motion': report['destination_motion'], 'animation_sha256': _sha(raw)}
    finally:
        if pending.exists():
            _require(pending.parent.resolve() == destination.parent and pending.name.startswith('.' + destination.name + '.pending-'),
                     'Unexpected actor staging folder')
            shutil.rmtree(pending)
