"""Offline fixtures for the passive reader; no process is opened by these tests."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from decoder import Decoder, ObservationEpochs, APP_GLOBAL, DATA_GLOBAL, BATTLE_VTABLE, MANAGER_VTABLE, SCRIPT_VTABLE, ACTOR_VTABLE
from memory import FixtureMemory, PinnedImage, Reader, Refused, EXE_SHA256

BASE=0x140000000
IMAGE_SIZE=0x2000000
DATA=0x500000
CONTAINER=0x501000
BATTLE=0x510000
MANAGER=0x520000
DIRECTION=0x530000
ACTION=0x540000
PARTY=0x550000
CASTER=0x600000
TARGET=0x610000
RESULT=0x620000
HANDLE=0x630000
FIGURE=0x640000
MODEL=0x650000
ARRAY=0x660000
ENTRY=0x670000
TRANSFORM=0x680000


class Builder:
    def __init__(self): self.regions={}
    def allocate(self,address,size): self.regions[address]=bytearray(size)
    def write(self,address,data):
        for start,region in self.regions.items():
            if start <= address and address+len(data) <= start+len(region): region[address-start:address-start+len(data)]=data; return
        raise AssertionError(f'No fixture allocation at {address:x}')
    def value(self,address,value,fmt='<Q'): self.write(address,struct.pack(fmt,value))
    def descriptor(self,address,identifier,party=2,kind=1): self.write(address,struct.pack('<6I',1,0,0,party,kind,identifier))
    def memory(self): return FixtureMemory([{'address':hex(a),'hex':bytes(d).hex()} for a,d in sorted(self.regions.items())])


def fixture():
    b=Builder()
    for address,size in [(BASE+APP_GLOBAL,8),(BASE+DATA_GLOBAL,8),(DATA,136),(CONTAINER,8),(BATTLE,384),
                         (MANAGER,360),(DIRECTION,520),(ACTION,1528),(PARTY,392),(CASTER,38616),(TARGET,38616),
                         (RESULT,11216),(HANDLE,272),(FIGURE,8624),(MODEL,24),(ARRAY,8),(ENTRY,152),(TRANSFORM,144)]:
        b.allocate(address,size)
    b.value(BASE+DATA_GLOBAL,DATA); b.value(DATA+128,CONTAINER); b.value(CONTAINER,BATTLE)
    b.value(BATTLE,BASE+BATTLE_VTABLE); b.value(BATTLE+304,MANAGER); b.value(BATTLE+280,PARTY)
    b.value(MANAGER,BASE+MANAGER_VTABLE); b.value(MANAGER+272,DIRECTION)
    b.value(DIRECTION,BASE+SCRIPT_VTABLE); b.value(DIRECTION+512,ACTION); b.value(DIRECTION+260,1,'<I'); b.value(DIRECTION+288,1,'<B')
    b.value(DIRECTION+500,1.0,'<f'); b.value(ACTION,6,'<I'); b.value(ACTION+4,14702,'<I'); b.value(ACTION+16,CASTER)
    b.write(ACTION+1504,struct.pack('<QQQ',RESULT,RESULT+11216,RESULT+11216)); b.descriptor(RESULT+8136,72)
    b.value(PARTY+280,TARGET)
    for address,identifier in ((CASTER,71),(TARGET,72)):
        b.value(address,BASE+ACTOR_VTABLE); b.descriptor(address+232,identifier); b.value(address+38600,1.0,'<f')
    b.value(CASTER+38592,HANDLE); b.value(HANDLE+264,FIGURE); b.value(FIGURE+8536,MODEL)
    b.write(FIGURE+8544,struct.pack('<4f',1,2,3,1)); b.value(MODEL+8,ARRAY); b.value(MODEL+16,ARRAY+8)
    b.value(ARRAY,ENTRY); b.value(ENTRY+144,TRANSFORM)
    b.write(TRANSFORM+112,struct.pack('<8f',0,0,0,1,1,5,3,1))
    return b


class ObserverTests(unittest.TestCase):
    def decode(self,b=None): return Decoder((b or fixture()).memory(),BASE,IMAGE_SIZE).sample()
    def test_current_script_action_targets_and_identity(self):
        result=self.decode(); action=result['current_action']; target=action['vectors']['results_1504'][0]
        self.assertEqual(result['status'],'script_action'); self.assertEqual(action['skill_id'],14702)
        self.assertEqual(target['descriptor']['identifier'],72); self.assertEqual(target['ordinary_matches'][0]['actor'],hex(TARGET))
        self.assertFalse(action['resolved_target_set_certified']); self.assertIsNone(action['native_action_generation'])
        self.assertTrue(all(a['native_spawn_generation'] is None for a in result['actors']))
    def test_base_and_rendered_position_are_distinct(self):
        caster=next(a for a in self.decode()['actors'] if a['address']==hex(CASTER))
        self.assertEqual(caster['base_position'],[1,2,3,1]); self.assertEqual(caster['transform']['position'],[1,5,3,1])
    def test_primary_scene_root(self):
        b=fixture()
        for a,n in [(0x700000,264),(0x701000,64),(0x702000,72),(0x703000,1100)]: b.allocate(a,n)
        b.value(BASE+APP_GLOBAL,0x700000); b.value(0x700000+248,0x701000); b.value(0x701000+56,0x702000)
        b.value(0x702000+64,6,'<I'); b.value(0x700000+256,0x703000+224); b.value(0x703000+1064,BATTLE)
        self.assertEqual(self.decode(b)['root_route'],'scene_type_6')
    def test_absent_root_never_allocates(self):
        b=fixture(); b.value(BASE+DATA_GLOBAL,0)
        result=self.decode(b); self.assertEqual(result['status'],'no_battle'); self.assertEqual(result['read_bytes'],32)
    def test_non_script_direction_has_no_action_interpretation(self):
        b=fixture(); b.value(DIRECTION,BASE+0x100000)
        result=self.decode(b); self.assertEqual(result['status'],'other_native_direction'); self.assertIsNone(result['current_action'])
    def test_invalid_battle_manager_and_actor_vtables(self):
        for address in (BATTLE,MANAGER,CASTER):
            with self.subTest(address=address):
                b=fixture(); b.value(address,BASE+0x100000)
                with self.assertRaisesRegex(Refused,'vtable'): self.decode(b)
    def test_result_count_guard(self):
        b=fixture(); b.value(ACTION+1520,RESULT+65*11216)
        with self.assertRaisesRegex(Refused,'count'): self.decode(b)
    def test_result_stride_guard(self):
        b=fixture(); b.value(ACTION+1512,RESULT+111)
        with self.assertRaisesRegex(Refused,'stride'): self.decode(b)
    def test_reversed_vector_guard(self):
        b=fixture(); b.value(ACTION+1512,RESULT-8)
        with self.assertRaisesRegex(Refused,'vector'): self.decode(b)
    def test_unreadable_target_is_refused(self):
        b=fixture(); b.value(PARTY+280,0x780000)
        with self.assertRaisesRegex(Refused,'Uncaptured'): self.decode(b)
    def test_unaligned_pointer_is_refused(self):
        b=fixture(); b.value(BATTLE+304,MANAGER+1)
        with self.assertRaisesRegex(Refused,'alignment'): self.decode(b)
    def test_nonfinite_transform_is_refused(self):
        b=fixture(); b.value(TRANSFORM+128,float('nan'),'<f')
        with self.assertRaisesRegex(Refused,'Nonfinite'): self.decode(b)
    def test_unresolved_transform_cache_is_not_called(self):
        b=fixture(); b.value(ENTRY+144,0)
        result=self.decode(b); self.assertTrue(any('will not call' in x for x in result['ambiguities']))
        self.assertIsNone(next(a for a in result['actors'] if a['address']==hex(CASTER))['transform'])
    def test_multiple_matches_are_reported_not_selected(self):
        b=fixture(); b.descriptor(CASTER+232,72); b.value(PARTY+288,CASTER)
        result=self.decode(b); matches=result['current_action']['vectors']['results_1504'][0]['ordinary_matches']
        self.assertEqual(len(matches),2); self.assertTrue(any('2 ordinary matches' in x for x in result['ambiguities']))
    def test_no_match_does_not_substitute_special_target(self):
        b=fixture(); b.descriptor(RESULT+8136,999)
        result=self.decode(b); self.assertEqual(result['current_action']['vectors']['results_1504'][0]['ordinary_matches'],[])
    def test_torn_control_snapshot_is_refused(self):
        source=fixture().memory()
        class Changing:
            reads=0
            def read(self,address,size):
                value=source.read(address,size)
                if address==MANAGER+272:
                    self.reads+=1
                    if self.reads==2: return struct.pack('<Q',DIRECTION+8)
                return value
        with self.assertRaisesRegex(Refused,'torn snapshot'): Decoder(Changing(),BASE,IMAGE_SIZE).sample()
    def test_recorded_fixture_replays_same_action(self):
        decoder=Decoder(fixture().memory(),BASE,IMAGE_SIZE); original=decoder.sample()
        captured=decoder.r.fixture(BASE,IMAGE_SIZE,0)
        replay=Decoder(FixtureMemory(captured['segments']),BASE,IMAGE_SIZE).sample()
        self.assertEqual(original,replay); self.assertFalse(captured['atomic'])
    def test_observation_epochs_do_not_claim_native_generations(self):
        epochs=ObservationEpochs(); first=epochs.label(self.decode()); second=epochs.label(self.decode())
        self.assertEqual(first['current_action']['observation_epoch'],second['current_action']['observation_epoch'])
        epochs.label({'status':'refused_sample','actors':[],'current_action':None})
        third=epochs.label(self.decode()); self.assertNotEqual(first['current_action']['observation_epoch'],third['current_action']['observation_epoch'])
        self.assertFalse(third['native_generation_certified'])
    def test_reused_pointer_with_changed_descriptor_changes_epoch(self):
        epochs=ObservationEpochs(); first=epochs.label(self.decode()); b=fixture(); b.descriptor(CASTER+232,171)
        second=epochs.label(self.decode(b)); old=next(a for a in first['actors'] if a['address']==hex(CASTER)); new=next(a for a in second['actors'] if a['address']==hex(CASTER))
        self.assertNotEqual(old['observation_epoch'],new['observation_epoch'])
    def test_fixture_ranges_and_budget(self):
        with self.assertRaises(Refused): FixtureMemory([{'address':'0x0','hex':'00'}])
        with self.assertRaises(Refused): FixtureMemory([{'address':'0x10000','hex':'00'},{'address':'0x10000','hex':'00'}])
        with self.assertRaises(Refused): Reader(fixture().memory()).read(CASTER,65537)
    def test_malformed_pe_rejected(self):
        for data in (b'',b'MZ'+bytes(100)):
            with self.assertRaises(Refused): PinnedImage.parse_pe(data)
    def test_unpinned_executable_rejected_before_decode(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'unknown.exe'; path.write_bytes(b'MZ'+bytes(100))
            with self.assertRaisesRegex(Refused,'SHA256'): PinnedImage(path)
    def test_capture_metadata_mismatch_rejected(self):
        decoder=Decoder(fixture().memory(),BASE,IMAGE_SIZE); decoder.sample(); document=decoder.r.fixture(BASE,IMAGE_SIZE,0)
        document['executable_sha256']='0'*64
        class Image: sha256=EXE_SHA256; image_size=IMAGE_SIZE
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'fixture.json'; path.write_text(json.dumps(document))
            with self.assertRaisesRegex(Refused,'profile mismatch'): FixtureMemory.load(path,Image())


if __name__=='__main__': unittest.main(verbosity=2)
