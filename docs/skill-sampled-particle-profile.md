# Captured particle sample profile

`src.skill_conversion.sampled_particle_profile.convert_samples(node, capture,
node_index)` converts actual source-engine particle samples into the existing
native particle-bake record format. The profile is
`sampled-world-view-billboard-v1`. It does not interpret, clamp or rewrite an
emission curve. Particle timing, capacity, random selection, size, rotation,
color and atlas cells come from the original prefab simulated in an explicitly
identified Unity Editor.

The bounded input is a stationary world-simulation view billboard, positive
uniform owner scale, zero pivot, no custom streams, no trails and no renderer
animation. Complete consecutive 30 Hz frames include empty frames before birth
and after death. Unique source particle seeds identify each finite particle;
duplicates and reuse after death fail. Every source getter sample is checked
against independently captured `ParticleSystemRenderer.BakeMesh` world vertices,
UVs and colors. Invalid clocks, identities, lifetimes, atlas cells, nonfinite
values and unsupported geometry fail before emitting native sample records.

Source Unity X is reflected into the established native/GLTF coordinates. The
existing live-camera native billboard carrier consumes the converted angle,
dimensions, color and atlas streams. The source getter's rotation is clockwise;
the native material angle has the opposite sign. Source owner scale applies
once to the sampled dimensions. Finite native activation uses the first visible
source frame, preserving the established 30 Hz asset-sampling convention.

The workspace tools `prepare_particle_asset_probe.py` and
`CapturedParticleAssetProbe.cs` accept a configuration-selected bundle, prefab,
node path, frame rate and finite duration. They load original bundle bytes and
never modify authored particle modules. Dependencies are hash-pinned. Explicitly
allowed missing shader bundles are recorded because this probe samples particle
geometry rather than rendering source shading.

`prepare_sampled_particle_bake.py` accepts an `nc-sampled-particle-job-v1` JSON
document. Its `source`, `manifest`, `capture` and optional `comparison` fields
are `{path, sha256}` pins; `node_index` selects the captured source node. The
manifest is the existing mounted finite-bake manifest. An optional second
capture must produce identical source sample arrays and converted particles.
The tool rejects stale inputs and existing output files. No per-skill Python
changes are needed.

The source engine version remains in the evidence. Matching two Editor
versions is useful comparison evidence; it does not claim parity with a
different captured game engine. Material conversion, native resource closure,
native camera composition and in-game appearance retain their own validation.
The finite sample output is an upstream input to the review-component pipeline,
not a gameplay implementation or an installable package.
