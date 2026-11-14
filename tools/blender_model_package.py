"""Real native fixture checks for isolated package publication and failures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('Time_Stranger_Importer', ROOT/'__init__.py',
                                            submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.model_package import package_native_model, plan_model_package
from Time_Stranger_Importer.src.anim.InterfaceHundredLine import AnimFileHundredLine
from Time_Stranger_Importer.src.anim.BinaryHundredLine import AnimFileBinary

native = Path(r'D:\Digimon Modding\Time Stranger Extracted\app_0.dx11')
fixture = native/'chr050.geom'
if not fixture.is_file():
    raise RuntimeError('This integration test requires local chr050 GEOM/ANIM/NLST and native images/shaders')


def rejects(call, text):
    try:
        call()
    except Exception as exc:
        assert text in str(exc), (text, str(exc))
    else:
        raise AssertionError(f'Expected rejection: {text}')


with tempfile.TemporaryDirectory(prefix='dsts_model_package_') as temp:
    temp = Path(temp)
    source = temp/'exported'
    source.mkdir()
    for suffix in ('.geom', '.anim', '.nlst'):
        shutil.copyfile(fixture.with_suffix(suffix), source/('chr050'+suffix))
    model = source/fixture.name
    original_geom = model.read_bytes()
    original_nlst = model.with_suffix('.nlst').read_bytes()
    original_anim = model.with_suffix('.anim').read_bytes()
    report, files = plan_model_package(model, native, False)
    assert len(files) == 3 and report['external_native_dependencies']
    assert report['material_passes'] and all(p['active_slots'] for p in report['material_passes'])
    assert not report['runtime_verified']
    out = temp/'package'
    package_native_model(model, out, native, False)
    saved = json.loads((out/'model-package.json').read_text())
    for relative, entry in saved['files'].items():
        raw = (out/'dsts-loader/patch'/relative).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry['sha256']
    assert not (out/'dsts-loader/patch/shaders').exists()
    assert not (out/'dsts-loader/patch/images').exists()
    rejects(lambda: package_native_model(model, out, native), 'existing folders')
    original_write = Path.write_bytes
    def fail_package_write(path, raw):
        if path.suffix == '.nlst' and '.dsts-package-' in str(path):
            raise OSError('simulated package I/O failure')
        return original_write(path, raw)
    with patch.object(Path, 'write_bytes', fail_package_write):
        rejects(lambda: package_native_model(model, temp/'failed_write', native, False), 'simulated package I/O failure')
    assert not (temp/'failed_write').exists() and not list(temp.glob('.dsts-package-*'))
    print('PASS exact native bytes, external dependency separation, non-overwriting publication')

    # One local custom resource must be packaged while remaining native files
    # stay external. A local broken resource must not silently use fallback.
    shader = next(p for p in report['external_native_dependencies'] if p.startswith('shaders/'))
    custom = source/shader
    custom.parent.mkdir(parents=True)
    custom.write_bytes((native/shader).read_bytes())
    local_report, local_files = plan_model_package(model, native, False)
    assert shader in local_files and shader not in local_report['external_native_dependencies']
    custom.write_bytes(b'broken')
    rejects(lambda: package_native_model(model, temp/'bad_shader', native, False), 'DXBC')
    assert not (temp/'bad_shader').exists()
    custom.unlink()
    rejects(lambda: package_native_model(model, temp/'missing', None, False), 'Missing native dependency')
    assert not (temp/'missing').exists()
    print('PASS local dependency precedence, missing shader/texture rejection, no partial output')

    damaged = bytearray(original_geom)
    struct.pack_into('<Q', damaged, 0x68, struct.unpack_from('<Q', damaged, 0x68)[0]+8)
    model.write_bytes(damaged)
    rejects(lambda: package_native_model(model, temp/'unaligned', native, False), 'alignment')
    assert not (temp/'unaligned').exists()
    model.write_bytes(original_geom)
    model.with_suffix('.nlst').write_bytes(original_nlst.replace(b'GRP_joint', b'BAD_joint', 1))
    rejects(lambda: plan_model_package(model, native, False), 'NLST')
    model.with_suffix('.nlst').write_bytes(original_nlst)
    binary = AnimFileBinary()
    binary.read(str(model.with_suffix('.anim')))
    wrong = AnimFileHundredLine()
    wrong.bone_count = binary.bone_count+1
    wrong.float_channel_count = binary.float_channel_count
    wrong.to_file(str(model.with_suffix('.anim')))
    rejects(lambda: plan_model_package(model, native, False), 'ANIM counts')
    model.with_suffix('.anim').write_bytes(original_anim)
    print('PASS native crash alignment, NLST identity and mismatched ANIM rejection')

    # Exact-copy supported motions are included; a mismatched clip rejects the
    # entire plan. A separate LOD base is not treated as a motion.
    motion = source/'chr050_test.anim'
    motion.write_bytes(original_anim)
    (source/'chr050_lod_2.anim').write_bytes(b'not this model')
    motion_report, motion_files = plan_model_package(model, native, True)
    assert motion.name in motion_files and 'chr050_lod_2.anim' not in motion_files
    assert len(motion_report['animations']) == 2
    wrong.to_file(str(motion))
    rejects(lambda: plan_model_package(model, native, True), 'ANIM counts')
    motion.unlink()
    model.with_suffix('.sprk').write_bytes(b'unsupported effect')
    rejects(lambda: plan_model_package(model, native, False), 'effect package')
    model.with_suffix('.sprk').unlink()
    model.with_suffix('.bson').write_bytes(b'unsupported scene')
    rejects(lambda: plan_model_package(model, native, False), 'effect package')
    model.with_suffix('.bson').unlink()
    fallback_with_effect = temp/'native_with_effect'
    fallback_with_effect.mkdir()
    (fallback_with_effect/'chr050.sprk').write_bytes(b'unsupported native companion')
    rejects(lambda: plan_model_package(model, fallback_with_effect, False), 'effect package')
    assert model.read_bytes() == original_geom
    assert model.with_suffix('.nlst').read_bytes() == original_nlst
    assert model.with_suffix('.anim').read_bytes() == original_anim
    print('PASS same-stem motion scope and explicit SPRK/BSON rejection')

addon.unregister()
print('ALL MODEL PACKAGE CHECKS PASSED', bpy.app.version_string)
