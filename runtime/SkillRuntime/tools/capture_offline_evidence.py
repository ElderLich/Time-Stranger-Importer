"""Capture hashes for an already completed offline runtime build; never rebuild/install."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pin(path):
    data = path.read_bytes()
    try:
        label = path.relative_to(ROOT).as_posix()
    except ValueError:
        label = str(path)
    return dict(path=label, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def main():
    report_path = ROOT / 'artifacts/build-report.json'
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    if report['core_tests']['failed'] or not report['host_tests']['hostSmokePassed']:
        raise ValueError('Expected passing core and host tests')
    if not report['native_profile']['FingerprintMatches'] or report['native_profile']['RuntimeEnabled']:
        raise ValueError('Expected known executable with runtime activation refused')
    outputs = []
    for original in report['outputs']:
        actual = pin(Path(original['path']))
        if actual['sha256'] != original['sha256']:
            raise ValueError('Build output changed since report: ' + original['path'])
        outputs.append(actual)
    sources = []
    for path in sorted(ROOT.rglob('*')):
        if not path.is_file() or set(path.relative_to(ROOT).parts) & {'bin', 'obj', 'artifacts', '__pycache__'}:
            continue
        if path.name == 'offline-build-evidence.json':
            continue
        sources.append(pin(path))
    logs = [pin(path) for path in sorted((ROOT / 'artifacts').glob('*.log'))]
    rejection = (ROOT / 'artifacts/rejected-interface-build.log').read_text(encoding='utf-8-sig')
    if 'Reloaded interface fingerprint differs' not in rejection:
        raise ValueError('Missing dependency fingerprint negative check')
    output = ROOT / 'research/offline-build-evidence.json'
    output.write_text(json.dumps({
        'schema': 'skill-runtime-offline-evidence-v1', 'sdk': report['sdk'],
        'core_framework': report['core_framework'], 'host_framework': report['host_framework'],
        'core_tests': report['core_tests'], 'host_tests': report['host_tests'],
        'unreviewed_interface_rejected': True, 'dependency': report['reloaded_interface'],
        'native_profile': report['native_profile'], 'outputs': outputs,
        'sources': sources, 'build_report': pin(report_path), 'logs': logs,
        'native_runtime_enabled': False, 'live_loader_tested': False,
        'public_addon_package_includes_runtime': False,
    }, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'sha256': pin(output)['sha256'], 'source_files': len(sources), 'logs': len(logs)}))


if __name__ == '__main__':
    main()
