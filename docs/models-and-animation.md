# Models and animation

## Edit a Time Stranger model

1. Choose **File → Import → Digimon Time Stranger → Geometry (.geom)** and select the extracted model. The importer looks for neighboring animations and nearby or shared textures. Enable **Camera and Light Previews** to edit the supported scene records with their owning model. [Native scenes](native-scene.md)
2. Keep one material per mesh. Apply object transforms and clear accidental poses before exporting rest geometry. The exporter preserves UV and normal seams and keeps the four strongest bone influences per vertex.
3. Choose **File → Export → Digimon Time Stranger → Geometry Only (.geom)** for the mesh and its name list, or **Export All (.geom + .anim)** for geometry and animations. To collect textures and shaders for a mod, continue to [native model packaging](native-model-package.md).

## Use a New Century model and donor

1. Choose **File → Import → Digimon New Century → Model (.glb)**. Select the original library GLB and a Time Stranger donor `.geom`. The importer converts the bind pose and root motion, rebuilds the export rig, and borrows the donor’s material settings. Enable the outline mesh if your model needs one.
2. In the **DSTS** sidebar, retarget the donor’s texture references to the model’s images. Split materials can keep independent shader slots, numeric uniforms, render settings and flags. Give each split material a distinct name. Animated material splits require float-channel remapping, which the exporter does not currently support.
3. Export the geometry, then choose **NC Moveset (.anim)** for the game's motion filenames. Play the exported motions and check their source clips. For example, knockdown (`bd02`) can reuse hurt, and get-up (`bd03`) can reuse stand or idle.

New Century meshes use their own indexed bone palettes. Borrowing a material preserves the target mesh’s palette mode. Native imports keep the original mode, and packaging checks its compatibility. See the [capability map](native-model-package.md#capability-map) for details on shaders, textures and dependencies.

**Outline thickness.** The generated shell sits at the body’s rest positions. Its faces and corner normals point inward. In game, the stock `MTR_line01` shader adds `normal × vertex red × Fat` to each position before skinning. The Demon Lords and Four Sovereigns packs use `-0.003` in both columns of the actor’s `model_outline.mbe/000_model_outline_battle.ap.csv` row. A more negative thickness expands this shell farther. The Blender material preview hides this special draw.

For an older scene with an untouched generated shell, reimport the original GLB with **Build Outline Mesh** enabled. To keep body edits, duplicate the edited body, keep its vertex groups and `uv1`, assign the donor line material and flip the faces inward once. Keep the shell at the body surface for the stock thickness. A manual Edit Mode shrink/fatten adjustment also survives export and adds to the in-game expansion. Export the GEOM and NLST for each model LOD your mod uses. The exporter supplies white if the shell lacks a `Color` layer and preserves authored colors when present.

**Import Related Skill Scenes** opens unambiguous captured casts or approaches in separate scenes. To pick a cinematic yourself, use **File → Import → Digimon New Century → Skill Phases (.glb)** on the original library GLB. The preview plays the actor, its captured camera and audio where available, then uses its stand or idle motion if supplied. The import dialog can reveal the source helper hierarchy. Effect markers point to the assets to convert for the game. [Skill phases and effects](skill-effect-workflows.md)

## Edit and export an animation

For missing guard, defeat, recovery and field motions, see [Author a missing moveset slot](author-a-moveset-slot.md).

1. Import **Animation (.anim)** onto its matching skeleton. Each clip becomes a muted NLA track. Unmute the track you want to preview.
2. Choose **Animation (.anim)** for one track or **All Animations (.anim)** for the set. Export uses the selected action slot, trim range, strip scale and scene FPS. Bake repeated, reversed, time-remapped or multi-strip tracks before exporting.
3. Inspect indexed `DSTS_float_N` curves for camera or material float channels. Native material targets survive export and follow material renames. [Texture animation](texture-animation.md)

## Work with cameras

For a native camera, import a rig such as `cam_chr039_bs01_pc.geom`. The DSTS sidebar exposes vertical FOV and near/far clip planes. Animated lens channels can override static FOV. Keep the rest rig and existing camera records intact. [Native camera fitting](native-camera-fit.md)

For a captured New Century preview JSON, choose **File → Import → Digimon New Century → Cinematic Preview (.json)**. Its separate scene holds the source actor, camera, animated FOV, sound strips and effect cue markers. To bind that presentation to a native attack, use the [skill recipe](skill-conversion.md) and [native sequence](native-skill-sequence.md) workflows.

## ReArise library models

In **File → Import**, choose **Digimon ReArise Model and Motions (.glb)** and open the actor’s `model/model.glb` from the ReArise library. Choose a Time Stranger donor GEOM for its native body material and vertex layout. Set **Model and Motion Scale** to fit the intended actor. The tested WarGreymon X forms use `chr027.geom` and a scale of 1.767.

The importer supports skinned body draws with named `a01`, `a02` and similar atlases. It imports their color textures, attachment bones and independently named motion tracks. Blended overlays and rigid attachments need their own native material or attachment mapping. Retarget the material texture names and use **Export All** for the model, name list, rest pose and captured motion files.

For the full battle presentation, the ReArise timeline’s camera, visual effects and sounds need separate native assets and dispatch bindings in Time Stranger’s skill and effect records.

For a second skill, check the actor clip in the ReArise skill timeline. A populated `BS02` can supply its own motion. Both WarGreymon X forms use `BA02` for their second skill, so that motion can be exported into Time Stranger’s `bs02` slot.

Use **Battle Plan Only** in the ReArise import dialog to inspect a character’s source actions. [Plan the port and validate the staged skill bindings](rearise-port-workflow.md)
