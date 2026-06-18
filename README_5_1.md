# Time Stranger Importer 5.1

This is the active Blender 5.1 addon for Digimon Story Time Stranger `.geom`
and `.anim` work. It is intentionally close to the original DSTS Unified addon,
with only the compatibility and import fixes that proved useful during testing.

The original Blender 4.5-and-below reference plugin is kept separately:

```text
D:\Digimon Modding\Programs\Blender Tools\DSTS_BlenderPlugin\DSTS_Unified
```

## Install

Source folder:

```text
D:\Digimon Modding\Programs\Blender Tools\Time Stranger Importer\Time_Stranger_Importer
```

Installed Blender 5.1 addon folder:

```text
C:\Users\Shanalotte\AppData\Roaming\Blender Foundation\Blender\5.1\scripts\addons\Time_Stranger_Importer
```

Do not enable this addon at the same time as older Nymic/Romsstar/DSTS Unified
addons, because they register overlapping import/export menus and modules.

## Current Scope

Addon version: `1.2.20`

Kept:

- Blender 5.1 registration and Python 3.13 compatibility.
- Compiled `dsts_formats.cp313-win_amd64.pyd` backend.
- `.geom` import and export for natively imported Geom collections.
- `.nlst` writing during geometry export.
- Texture export from imported materials.
- `.anim` import, single animation export, and all-NLA animation export.
- Automatic import of base and related `.anim` files found beside a `.geom`.
- Texture folder auto-detection near the imported `.geom`.
- DLC/shared `app_0` texture lookup.
- Triangle-list mesh import support.
- Diffuse/material preview fallbacks, including DDS previews.
- Eye overlay preview fixes for shell/pupil layering.
- Blender 5.1 action/NLA compatibility helpers.
- Original-plugin support modules under `src`, including `DDS.py`,
  `Globals.py`, `Logging.py`, and `utilities/Hash.py`.
- Bone-name import safeguards for Blender-renamed duplicates, so mesh vertex
  groups continue to bind to the imported armature.
- Armature import sets pose bones to avoid Blender local-location behavior that
  can make imported DSTS animations look wrong.
- Export refuses unapplied mesh/armature object transforms and non-rest
  pose-mode bones, because those make Blender display a pose that `.geom`
  export cannot currently bake.
- A `DSTS` tab in the 3D View sidebar (`N`) provides safe workflow buttons for
  applying the visible pose as rest pose, clearing pose transforms, applying
  mesh object transforms, checking export readiness, linking selected objects
  into the Geom collection, and loading extracted video frames as viewport
  references.

Removed from the active addon:

- Pure-Python `.geom` reader/writer fallback.
- `src/dsts_py` and `py_geom_*` modules.
- Template-backed custom `.geom` export for arbitrary Blender models.
- One-button custom Digimon export automation.
- Legacy material borrowing/retargeting, model scale fitting, target material
  generation, root locks, LOD fallback copying, neutral base animation
  synthesis, and New Century action automation.

Custom model packaging should currently be handled outside this clean addon
with dedicated helper tools and known-good patch references.

## Backend

Blender 5.1 uses Python 3.13, so the active addon needs:

```text
src\dsts_formats.cp313-win_amd64.pyd
```

The parser/backend source is kept here:

```text
D:\Digimon Modding\Programs\Blender Tools\DSTS_Formats Parser
```

If Blender reports that the backend cannot load, rebuild the parser for
Blender's embedded Python 3.13 and copy the resulting `.pyd` into `src`.

## Menus

Import:

```text
File > Import > Digimon Time Stranger (.geom, .anim) > Geometry (.geom)
File > Import > Digimon Time Stranger (.geom, .anim) > Animation (.anim)
```

Export:

```text
File > Export > Digimon Time Stranger (.geom, .anim) > Export All (.geom + .anim)
File > Export > Digimon Time Stranger (.geom, .anim) > Geometry Only (.geom)
File > Export > Digimon Time Stranger (.geom, .anim) > Animation (.anim)
File > Export > Digimon Time Stranger (.geom, .anim) > All Animations (.anim)
```

Sidebar:

```text
3D View > N > DSTS > Time Stranger
```

## Practical Checks

- Import a real game `.geom` before exporting; export expects DSTS metadata that
  is stored on the imported `Geom` collection.
- Keep the exported `.geom`, generated `.nlst`, textures, and animations beside
  the patch paths referenced by the data tables.
- If a mesh reports missing bones during import, compare the source bone names
  against the imported armature and fix the source rig or palette before using
  the exported files.
- If material preview looks wrong in Blender, check that the nearby `images`
  folder or shared `app_0` images are available.
- If Blender looks correct but the game model exports sideways or folded, check
  the export armature first. Object transforms should be applied, and pose bones
  should be clear unless you intentionally used Pose > Apply > Apply Pose as
  Rest Pose.
- The sidebar `Pose As Rest` button performs the same pose bake that fixed the
  Zeak/Youkomon test export. Use `Clear Pose` if the current pose should not be
  exported.
- The sidebar frame reference loader defaults to:

```text
D:\Digimon Modding\Models Workshop\Video Tutorials\Extracted Frames
```

- `src/utils.py` already handles the DSTS axis conversion with `flop` and
  `unflop`; do not change those functions to fix a scene pose problem.
