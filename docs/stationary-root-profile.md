# Stationary root particle profile

`stationary-root-yaw-v1` converts captured world-space particles with locally
aligned billboard or mesh surfaces through the existing native current-owner
Model4 pipeline. It is an explicit finite asset profile, not general mixed-owner
support. Existing producer defaults continue to reject world-birth/local-surface
combinations unless this profile is selected.

The admitted particles have no translation, velocity or gravity; their centers
are constant points on the root Y axis. The native owner must keep its
translation and positive uniform scale fixed during each particle's lifetime.
Its upright yaw may change every frame. Under those conditions, birth yaw and
current yaw produce exactly the same center, while the surface uses current yaw.
The source `simulationSpace=1` remains in the captured JSON and output
provenance. The profile does not relabel or modify source files.

`mixed_owner_profile.validate_node` checks source geometry and motion inputs.
`validate_samples` checks every captured center and velocity and returns the
explicit contract stored in the producer manifest. `validate_owner_samples`
can check a supplied root track, rejecting translation, scale changes and tilt;
the exporter does not invent a root track from static assets. `mesh_profile`
accepts an optional `ownership_profile` in its validation, sample preparation
and vertex-source functions. The ordinary local mesh API remains unchanged by
default.

The reusable offline producer is
`Time Stranger/tools/build_native_mixed_owner.py`. It takes existing `--stage`,
`--bake`, a new `--output`, a unique `--prefix`, and explicit `--node` choices.
It reuses the existing billboard and mesh producers, materials, textures and
sampled animations. Choose one geometry family per output. Short destination
paths avoid Windows MAX_PATH when native shader identifiers are long. The
billboard and mesh producers also expose the explicit `--ownership-profile`
option themselves.

The three reviewed Sevenfold systems are spell node2, target node14 and target
node30, comprising five particles and293 source samples. Blender5.3 generated
their isolated assets. Independent native readback checks13,998 vertices over
three fixed-root scenarios with independently varied birth/current yaw. The
maximum vertex error is1.163e-6; mesh source geometry is byte-preserved.

Every component carries the fixed translation/scale assumption. Actual native
caster/target root behavior and visual appearance remain for in-game testing.
A translating root causes a real discrepancy: a one-unit native translation
moves a current-owner particle by one unit while a source world-birth center
stays fixed. The profile neither hides that limitation nor adds gameplay code.
