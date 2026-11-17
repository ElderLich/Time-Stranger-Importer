# Import native rigid effects

Use **File → Import → Digimon Time Stranger → Geometry (.geom)** and select the
effect from its extracted game folder. Keep its matching `.anim`, `.nlst`,
`images/` and `shaders/` accessible. Reimport the original GEOM to populate
the draw names and material previews in an older Blender project.

## See every component

Enable **Inspect Rigid Effect Components** in the Geometry file browser, or
select an imported rigid effect and choose **3D Viewport → N → DSTS → Inspect
Rigid Effect Components**. Blender opens a separate, framed Material Preview
scene. It places a copy of each mesh at its local origin and shows its color
texture, including RGB under transparent pixels. This lets you select and
inspect pieces whose native scale is zero. Materials without a color texture
appear white.

For `ef_b_chr220_bs01`, this reproduces the older fork’s white enclosure,
rectangular smoke card and central ring. The enclosure is `bg` and the large
card is `sumo`. The matching NLST supplies their draw names. An ordinary import
opens the animated rig at its native rest pose, where just three draws have
nonzero extent.

Choose **N → DSTS → Return to Native Effect** to play or export the rig.
Reopening the inspection scene reuses its mesh and material copies. Texture
images are shared. Delete the inspection scene to build a new snapshot after
editing the source geometry.

## Play the effect

1. Select the imported armature and open the **NLA Editor**.
2. Keep **[DSTS] Native rest scale (keep enabled)** enabled.
3. Unmute the effect’s animation track. If you use Solo, also solo the rest track.
4. Set the timeline range to the clip and play. Particle groups can begin at
   zero scale and appear later; a mostly empty starting pose can be intentional.

Fully rigid scenes attach each mesh to its native bone. Their source animation
contains absolute local transforms, so their motion strips use **Replace**.
Ordinary character animation keeps its existing **Combine** behavior.

Use **Material Preview** for animated color and transparency. With the
reported effect at 24 fps, Blender frame 48 shows the slash, sparks and smoke.
For reviewed vertex/pixel shader pairs, Blender previews the color sampler,
diffuse tint, visibility and applicable vertex colors, emission and alpha
cutouts. The color preview approximates additive blending. Native distortion,
billboarding, atlas animation, depth effects, additional shader passes and
runtime particle scheduling still require separate verification. When the matching
shader pair is unavailable or unsupported, the collection’s
`DSTS_effect_material_preview_notes` records the materials using the generic preview.

## Read warnings

The importer shows a compact summary and saves the full report in Blender’s
**Text Editor → Import Warnings**. It also retains diagnostics on the imported
collection. Repeated messages are grouped in the popup.

The file browser no longer attempts to duplicate an editor to show warnings.
An unavailable popup cannot undo an otherwise completed import. Structural
errors still reject the import and clean up the newly created data.

## Export

Mute motion tracks and clear the active Action, leaving the rest-scale track
enabled, before exporting Geometry. Export the effect motion separately as
Animation. Preserve the rigid bone attachments and the original rest rig.

For hash-only draws, export preserves the original GEOM hash and its external
name storage. Blender suffixes added during import have no effect on the
exported name. Renaming the native mesh or object after import explicitly
changes its draw name. If a matching NLST name is unavailable, the Outliner
uses the hash as a label and the import report records the missing name.

Unchanged spatial data preserves the native culling bounds, even when those
bounds differ from the initial hidden particle pose. Geometry or rest-pose edits
invalidate that preservation and use the writer’s calculated bounds. Review
edited bounds and playback in-game before installation.

## Verified case and limits

`ef_b_chr220_bs01.geom` imports 205 meshes on 213 bones without the old bounds,
identity-helper or zero-scale warnings. The regression compares bone and mesh
transforms to the source timeline, including the visibility transition at source
frame 1349, and checks GEOM bounds/bind matrices and ANIM keys through export.

This fixes geometry and animation interchange. Blender’s material preview does
not reproduce every game shader, runtime effect schedule or postprocessing pass.
Mixed skinned/rigid resources retain the existing import path.
