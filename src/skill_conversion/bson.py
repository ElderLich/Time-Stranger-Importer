"""Bounded native Figure-set composition from a user-selected BSON template.

Promotes the verified structural operation from stage_youkomon_bson without
its model paths, IDs, captured assets or parallel BSON reader. EffectDocument
owns parsing, string handling and final validation. This profile builds an
event-local review effect; it does not invent battle routing or hit timing.
"""
from __future__ import annotations

import re
import struct

try:
    from ..effect_bson import EffectDocument, END, HEADER
except ImportError:
    from effect_bson import EffectDocument, END, HEADER


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(key, children):
    payload = struct.pack('<I', len(children)) + b''.join(children) + END
    high, low = divmod(len(payload) + 8, 65535)
    _require(high <= 255, 'Composed BSON object exceeds native length')
    return struct.pack('<BBHI', 1, high, low, key) + payload


def _plain(document, node):
    if node.children is None:
        return document._value(node)
    result = {}
    for child in node.children:
        key = document._text(child.key)
        _require(key not in result, 'Duplicate donor field: ' + key)
        result[key] = _plain(document, child)
    return result


def _neutral_emitter(drawable):
    def axis(prefix, axis, value):
        return {f'{prefix}_{axis}': value, f'{prefix}_random': 0.0,
                f'{prefix}_random_list': '', f'{prefix}_random_distributed': 0}
    dae = {**{f'o{kind}{axis_name}': axis(f'o{kind}{axis_name}', axis_name, default)
               for kind, default in (('s', 1.0), ('r', 0.0), ('t', 0.0))
               for axis_name in 'xyz'},
           'lbt': 0, 'cl': 0, 'osa': 0.0, 'pt': 0, 'osa_random_distributed': 0,
           'pc': {'pc_calc': 0, 'pc_random': 0},
           'lr': {'lr_begin': -1, 'lr_end': -1, 'lr_cancel': -1},
           'osa_list': '', 'zsort': 0, 'dl': 1, 'ff': 0, 'l': -1,
           'sf': {'sf_random': 0, 'sf_frame': 0}}
    return {'dn': drawable, 'el': {'el_type': 0, 'el_random': 0, 'el_life': 0},
            'daectrl': dae, 'ei': {'ei_interval': 0, 'ei_random': 0},
            'ec': {'ec_count': 1, 'ec_random': 0}}


