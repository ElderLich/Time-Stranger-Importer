# Import native rigid effects

Use **File → Import → Digimon Time Stranger → Geometry (.geom)** and select the
effect from its extracted game folder. Keep its matching `.anim`, `.nlst` and
textures beside it so the importer can find them.

## Play the effect

1. Select the imported armature and open the **NLA Editor**.
2. Keep **[DSTS] Native rest scale (keep enabled)** enabled.
3. Unmute the effect’s animation track. If you use Solo, also solo the rest track.
4. Set the timeline range to the clip and play. Particle groups can begin at
   zero scale and appear later; a mostly empty starting pose can be intentional.

Fully rigid scenes attach each mesh to its native bone. Their source animation
contains absolute local transforms, so their motion strips use **Replace**.
Ordinary character animation keeps its existing **Combine** behavior.

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
