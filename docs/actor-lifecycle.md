# Captured actor lifecycle conversion

`src/skill_conversion/actor_lifecycle.py` supplies bounded reusable primitives
for captured New Century ordinary attacks. Importing the module does not require
Blender. It contains no model IDs, private asset locations or automatic motion
slot assignments.

- `automatic_stand_schedule(clip_length, delta_times)` reproduces the source
  seconds-based timer and delayed queue insertion. Role updates run before the
  battle's skill dispatch, so newly scheduled timers merge on the next role tick
  and begin decrementing on the following tick. Deltas are rounded to Unity
  float32 before Lua-style double subtraction. A missing sample horizon fails.
- `sample_linear_role_movement(config, start, target, ...)` samples the captured
  ordinary single-target path. Approach uses the selected target's front
  distance plus the source offset; return uses the caster parent's position.
  Facing remains separate from the skeleton. Empty phase animations do not
  replace ongoing playback. AOE, multihit placement, authored movement curves
  and eased return rotation are explicitly rejected by this bounded profile.
- `load_controller_samples(path, expected_sha256, sequence=...)` loads a pinned
  real-Animator sample report, validating frame continuity, stable transform
  identity, finite values and normalized quaternions.
- `animation_from_controller_samples(geometry, samples, bone_paths,
  coordinate_profile='unity-reflect-x-v1')` produces an `AnimFileHundredLine`
  using the maintained serializer. Every native bone requires an explicit
  source path; parent mappings must agree and ancestors above native roots must
  be identity. Native rest transforms are retained. Position uses parent-space
  delta, rotation uses rest inverse times sampled rotation, and scale uses
  sampled/rest components. No role placement is baked into this output.

The source return calls `Animator.CrossFade(stand, 0.2)`. The real captured battle
prefab/controller was sampled in Unity 2022.3.15f1 and 6000.4.4f1. For the reference
return of 0.9666666984558105 seconds, transition progress uses a duration of about
0.19333334 seconds. The target state's first sampled time is zero. It cannot be
replaced with a guessed six-frame blend or preview-phase concatenation.

The two engines agree across 1,481,040 position/quaternion/scale components
(maximum difference 1.1920928955078125e-7). Direct clips are identical. Both
Blender 5.1.2 and 5.3.0 converted a 61-frame, 103-bone return/stand fragment to
byte-identical ANIMs. Independent world-matrix reconstruction bounds maximum
position error to 0.000436 and matrix component error to 0.000392. Private
samples remain outside the source/package; the captured game uses Unity
2020.3.37f1, so its version parity is not directly tested.

Run `python tools/test_actor_lifecycle.py` for placement/timer/error checks and
available private real-controller comparisons. `tools/blender_actor_lifecycle_smoke.py`
accepts an explicit installed add-on directory, evidence report and new output
directory to repeat conversion and world-matrix checks against actual installed
modules.

This is actor conversion support, not an installable complete skill. Dynamic
native root placement/facing, source-version acceptance, full skill routing,
effects/audio and in-game behavior remain separate requirements. No complete
Triple native `bs03` is emitted while those gates are open.
