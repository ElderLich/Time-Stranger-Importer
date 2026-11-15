# Native skill sequences

Use **File → Export → Digimon Time Stranger → Native Skill Sequence (.json)**.
Select a recipe and a new output folder. The exporter writes the named motion,
both requested cameras, any composed caster BSON and a hash and timing report.
The actor moves, each camera cut and the effect starts share one authored timeline.

Export the source tracks first. Import the NC model with its native material
donor, unmute the required NLA track and use **Animation (.anim)**. Use one
export rig and frame rate for every actor clip in the recipe. Supply a captured
cinematic camera preview JSON for a showcase and the donor's native PC/EM
camera GEOM files for a cast. Keep the donor ANIM and NLST beside each GEOM.
The camera template must have one root camera and a single bound FOV track.

```json
{
  "format": "dsts-native-skill-sequence-v1",
  "stem": "chr763_bs02",
  "camera_template": "native/cam_chr395_bs01_pc.geom",
  "phases": [
    {
      "clip": "skill2_display",
      "motion": "tracks/skill2_display.anim",
      "count": 150,
      "camera": {"preview": "showcase.json"}
    },
    {
      "clip": "skill2",
      "motion": "tracks/skill2.anim",
      "offset": 150,
      "camera": {
        "pc": "native/cam_chr038_bs01_pc.geom",
        "em": "native/cam_chr038_bs01_em.geom"
      }
    }
  ]
}
```

Paths may be absolute or relative to the recipe. Frame indices start at zero.
`start` selects the first source sample, `count` selects the number of samples,
and `hold` repeats the final sample for the given number of frames. The defaults
select the full motion. Optional `offset` checks the expected first output frame.
Optional `sha256` pins the input motion. Omit camera entries from every phase
for an actor-only export. Camera phases currently use 30 Hz.

To sequence existing native caster effects, add an `effect` BSON path to the
applicable phases. Add `bound_effect_to_phase: true` to limit neutral Figure
emitters to the phase's visible samples. The composer preserves Figure/socket
bindings, offsets the associated native hit/cue frames and rejects duplicate
owner IDs or unsupported controls. Place the referenced Figure resources and
sound cues in the mod. Use native target effects and normal battle skill rows
for damage and target placement. Particle conversion takes place before this
step. A sequential adaptation shows the cast at the authored cut. New Century's
independent display actor, skip behavior and battle pause run on separate clocks.
