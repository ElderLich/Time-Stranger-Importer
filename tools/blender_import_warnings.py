"""Run in Blender to check GEOM warning reporting and import rollback.

Use --background for the headless checks, or a windowed --factory-startup session
to also test callbacks with no editor area and a failed warning popup.
Arguments after -- optionally specify --addon-root and --source.
"""
import argparse
from collections import Counter
import importlib.util
from pathlib import Path
import sys
import tempfile
import shutil
import struct

import bpy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--addon-root", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--source", type=Path, default=Path(
    "D:/Digimon Modding/Time Stranger Extracted/app_0.dx11/ef_b_chr220_bs01.geom"))
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
root = args.addon_root.resolve()
spec = importlib.util.spec_from_file_location("Time_Stranger_Importer", root/"__init__.py",
                                            submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from Time_Stranger_Importer.src.backend import load_format_modules


def check():
    formats = load_format_modules()[0]
    formats.set_throw_errors(False)
    formats.set_error_list([])
    source = formats.Geom.from_file(str(args.source))
    # The reported effect no longer produces false diagnostics. Deliberately
    # alter one geometry IBPM in an isolated copy to exercise a real warning.
    fixture = tempfile.TemporaryDirectory(prefix='dsts-warning-fixture-')
    original = args.source
    args.source = Path(fixture.name)/original.name
    raw = bytearray(original.read_bytes())
    index = next(i for i, bone in enumerate(source.skeleton.bones) if bone.is_geometry)
    ibpm = struct.unpack_from('<Q', raw, 0x68)[0]
    struct.pack_into('<f', raw, ibpm + 48*index + 12, 1)
    args.source.write_bytes(raw)
    for extension in ('.anim', '.nlst'):
        other = original.with_suffix(extension)
        if other.exists():
            shutil.copyfile(other, args.source.with_suffix(extension))
    formats.set_error_list([])
    source = formats.Geom.from_file(str(args.source))
    diagnostics = list(formats.get_error_list())
    assert diagnostics, "Select a real GEOM with import diagnostics"
    formats.set_error_list(["Existing caller diagnostic"])
    before = set(bpy.data.objects)
    windows = bpy.context.window_manager.windows

    # File-browser callbacks can have a window without an editor area.
    with bpy.context.temp_override(window=windows[0], area=None, region=None):
        assert bpy.ops.import_scene.dsts_geom(filepath=str(args.source)) == {"FINISHED"}
    collection = next(c for c in bpy.data.collections if c.get("DSTS_import_warnings"))
    assert len([o for o in collection.objects if o.type == "MESH"]) == len(source.meshes)
    rig = next(o for o in collection.objects if o.type == "ARMATURE")
    assert len(rig.data.bones) == len(source.skeleton.bones)
    assert any(t.strips for t in rig.animation_data.nla_tracks)
    assert before <= set(bpy.data.objects)
    assert formats.get_error_list() == ["Existing caller diagnostic"]
    assert formats.get_throw_errors() is False
    warnings = collection["DSTS_import_warnings"]
    assert Counter(warnings.splitlines()) == Counter(diagnostics)
    print("PASS actual GEOM meshes, rig, animation, warnings and caller state", flush=True)

    # EXEC_DEFAULT must retain the report even without a window.
    with bpy.context.temp_override(window=None, area=None, region=None):
        assert bpy.ops.wm.show_errors_window(errors=warnings) == {"FINISHED"}
    reports = {t.name: t.as_string() for t in bpy.data.texts if t.name.startswith("Import Warnings")}
    assert reports and all(warnings.replace("\n", "") in content.replace("\n", "") for content in reports.values())
    collection_name = collection.name
    with tempfile.TemporaryDirectory(prefix="dsts-import-warnings-") as folder:
        folder = Path(folder)
        bpy.ops.wm.save_as_mainfile(filepath=str(folder/"effect.blend"))
        bpy.ops.wm.open_mainfile(filepath=str(folder/"effect.blend"))
        assert bpy.data.collections[collection_name]["DSTS_import_warnings"] == warnings
        for name, content in reports.items():
            assert bpy.data.texts[name].as_string() == content
        print("PASS full reports persist through save and reopen", flush=True)

        unchanged = set(bpy.data.objects)
        invalid = folder/"incomplete.geom"
        invalid.write_bytes(b"invalid")
        try:
            bpy.ops.import_scene.dsts_geom(filepath=str(invalid))
        except RuntimeError as exc:
            assert "complete Time Stranger GEOM" in str(exc)
        else:
            raise AssertionError("Invalid geometry must fail")
        assert set(bpy.data.objects) == unchanged
        print("PASS invalid source still fails without disturbing existing objects", flush=True)

    if not bpy.app.background:
        class FailedWarningPopup(bpy.types.Operator):
            bl_idname = "wm.show_errors_window"
            bl_label = "Failed warning popup fixture"
            errors: bpy.props.StringProperty()

            def invoke(self, context, event):
                raise RuntimeError("Simulated unavailable popup")

        bpy.utils.unregister_class(addon.DSTS_OT_ShowErrors)
        bpy.utils.register_class(FailedWarningPopup)
        try:
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0], area=None, region=None):
                assert bpy.ops.import_scene.dsts_geom(filepath=str(args.source)) == {"FINISHED"}
            imported = [c for c in bpy.data.collections if c.get("DSTS_import_warnings")]
            assert len(imported) == 2
            assert all(sum(o.type == "MESH" for o in c.objects) == len(source.meshes) for c in imported)
            print("PASS unavailable warning popup retains completed import", flush=True)
        finally:
            bpy.utils.unregister_class(FailedWarningPopup)
            bpy.utils.register_class(addon.DSTS_OT_ShowErrors)
    print("PASS GEOM import warnings regression", flush=True)
    fixture.cleanup()


if bpy.app.background:
    check()
else:
    def run():
        try:
            check()
        finally:
            bpy.ops.wm.quit_blender()
    bpy.app.timers.register(run, first_interval=0.5)
