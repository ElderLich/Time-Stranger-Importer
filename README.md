# Time Stranger Importer

Version 1.5.2 resolves duplicate cinematic camera socket names using the mesh
skin and the selected clip's animation hierarchy. For Shoutmon X7, open
**File → Import → Digimon New Century → Skill Phases (.glb)**, select its library `model.glb`, and choose
**Skill 2 · Fusion Blazing Rocker — Cinematic**. Both the ordinary and triggered
cinematic entries use the moving camera on `u288_b/root/JT1/JT2`. Use Numpad 0
in the 3D Viewport to look through the scene camera, then play the timeline.

Version 1.5.1 corrects New Century animation orientation and preserves native
facial rest scales. See [import and animation corrections](docs/import-animation-fixes.md)
for reimport instructions and the supported facial preview workflow.

Native skill camera dependencies and reusable whole-clip framing are documented
in [native camera fitting](docs/native-camera-fit.md). Version 1.5.0 preserves
camera, rig, FOV and NLST bindings when assigning native motion camera assets.

**Bring Digimon into Blender. Keep the game data intact.**

A Blender **5.x** add-on for *Digimon Story: Time Stranger* models and animations,
with a New Century model-conversion workflow and cinematic preview tools.

[Install](#install) · [Workflow](#workflow) · [Cameras & effects](#cameras--effects) · [Technical notes](docs/architecture.md)

## What it does

| Asset | Import | Export |
| --- | --- | --- |
| Time Stranger `.geom` | Skeleton, meshes, materials, textures | Geometry + `.nlst`, native bind data and material animation targets |
| Time Stranger `.anim` | Bone motion and indexed float channels | One clip, NLA tracks, or an NC moveset |
| New Century `.glb` | Skinned model, bind pose, root motion and clips | Through a native TS donor `.geom` |
| Textures | `.img`, `.dds`, `.png` | Game-ready `.img` DDS with mipmaps, or PNG |
| Native camera rig | `cam_*.geom` + neighboring `.anim` | Motion channels, FOV and clip planes; unknown data retained |
| Cinematic preview JSON | Source actor, sampled camera/FOV and sound cues | Blender scene; no native TS effect package |
| Captured NC skill library | Select cast, approach, return or cinematic beside the source model | Independent Blender scenes with source animation/audio and optional captured camera |
| NC native skill recipes | Captured phase, explicit actor/socket/cue mappings and pinned converted components | Isolated native review assets and template-backed BSON; incomplete skills remain non-installable |
| Native `.sprk` effect graph | Verified common ranges, timing and transform keys | Scoped native edits preserving unknown bytes; translation guides only |
| Native effect `.bson` | Duration, hits, cue timing/cleanup and drawable references | Validated existing-field edits; 30 Hz timeline markers |
| Native collision `.phys` | Rigid bodies, boxes, spheres and triangle meshes | Existing body/shape/vertex/triangle/association edits; runtime validation pending |
| Native light records | Static RGB/intensity for verified types; approximate point-light preview | Scoped GEOM light edits, preserving other records; render parity pending |
| Cyber Sleuth PC model | Dedicated GEOM/NAME/SKEL/base ANIM reader, textures, matching clips and optional camera guides | Native export explicitly unsupported; see [Cyber Sleuth workflow](docs/cyber-sleuth.md) |
| Exported native model package | Existing `.geom` + base `.anim` + `.nlst` | Validated new package with local shaders/textures and a dependency manifest |

**Current verification target:** Windows x64, Blender **5.3.0**. Earlier milestones
also passed **5.1.2**; that installation has since been removed. The add-on targets the
5.x API family. Native geometry needs a backend matching Blender's embedded
Python; the included build supports **CPython 3.13**. See
[compatibility details](docs/architecture.md#blender-5x-compatibility).

## Install

1. Build this checkout with `python tools/package_addon.py` and use
   `dist/Time_Stranger_Importer.zip`. Version 1.5 is currently a local build;
   it has not been published to
   [Releases](https://github.com/ElderLich/Time-Stranger-Importer/releases).
2. In Blender, open **Edit → Preferences → Add-ons → Install from Disk** and
   select the ZIP. Enable **Time Stranger Importer**.
3. Find **Digimon Time Stranger** under **File → Import/Export**, and the
   **DSTS** tab in the 3D View sidebar (`N`).

New Century imports have their own **File → Import → Digimon New Century**
submenu, immediately above **Digimon Next Order** when that add-on is enabled.
It contains Model, Skill Phases, Native Skill Recipe and Cinematic Preview; native geometry,
animation and effect imports stay under **Digimon Time Stranger**.
Cyber Sleuth PC imports use **Digimon Cyber Sleuth**. Its different native
format is kept separate from Time Stranger export.

Disable older DSTS Unified/Nymic/Romsstar add-ons that register the same menus.

## Workflow

### Build a native skill review candidate

Choose **Native Skill Recipe** from the New Century import menu, then use
**New Century Native Skill** in the DSTS sidebar. Map the native actor, motion,
caster/target sockets, donors and audio cues; add reviewed component manifests,
inspect the requirements and build into a new isolated folder. The recipe and
review persist in the `.blend`. See [the recipe workflow and API](docs/skill-conversion.md).
For supported local meshes, **Load Component Job** selects a pinned source bake,
reviewed material carriers and native donors. **Generate and Add Components**
authors the native particle resources and adds them to the recipe, without
editing per-skill scripts. See [component generation](docs/finite-producer.md)
for the required inputs and supported profile.
The bounded [velocity billboard profile](docs/SKILL_VELOCITY_BILLBOARD_PROFILE.md)
also preserves fixed source 3D rotation, pivot and hierarchy scaling for reviewed
additive effects. It is a producer API; the saved local-mesh job does not select it.
**Use Audited Actor Motion** reuses an already verified native animation in the
selected motion slot, preserving every animation byte. **Author Native Effect
Bindings** writes named caster/target effects, native hit frames and the mapped
source sound. **Native Hit Origin** retains the game's normal target locator.
These choices survive save/reopen; see [native asset bindings](docs/native-skill-bindings.md).
The source-sampled billboard, layered dissolve and stationary-root profiles
finish finite representation of the current reference's 74 source systems.
The stationary-root profile explicitly excludes moving roots. These are asset
conversions; game execution stays native and in-game appearance needs testing.

### Edit a Time Stranger model

Import a `.geom` from extracted game data. The importer finds neighboring
animations and nearby/shared texture folders. Edit the mesh, then use
**Geometry Only** or **Export All**. Keep one material per mesh, apply object
transforms, and clear accidental poses before exporting the rest geometry.
UV and normal seams are preserved; skin weights are limited to the four
strongest bone influences.

Enable **Camera and Light Previews** to import the GEOM's own scene records
with the model. Supported perspective cameras and point lights follow their
native bones; camera lenses and verified light fields export with the owning
model. Mixed camera/light/mesh files are supported. Scene records remain
preserved when previews are disabled. See [combined native scene workflow](docs/native-scene.md)
for the supported controls and remaining animation/rendering limits.

### Add a New Century model

Use **New Century Model (.glb)** and select a native Time Stranger donor `.geom`.
The importer converts bind poses and root motion, rebuilds the export rig,
borrows the donor's material metadata, and optionally creates an outline mesh.
Retarget the donor texture references to your model's actual images using the
sidebar tools. Export the geometry and choose **NC Moveset** for TS clip names.

Enable **Import Related Skill Scenes** in the model import dialog to also open
each unambiguous captured cast/approach in a separate scene. Or choose
**New Century Skill Phases (.glb)** to select an individual phase, including a
cinematic. The default selects the actual attack rather than the showcase.
Cameras are imported only where the selected source phase contains one;
effect timeline records remain inspection references. See the
[skill/effect workflow](docs/skill-effect-workflows.md) for the current limits.

After the selected motion, the isolated preview plays its captured stand/idle
clip instead of falling back to the model's bind pose. Source helper empty
hierarchies are hidden by default and can be shown in the import dialog.

The donor provides the game's shader/layout contract. It does not make Unity
materials or effects automatically equivalent to Time Stranger's renderer.
Mod IDs, MBE entries and sound-bank installation remain separate tasks.
Split donor materials retain independent native data: all 14 shader slots,
numeric uniforms, render settings and material flags. Give split materials
distinct names before export. Splitting an animated material requires explicit
float-channel remapping and is currently rejected.

New NC meshes use their own indexed bone palettes. Borrowing a material retains
the target mesh's palette mode instead of copying the donor's contiguous-bone
assumption. Native imports preserve their original mode; export and packaging
reject an incompatible sparse palette. Fresh skeleton exports also write valid
native parent padding, preventing a one-bone effect from losing its placement.

Importing the next NC model does **not** automatically bring its Unity shaders,
particles, scenery, persistent effects or complete skill cinematic into the
game. The model workflow handles the rig and supplied motion clips; it borrows
a native donor material contract. Our separate conversion research has not yet
become a general effect/shader converter. See the
[capability map](docs/native-model-package.md#capability-map).

### Package an exported native model

After exporting geometry and its compatible base/motion animations, choose
**File → Export → Digimon Time Stranger → Package Exported Native Model**.
Select the `.geom`, choose a **new** output folder, and optionally select the
extracted asset folder containing `images/` and `shaders/`.

This checks name-list identity, animation channel counts, native matrix
alignment, all active shader-pass dependencies and texture bindings. Local
images/shaders enter `dsts-loader/patch`; resources resolved only from the
native extraction are recorded as external dependencies, so they are not
copied over unrelated mods. Missing or invalid dependencies cancel before a
package is published. Existing output folders are never merged or overwritten.

The result is an asset package and JSON manifest, not an installed Reloaded II
mod. MBE hooks, LODs, camera/effect packages and sound routing remain separate.
Models with a same-stem SPRK/BSON companion are explicitly rejected by this
model-only packager. [Package checks and limits](docs/native-model-package.md)
describe what validation establishes.

Native effect timing and collision editing have separate import/export menus.
See [native timeline and collision editing](docs/native-timeline-collision.md).
Those files are not yet assembled into complete effect/scene packages.

### Edit animations

Import `.anim` onto the matching skeleton. Each clip becomes a muted NLA track;
unmute the desired track to preview it. Export one track or all tracks.
Timing follows the scene FPS and strip scale. The exporter uses the selected
slot and trim range; bake repeated/reversed/time-remapped or multi-strip tracks
before exporting. Camera/material float channels remain visible as
`DSTS_float_N` custom-property curves rather than being silently discarded.
Native material targets survive export and follow material renames.

## Cameras & effects

**Native cameras:** import a file such as Lucemon CM's `cam_chr039_bs01_pc.geom`.
Its motion channels are editable. The DSTS sidebar exposes native vertical FOV
and near/far clip planes; unknown records and the name list are preserved.
Animated lens channels can override static FOV. Changing the rest rig or adding
new native camera records is not supported yet.

**New Century cinematics:** import a `dsts-cinematic-preview-v1` JSON through
**New Century Cinematic Preview**. A separate scene contains the source actor,
animated camera, vertical FOV and sound strips. Effect-prefab cues are retained
as source references and timeline markers. Unity particles and shaders are
**not rendered or converted to native Time Stranger effects** by this preview.

[Architecture, repaired bugs and validation](docs/architecture.md) describes the
boundaries and the reproducible headless checks.

## How it works

The compiled **Time Stranger Formats** backend, derived from DSTS Formats Parser,
reads and writes geometry. Blender objects
retain material layouts and game metadata needed for export. Pherakki's Python
animation serializers handle `.anim`; a shared Blender adapter preserves
animation channels, timing and action slots. The NC conversion fits the source
inverse bind matrices instead of guessing a model rotation.

## Credits

- **Senator John, Nymic_Razor and Zeak** — DSTS tools and the earlier unified
  importer, parser integration and workflows this project builds on.
- **Named-Blade / Nymic_Razor** — original
  [DSTS Formats Parser](https://github.com/Named-Blade/dsts-formats/tree/parser),
  the upstream source for the maintained geometry backend.
- **Pherakki** — animation serialization, DSCS tooling, and the bundled
  `blunger`/`exbip` foundations; see
  [Blender Tools for DSCS](https://github.com/Pherakki/Blender-Tools-for-DSCS).
- **Romsstar** and the Digimon modding community — earlier tools and practical
  model/animation workflows.
- **SydMontague** — [DSCSTools](https://github.com/SydMontague/DSCSTools) and the
  wider extraction/modding ecosystem.
- **Hyotan's workshop** — [Time Stranger guides](https://hyotan-dev.github.io/dstsworkshop/).

Game assets are not included. Upstream components retain their original terms.
