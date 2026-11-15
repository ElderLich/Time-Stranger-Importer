"""Synthetic camera binding regressions; no private native assets are embedded."""
import struct
import sys
from pathlib import Path
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from skill_conversion import camera_assets as camera


def fixture():
    raw = bytearray(768)
    struct.pack_into('<I', raw, 0, 316)
    struct.pack_into('<4H', raw, 4, 0, 0, 0, 1)
    for at,value in [(0x60,176),(0x78,320),(0x90,240),(0x98,448)]:
        struct.pack_into('<Q', raw, at, value)
    old = 'Cam_test'; ident = zlib.crc32(old.encode()) ^ 0xffffffff
    struct.pack_into('<I7f', raw, 176, ident, 43.0, 1.7, .1, 2000, 6, 25, 44)
    struct.pack_into('<IIQQ', raw, 240, 2, 0, 272, 0)
    struct.pack_into('<2Q', raw, 272, 1, 10)
    raw[320:335] = b'\0Cam_test\0Root\0'
    struct.pack_into('<HH', raw, 464, 2, 1)
    struct.pack_into('<I', raw, 480, 80)
    struct.pack_into('<I', raw, 488, 80)
    struct.pack_into('<2I', raw, 560, ident, 123)
    struct.pack_into('<I', raw, 568, ident)
    return bytes(raw), f'Cam_test, {ident:08x}, null, camera\r\nAim, 0000007b, null, locator\r\n'


class CameraBindingTests(unittest.TestCase):
    def test_camera_bone_fov_and_nlst_all_rename(self):
        raw,nlst=fixture(); out,names=camera.rename_camera_bindings(raw,nlst,'Cam_new_PC')
        expected=zlib.crc32(b'Cam_new_PC') ^ 0xffffffff
        for at in (176,560,568): self.assertEqual(struct.unpack_from('<I',out,at)[0],expected)
        self.assertEqual(len(out),len(raw))
        self.assertEqual(out[180:232],raw[180:232])
        self.assertEqual(out[572:],raw[572:])
        self.assertIn(f'Cam_new_PC, {expected:08x}, null, camera\r\n',names)
        self.assertTrue(names.endswith('Aim, 0000007b, null, locator\r\n'))

    def test_bad_nlst_and_names_reject(self):
        raw,nlst=fixture()
        for bad in ('../camera','x'*200,'cam name'):
            with self.assertRaises(ValueError):camera.rename_camera_bindings(raw,nlst,bad)
        for bad in ('',nlst.replace('camera','locator'),nlst+nlst,nlst.replace('Cam_test','Wrong')):
            with self.assertRaises(ValueError):camera.rename_camera_bindings(raw,bad,'Cam_new')

    def test_zero_float_donor_retains_child_and_updates_parent(self):
        raw,nlst=fixture();raw=bytearray(raw);struct.pack_into('<H',raw,466,0)
        old=struct.unpack_from('<I',raw,176)[0]
        nlst=nlst.replace('Aim, 0000007b, null',f'Aim, 0000007b, {old:08x}')
        out,names=camera.rename_camera_bindings(bytes(raw),nlst,'Cam_new_EM')
        new=zlib.crc32(b'Cam_new_EM') ^ 0xffffffff
        self.assertIn(f'Aim, 0000007b, {new:08x}, locator',names)
        self.assertEqual(struct.unpack_from('<H',out,466)[0],0)
        self.assertEqual(out[568:],raw[568:])

    def test_unsupported_geometry_and_corrupt_offsets_reject(self):
        raw,nlst=fixture()
        for at,fmt,value in [(4,'H',1),(10,'H',2),(0x60,'Q',900),(0x90,'Q',900),(272,'Q',999)]:
            changed=bytearray(raw);struct.pack_into('<'+fmt,changed,at,value)
            with self.assertRaises(ValueError):camera.rename_camera_bindings(bytes(changed),nlst,'Cam_new')

    def test_native_header_adjacent_name_table_is_valid(self):
        raw,nlst=fixture();raw=bytearray(raw)
        record=bytes(raw[176:208]);table=bytes(raw[240:264])
        raw[208:240]=record;raw[168:192]=table
        struct.pack_into('<Q',raw,0x60,208);struct.pack_into('<Q',raw,0x90,168)
        out,names=camera.rename_camera_bindings(bytes(raw),nlst,'Cam_new_PC')
        self.assertIn('Cam_new_PC',names)
        self.assertEqual(struct.unpack_from('<I',out,208)[0],zlib.crc32(b'Cam_new_PC') ^ 0xffffffff)


if __name__ == '__main__':unittest.main()
