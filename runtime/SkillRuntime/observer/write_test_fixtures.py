"""Write explicitly synthetic offline fixtures; never attach to a process."""
import argparse
import json
from pathlib import Path
from decoder import Decoder, DATA_GLOBAL
from memory import PinnedImage
from test_observer import fixture, BASE, DIRECTION

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--executable',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
args=parser.parse_args()
image=PinnedImage(args.executable); args.output.mkdir(parents=True,exist_ok=True)
for label in ('current-action','no-battle','other-direction'):
    builder=fixture()
    if label=='no-battle': builder.value(BASE+DATA_GLOBAL,0)
    if label=='other-direction': builder.value(DIRECTION,BASE+0x100000)
    decoder=Decoder(builder.memory(),BASE,image.image_size); result=decoder.sample()
    document=decoder.r.fixture(BASE,image.image_size,0); document['origin']='synthetic_test_fixture'
    (args.output/(label+'.json')).write_text(json.dumps(document,indent=2)+'\n',encoding='utf8')
    (args.output/(label+'.expected.json')).write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps({'fixtures':3,'output':str(args.output),'origin':'synthetic_test_fixture'}))
