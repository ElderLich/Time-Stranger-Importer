"""Failed ANIM/DDS serialization or publication must preserve prior exports."""
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# Load the maintained serializers without importing Blender's UI entry point.
for name, folder in [('atomic_test_addon', ROOT), ('atomic_test_addon.src', ROOT/'src')]:
    module = types.ModuleType(name)
    module.__path__ = [str(folder)]
    sys.modules[name] = module

from atomic_test_addon.src.serialization.DSCSStructs import DSCSSerializable
from atomic_test_addon.src import spark


class Record(DSCSSerializable):
    def __init__(self, fail=False):
        self.value = 42
        self.fail = fail

    def exbip_rw(self, rw):
        self.value = rw.rw_uint32(self.value)
        if self.fail:
            raise ValueError('injected serialization failure after partial output')


class AtomicSerializationTests(unittest.TestCase):
    def test_partial_serialization_keeps_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'motion.anim'
            path.write_bytes(b'previous export')
            with self.assertRaisesRegex(ValueError, 'partial output'):
                Record(fail=True).write(path)
            self.assertEqual(path.read_bytes(), b'previous export')
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_failed_publication_keeps_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'texture.dds'
            path.write_bytes(b'previous export')
            with patch.object(spark.os, 'replace', side_effect=OSError('injected replace failure')):
                with self.assertRaisesRegex(OSError, 'replace failure'):
                    Record().write(path)
            self.assertEqual(path.read_bytes(), b'previous export')
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_unicode_export_reopens_and_replaces(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'光.anim'
            for value in (42, 73):
                original = Record()
                original.value = value
                original.write(path)
                self.assertEqual(path.read_bytes(), original.tobytes())
                reopened = Record()
                reopened.read(path)
                self.assertEqual(reopened.value, value)
            self.assertEqual(list(Path(folder).iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
