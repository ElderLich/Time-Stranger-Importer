# Port a ReArise Digimon and check its battle skills

## Choose the action in the source library

In Blender, open **File → Import → Digimon ReArise Model and Motions (.glb)**. Select **Battle Plan Only** and open the original `Digimon_Library/<actor>/model/model.glb`. In Blender’s Text Editor, select the new **ReArise battle plan.json** text block. The same report is available from a checkout with Python 3 and the following PowerShell commands.

```powershell
$library = 'D:/Digimon Modding/Digimon Library/Re-Arise'
$model = "$library/Digimon_Library/chr339/model/model.glb"
python tools/rearise_battle_plan.py $model
```

Choose each action by its timeline key and master skill ID. Friendship and raid variants can share a motion with different effects or cameras. Play the exact selection in Library Viewer and note the caster and target positions at charge, release, impact and recovery. The plan lists each action’s GLB clip, export track, camera and effect manifest paths, spawn frames, anchors and available sound files. A missing WAV can still have a named cue in the original sound bank.

For example, BlackWarGreymon X’s Hades Force selects `chr339_bs01_rd`. Its Multi Refresh timeline selects the shared clip `chr110_ba02_rd`, which imports on track `chr339_ba02`. Assign that track to native BS02 if Multi Refresh occupies the second Special slot. Other models have a distinct BS02. For example, `chr135_bs02_rd` is a captured skill on chr135, which also has separate BA02-based subskills. Use the selected action to decide.

## Export the actor and build the battle presentation

1. Clear **Battle Plan Only**, select a comparable donor GEOM and import the same model. Check size, bind pose, feet, attachment bones, body material slots, transparent parts and moving accessories. The report lists rigid draws and materials that need an explicit native mapping. The X pack’s model scale, caster placement correction and billboard rotation work were measured for those two forms.
2. Retarget the donor texture names and use **Export All** for geometry, the name list, rest pose and motions. Assign any shared attack clip to the selected native BS slot. Use **Package Exported Native Model** to check skinning, referenced shaders, DDS formats and mipmaps. [Model and animation](models-and-animation.md#rearise-library-models) · [Native model packaging](native-model-package.md)
3. Build PC and enemy cameras for the chosen motion. A ReArise camera manifest contains its sampled action camera when the skill has one. For skills without a captured camera, choose and inspect a native donor. Check the first and final frames, shot cuts, widescreen framing and camera handoff. Victory uses its own cameras.
4. Convert or author each required caster and receiver effect as native Figures and BSONs. Map the source trigger frame and anchor for each event. A target-group burst can need both a group effect and a separate per-enemy contact effect for damage. In a native group-damage Special such as Grenade Launcher, inspect the battle skill’s caster, group and contact columns together. X-Antibody Pack 1.0.6 routes Stun Break Prominence through caster 21102, group burst 21104 and per-enemy fire contact 21105 at CP01. The original sound plays once with the cast. Confirm healing, damage, statuses and multi-target behavior in battle. The captured ReArise JSON is input for this work. Existing native effect composition supports specific resource profiles. [Native effect and actor bindings](native-skill-bindings.md)
5. For each new sound, provide the custom Ryo cue and HCA, select its ID in the native BSON, and check the trigger frame and stop behavior. Compare a recording against the source waveform at normal and accelerated battle speed. A hit can cancel a caster-owned cue, and a playing cue can continue into the next shot.
6. Create the actor, free IDs, evolution links and native skills in Mod Studio. Export to a staging mod. Assign the new model and motion references, Field Guide slot, portraits and four-frame dot icon. Check the Special selector and category as well as the native effect IDs. For a Viewer-style black main shot, compare against a stock skill using the native map-hide control and verify the arena returns at the hit. Use the background selected for each skill.

## Validate a stage before the game test

For the native checks, use Python with NumPy installed, such as Blender’s bundled Python. Pass a captured timeline key and the native battle skill ID once per Special. Each additional `--resources` directory supplies loose native files after the staged patch. `--actor` selects the native `chr` code when it differs from the source. The CLI prints JSON to standard output. A nonzero exit indicates an invalid input or a failed binding.

```powershell
$pack = 'D:/Digimon Modding/Programs/Reloaded II/Mods/X-Antibody Pack'
$stock = 'D:/Digimon Modding/Time Stranger Extracted/app_0.dx11'
python tools/rearise_battle_plan.py $model --pack $pack --resources $stock --bind chr339_bs01_rd 23391 --bind subskill_heal1_b_skill_14152052 23392
```

The check reads staged first-sheet `.ap.csv` files for Digimon status, battle skills and effects. It follows the native motion selector, both camera triplets, caster/group/receiver BSONs and their Figure triplets. It reports native hit frames, sockets, scene controls and Ryo cue offsets. It compares a renamed slot with its exported source track when both files are present. `staged.errors` lists broken bindings. `staged.review` records missing source evidence or intentional differences to inspect. This profile covers custom ordinary Specials with staged effect rows and Ryo cues. Stock-only effect rows, alternate table layouts and native sound-bank references need separate verification against the extracted game.

Start the game with the staged mod and test on both sides of battle. Use damaged allies for healing and multiple enemies for group damage. Check the HP change, displayed result and statuses on each recipient. Compare caster registration, target effects, sound, stage restoration and the return to idle with the selected Viewer timeline. Test the Field Guide, mode change and victory camera separately.

If damage registers while the effect is invisible, test the group burst and the per-target contact independently. Compare the first visible frame, the impact and the fade from the same angle as the Viewer. For animated effects, check that the model and mesh bounds enclose the particles throughout their motion, including billboard rotation. Use a shader and material layout already seen working in the game. A standalone rendering test can supply texture slots, palette data or material constants that the game leaves unset.
