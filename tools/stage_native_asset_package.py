"""Stage exact native assets over a pinned baseline; never install a live mod."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from skill_conversion.compiler import _check_pin, _destination, _json, _require, _sha, pin_file


def stage(job, output):
    _require(job.get('format') == 'nc-native-asset-package-job-v1', 'Unsupported package job')
    destination = Path(output).absolute()
    _require(not any(p.is_symlink() for p in (destination, *destination.parents)), 'Redirected package output is unsupported')
    destination = destination.resolve()
    _require(not destination.exists() and destination.parent.is_dir(), 'Choose a new package folder with an existing parent')
    baseline_path, raw = _check_pin(job['baseline'])
    baseline = json.loads(raw)
    inputs, claims, replacements, protected_roots = [job['baseline']], {}, [], [baseline_path.parent]

    def add(source, name, expected, origin, *, replace=None):
        name = _destination(name)
        key = name.casefold()
        _require(key.split('/')[0] not in ('assembly-report.json', 'package-job.json'),
                 'Reserved package metadata destination')
        actual = pin_file(source)
        _require(actual['sha256'] == expected['sha256'] and ('bytes' not in expected or actual['bytes'] == expected['bytes']),
                 'Stale package input: ' + str(source))
        prior = claims.get(key)
        _require(not any(k.startswith(key + '/') or key.startswith(k + '/') for k in claims), 'Package file/directory collision')
        if prior:
            _require(prior['destination'] == name, 'Case-aliased package destination')
            if prior['sha256'] == actual['sha256']:
                return
            _require(replace == prior['sha256'] and prior['origin'] == 'baseline', 'Unreviewed package replacement: ' + name)
            replacements.append({'path': name, 'before': prior['sha256'], 'after': actual['sha256']})
        else:
            _require(replace is None, 'Replacement does not exist in baseline: ' + name)
        claims[key] = {**actual, 'destination': name, 'origin': origin}

    for entry in baseline['files']:
        name = _destination(entry['path'])
        add(baseline_path.parent / name, name, entry, 'baseline')
    candidate_reports = []
    for candidate_pin in job.get('candidates', []):
        path, raw = _check_pin(candidate_pin)
        inputs.append(candidate_pin)
        protected_roots.append(path.parent)
        candidate = json.loads(raw)
        _require(candidate.get('format') == 'nc-native-skill-candidate-v1', 'Unsupported recipe candidate')
        _require(candidate['coverage']['represented'] == candidate['coverage']['systems'] and not candidate['coverage']['uncovered'],
                 'Package candidate has unrepresented source systems')
        for entry in candidate['files']:
            relative = _destination(entry['path'])
            if relative.startswith('resources/patch/'):
                name = 'dsts-loader/patch/' + relative[len('resources/patch/'):]
            elif relative.startswith('resources/mod/'):
                name = relative[len('resources/mod/'):]
            else:
                raise ValueError('Unexpected candidate resource namespace: ' + relative)
            add(path.parent / relative, name, entry, 'candidate')
        candidate_reports.append({'pin': candidate_pin, 'coverage': candidate['coverage'], 'plan_sha256': candidate['plan_sha256']})
    for overlay in job.get('overlays', []):
        path, _ = _check_pin(overlay['file'])
        inputs.append(overlay['file'])
        protected_roots.append(path.parent)
        add(path, overlay['destination'], overlay['file'], 'reviewed-overlay', replace=overlay.get('replaces_sha256'))
    _require(claims and candidate_reports, 'Package requires a baseline and at least one complete source candidate')
    _require(not any(destination == p or destination.is_relative_to(p) or p.is_relative_to(destination) for p in protected_roots),
             'Package output overlaps an input tree')
    pending = destination.parent / ('.' + destination.name + '.pending-' + uuid.uuid4().hex)
    pending.mkdir(mode=0o777)
    files = []
    try:
        for entry in sorted(claims.values(), key=lambda row: row['destination']):
            source = Path(entry['path'])
            raw = source.read_bytes()
            _require(_sha(raw) == entry['sha256'] and len(raw) == entry['bytes'], 'Package input changed during staging')
            target = pending / entry['destination']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            _require(_sha(target.read_bytes()) == entry['sha256'], 'Staged package hash differs')
            files.append({'path': entry['destination'], 'sha256': entry['sha256'], 'bytes': entry['bytes'], 'origin': entry['origin']})
        for pin in inputs:
            _check_pin(pin)
        # Recheck source resource hashes, not only their manifest files.
        for entry in claims.values():
            _check_pin(entry)
        report = {'format': 'nc-native-asset-package-v1', 'status': 'staged for native visual testing; not installed',
                  'game_acceptance': False, 'live_payload_writes': 0, 'baseline': job['baseline'],
                  'managed_files': len(files), 'bytes': sum(row['bytes'] for row in files),
                  'replacements': replacements, 'new_files': sum(row['origin'] != 'baseline' for row in files) - len(replacements),
                  'candidates': candidate_reports, 'limitations': job.get('limitations', []), 'files': files}
        (pending / 'assembly-report.json').write_bytes(_json(report))
        (pending / 'package-job.json').write_bytes(_json(job))
        _require(not destination.exists(), 'Package destination appeared during staging')
        os.rename(pending, destination)
        return report
    finally:
        if pending.exists():
            _require(pending.parent.resolve() == destination.parent and pending.name.startswith('.' + destination.name + '.pending-'),
                     'Unexpected package staging folder')
            shutil.rmtree(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = stage(json.loads(args.job.read_bytes()), args.output)
    print(json.dumps({key: report[key] for key in ('status', 'managed_files', 'bytes', 'new_files', 'replacements', 'live_payload_writes')}))


if __name__ == '__main__':
    main()
