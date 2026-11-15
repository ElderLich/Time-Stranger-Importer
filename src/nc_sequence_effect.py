"""Sequence neutral native caster Figure sets while preserving their nested bindings."""
import re
import struct
from pathlib import Path

from .effect_bson import EffectDocument, HEADER, END
from .skill_conversion.bson import _object


def build_effect(job, phases, output, directory):
    events = [(p, EffectDocument.read(directory/p['effect'])) for p in phases if p.get('effect')]
    if not events:
        return None
    base = events[0][1]
    hits, sets, names, cues = [], [], set(), []
    end = 0
    for phase, source in events:
        offset = phase['offset']
        bounded = phase.get('bound_effect_to_phase', False)
        if type(bounded) is not bool:
            raise ValueError('bound_effect_to_phase must be true or false')
        if source.value(('bs',)) != 0 or source.value(('sg',)) != 0:
            raise ValueError('Unsupported source skill effect root control')
        keys = [source._text(n.key) for n in source._root.children]
        if set(k for k in keys if not re.fullmatch(r'set\d+', k)) != {'mf','hf','bs','sg'}:
            raise ValueError('Unsupported source skill effect root fields')
        duration = phase['count']+phase['hold'] if bounded else source.value(('mf',))
        if duration <= 0:
            raise ValueError('An unbounded effect requires an explicit phase lifetime')
        end = max(end, offset+duration)
        for index in range(8):
            hit = source.value(('hf',f'hf{index}'))
            if hit:
                if not 0 <= hit <= duration:
                    raise ValueError('Hit outside retained part of effect')
                hits.append(offset+hit)
        for item in source.sets:
            key = item.name
            common = source._get((key,'common'))
            keys = {source._text(n.key) for n in common.children}
            if not keys <= {'id','so','se','ca','bt','emitter0'} or not {'id','so','emitter0'} <= keys:
                raise ValueError(f'{key}: unsupported Figure set controls')
            identity = source.value((key,'common','id'))
            if identity in names:
                raise ValueError(f'Duplicate source effect owner ID {identity}')
            names.add(identity)
            start = source.value((key,'common','so'))
            if not 0 <= start < duration:
                raise ValueError(f'{identity}: start outside retained phase')
            overrides = {('so',): start + offset}
            if bounded:
                life = ('emitter0','el')
                if source.value((key,'common',*life,'el_type')) != 0 or source.value((key,'common',*life,'el_random')) != 0:
                    raise ValueError(f'{identity}: unsupported emitter lifetime for a bounded phase')
                overrides[(*life,'el_type')] = 1
                overrides[(*life,'el_life')] = duration-start

            def transfer(node, path):
                name = source._text(node.key)
                path = (*path,name)
                field = base._intern(name)
                if node.children is not None:
                    return _object(field,[transfer(child,path) for child in node.children])
                if node.kind not in {0x10,0x11,0x14}:
                    raise ValueError('Unrecognized effect scalar with possible string-pool references')
                value = overrides.get(path,source._value(node))
                if node.kind == 0x14:
                    raw = struct.pack('<I',base._intern(value))
                else:
                    raw = struct.pack('<i' if node.kind == 0x10 else '<f',value)
                return struct.pack('<BBHI',node.kind,0,12,field)+raw

            def add(fields):
                sets.append(_object(base._intern(f'set{len(sets)}'),[_object(base._intern('common'),fields)]))

            # Use an independent zero-start sound set for delayed phases.
            # Native compares se_offset with elapsed time and a Figure's so
            # independently controls its start.
            split_sound = bool(offset and 'se' in keys)
            add([transfer(c,()) for c in common.children if not (split_sound and source._text(c.key)=='se')])
            if 'se' in keys:
                frame = offset+item.sound_start_frame
                cues.append((item.sound_name,frame))
            if split_sound:
                new_id = identity+'_se'
                if new_id in names or len(new_id.encode('utf8')) > 31:
                    raise ValueError('Cannot create a unique native sound owner ID')
                names.add(new_id)
                overrides = {('id',):new_id,('so',):0,('se','se_offset'):frame}
                add([transfer(c,()) for c in common.children if source._text(c.key)!='emitter0'])
    if len(hits)>8 or hits != sorted(set(hits)):
        raise ValueError('Native root supports up to eight ordered distinct hit frames')
    base.set_field(('mf',),end)
    for i in range(8):
        base.set_field(('hf',f'hf{i}'), hits[i] if i<len(hits) else 0)
    roots = [bytes(base._prefix[n.start:n.end]) for n in base._root.children if not base._text(n.key).startswith('set')]
    high,low = divmod(len(base._pool),65535)
    raw = HEADER+_object(base._root.key,roots+sets)+struct.pack('<BBH',0x20,high,low)+base._pool+END
    restored = EffectDocument.from_bytes(raw)
    if [(i.sound_name,i.sound_start_frame) for i in restored.sets if i.sound_name] != cues:
        raise ValueError('Native sound sequence readback differs')
    target = Path(output)/('ef_b_'+job['stem']+'.bson')
    target.write_bytes(raw)
    return {'sets':len(restored.sets),'duration':end,'hits':hits,'sounds':cues}
