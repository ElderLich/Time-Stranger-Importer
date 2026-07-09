# Time Stranger Importer 5.1

This is the active Blender 5.1 addon for Digimon Story Time Stranger `.geom`
and `.anim` work. It is intentionally close to the original DSTS Unified addon,
with only the compatibility and import fixes that proved useful during testing.

The original Blender 4.5-and-below reference plugin is kept separately:

```text
D:\Digimon Modding\Programs\Blender Tools\DSTS_BlenderPlugin\DSTS_Unified
```

## Install

Repository folder:

```text
D:\Programs\GitHub\Digimon Modding\Time Stranger Importer
```

Installed Blender 5.1 addon folder:

```text
C:\Users\Shanalotte\AppData\Roaming\Blender Foundation\Blender\5.1\scripts\addons\Time_Stranger_Importer
```

Do not enable this addon at the same time as older Nymic/Romsstar/DSTS Unified
addons, because they register overlapping import/export menus and modules.

## CI Package

GitHub Actions validates the Python syntax and builds an installable addon zip
on every push and pull request:

```text
Time_Stranger_Importer.zip
```

Tagged releases named like `v1.2.26` attach the zip to the GitHub Release.

## Current Scope

Addon version: `1.2.26`

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
- New Century / Unity-origin `.glb` import pre-bakes glTF `CUBICSPLINE`
  transform animation to exact 30 fps `LINEAR` keys before Blender import.
  This avoids Blender reconstructing Unity cubic quaternion tracks as Bezier
  F-Curves, which can reintroduce tiny leg/foot jitter that is already fixed
  in the source `.glb`.
- New Century / Unity-origin `.glb` import isolates per-skin bind poses before
  fitting the DSTS export armature. This handles files with several Unity scene
  roots or duplicated skeleton node names, such as `x158_b`, `x158_d`,
  `x158_m`, and `x158_p`, without letting unrelated skins poison the bind-pose
  alignment check.
- New Century / Unity-origin `.glb` import still prefers skinned `*_lod0`
  meshes, but can fall back to skinned `body*` meshes when Unity exports omit
  the `_lod0` suffix.
- New Century / Unity-origin `.glb` import has a helper-bone fallback for files
  like `u123`, where Blender promotes non-skin Unity nodes such as `R` and
  `Bip001` into bones and doubles the body rest offset. The importer first
  tries Blender's normal glTF import path; if that rest pose fails but the raw
  glb node hierarchy fits the inverse bind matrices, it discards only that
  just-imported glb and retries with Blender's original-bind guessing disabled.
- New Century / Unity-origin `.glb` import probes each action for Blender glTF
  bone-location semantics that would drift under DSTS playback, then bakes only
  the affected clips into DSTS/game semantics. This fixes models such as x133
  whose idle can look grounded in Blender while exported `.anim` playback makes
  feet hover or weapons offset in-game, without rewriting clips that already
  evaluate the same way.
- New Century / Unity-origin `.glb` import accepts tiny bind-pose discrepancies
  up to `0.01` Blender units before declaring that no axis-aligned conversion
  exists. This covers models such as `c112`, whose main rig is aligned but a few
  helper/accessory bones differ from the inverse bind matrices by about five
  millimeters.
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
  armature/mesh object transforms, cleaning action object-transform curves,
  renaming primary UV maps to `uv1`, borrowing donor `.geom` material metadata,
  retargeting material textures, preparing the locked vanilla base NLA strip,
  checking export readiness, linking selected objects into the Geom collection,
  and writing tutorial workflow notes.
- The material retarget workflow now prefers an active/model collection name
  such as `chr110` over stale stored `chr050` values, and reports missing target
  texture stems in Blender when some borrowed material families are unavailable.
  If an exact old-family rename such as `chr027a02 -> chr110a02` is missing, it
  falls back to the standard target slot for that uniform, such as `chr110a01`
  for diffuse color.
- New Century / Unity-origin `.glb` import tags each sorted body mesh with a
  preferred DSTS body atlas (`body_0 -> a01`, `body_1 -> a02`, etc.). Material
  texture retargeting uses that hint before falling back, so multi-material
  models such as BeelStarmon X keep wings/capes/accessories on their generated
  atlas instead of collapsing every material to `a01`.

Removed from the active addon:

- Pure-Python `.geom` reader/writer fallback.
- `src/dsts_py` and `py_geom_*` modules.
- Template-backed custom `.geom` export for arbitrary Blender models.
- One-button custom Digimon export automation.
- Model scale fitting, target material generation, root locks, LOD fallback
  copying, neutral base animation synthesis, and broad New Century action
  automation.
- Preset-driven action generation that guessed every battle/field animation
  from names such as `attack_move`, `stand`, or `idle`.

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
- If a Unity-origin `.glb` still fails bind-pose alignment, do not loosen the
  axis-alignment threshold blindly. The importer only uses the helper-bone
  fallback when the glb node hierarchy itself fits the inverse bind matrices;
  otherwise the source file needs a new diagnostic case.
- The sidebar `Pose As Rest` button performs the same pose bake that fixed the
  Zeak/Youkomon test export. Use `Clear Pose` if the current pose should not be
  exported.
- The extracted `New Digimon.mp4` tutorial frames were used to identify this
  manual workflow: import a Nymic/DSTS-style `.geom`, preserve donor material
  context, retarget texture nodes, rename the UV map to `uv1`, import a
  Romsstar-style rig/source for base animation context, then export and test.
- Use `Setup Base NLA` after importing the vanilla/Romsstar source. It pushes
  down `target_chr_base` as a short Hold/Replace strip, locks it, clears the
  active action back to `No Action`, and leaves the real animation pushdown and
  export selection manual. Export the target animation track, not the locked
  base track.

- `src/utils.py` already handles the DSTS axis conversion with `flop` and
  `unflop`; do not change those functions to fix a scene pose problem.
