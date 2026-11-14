# Cyber Sleuth PC Blender compatibility

The separate **File > Import > Digimon Cyber Sleuth** submenu imports Complete
Edition PC assets. Time Stranger's GEOM v316 and Cyber Sleuth's GEOM v100 are
incompatible; the existing Time Stranger exporter explicitly rejects imported
Cyber Sleuth collections, objects and actions. Native Cyber Sleuth export is not
implemented. This is a bounded import/preview milestone, not a complete release
or proof that every Cyber Sleuth asset works.

## User workflow

1. Extract an archive with MVGLTools' `--game=dscs` mode. Work on extracted copies.
2. Select a `.geom`, `.name` or `.skel`. The matching NAME, SKEL, GEOM and base
   ANIM must be in the same directory, with DDS-backed `.img` files in `images/`.
3. Enable **Matching motion clips** to import compatible `modelname_*.anim`.
   Separate model/camera companions are excluded, and rejected clips are listed
   in import notes. The importer never scans every animation in the archive.
4. Enable **Matching camera rigs** to import `cam_modelname_*.geom` companions.
   Cameras are separate animated rigs. The **Cyber Sleuth** sidebar offers clip
   buttons and camera buttons. Choose a clip, then play Blender's timeline.
5. Edit the Blender meshes, rig, Actions or material nodes and save a `.blend`.
   Imported images are packed, so a saved project retains its textures. Neither
   import nor save modifies the extracted native files or installed game.

The base NLA track supplies the rest pose; the selected motion track overlays
it. Source playback rates are respected using NLA scaling against scene FPS.
Declared frame counts/durations are retained for sparse terminal holds. Camera
motion uses the actual hashed bone. The camera preview uses degree FOV converted
to a vertical sensor lens, near/far clips and perspective/orthographic type.
This fixes the old upstream degree-as-millimetre assignment; native in-game
shot/frustum parity remains unverified.

## Supported and incomplete behavior

Mesh positions, triangles, weights, normals, up to three UV sets and vertex
colors are editable Blender data. No vertices are merged or discarded. Native
bone hierarchy/bind matrices and animation curves become an armature and
slot-aware Actions compatible with Blender 5.1 and 5.3. Materials use a textured
Principled preview with source diffuse color/alpha and vertex colors. Sampler
images, native shader identifiers, uniforms and render-state records remain
inspectable. The `Fat` expansion pass gets explicit approximate backface culling
and transparent shadow rays, so its hull does not obscure the textured surface.
Its native vertex expansion is not implemented.

Native shader programs, toon lookup equivalence, reflections, material/user
channel animation, effects, collisions, audio, native packaging and native
export remain compatibility blockers. Native light records become attached
inspectable empties; they are **not** rendered lights. Camera preview and
component quaternion interpolation still need in-game/subframe parity checks.
Source preservation of these unsupported fields does not count as editing
support. No unsupported feature is represented as completed.

## Evidence (2026-11-14)

`tools/audit_models.py` parsed all **29 DSDBA** model bundles using the credited
readers. It found one missing texture in `d1109_hide`; other tested DSDBA
dependencies were present. This is a sampled corpus, not all 8,442 extracted
base-archive GEOMs.

`Time Stranger Importer/tools/blender_cyber_sleuth.py` runs registered import,
clip and camera operators, validates geometry/skin/action data, evaluates real
skill deformation, checks camera transforms against their bound source bones,
packs textures, saves/reopens a `.blend`, injects a failure after mesh creation
to prove rollback, and verifies the Time Stranger geometry/animation export
guards. Both Blender **5.1.2** and **5.3.0** passed:

- `chr002`: 4 meshes, 38 bones, 12 actions, 48,026 scalar keys, 3 images and
  3 related camera rigs. Native `bs01` changes evaluated mesh vertices by up to
  0.6943 source units in the test sample.
- `d1101_hide`: 43 meshes, 56 bones, 9 images and one explicitly non-rendered
  light record.
- Initial additional checks: `chr765` (5 meshes/28 bones), `chr000` (11 actions)
  and `cam_chr000_bs01_pc` camera motion.

Reports and rendered audit artifacts stay local under
`Cyber Sleuth/local/compatibility/`; no proprietary assets are committed.
`chr002-preview.png` was visually inspected: native body/face textures now render
after fixing the opaque expansion hull preview. That preview is not a game
shader parity claim.

## Credits

The vendored reader sources come from
[Romsstar/Blender-Tools-for-DSCS](https://github.com/Romsstar/Blender-Tools-for-DSCS),
commit `93221225d8576895e17a94522023896e530542b8`, originally by Pherakki
(copyright 2020, MIT), with Romsstar's maintenance. Upstream credits SydMontague's
DSCSTools and xdanieldzd's CSGeom. Full license and provenance accompany the
distributed `external/cyber_sleuth` package. Upstream's old Blender UI/export
operators and placeholder texture are not distributed.
