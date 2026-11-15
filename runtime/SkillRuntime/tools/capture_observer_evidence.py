"""Pin the passive observer milestone and verify native root prefixes offline."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
WORKSPACE=ROOT.parents[2]
sys.path.insert(0,str(ROOT/'observer'))
from memory import PinnedImage


def pin(path):
    data=path.read_bytes()
    try: label=path.relative_to(WORKSPACE).as_posix()
    except ValueError: label=str(path)
    return {'path':label,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--executable',required=True,type=Path); args=parser.parse_args()
    image=PinnedImage(args.executable)
    native=WORKSPACE/'Time Stranger/local/ordinary-skill-native-candidate/actors/passive-observer-roots-2026-11-15'
    index_path=native/'index.json'; index=json.loads(index_path.read_text('utf8')); evidence=[pin(index_path)]; prefixes=[]
    for function in index['functions']:
        if 'first_64_bytes' in function:
            expected=bytes.fromhex(function['first_64_bytes']); rva=int(function['address'],16)-image.image_base
            if image.code(rva,len(expected)) != expected: raise ValueError('Native root prefix differs from executable')
            prefixes.append({'rva':hex(rva),'hex':expected.hex(),'file_match':True})
        for item in function.get('files',[]):
            actual=pin(native/item['path'])
            if actual['sha256'] != item['sha256']: raise ValueError('Native evidence hash changed')
            evidence.append(actual)
    for name in ('0000000140c54c20.cpp',): evidence.append(pin(WORKSPACE/'Time Stranger/src/native/functions'/name))
    sources=[]
    for path in sorted(ROOT.rglob('*')):
        if not path.is_file() or set(path.relative_to(ROOT).parts)&{'bin','obj','artifacts','__pycache__'}: continue
        if path.name in ('passive-observer-evidence.json','offline-build-evidence.json'): continue
        sources.append(pin(path))
    build=json.loads((ROOT/'artifacts/build-report.json').read_text('utf-8-sig'))
    test_log=(ROOT/'artifacts/observer-tests.log').read_text('utf-8-sig')
    if 'Ran 23 tests' not in test_log or not test_log.rstrip().endswith('OK'): raise ValueError('Observer tests are not passing')
    if build['host_framework']!='net9.0' or not build['host_tests']['hostSmokePassed']: raise ValueError('net9 host smoke missing')
    logs=[pin(path) for path in sorted((ROOT/'artifacts').glob('*.log'))]
    fixtures=[pin(path) for path in sorted((ROOT/'artifacts/observer-fixtures').glob('*.json'))]
    readiness=json.loads((ROOT/'artifacts/observer-readiness.json').read_text('utf-8-sig'))
    output=ROOT/'research/passive-observer-evidence.json'
    output.write_text(json.dumps({'schema':'skill-runtime-passive-observer-evidence-v1','executable':pin(args.executable),
        'prefixes':prefixes,'native_evidence':evidence,'source_files':sources,'core_tests':build['core_tests'],
        'host_tests':build['host_tests'],'host_framework':'net9.0','observer_tests':{'run':23,'passed':23},
        'build_report':pin(ROOT/'artifacts/build-report.json'),'logs':logs,'synthetic_fixtures':fixtures,
        'reference_package':json.loads((ROOT/'artifacts/dependencies/reference-package.json').read_text('utf-8-sig')),
        'readiness':readiness,'native_runtime_enabled':False,'live_game_capture_verified':False},indent=2)+'\n',encoding='utf8')
    print(json.dumps({'output':str(output),'sha256':pin(output)['sha256'],'verified_native_prefixes':len(prefixes),'source_files':len(sources)}))


if __name__=='__main__': main()
