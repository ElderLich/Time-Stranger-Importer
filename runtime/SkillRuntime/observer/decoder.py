"""Pure decoder of a proven, bounded subset of Time Stranger's battle state."""
from __future__ import annotations
import struct
from memory import Reader, Refused

APP_GLOBAL = 0x1BA6B60
DATA_GLOBAL = 0x1B9A5D0
BATTLE_VTABLE = 0x143CED0
MANAGER_VTABLE = 0x143C5E0
SCRIPT_VTABLE = 0x1387BB8
ACTOR_VTABLE = 0x143DCA8
MAX_RECORDS = 64


class Decoder:
    def __init__(self, source, module_base, image_size):
        if module_base < 0x10000 or module_base % 65536 or image_size < APP_GLOBAL+8:
            raise Refused('Invalid module mapping')
        self.r = Reader(source); self.base = module_base; self.image_size = image_size
        self.actors = {}; self.ambiguities = []

    def typed(self, address, rva, name):
        if not address or self.r.ptr(address) != self.base+rva: raise Refused(f'{name} vtable differs from profile')
        return address

    def root(self):
        r = self.r; app = r.ptr(self.base+APP_GLOBAL); battle = 0
        if app:
            state = r.ptr(app+248)
            if state:
                info = r.ptr(state+56)
                if info and r.integer(info+64, '<I', True) == 6:
                    scene = r.ptr(app+256)
                    if scene:
                        if scene < 0x10000+224: raise Refused('Invalid scene subobject')
                        battle = r.ptr(scene-224+1064)
                        if battle: return self.typed(battle, BATTLE_VTABLE, 'BattleSystem'), 'scene_type_6'
        data = r.ptr(self.base+DATA_GLOBAL)
        if data:
            container = r.ptr(data+128)
            if container: battle = r.ptr(container)
        # Never call the game's lazy singleton constructors to obtain a missing root.
        return (self.typed(battle, BATTLE_VTABLE, 'BattleSystem'), 'existing_game_data') if battle else (0, 'not_allocated')

    def vector(self, header, offset, stride):
        begin, end, capacity = struct.unpack_from('<QQQ', header, offset)
        if begin == end == capacity == 0: return []
        if not begin or begin % 8 or not begin <= end <= capacity: raise Refused('Invalid vector pointers')
        if (end-begin) % stride or (capacity-begin) % stride: raise Refused('Misaligned native vector stride')
        if (capacity-begin)//stride > MAX_RECORDS: raise Refused('Native vector exceeds bounded record count')
        if capacity > 0x7FFFFFFFFFFF: raise Refused('Vector outside user memory')
        return [begin+i*stride for i in range((end-begin)//stride)]

    @staticmethod
    def descriptor(raw):
        fields = struct.unpack('<6I', raw)
        return {'raw_hex':raw.hex(), 'party_code':fields[3], 'type':fields[4], 'identifier':fields[5]}

    def actor(self, address):
        if address in self.actors: return self.actors[address]
        self.typed(address, ACTOR_VTABLE, 'BattleUnit')
        r = self.r; descriptor = self.descriptor(r.read(address+232, 24, guard=True))
        handle = r.ptr(address+38592)
        result = {'address':hex(address), 'descriptor':descriptor, 'native_spawn_generation':None,
                  'native_group':None, 'native_index':None,
                  'rotation_lock':r.integer(address+38605, '<B'),
                  'position_tween_active':r.integer(address+38320, '<B'),
                  'speed':r.floats(address+38600, 1)[0],
                  'position_tween_hex':r.read(address+38336, 64).hex(),
                  'rotation_tween_hex':r.read(address+38400, 64).hex(),
                  'handle':hex(handle), 'transform':None}
        if handle:
            figure = r.ptr(handle+264); result['figure'] = hex(figure)
            if figure:
                container = r.ptr(figure+8536)
                result['base_position'] = r.floats(figure+8544, 4)
                result['figure_flag_8560'] = r.integer(figure+8560, '<B')
                result['height_state_hex'] = r.read(figure+8564, 48).hex()
                if container:
                    begin, end = r.ptr(container+8), r.ptr(container+16)
                    if begin or end:
                        if not begin <= end or (end-begin) % 8 or (end-begin)//8 > 1024: raise Refused('Invalid model entry range')
                        if begin != end:
                            entry = r.ptr(begin)
                            if entry:
                                transform = r.ptr(entry+144)
                                if transform:
                                    result['transform'] = {'address':hex(transform), 'quaternion':r.floats(transform+112,4), 'position':r.floats(transform+128,4)}
                                else: self.ambiguities.append('Transform cache unresolved; observer will not call virtual resolver')
        self.actors[address] = result
        return result

    def candidates(self, battle, descriptor):
        # Reproduce only ordinary party searches. Never substitute the special-target fallback.
        party_offset = 272 if descriptor['party_code'] in (1, 3) else 280
        party = self.r.ptr(battle+party_offset)
        if not party: return []
        if descriptor['type'] == 1: offsets = range(280, 360, 8)
        elif descriptor['type'] == 2: offsets = range(360, 392, 8)
        else:
            self.ambiguities.append('Unsupported descriptor type; special resolver branches not followed'); return []
        matches = []
        for offset in offsets:
            actor = self.r.ptr(party+offset)
            if actor:
                current = self.actor(actor)
                if current['descriptor']['type'] and current['descriptor']['identifier'] == descriptor['identifier']:
                    matches.append({'actor':hex(actor), 'party_pointer':hex(party), 'party_slot_offset':offset})
        if len(matches) != 1:
            self.ambiguities.append(f'Descriptor {descriptor["identifier"]}: {len(matches)} ordinary matches; native special fallback intentionally omitted')
        return matches

    def sample(self):
        battle, root_route = self.root()
        result = {'status':'no_battle' if not battle else 'no_direction', 'battle':hex(battle), 'root_route':root_route,
                  'native_generation_certified':False, 'native_clock_seconds':None, 'atomic':False,
                  'ambiguities':self.ambiguities, 'actors':[], 'current_action':None}
        if battle:
            manager = self.r.ptr(battle+304)
            if manager:
                self.typed(manager, MANAGER_VTABLE, 'BattleDirectionManager')
                result['manager'] = hex(manager)
                direction = self.r.ptr(manager+272)
                if direction:
                    vtable = self.r.ptr(direction)
                    result.update(status='other_native_direction', direction=hex(direction), direction_vtable=hex(vtable))
                    if vtable == self.base+SCRIPT_VTABLE:
                        action = self.r.ptr(direction+512)
                        if not action: raise Refused('Script direction has no copied action')
                        header = self.r.read(action, 1528, guard=True)
                        caster = struct.unpack_from('<Q', header, 16)[0]
                        self.actor(caster)
                        vectors = {}
                        for name, offset, stride in [('selection',1384,1296), ('results_1480',1480,11216), ('results_1504',1504,11216)]:
                            entries = []
                            for index, record in enumerate(self.vector(header, offset, stride)):
                                item = {'index':index, 'record_address':hex(record)}
                                if stride == 11216:
                                    desc = self.descriptor(self.r.read(record+8136,24,guard=True))
                                    callbacks = self.r.read(record+9568,16,guard=True)
                                    item.update(descriptor=desc, ordinary_matches=self.candidates(battle,desc), callback_range_nonempty=callbacks[:8] != callbacks[8:])
                                else:
                                    item['prefix_hex'] = self.r.read(record,24,guard=True).hex()
                                    item['interpretation'] = 'selection record prefix only; not certified resolved target'
                                entries.append(item)
                            vectors[name] = entries
                        result['status'] = 'script_action'
                        result['current_action'] = {'direction':hex(direction), 'copy':hex(action), 'caster':hex(caster),
                            'kind':struct.unpack_from('<I',header,0)[0], 'skill_id':struct.unpack_from('<I',header,4)[0],
                            'script_state_260':self.r.integer(direction+260,'<I',True),
                            'scheduler_active_288':self.r.integer(direction+288,'<B',True),
                            'script_name_pointer':hex(self.r.ptr(direction+504)),
                            'direction_speed_500':self.r.floats(direction+500,1)[0], 'vectors':vectors,
                            'native_action_generation':None, 'resolved_target_set_certified':False}
                        self.ambiguities.append('Result vector identity matches are observed; redirect/counter/multi-hit finality and coroutine timing remain uncertified')
        self.r.finish()
        result['actors'] = list(self.actors.values())
        result['read_bytes'] = self.r.bytes_read
        result['ambiguities'].append('Polling cannot prove allocation lifetimes or detect reuse entirely between samples; observation epochs are not native generations')
        return result


class ObservationEpochs:
    """Continuity labels only; never converted to the core's certified action/spawn tokens."""
    def __init__(self):
        self.counter = 0; self.previous = {}; self.last_battle = None

    def label(self, result):
        current = {}
        def epoch(kind, identity):
            key = (kind, identity)
            if key in self.previous: value = self.previous[key]
            else: self.counter += 1; value = self.counter
            current[key] = value; return value
        battle = result.get('battle')
        if battle and battle != '0x0': result['battle_observation_epoch'] = epoch('battle', battle)
        action = result.get('current_action')
        if action:
            action['observation_epoch'] = epoch('action', (battle,action['direction'],action['copy'],action['caster'],action['skill_id']))
        for actor in result.get('actors',[]):
            actor['observation_epoch'] = epoch('actor',(battle,actor['address'],actor['descriptor']['raw_hex'],actor['handle'],actor.get('figure')))
        self.previous = current
        return result
