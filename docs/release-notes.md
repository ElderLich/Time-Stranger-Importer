# Release notes

## 1.5.8

- Fixed the warning-window crash when importing `ef_b_chr220_bs01.geom` from Blender’s file browser. A compact popup and saved Text Editor report replace editor duplication; a missing popup context no longer discards a completed import.
- Fully rigid native effects now use bone-local attachments and absolute animation channels. Zero-scale particle visibility keys are preserved, including groups that become visible later in the clip.
- Removed false bounds and unused-helper bind warnings for these rigid scenes. Unchanged spatial data retains its authored culling bounds on export; malformed numeric bounds and unexpected bind matrices remain diagnosed.
- Verified the reported effect’s meshes, animation keys, bind matrices and bounds, plus ordinary character, camera and New Century regressions. [Import and preview native effects](native-rigid-effects.md)

## 1.5.6

- The README and [guide index](README.md) now lead directly to model editing, cinematic previews, skill conversion and packaging.
- Blender operators are organized by workflow. Existing scripts can still import their helpers from the add-on entry point. The [credits](credits.md) distinguish this repository’s author from the developers of earlier tools.
- Each push to `master` now publishes an installable Blender ZIP and change notes on GitHub Releases.

## 1.5.5

Geometry export preserves the donor mesh’s tangent declaration per draw. Re-exporting a saved New Century scene restores its native outline layout and keeps its body tangents. Draws sharing a material can retain distinct layouts.

## 1.5.4

Large skinned meshes split into native draws of up to 128 bones. New Century imports select the donor shader variant for each mesh’s actual weight count, including the outline. This preserves skin weights on high-bone-count bodies, clothing and wings.

## 1.5.3

**File → Export → Digimon Time Stranger → Native Skill Sequence** builds one attack from trimmed actor phases, source cinematic and battle cameras, and converted caster effects. [Sequence recipes](native-skill-sequence.md)

## 1.5.2

Cinematic cameras resolve duplicate socket names using the mesh skin and the selected clip’s animation hierarchy. Both Shoutmon X7 **Fusion Blazing Rocker** cinematics use the moving camera on `u288_b/root/JT1/JT2`. Select either cinematic in **Skill Phases (.glb)** and press **Numpad 0** in the 3D Viewport to look through the camera. [Cinematic walkthrough](add-a-playable-digimon.md#preview-the-cinematic)

## 1.5.1

New Century animation orientation and native facial rest scales were corrected. [Reimport instructions and facial preview](import-animation-fixes.md)

## 1.5.0

Assigning a native motion camera preserves its camera, rig, FOV and NLST bindings. [Native camera fitting](native-camera-fit.md)

Earlier implementation and validation notes are in [the architecture guide](architecture.md).
