# Combined native model, camera and light import (1.4.3)

In **File → Import → Digimon Time Stranger → Geometry**, enable **Camera and
Light Previews**. The existing mesh/material/texture, rig and neighboring ANIM
import remains in use. Cameras and lights stored inside that GEOM appear in
owned child collections. This does not infer external skill, scene, effect or
collision dependencies from filenames.

Supported perspective records create actual Blender cameras. Select one and
choose **DSTS → Native Scene Records → Use Native Preview Camera**. The native
camera looks down local -Z, matching Blender, and its FOV is vertical. Its
transform follows the original native bone, including imported rig animation.
Edit FOV/near/far in **Native Scene Records**; controlled preview drivers update
the Blender lens. Unsupported projection modes, invalid source optical values,
and missing/ambiguous bone targets remain explicit inspection guides.

Native point lights use the existing light controls and follow their owning
rig's bone positions. Other light types retain the documented static optical
controls or preserved guide behavior. Relative intensity/range preview remains
approximate. Native animated optical channels are preserved as ANIM float
channels but are not yet evaluated into these camera/light previews. Editing
raw Blender camera/light settings is rejected on native export because those
settings are not the native source fields.

**Geometry Only** and **Export All** export the owning GEOM collection. This
combines model edits and its camera/light record edits in one file. The importer
rejects detached, foreign, deleted or duplicated preview collections, altered
bone bindings, unsupported camera drivers/settings, and a separate light export
from a model-owned preview. Target-bone renaming/removal requires explicit native
rebinding and remains rejected. Camera/light-only resources retain their exact
original GEOM bytes except requested optical changes, and keep their original
rest rig and NLST. Edit their motion through ANIM. Mixed mesh resources continue
through the existing mesh writer while retaining every camera/light record and
unchanged unknown record bytes.

Disabling **Camera and Light Previews** retains native records for export while
omitting the preview objects. Unsupported authored values are preserved rather
than normalized. Each file publishes atomically, including camera-only GEOM and
NLST outputs; a GEOM/ANIM/NLST/texture package transaction remains separate work.

## Evidence

Native `0x1402E2D80` binds a camera to its named bone and reads the native lens
fields. `0x1402CDFC0` composes camera and parent-bone transforms.
`0x1402CBEE0` constructs the perspective projection with `clip.w = -view.z`,
proving local -Z forward; constants and disassembly were checked in
`Time Stranger/local/camera-forward-native/projection-clock`. The vertical-FOV
derivation is in `0x1405018E0`. This is source evidence for the mapping; it does
not establish full cinematic framing, postprocessing or game-render parity.

`tools/blender_native_scene.py` passes on Blender 5.1.2 and 5.3.0. It covers
actual `cam_chr690_bs01_em`, `cam_chr690_bs01_pc` mixed camera/light resources
and `dep01t0103p` scene mesh/light data; native-record composition additionally
exercises simultaneous mesh, point RGB/intensity and camera-lens changes.
Checks include unchanged record bytes, camera-only exact file preservation,
bone-following transforms, centered -Z projection, vertical FOV updates,
save/reopen, no-preview retention, ownership/driver/DOF rejection and failed
atomic replacement preserving the previous output. The scene mesh exposed a
real no-UV tangent calculation failure, now fixed without inventing UV data.

No live game file was changed. Full optical animation, other projection modes,
native light falloff/shadows, effect scheduling, scene dependency discovery,
complete packaging and in-game acceptance remain open release requirements.
