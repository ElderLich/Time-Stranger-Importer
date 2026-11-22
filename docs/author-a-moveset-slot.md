# Author a missing moveset slot

Open the converted New Century rig or import the mod’s exported geometry. Work
on the skeleton that will ship with the mod. A donor can show the timing of a
motion, but a native animation addresses bones by index. A donor with a different
bone order needs retargeting and baking onto the destination rig.

## Make an editable action

1. Select the armature and open **Dope Sheet → Action Editor**. Make a new
   action, or make a single-user copy of an existing action before editing its
   keys. Take the first pose from the target's own battle idle for a smooth entry.
2. In **Pose Mode**, key the bones that move using **Location, Rotation & Scale**.
   Keep the armature object and root in place for stationary battle clips. Key
   the last held pose as well so the strip includes the hold. Bake constraints
   or drivers to visual bone keys and disable them on the export rig.
3. Push the action down to an NLA track containing one action strip. Name the
   **track** after the output slot, such as `chr761_bg01`. Select the strip and
   check its action, frame range and playback scale. Mute the other motion tracks
   while previewing. On a native rig with a **Native rest scale** track, keep
   that correction enabled.
4. Export **File → Export → Digimon Time Stranger → NC Moveset (.anim)** to an
   empty folder. Set **Target Chr** to the character that owns the new motions.
   A track named `chr761_bg01` takes priority for `chr761`, followed by `bg01`,
   then source aliases such as `guard` or `idle`. This also lets an authored
   `bs02` override the automatic three-phase New Century skill sequence.
5. Reimport the exported clips onto a fresh copy of the matching geometry and
   play them in sequence. Copy only the reviewed motion files into the mod’s
   `dsts-loader/patch` folder. This lets you replace three motions without
   disturbing existing cinematic skills or cameras.

## Guard, knockdown and recovery

At 30 fps, the following frames make a short first pass. Adjust timing to suit
the model and compare it with an official Digimon of similar size and posture.

| Slot | Poses to key |
| --- | --- |
| `bg01` guard | Start in battle idle. Over 5–7 frames, lower the center of mass, bend the supporting legs and bring the arms, shield, wings or head into a readable brace. Hold through frame 16 and ease back to idle by frame 23. Check the silhouette from the battle camera. |
| `bd02` knockdown or defeat | Start from idle, react to impact, shift weight onto the supporting side, then settle into a grounded pose. Hold the final pose for several frames. Check the feet, torso and tail against the floor. |
| `bd03` recovery | Copy the exact final pose of `bd02` to frame 1. Plant the supporting limb, lift the torso and settle into the same idle pose used at the beginning of `bd02`. Play the clips back-to-back to check the seam. |

## Other slots

| Slots | Motion |
| --- | --- |
| Base `chrNNN.anim` | NC Moveset writes a rest clip that uses the geometry's bind pose. |
| `ba01`, `ba02`, `bs01`, `bs02` | Physical attack, skill attack and the two special attacks. Key anticipation, contact or cast, then recovery. |
| `bd01`, `bn01`, `br01`, `bv01` | Hit reaction, battle idle, run and victory. Match both ends of a looping idle or run. |
| `fn01_01`, `fw01_01`, `fr01_01b` | Field idle, walk and run. A walk needs different contact timing from a run. |
| `fe01`–`fe04` | Eating and field events, including a yawn in `fe03`. Key the head and jaw where the target rig supports them. |
| `fq01`, `fq02`, `bn02` | Positive and negative conversation gestures, and optional sleeping idle. Keep each gesture visually distinct. |
| `rNNN_fn01`, `rNNN_fw01` | Ride idle and walk. For model `chr761`, use `chr761_r761_fn01` and `chr761_r761_fw01` and check rider contact. |

Review the exporter's per-slot source names. A successful `bd02<-hurt` or
`bg01<-idle` entry identifies a working fallback. An authored motion appears
with the name of its own NLA track.
