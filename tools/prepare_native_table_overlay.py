"""Apply explicit pinned native-table row edits into a new review directory."""
import argparse
import copy
import csv
import io
import json
import math
import os
from pathlib import Path
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from skill_conversion.compiler import _check_pin, _destination, _require, pin_file


def rows(pin):
    _, raw = _check_pin(pin)
    result = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True))
    _require(result and len(result[0]) > 0 and all(len(row) == len(result[0]) for row in result), 'Inconsistent native table width')
    _require(all(row[0] and '\0' not in row[0] for row in result[1:]), 'Empty or invalid native table ID')
    _require(len({row[0] for row in result[1:]}) == len(result)-1, 'Duplicate native table ID')
    return result


def _cell(value):
    _require(type(value) in (str, int, float) and (type(value) is not float or math.isfinite(value)),
             'Native table values must be finite scalar cells')
    result = str(value)
    _require('\0' not in result, 'Native table cells cannot contain NUL')
    return result


def _redirected(path):
    return any(p.is_symlink() or (getattr(p, 'is_junction', lambda: False)()) for p in (path, *path.parents))


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(job, output):
    job = copy.deepcopy(job)
    _require(job.get('format') == 'nc-native-table-overlay-job-v1', 'Unsupported native table overlay job')
    _require(isinstance(job.get('tables'), list) and job['tables'], 'Select at least one native table')
    output = Path(output).absolute()
    _require(not _redirected(output), 'Redirected table output is unsupported')
    output = output.resolve()
    _require(not output.exists() and output.parent.is_dir(), 'Choose a new table overlay directory')
    prepared, inputs, destinations = {}, [], set()
    for table in job['tables']:
        name = _destination(table['destination'])
        folded = name.casefold()
        _require(name.lower().endswith('.ap.csv') and folded not in destinations, 'Choose a unique native patch CSV destination')
        _require(not any(folded.startswith(other + '/') or other.startswith(folded + '/') for other in destinations),
                 'Native CSV destinations have a file/directory collision')
        destinations.add(folded)
        _require(isinstance(table.get('operations'), list) and table['operations'], 'Select at least one explicit table operation')
        original = rows(table['source'])
        result = copy.deepcopy(original)
        inputs.append(table['source'])
        changes = []
        for operation in table['operations']:
            clone = operation.get('kind') == 'clone'
            _require(clone or operation.get('kind') == 'update', 'Unknown table operation')
            donor_pin = operation.get('source', table['source'])
            donor = rows(donor_pin)
            inputs.append(donor_pin)
            _require(donor[0] == original[0], 'Native donor table schema differs')
            source_id = _cell(operation['source_id'])
            selected = [row for row in donor[1:] if row[0] == source_id]
            _require(len(selected) == 1, 'Expected one source table row: ' + source_id)
            row = list(selected[0])
            before = list(row)
            if clone:
                row_id = _cell(operation['new_id'])
                _require(row_id and not any(c in row_id for c in '\r\n'), 'New table ID must be nonempty and single-line')
                _require(not any(r[0] == row_id for r in result[1:]), 'New table ID collides: ' + row_id)
                row[0] = row_id
            else:
                _require(donor_pin == table['source'], 'Updates must use the selected baseline table')
                row_id = source_id
                row = next(r for r in result[1:] if r[0] == row_id)
                before = list(row)
            _require(isinstance(operation.get('fields'), dict), 'Native table fields must be an explicit column map')
            columns = set()
            for field, edit in operation['fields'].items():
                _require(type(field) in (str, int), 'Native table columns must be integer indices')
                index = int(field)
                _require(str(index) == str(field) and index not in columns, 'Duplicate or noncanonical native table column')
                columns.add(index)
                _require(0 < index < len(row) and isinstance(edit, dict) and set(edit) == {'before', 'after'}, 'Invalid native table field edit')
                _require(row[index] == _cell(edit['before']), 'Native field precondition differs')
                row[index] = _cell(edit['after'])
            if clone:
                result.append(row)
            changes.append({'kind': operation['kind'], 'source': donor_pin, 'source_id': source_id, 'id': row_id,
                            'fields': [{'column': i, 'before': a, 'after': b} for i,(a,b) in enumerate(zip(before,row)) if a != b]})
        stream = io.StringIO(newline='')
        csv.writer(stream, lineterminator='\n').writerows(result)
        raw = stream.getvalue().encode('utf8')
        _require(list(csv.reader(io.StringIO(raw.decode('utf8')))) == result, 'Native table roundtrip differs')
        prepared[name] = (raw, table['source'], changes)
    for pin in inputs:
        path, _ = _check_pin(pin)
        _require(not output.is_relative_to(path.parent) and not path.is_relative_to(output), 'Table output overlaps an input tree')
    pending = output.parent / ('.' + output.name + '.pending-' + uuid.uuid4().hex)
    pending.mkdir(mode=0o777)
    try:
        overlays, changes = [], []
        for name, (raw, source, edits) in prepared.items():
            path = pending / name
            _write(path, raw)
            pin = pin_file(path)
            pin['path'] = str(output / name)
            overlays.append({'file': pin, 'destination': name, 'replaces_sha256': source['sha256']})
            changes.append({'destination': name, 'source': source, 'operations': edits})
        report = {'format': 'nc-native-table-overlay-v1', 'live_payload_writes': 0,
                  'inputs': list({pin['path']: pin for pin in inputs}.values()), 'overlays': overlays, 'changes': changes}
        _write(pending / 'table-overlay-report.json', (json.dumps(report, indent=2) + '\n').encode('utf8'))
        for pin in inputs:
            _check_pin(pin)
        _require(not output.exists() and not _redirected(output), 'Table destination appeared or changed during generation')
        # On the supported Windows workspace, rename fails if any destination
        # already exists; a racing directory never replaces the staged result.
        os.rename(pending, output)
        return report
    finally:
        if pending.exists():
            _require(pending.parent.resolve() == output.parent and not _redirected(pending)
                     and pending.name.startswith('.' + output.name + '.pending-'), 'Unexpected table staging folder')
            shutil.rmtree(pending)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = prepare(json.loads(args.job.read_bytes()), args.output)
    print(json.dumps({'tables': len(report['overlays']), 'live_payload_writes': 0}))
