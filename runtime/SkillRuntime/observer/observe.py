"""Passive battle observer: explicit PID or bounded captured memory; writes local evidence only."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from decoder import Decoder, ObservationEpochs
from memory import PinnedImage, FixtureMemory, Refused, PROFILE


def emit(stream,value):
    stream.write(json.dumps(value,separators=(',',':'),allow_nan=False)+'\n'); stream.flush()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    decode=commands.add_parser('decode'); decode.add_argument('--fixture',required=True,type=Path); decode.add_argument('--executable',required=True,type=Path)
    live=commands.add_parser('observe'); live.add_argument('--pid',required=True,type=int)
    live.add_argument('--seconds',type=float,default=30); live.add_argument('--interval-ms',type=int,default=33)
    live.add_argument('--output',required=True,type=Path); live.add_argument('--capture-dir',type=Path)
    live.add_argument('--capture-every',type=int,default=1)
    args=parser.parse_args()
    if args.command == 'decode':
        image=PinnedImage(args.executable); memory,base,fixture=FixtureMemory.load(args.fixture,image)
        result=Decoder(memory,base,image.image_size).sample(); result['fixture_sample']=fixture.get('sample')
        result['fixture_origin']=fixture.get('origin','unspecified'); result['fixture_metadata_not_independent_proof']=True
        emit(sys.stdout,ObservationEpochs().label(result)); return 0
    if not 0 < args.seconds <= 120 or not 5 <= args.interval_ms <= 5000 or not 1 <= args.capture_every <= 1000:
        raise Refused('Duration/interval/capture rate outside bounded limits')
    if args.seconds*1000/args.interval_ms > 10000: raise Refused('More than 10000 samples requested')
    # Importing this module does not open a process. Only the explicit observe command does.
    from windows_memory import WindowsMemory
    with WindowsMemory(args.pid) as memory:
        if args.capture_dir:
            args.capture_dir.mkdir(parents=True,exist_ok=False)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf8') as stream:
            emit(stream,{'kind':'session','profile':PROFILE,'pid':args.pid,'module_base':hex(memory.base),
                         'executable':memory.image.path,'executable_sha256':memory.image.sha256,'image_size':memory.image.image_size,
                         'read_only':True,'native_calls':False,'process_suspended':False,'runtime_enabled':False})
            epochs=ObservationEpochs(); started=time.monotonic(); index=0; captured=0; failures=0
            while time.monotonic()-started < args.seconds:
                tick=time.monotonic(); decoder=Decoder(memory,memory.base,memory.image.image_size)
                try:
                    value=epochs.label(decoder.sample())
                except Refused as error:
                    value=epochs.label({'status':'refused_sample','reason':str(error),'current_action':None,'actors':[]}); failures+=1
                value.update(kind='sample',sample=index,utc=datetime.now(timezone.utc).isoformat(),
                             elapsed_seconds=time.monotonic()-started,read_duration_seconds=time.monotonic()-tick)
                emit(stream,value)
                if args.capture_dir and value['status'] != 'refused_sample' and index % args.capture_every == 0:
                    payload=json.dumps(decoder.r.fixture(memory.base,memory.image.image_size,index),separators=(',',':')).encode('utf8')
                    if captured+len(payload) > 64*1024*1024:
                        emit(stream,{'kind':'stop','reason':'64 MiB capture budget reached'}); break
                    (args.capture_dir/f'{index:05d}.json').write_bytes(payload); captured+=len(payload)
                index+=1
                time.sleep(max(0,args.interval_ms/1000-(time.monotonic()-tick)))
            summary={'kind':'summary','samples':index,'refused_samples':failures,'capture_bytes':captured,'runtime_enabled':False}
            emit(stream,summary); emit(sys.stdout,summary)
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (Refused,OSError,ValueError) as error:
        print(json.dumps({'status':'refused','reason':str(error),'runtime_enabled':False}),file=sys.stderr); raise SystemExit(2)