def compose_event(template, figures, *, socket, duration_frames, template_set='set1',
                  hit_frames=(), audio=None, sound_template_set='set0', figure_start_frames=None):
    """Clone the neutral attached Figure template for one source event.

    Only the proven one-Figure emitter profile is accepted. Donor visual
    controllers are deliberately excluded from the generated template, with
    every omitted field listed in the returned provenance. Imported source
    records and donor bytes are never rewritten.
    """
    _require(type(duration_frames) is int and 0 < duration_frames <= 216000,
             'Event duration must be a positive bounded 30 Hz frame count')
    _require(isinstance(hit_frames, (list, tuple)) and len(hit_frames) <= 8 and
             all(type(frame) is int and 0 <= frame <= duration_frames for frame in hit_frames) and
             list(hit_frames) == sorted(set(hit_frames)), 'Native hit frames must be distinct increasing nonnegative frames within the effect')
    _require(isinstance(socket, str) and socket and '\0' not in socket,
             'Select a native socket or explicit @root')
    _require(not socket.startswith('@') or socket in ('@root', '@effect'), 'Unknown native attachment origin')
    native_socket = '' if socket in ('@root', '@effect') else socket
    owner = '' if socket == '@effect' else 'CompositeModel'
    follow = 0 if socket == '@effect' else 1
    _require(len(native_socket.encode('utf8')) < 32, 'Socket exceeds native 31-byte limit')
    _require(isinstance(figures, list) and 0 < len(figures) <= 4096, 'Expected bounded Figure list')
    explicit_start_frames = figure_start_frames is not None
    if figure_start_frames is None:
        _require(len(set(figures)) == len(figures), 'Duplicate event Figure')
        figure_start_frames = [0] * len(figures)
    else:
        _require(isinstance(figure_start_frames, (list, tuple)) and len(figure_start_frames) == len(figures)
                 and all(type(frame) is int and 0 <= frame < duration_frames for frame in figure_start_frames),
                 'Each Figure needs a nonnegative native start frame within the effect')
        _require(len(set(zip(figures, figure_start_frames))) == len(figures), 'Duplicate Figure at the same native start frame')
    for figure in figures:
        _require(isinstance(figure, str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,30}', figure),
                 'Invalid Figure basename')
    donor = EffectDocument.from_bytes(template)
    root_keys = [donor._text(node.key) for node in donor._root.children]
    _require(len(root_keys) == len(set(root_keys)), 'Duplicate donor root fields')
    _require(set(key for key in root_keys if not re.fullmatch(r'set\d+', key)) == {'mf', 'hf', 'bs', 'sg'},
             'Donor root has unsupported controls')
    _require(donor.value(('bs',)) == 0.0 and donor.value(('sg',)) == 0, 'Nonneutral donor root controls')
    hit_node = donor._get(('hf',))
    _require({donor._text(node.key) for node in hit_node.children} == {f'hf{i}' for i in range(8)},
             'Donor hit table must contain exactly eight native fields')
    common = donor._get((template_set, 'common'))
    keys = [donor._text(node.key) for node in common.children]
    _require(len(keys) == len(set(keys)), 'Duplicate donor common fields')
    retained = {'id', 'ca', 'so', 'bt', 'emitter0'}
    _require(retained <= set(keys), 'Donor lacks attached Figure template fields')
    _require([donor._text(n.key) for n in donor._get((template_set,)).children] == ['common'],
             'Donor set has unsupported non-common records')
    emitter = _plain(donor, donor._get((template_set, 'common', 'emitter0')))
    _require(emitter == _neutral_emitter(emitter.get('dn')), 'Donor emitter is outside the neutral Figure profile')
    ca = _plain(donor, donor._get((template_set, 'common', 'ca')))
    _require(set(ca) == {'ca_ref', 'ca_sca', 'ca_frame', 'ca_rot', 'ca_pos'} and ca['ca_frame'] == -1,
             'Donor attachment clock is unsupported')
    bt = _plain(donor, donor._get((template_set, 'common', 'bt')))
    _require(set(bt) == {'bt_pos', 'bt_sca', 'bt_rot', 'bt_id'}, 'Unsupported donor socket record')
    donor.set_field(('mf',), duration_frames)
    for i in range(8):
        donor.set_field(('hf', f'hf{i}'), hit_frames[i] if i < len(hit_frames) else 0)

    sound_raw = None
    if audio is not None:
        _require(isinstance(audio, dict) and set(audio) == {'cue_id', 'frame', 'stop_on_end'},
                 'Audio binding requires cue_id, frame and stop_on_end')
        _require(type(audio['cue_id']) is int and 0 < audio['cue_id'] <= 2147483647 and
                 type(audio['frame']) is int and 0 <= audio['frame'] <= duration_frames and
                 type(audio['stop_on_end']) is bool, 'Invalid native sound binding')
        sound_path = (sound_template_set, 'common', 'se')
        sound = donor._get(sound_path)
        values = {'se_name': str(audio['cue_id']), 'se_offset': audio['frame'],
                  'se_stop': int(audio['stop_on_end']), 'se_fadeout': 0,
                  'se_volume': 1.0, 'se_stream': 1, 'se_3D': 0}
        original_sound = _plain(donor, sound)
        extra = set(original_sound) - set(values)
        _require(set(values) <= set(original_sound) and extra <= {'se_3D_range', 'se_3D_max_volume_range'}
                 and all(original_sound[key] == 0.0 for key in extra), 'Unsupported donor sound schema')
        for name, value in values.items():
            donor.set_field(sound_path + (name,), value)
        sound_raw = bytes(donor._prefix[sound.start:sound.end])

    def patch(path, value):
        node = donor._get(path)
        donor._prefix[node.start + 8:node.end] = donor._encode_scalar(node, value)

    children = [bytes(donor._prefix[node.start:node.end]) for node in donor._root.children
                if not re.fullmatch(r'set\d+', donor._text(node.key))]
    for order, figure in enumerate(figures):
        patch((template_set, 'common', 'id'), f'nc_{order:04d}')
        patch((template_set, 'common', 'so'), figure_start_frames[order])
        patch((template_set, 'common', 'bt', 'bt_id'), owner)
        for field in ('bt_pos', 'bt_rot', 'bt_sca'):
            patch((template_set, 'common', 'bt', field), native_socket)
        for field in ('ca_pos', 'ca_rot', 'ca_sca', 'ca_ref'):
            patch((template_set, 'common', 'ca', field), follow)
        donor.set_field((template_set, 'common', 'emitter0', 'dn'), figure)
        fields = [bytes(donor._prefix[node.start:node.end])
                  for node in common.children if donor._text(node.key) in retained]
        if order == 0 and sound_raw is not None:
            fields.append(sound_raw)
        common_raw = _object(common.key, fields)
        children.append(_object(donor._intern(f'set{order}'), [common_raw]))
    root = _object(donor._root.key, children)
    high, low = divmod(len(donor._pool), 65535)
    _require(high <= 255, 'Composed BSON pool exceeds native length')
    result = HEADER + root + struct.pack('<BBH', 0x20, high, low) + donor._pool + END
    parsed = EffectDocument.from_bytes(result)
    _require([s.drawables for s in parsed.sets] == [(figure,) for figure in figures], 'Figure composition readback differs')
    _require(all(s.attachment_owner == owner and s.position_socket == native_socket
                 and s.rotation_socket == native_socket and s.scale_socket == native_socket for s in parsed.sets),
             'Attachment composition readback differs')
    _require([parsed.value(('hf', f'hf{i}')) for i in range(8)] == list(hit_frames) + [0] * (8 - len(hit_frames)),
             'Native hit-frame readback differs')
    _require([parsed.value((effect_set.name, 'common', 'so')) for effect_set in parsed.sets] == list(figure_start_frames),
             'Native Figure start-frame readback differs')
    if audio is not None:
        _require(parsed.sets[0].sound_name == str(audio['cue_id']) and parsed.sets[0].sound_start_frame == audio['frame'],
                 'Native sound binding readback differs')
    return bytes(result), {'profile': 'neutral-attached-figure-event-v1', 'template_set': template_set,
                           'omitted_donor_common_fields': sorted(set(keys) - retained),
                           'figures': figures, 'socket': socket, 'duration_frames': duration_frames,
                           'event_dispatch': 'external route required; local clock starts at zero',
                           'hit_routing': 'native BSON hit fields' if hit_frames else 'not authored; zeroed event-local hit table',
                           'hit_frames': list(hit_frames), 'audio': audio,
                           **({'figure_start_frames': list(figure_start_frames)} if explicit_start_frames else {})}
