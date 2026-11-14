# Skill phases and native effect editing

Version 1.4 adds two user workflows. Neither is a complete source-to-native
particle renderer or full release acceptance.

## Import the actual attack phase

Use **File → Import → Digimon Time Stranger → New Century Skill Phases (.glb)**.
Select `model.glb` inside a captured New Century library. The operator finds the
model's captured skill catalog without a hardcoded character ID. Choose
**Primary attack phases** or one named phase. Cast, cinematic, held-pose,
approach and return choices remain separate.

The primary choice imports the non-triggered cast, or the approach when the
source uses that branch, for each unambiguous equipped attack. For Leviamon X
this imports **Triple Demon Jaw · Approach · attack_move_to** and **Sevenfold
Lightning · Cast · skill2**. The source motion lengths are 71 and 135 frames at
30 fps. Sevenfold's one-frame gameplay scheduling interval does not truncate
its motion. No cinematic camera is invented for either phase.

The dialog independently selects the source actor/animation, captured camera,
audio and effect references. Model import also offers **Import Related Skill
Scenes**. Ordinary model import still supplies the existing native donor
workflow and all captured model motions. Choose the scene in Blender's scene
selector to inspect each imported phase.

Held-pose phases depend on previous actor state. Importing one with **Source
Actor and Animation** enabled is rejected; disabling that option allows its
audio/effect records to be inspected without substituting a stand animation.
The importer does not concatenate phases or claim concurrent native clocks.

Source clips are placed at frame 1 plus their captured delay. Camera samples,
sound cues and effect markers use the same origin. Source cameras and lens
actions remain editable in Blender. Source effect records are explicitly
nonrendered; Unity shaders, particles, target sockets and native game output
are still release blockers. Imported material preview is the GLB's Blender
representation, not a promise of native shader parity.

## Edit native SPRK common fields

Use **Native Effect Graph (.sprk)**, select a node, then open **DSTS → Native
Effect Node**. The source bytes and hash are embedded in the `.blend`; saved
projects do not need the original SPRK to export.

Supported edits are primary and secondary XYZ translation/rotation/scale
ranges, signed-frame delay/lifetime ranges and existing common transform keys.
Rotation fields use degrees. A lifetime of `(-1, -1)` is the native infinite
sentinel. The key grid supports constant/linear interpolation and keeps the
original key count, channel metadata and tangent payload. Other channels and
unsupported node types remain intact and read-only.

The viewport axes mark minimum primary translations. They do not render
particles or evaluate ranges, curves, parent/socket inheritance or secondary
composition. Edit the panel properties; moving/keying the guide objects,
changing graph references, deleting nodes, or adding channels/keys is rejected
until supported. Nodes sharing one animation must not contain conflicting edits.

**Export Native Effect Graph** validates all edits before publication. Choose a
new file by default; **Replace Existing File** explicitly permits atomic
replacement. Unknown bytes and authored zero/negative scales are preserved.
These editors do not repair legacy source assets implicitly.

## Evidence and remaining work

`tools/blender_effect_workflows.py` passes on Windows x64 Blender 5.1.2 and 5.3.0.
It edits ranges, timing and a curve value; saves/reopens the `.blend`; exports,
reimports and compares scoped bytes; rejects unsorted keys, graph edits and an
existing destination; and imports both requested real skills through the
registered operator. Source camera absence, 71/135-frame motion, cue records and
save/reopen are checked. Existing full Blender smoke also passes on both
versions with the updated backend.

These checks establish editing and serialization behavior. Full particle
preview, socket lifecycle, emitter modules, refraction/billboards, native effect
BSON timelines, lights, collision editing, full package dependencies and runtime
game acceptance remain incomplete. The model-only packager still rejects effect
companions. No live Revision 14 asset is modified by these workflows.

## Maintained source and attribution

`src/spark.py` is a byte-identical copy of **Time Stranger Formats**
`addon/spark.py` at commit `bf333a4`, SHA256
`9ebfdc90c89f4696aaa35e69003ce34f9b6a80b99821912a6d9a35cb9bee8607`.
Make codec changes in Formats, then sync this module; do not add a second parser
inside the Blender adapter.

`src/nc_preview_source.py` promotes the source-backed camera/phase builder from
`Time Stranger/tools/export_nc_preview.py`, SHA256
`95f457ba097be73333dd05633432d4ccdee900464f3d123a1392958dd658539a`.
It depends only on Python and Blender's bundled NumPy, with no research-script
path or hardcoded actor ID. Its 12 source tests cover malformed GLB/accessor
ranges, cyclic hierarchies, nonfinite timing, stale selections, output failures
and Windows cross-drive references. English skill labels reuse the maintained
NC viewer's `ncSkillNames.json`; original captured names remain in provenance.

The bundled geometry backend is Formats `bf333a4`, CPython 3.13 Windows x64,
SHA256 `657755f90750f1c35de07b40dbf82e8b58793bdf5d3bc3a9f6763a8f99c4ff78`.
It stages a complete file before replacing an existing destination. Preserve
Named-Blade / Nymic_Razor, Pherakki and all existing upstream credits; this work
does not invent a license for upstream code.
