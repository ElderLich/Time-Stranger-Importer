# Add a playable New Century Digimon to Time Stranger

**Already imported the model and want to see the attack?** In Blender, open **File → Import → Digimon New Century → Skill Phases (.glb)**, select the original library model, and follow [Preview the cinematic](#preview-the-cinematic). To make that attack play in Time Stranger, continue through the native export and Mod Studio steps below.

## Choose the model and its Time Stranger counterpart

Use Blender 5.3 with the current Time Stranger Importer, the extracted PC game assets, Time Stranger Mod Studio, Art Studio, and an extracted New Century library. Keep the library layout intact. Blender reads the GLB, the skill catalog under `MANIFEST`, the exported WAV files, and the prefab captures under `Vfx_assets`. A loose GLB can play its embedded animations.

1. Open the New Century viewer. Find the exact form and note the model code. For example, Lucemon X is **x151**, and Lilithmon X is **x118**. Select the named skills before opening **Show source combat clips**. The clip list also contains internal motions. A passive can have an entry with no playable action. A triggered attack can have its own cinematic.
2. In Mod Studio, use **Configure Paths** and check **Field Guide → Slot conflicts** against your installed mods. Choose a free Digimon ID, `chr` model ID, character key, Field Guide number, skill IDs and effect IDs. Record the source model and your chosen IDs together. Custom Field Guide slot 578 uses `chr768` in this pack because 766 and 767 are occupied.
3. Choose a native form with comparable posture and attack presentation. For Lucemon X, select **Lucemon CM** (Falldown Mode, `chr039`). Open the native model, motions, battle skills, and PC/enemy cameras to see what the game expects for that form. Keep the source model and donor in separate Blender scenes.

## Export the model and ordinary motions

1. Open **File → Import → Digimon New Century → Model (.glb)**. Select the original `Digimon_Library/<form>/model/model.glb` and the donor's native `.geom`. Use the converted export rig and select the source textures for each native material. Inspect detached meshes such as weapons and sigils from both sides.
2. Export **Geometry Only (.geom)** and the associated base animation/name list, or use **Export All (.geom + .anim)**. Use **NC Moveset (.anim)** for the standard battle and field slots. Play several exported clips. `bd02` is knockdown and can reuse hurt. `bd03` is get-up and can reuse stand or idle. Matching filenames alone cannot tell you which motions are distinct.
3. Use **File → Export → Digimon Time Stranger → Package Exported Native Model (.geom)**. Select the geometry, the extracted resource root containing `images` and `shaders`, and a new output folder. Read `model-package.json` for the included files and the dependencies supplied by the base game. [Native model packaging](native-model-package.md)

## Preview the cinematic

1. From **File → Import → Digimon New Century → Skill Phases (.glb)** select the **same original library GLB**. In **Skill phase**, choose the named **Cinematic**. The clip is often called `skill2_display`. **Primary attack phases** chooses ordinary cast or approach scenes. Select **Cast**, **Approach**, or **Return** individually to see what happens on the battle actor.
2. Leave **Source Actor and Animation**, **Captured Camera (when present)**, **Captured Audio**, and **Effect Timeline References** enabled. Click **Import**. For a phase that holds the previous pose, disable **Source Actor and Animation** to inspect its cues on their own.
3. Select the newly named scene in Blender's scene dropdown. With the pointer over the 3D Viewport, press **Numpad 0** and **Space**. Without a numpad, use **View → Cameras → Active Camera**. Look at the timeline markers for effect starts and the sequencer for sound. The markers identify the prefab, time and attachment for the later conversion.
4. To reuse this camera in a sequence, open **N → DSTS → New Century Skills → Save Phase Manifest**. Choose a new JSON file in your work folder. The saved manifest contains sampled camera transforms and FOV when available. Keep it beside the sequence recipe or use its full path.

**Two examples to try.** Leviamon X's **Sevenfold Lightning → Cinematic** has a sampled camera, one sound and four effect references. Its playable English Time Stranger skill is named **Seventh Lightning**. Shoutmon X7's **Fusion Blazing Rocker → Cinematic** works with the duplicate-`JT2` resolver introduced in 1.5.2. Its camera follows `u288_b/root/JT1/JT2`. If another rig reports an ambiguous socket, compare the full paths and the selected clip's animated hierarchy. [Skill phase guide](skill-effect-workflows.md)

## Convert the skill effects and audio

1. Open **File → Import → Digimon New Century → Native Skill Recipe (.glb)**. Pick the same library GLB and the phase you are converting. In **N → DSTS → New Century Native Skill**, set the native actor, motion slot, caster effect ID and target effect ID.
2. Choose the caster and target BSON donors and their Figure template sets. Map the source attachment to a real bone in the proper `.nlst`, the actor root, or the native hit origin. Keep a hand flash on the caster and an impact on the target. Check stationary scenery, moving projectiles, and persistent head effects individually because they have different owners and lifetimes.
3. Use **Add Reviewed Component** for existing converted resources. If your capture has a compatible prepared job, use **Load Component Job → Generate and Add Components**. A Figure can require a GEOM, NLST, animation, SPRK, textures and shader passes. A capture with a new mesh renderer, shader, rig or missing motion needs its own conversion work.
4. Map the source audio key to an available native cue. The cue number in BSON and the Ryo bank's local ID are different values. For example, the staged Leviamon cast uses native sound `329512` and local cue `9512`. Preserve the source start frame and stage the converted HCA and its cue configuration in the mod's `Ryo` folder.
5. Choose **Inspect Conversion**, read the coverage report, then **New Candidate Folder → Build Review Candidate**. Save the Blender file to retain the mappings. Check the uncovered source nodes as well as the files the producer wrote. [Skill recipe and coverage](skill-conversion.md) · [Native hit and audio bindings](native-skill-bindings.md)

## Combine the cinematic and cast

1. On the same converted rig, unmute each source NLA track you need and export it through **File → Export → Digimon Time Stranger → Animation (.anim)**. Use the same rig and 30 fps for the showcase, cast and any approach or return.
2. Copy the JSON example in the [sequence recipe guide](native-skill-sequence.md). Set `stem` to your native actor and motion, such as `chr763_bs02`. For the cinematic, set `camera.preview` to the saved phase manifest. For the cast, supply both native `camera.pc` and `camera.em` donor paths. Keep each camera's GEOM, ANIM and NLST together.
3. Set the cut using `count` and `offset`. For Creepymon X, Hellfire Inferno shows the first **150** samples of `skill2_display`, then all **134** samples of `skill2`. The cut falls at five seconds. New Century can run display and battle actors on separate clocks. Choose where the cast, hit and sound should land in the Time Stranger sequence.
4. Add a phase's converted caster BSON with `effect` if you have one. Use `bound_effect_to_phase` for scenery or neutral emitters that should end at the cut. Open **File → Export → Digimon Time Stranger → Native Skill Sequence (.json)**, select the recipe, choose **New Output Folder**, and export. Check `sequence-report.json` for total samples, each cut, both cameras, and any composed hit and sound events. The JSON controls the sequence across Digimon.

## Create the entry, mode change and art

1. Launch **Time Stranger Mod Studio.cmd**. In **Create New → Select Template**, choose the native base form. Enter the new IDs, character key, name, Field Guide number, and English profile. Set the model and animation reference to your exported actor. Reusing the base animation reference here makes the game look up the base form's motions.
2. In **Evolution**, add the base-to-X and X-to-base form paths with **Mode Change** (type 2). Reopen the entry and check both directions. Chrono Devolution is a separate family record. Under **Skills → Signature Skills**, use **New skill…** for each slot, choose a template for its battle behavior, and give it a free ID, a name and a description. Set power, SP, hit count, element and effects to agree with that description. The staged examples use selectors 3, 4 and 5 for `bs01`, `bs02` and `bs03`.
3. Save the entry and **Export / Copy Mod** into a staging folder. Check that the ordinary battle skill row selects the intended actor motion and refers to the new native caster and target effect rows. The effect rows name the BSON files. The game controls targeting, damage, buffs and turn execution. [Mod Studio and its skill editor](https://github.com/ElderLich/Time-Stranger-Mod-Studio)
4. Launch **Time Stranger Art Studio.cmd**. In **Portraits**, use **Render preview**, adjust **Camera turn** and **Tilt**, and drag a square close around the face. Compare the eye line and face size to the base form's official `ui_chara_icon_*.img`. Check **Game portrait**, enter the icon ID from the new `char_info` row, then **Save profile picture** to staging. The X forms here each use an individual face crop.
5. In **Dot Sprites**, open the source sheet, fill the four atlas positions, enter the Digimon ID and click **Export dot sprite**. Preview all four frames. For the five X forms in this pack the source cell order is 0, 1, 11, 10. Other sheets can use different layouts.

## Check the staged mod

Open the staged folder in Mod Studio. Read back the name and profile, both mode-change paths, assigned skills, icon and available Field Guide slot. Inspect `dsts-loader/patch` for the model, LODs, motions, PC/enemy cameras, BSON, converted Figures, images and shaders. Keep English names and profiles under `patch_text01` and the cue bank at the mod root. Check texture and shader dependencies against the extracted game and check new IDs and output paths against installed mods.

When testing the staged copy in game, start with the Field Guide portrait and dot, change from the base form and back, then use each attack on both sides of battle. Check the camera cut, hit frame, target placement, sound, and whether scenery and effects clear when control returns. Compare any differences with the corresponding captured phase and its conversion report.
