# Implementation and validation

## Source layout

Blender loads the root `__init__.py` for add-on metadata and registration. It also exposes the operator classes and helpers used by older Blender scripts. Edit their implementations under `src/operators/`.

| If you are changing… | Look in |
| --- | --- |
| Menu items, sidebar controls, or the registration lifecycle | `src/operators/ui.py`, `registration.py` |
| Donor paths, character IDs or collection selection | `src/operators/context.py` |
| Geometry import/export buttons or model packaging | `src/operators/geometry.py` |
| Pose, transforms, UV preparation or material retargeting | `src/operators/rig.py`, `materials.py` |
| Animation file operators or NLA track setup | `src/operators/animation.py`, `nla.py` |
| New Century model import, moveset slots and fallback clips | `src/operators/new_century.py` (including `_NC_MOVESET_MAP`) |
| Captured phase selection, previews or native skill recipes | `src/nc_skill_ui.py`, `src/nc_preview_source.py`, `src/nc_native_skill_ui.py`, `src/skill_conversion/` |
| Low-level geometry, material, skeleton or animation conversion | `src/geom.py`, `src/mesh.py`, `src/material.py`, `src/skeleton.py`, `src/animation.py` |

Blender-independent tests live under `tests/` and `tools/test_*.py`. Blender integration checks and the ZIP builder live under `tools/`. Use `build/` for generated test output and `dist/` for installable ZIPs. [Run the checks](#run-the-checks)

## Rigid effect inspection in 1.5.9

The bundled Windows backend is built from Time Stranger Formats `cab2298`.
Its `Mesh.set_external_name` retains draw hashes stored without an embedded
name. The importer resolves their Blender labels from the companion NLST.
`src/effect_material_preview.py` builds color and coverage nodes for reviewed
shader pairs. `src/operators/rigid_components.py` creates the separate static
inspection scene. [Import and inspect a rigid effect](native-rigid-effects.md)

## Version 1.4 skill/effect additions

The [skill/effect workflow](skill-effect-workflows.md) documents the new generic
source phase selector, optional camera/audio import, related-model choices and
native common SPRK range/key editing. Actual cast and approach clips are chosen
independently from cinematic showcases. Native effect export uses the maintained
Formats codec; the axes in Blender are translation guides, not particle draws.
Both workflows passed import/edit/save/reopen checks in Blender 5.1.2 and 5.3.0.
The backend is now Formats `bf333a4`, SHA256
`657755f90750f1c35de07b40dbf82e8b58793bdf5d3bc3a9f6763a8f99c4ff78`, including
atomic file replacement. The older implementation narrative below records the
1.3 baseline; its shader/rig repairs remain in force.

## What was rewritten in 1.3

The binary serializers and source inverse bind matrices are retained.
The Blender-facing animation and mesh export paths are rebuilt around explicit
data ownership and testable conversion functions.

| Module | Responsibility |
| --- | --- |
| `src/blender_compat.py` | Detect action APIs; select one owner's action slot; create NLA tracks. |
| `src/animation.py` | Shared animation import, sampled vector/quaternion export, duration, masks and opaque float channels. |
| `src/mesh.py` | Convert per-corner Blender attributes into seam-split GEOM vertices; filter/normalize skin weights. |
| `src/scene_state.py` | Track newly created datablocks; roll back imports without deleting unrelated user data. |
| `src/native_camera.py` | Preserve camera GEOM/NLST data; edit proven FOV and clipping fields without rewriting unknown sections. |
| `src/cinematic.py` | Import a source-backed NC camera/audio preview into a separate scene. |
| `src/nc_glb.py` | Preserve NC bind poses, root motion and DSTS bone-location semantics; borrow donor material metadata. |
| `src/backend.py` | Load the geometry extension using CPython's supported ABI suffixes. |
| `src/model_package.py` | Validate one native mesh model and exact dependencies; publish a new package with hashes and explicit external native resources. |

### Repaired failures

- A 30 fps scene used 24 fps NLA conversion, changing playback speed.
- Bezier quaternion component curves overshot native spherical interpolation.
- Animation export read isolated key values, resetting other components at
  asynchronous keys and truncating fractional key times. It now samples complete
  vectors, normalizes quaternions, and handles Euler/axis-angle bone curves.
- Float channels, blend masks, unknown animation header values and static-clip
  duration were discarded. They now survive the Blender round trip. Float
  channels remain indexed custom properties; their game-specific bindings are
  not guessed.
- Layered actions could mix channels from different owners. NLA export uses its
  exact action slot and trim range. Reversed, repeated, time-remapped and
  multi-strip tracks require an explicit bake instead of silently exporting
  just their first action.
- Creating an NLA strip before choosing its layered pose slot could retain the
  empty first slot's 0–1 interval. Sixteen real Leviamon clips then exported only
  two frames. Strip creation now sets the selected slot's full source range.
- Rebuilding the NC root changed its children's rest basis while retaining their
  previous local quaternions. The native reimport rotated the model relative to
  its cinematic camera. Conversion now captures source world poses first and
  solves every target basis against the final hierarchy and bone flags. All 17
  Leviamon clips reconstruct within 0.0000076 matrix units before compression.
- Mesh export collapsed UV, split-normal, color and tangent seams onto the
  first corner of each Blender vertex. Export now splits by the full corner
  tuple and retains the original skin influences.
- Non-bone vertex groups could displace actual skin weights. Filtering now
  precedes the four-influence limit. Empty/unweighted meshes fail clearly.
- New Century cleanup removed *all* unused meshes/materials/images in the file.
  Only datablocks created by the current import are eligible for cleanup.
- Texture export reused stale source DDS bytes after pixel editing, wrapped
  out-of-range channels and omitted mipmaps. Edited/generated textures now
  export clipped BGRA DDS with mipmaps and the game's `.img` extension.
- Loaded byte-backed sRGB images received a second gamma conversion (source
  value 64 became 137). Byte buffers now decode before linear mip filtering;
  float buffers retain their linear interpretation. All 109 real cinematic
  texture base levels match their source PNG pixels exactly after DDS export.
- Shader preview and export relied on the original backend's incorrect parameter
  labels. Nodes now retain numeric GEOM IDs; texture roles use those IDs. A
  bundled legacy-label snapshot preserves bindings in existing `.blend` files
  when the native parameter table is corrected.
- Native material float bindings now survive geometry import/export as well as
  their ANIM curves. Material hash targets follow renamed materials; missing
  targets fail explicitly. Metadata lookup uses the native data node type, so
  renaming a material or its node group cannot disconnect its export settings.
- Splitting an NC donor material previously copied only the outer Blender
  material; its nested native data group remained shared. Retargeting one atlas
  could therefore change another. Splits now own independent native groups,
  including the 14 pass descriptors, vertex layout, numeric uniforms and render
  settings. Retargeting also isolates native groups shared by ordinary Blender
  material copies. Labels are presentation only when a numeric parameter ID is
  present. Material header and parameter trailer words survive saved scenes;
  uint32 values use decimal strings in Blender properties. Older saved scenes
  without trailer properties retain the backend's existing defaults.
  Numeric uniforms also retain the unused bytes after their active floats in
  the 16-byte payload. Those bytes are opaque and can be nonzero in native
  donors; editing an active value does not zero the remaining payload.
- Native-name collisions after removing Blender suffixes and ambiguous animation
  targets after splitting an animated material now fail explicitly. This avoids
  silently assigning every affected mesh or float channel to the last material.
  Broadcasting/remapping animated material channels is not implemented.
- The bundled Formats backend validates static texture bindings against native
  parameter type and scope, rejecting matrices, scalars and global-only inputs.
  Numeric payload counts and opaque metadata remain preserved; no shader-pass
  meaning is inferred from a slot number. These checks establish data retention,
  not correct in-game rendering for an arbitrary donor/shader combination.
- Generated base `.anim` files explicitly keyed identity transforms, erasing
  native bone offsets before motion blending. They now use sparse one-frame
  clips matching the genuine chr050/chr395 base files. Native `0x140364070`
  initializes channels from GEOM; `0x1403A5B30` and `0x1403A65B0` replace keyed
  values rather than interpreting identity keys as the model's rest pose.
- Motion translation now follows the native additive contract: parent-space
  translation sums, quaternion products, and component-wise scale products.
  Roots convert Blender Z-up location channels to native Y-up; bones with
  local-location enabled also convert through their rest basis. Import applies
  the inverse mapping. Object motion is composed as an absolute root pose before
  recovering native deltas. Tests compare evaluated rotated Blender rigs with
  independent native pose assembly, including both location modes. Older native
  imports retain their stored vector convention when exporting saved actions;
  reimport their ANIM files to update their Blender preview coordinates.
- The updated geometry backend bounds binary reads and preserves camera, float,
  and parent-table records. A compact source bind-pose snapshot survives saved
  Blender scenes. Untouched bones retain exact source TRS and inverse bind
  matrices; rest edits invalidate the snapshot and recalculate the matrices.
  Flagged native parent records survive unchanged hierarchies, while unsupported
  topology edits fail instead of dropping flags.
- Mixed-rate cinematic sequences were concatenated without resampling. They
  now sample each source strip at a shared output rate and share join endpoints
  without adding a frame. The next phase owns the key at a cut.
- Failed donor animation replacement deleted the previous clip before validation.
  Replacement now stages successfully before removing the old track. Temporary
  export actions restore the user's prior active action and slot.

## Blender 5.x compatibility

The Python API layer uses capability detection rather than a hardcoded 5.1
branch. The bundled geometry backend is Windows x64 CPython 3.13; it was loaded
and exercised in Blender **5.1.2** (Python 3.13.9) and **5.3.0** (Python 3.13.13).
Other 5.x releases need the same supported APIs and a backend matching their
embedded Python ABI. This is a compatibility target, not a claim that every
uninstalled or future release has been tested.

`dsts_formats` is a pybind11 C++ extension. The bundled build uses the maintained
**Time Stranger Formats** source, derived from DSTS Formats Parser. To build
another ABI/platform, use that source with the Blender release's Python headers and
libraries, CMake and a C++ compiler, then place the resulting
`dsts_formats<extension-suffix>` file in `src/`. Renaming a CPython extension
does not change its ABI. Animation reading/writing uses the vendored Python
serializers; native geometry still requires the extension.

## Cameras, particles and audio

Native `cam_chr039_bs01_pc.geom` (Lucemon Chaos Mode) has a camera section that
the original C++ writer could not emit. The updated backend preserves those
records; the add-on retains its conservative original-byte camera workflow.
Import retains its complete original GEOM
and NLST in collection properties, imports the skeleton and neighboring ANIM,
and allows motion-channel editing. Geometry export refuses a changed rest rig.
Pinned native runtime evidence establishes **56-byte** camera records (the
single-camera fixture's 64-byte section includes 8 alignment bytes):
`0x1402E05E0` copies the file records and resolves the bone hash;
`0x1402E2D80` reads FOV degrees at +4, near at +12 and far at +16;
`0x1405018E0` constructs a vertical-FOV frustum. These three fields are editable
in the sidebar and patched in place. Other bytes remain unchanged. Native base
camera ANIM files use absolute pose channels; the research staging codec handles
that separately from ordinary skeletal skill deltas. Mixed mesh/light/camera
GEOM files are rejected.

`dsts-cinematic-preview-v1` is a preview interchange, not a game effect format.
It supplies a source actor GLB/clip, frame rate/origin, sampled Unity camera
position/quaternion/vertical FOV, audio cues, and effect-prefab references.
The importer reflects Unity X, converts glTF Y-up to Blender Z-up, and converts
camera +Z forward to Blender -Z forward. It creates a separate scene, camera
motion and lens actions, VSE sound strips and nonrendered source-effect records.
It does **not** translate Unity particles/shaders into Time Stranger's effect
format or inject a sound bank into the game.

## Publish a release

Push to `master`. Each push gets one patch version. GitHub builds the ZIP, checks its embedded Blender version and publishes the tagged source alongside the ZIP. Release notes list the commit subjects in that push. To add a user-facing overview, write it under the matching version heading in `docs/release-notes.md`.

The push-only `release.yml` workflow supplies the run number, so retrying a failed run keeps its version. The tag contains the version-stamped source. On `master`, `bl_info` records the starting version of the series. For a new minor or major series, update that tuple and set `FIRST_SERIES_RUN` in `tools/prepare_release.py` to the next push workflow run number.

## Run the checks

To build a released version locally, check out its Git tag and run `python tools/package_addon.py`. Blender installs the resulting `dist/Time_Stranger_Importer.zip`.

From the repository root, run both Python suites. Run the Blender checks with the geometry backend and the local game fixtures available.

```powershell
python -m unittest discover -s tests
python -m unittest discover -s tools -p "test_*.py"
blender --background --factory-startup --python-exit-code 1 --python tools/blender_smoke.py
blender --background --factory-startup --python-exit-code 1 --python tools/blender_smoke.py -- --nc-export
blender --background --factory-startup --python-exit-code 1 --python tools/blender_material_contract.py
blender --background --factory-startup --python-exit-code 1 --python tools/blender_model_package.py
python tools/package_addon.py
```

The smoke script verifies registration lifecycle, layered slots, escaped bone
names, asynchronous component keys, quaternion normalization, native animation
serialization, masks/float channels and FPS conversion. When local fixtures
exist, it also imports/exports chr050, tests UV seams/non-bone weights, verifies
exact source bind matrices, exercises a real material float channel and renamed
target, preserves chr800's flagged parent records, verifies Lucemon's camera
bytes, and loads the real Leviamon X cinematic manifest.
`--nc-export` additionally runs the full Leviamon X donor-model conversion and
geometry/animation export. Mixed-rate multi-part sequence timing is covered by
the synthetic fixture even when the chosen NC actor has no three-part skill.
Game assets remain local.

The material-contract check uses the local native body donor (or chr050), splits
its Blender material, edits one atlas/numeric value and verifies the other copy
is unchanged. It compares every pass, uniform, setting and exposed metadata word
through `.blend` save/reload and GEOM serialization, and rejects a truncated pass
table or matrix-as-texture binding. Its historical donor's invalid sparse fast
palette is explicitly rejected without truncating an existing destination, then
repaired only in the temporary test object before unrelated material checks.
The updated backend, full smoke/NC export, material and package checks passed
Blender 5.1.2 and 5.3.0. The backend is built from Formats commit `a6b03ae`,
CPython 3.13 Windows x64, SHA256
`fb45694aa8682d5b5dbc5e9798eb470e4f4d961b87256d6d341c7a946c939bed`.
It was not renamed from another ABI.

Native matrix upload ignores later palette indices when mesh `flag_2` is set;
only an ascending contiguous skeleton range is valid. New NC geometry therefore
uses indexed gathering. Borrow Material Setup retains the target's palette mode;
ordinary native import retains its original flag for inspection. The backend
writer and exact-byte packager reject invalid flagged palettes. The sidebar
names the flag **Contiguous bone palette** so intentional edits are reviewable.

The native skeleton evaluator executes every lane of a four-pair parent block.
New or regenerated skeletons now fill and count those lanes with repeated valid
pairs instead of zero `(0,0)` self-parent operations. Unchanged imported pair
records and physical tails are preserved, including forensic legacy failures;
reading or rewriting such a file is not a repair. The exact-byte packager checks
the physical lanes and refuses unsafe inputs before output. These guards change
neither the completed bind-pose conventions nor animation axes.

The smoke tests establish serializer and Blender behavior. In-game rendering,
particle parity and optical-camera equivalence need separate game validation.

The native model package step promotes dependency and matrix-alignment checks
from the conversion research without importing its unfinished model-specific
shader/effect adapters. It preserves existing native bytes, validates the base
ANIM/NLST and optional matching motions, and separates local payloads from native
resources. It explicitly rejects effect/scene packages. See the
[capability map and package contract](native-model-package.md).

## Reference comparison and audit coverage

The local **Romstar** copy was inspected directly: its README/license,
registration code, Time Stranger geometry importer, shared animation importer,
DSCS camera binary record and Blender camera creation code. Despite its inherited
"Import/Export" name and README, this particular copy has its export imports,
classes and menu registration commented out, and no `BlenderIO/Export` directory.
It supplies Time Stranger geometry and animation **import**, not an enabled TS
animation export workflow. Its animation adapter hardcodes `24/playback_rate`,
uses Bezier quaternion component curves, and receives float channels without
constructing their curves. The new adapter replaces those behaviors and tests
both import and export against native animation files.

Romstar's DSCS camera record is 48 bytes and reads a projection integer at
offset `0x18`; native evidence proves Time Stranger uses 56-byte records and
has a float value of 25.4 there. Its DSCS camera helper therefore cannot be used
as a verified Time Stranger lens decoder. The Time Stranger import path does
not call that camera helper. This is why native camera records remain preserved
rather than being rewritten using the older layout.

The **DSTS Unified** reference README and the **DSTS Formats Parser** C++ geometry,
camera-header, skeleton and Python-binding code were inspected. The reference
README identifies its older Blender 4.5.1/CPython 3.11 baseline. The compiled
backend's camera/light writer limitation was confirmed directly in its source.
The third-party **dsts-extractor** geometry reader and format/camera research
notes were also consulted; those notes are evidence about their own observed
files, not a replacement for the current parser's record layout.

Material review covered image lookup, texture-reference names, donor metadata
and vertex-layout retention, corner attributes/skin weights, and DDS output.
Native chr050 and the Leviamon donor export were exercised. This was not an
in-game pixel comparison of every shader/material family. The separate
`dsts-viewer` checkout was inventoried, not independently audited in full.

The video directory was inventoried. Existing contact sheets `contact_01.jpg`,
`contact_04.jpg` and `contact_08.jpg` were visually inspected: they show the
Nymic-derived donor geometry workflow, explicit `uv1` renaming, export dialogs
and an in-game result. This was a representative frame review, **not** a full
playthrough or transcription of all eight videos. The new workflow retains
donor material metadata and the `uv1` contract; the full-length skills/tutorial
videos were not independently reproduced step by step in this pass.

Only installed Blender 5.1.2 and 5.3.0 were executed. Blender 5.0, 5.2, other
platforms, alternative Python ABIs and future 5.x releases remain untested.
No new GitHub release or installed add-on update was performed; the delivered
ZIP was built from this repository.
