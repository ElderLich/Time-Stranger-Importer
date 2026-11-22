# Official attack references for the New Century ports

Start with Leviamon’s **Biting Crush** and Lucemon CM’s **Paradise Lost**. The first shows how a short physical attack can move the camera around a bite. The second shows what a very narrow opening lens does before a long reveal. Both have complete player and enemy shots in the extracted game.

The comparisons below use the installed **Demon Lords** and **Four Sovereigns** packs on November 22, 2026. Frames start at zero and run at 30 Hz. A camera length is a sample count. The field of view is vertical. Both battle sides were sampled from their native camera GEOM and ANIM. These are file measurements, with final framing still dependent on the encounter.

## Cast and impact

| Port | Official reference | What to compare in playback |
| --- | --- | --- |
| Leviamon X · **Biting Crush Triadens** | Leviamon · **Biting Crush**, skill 20371, `chr037_bs01` | In the stock 81-sample shot the lens goes from 53.9° to 24.4° by frame 28 and back to 53.9° at 40. The X form’s 72-sample `chr761_bs03` camera holds one position, rotation and 43.3° lens on both sides through hits 35, 50 and 60. Compare the first lunge and jaw contact side by side before choosing how much camera motion to add. |
| Lucemon X · **Sin-Cleansing Flames** | **Holy Light III**, skill 30263, and Lucemon CM · **Dead or Alive**, 20391, `chr039_bs01` | Holy Light III uses the actor’s `ba02`, with the target effect’s hit at local frame 33 of a 60-frame effect. Dead or Alive supplies a moving 43.3° signature camera. Sin-Cleansing Flames already has a moving 43.3° camera, an authored hit at 45 and 111 camera samples. Check the flame and receiver at 45 before adjusting its lens. |
| Creepymon X · **Sevenfold Graviton** and **Hellfire Inferno** | Creepymon · **Flame Inferno**, 20381, `chr038_bs01` | Stock Flame Inferno starts wide at 64.8°, reaches 24.2° at midpoint and finishes wide over 176 samples. Graviton reaches its hit at 60 of 114 samples. Hellfire returns to the arena at 150, then hits at 180 of 284 samples. Compare the one-second cast after the return against the stock release. |
| Barbamon X · **Dark Palace Destruction** | Barbamon · **Pandemonium Lost**, 20361, `chr036_bs01` | The stock and X shots both hold a 40° lens and turn by more than 15° between adjacent samples at frames 29 and 49. The X-form hit is at 20. Scrub those turns against the receiver after impact. |
| Beelzemon X · **Double Attack** | Beelzemon · **Double Impact**, 20351, `chr035_bs01` | Both shots span 26.0°–74.3° and make a large turn at frame 41. Double Attack puts its hit at 40 and ends at sample 61. The stock camera continues through sample 110. The closest comparison is the transition from recoil into recovery. |
| Lilithmon X · **Nazar Claw** | Beelzemon · **Darkness Claw**, 20352, `chr035_bs02` | Darkness Claw uses 83 samples with a 39.9° lens and rapid turns around frames 22–24 and 70–71. Nazar Claw uses 80, opens at 27.4°, widens to 56.6° and hits at 53. Check which side of the target the claws occupy at contact. |
| Azulongmon · **Thunder Chains** | **Thunder Fall III**, 30153, `ef_b_gth_a01.bson` | The official group effect lasts 60 frames and carries three local hit markers at 14, 28 and 41. Thunder Chains has one authored hit marker at 40, a 96-sample camera and a 44° lens. Scrub the first visible ground bolt against the attack pose and that marker. |

## Cinematic returns

**Lucemon X · Seventh Holy Cross.** Stock Paradise Lost (20392, `chr039_bs02`) has a 333-sample actor motion and 351-sample cameras. Its player lens opens at 5.7° and finishes at 57.3°. The X form holds 32° through showcased frame 204, cuts to 5.7° at the return on 205 and reaches 36.8° at its hit on 285. The same narrow lens that opens the stock shot is now the audience’s first view after a long showcase. Frames 204–205 are the most informative pair to review for a jarring transition.

**Azulongmon · Dragon God’s Wrath.** The captured 35° showcase runs through 179. Both native sides switch to the 44° cast at 180 and the effect hit marker is 220. Compare frames 180–220 directly with Thunder Chains 0–40. This isolates the common cast interval while showing how the larger attack reintroduces the arena. The combat row specifies fourteen hits. The caster BSON has one timed marker.

## Finding the assets

Use the English `skill_name.mbe` table to resolve the skill ID. In `battle_skill.mbe/000_battle_skill_list`, zero-based column 10 selects the actor motion. Columns 12, 14 and 16 resolve separate effect rows in `battle_effect.mbe/000_battle_effect_list`. Inspect the named BSON’s `hf0…hf7` and `mf` to read its local hit offsets and effect lifetime. For a caster BSON with zero hit offsets, trace the target effect and the actor’s release pose to locate the visible impact.

For a specific signature, open the actor’s `chrNNN_bsNN.anim` and both `cam_chrNNN_bsNN_pc` and `_em` camera triplets from the extracted base or patch. For the port, open the equivalent files under the installed mod’s `dsts-loader/patch`. Compare the attack pose, receiver and camera on the same sampled frame. [Native camera fitting](native-camera-fit.md) · [Native skill sequences](native-skill-sequence.md)
