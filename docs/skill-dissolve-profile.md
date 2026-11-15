# Singular dissolve material profile

`src/skill_conversion/dissolve_profile.py` defines
`honor-singular-dissolve-layered-v1`. It admits the captured Honor dissolve
material with Spread 0, Softness .5 and RimSize 1, CullBack, no depth writes,
and separate source-alpha RGB / source-one alpha blending. Other variants
remain rejected. No epsilon or threshold approximation replaces the captured
shader equations, and no source HDR color is clamped or premultiplied.

Native GEOM's existing material state uses one source/destination factor pair.
The converter emits two native Figure resources for each sampled particle:

| Layer | Native blend factors | Native RGBA write mask |
| --- | --- | --- |
| RGB | SRC_ALPHA / INV_SRC_ALPHA | 1,1,1,0 |
| Alpha | ONE / INV_SRC_ALPHA | 0,0,0,1 |

Both resources share identical geometry, shader programs, textures and ANIM
bytes. They reference the same native SPRK animation with identical birth,
lifetime, transform, source color and sorting settings. Only native Figure
names, identifiers and the two material state entries differ. There are still
five source particles in the current fixture; the ten native draws are not
counted as ten source particles. The game owns normal resource execution.

RGB does not depend on destination alpha, so RGB and alpha layers can interleave
without changing their equations. Layered blending preserves source particle
order within each color channel. Alpha accumulation is itself order independent.
Native in-game visual ordering remains an acceptance check.

The workspace's `Time Stranger/tools/build_native_dissolve_billboards.py` reuses
the finite billboard converter. Run it in Blender with `--stage`, `--bake`,
`--output`, `--prefix` and one or more `--node` arguments. It selects no skill or
node internally, verifies the captured material domain, copies reviewed texture
bytes, records input hashes and atomically publishes into a new local directory.
Existing outputs and source assets are never overwritten. The general shader
translator still rejects singular materials unless this explicit profile is
selected.

Independent native readback checks every material animation value, source color,
layer state, paired geometry/animation, and exact Figure dependency closure. The
existing finite billboard audit checks source corners under three camera poses
and moving nonuniform owners. A separate D3D11 GPU harness compares ordinary
source blending against per-particle layers, all RGB layers before alpha layers,
and all alpha layers before RGB layers, with single and overlapping particles.
It also executes the actual emitted pixel shaders in slots 0, 7 and 13.

On NVIDIA hardware the float32 and RGBA8 results agree exactly for the measured
cases. WARP float32 error is at most 2.38419e-7 for emitted shaders. WARP's
RGBA8 masked-write route differs by up to one code value on captured cases and
two code values on synthetic HDR overlap controls. The harness records the
explicit two-code-value UNORM tolerance; this is not a claim of universal
bit-identical normalized blending. Captured Android precision and in-game
visual acceptance remain unverified. These are asset-conversion limits, not
requirements for a new battle runtime.
