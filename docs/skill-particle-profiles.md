# Bounded skill particle profiles

The native skill compiler composes pinned components into review candidates.
These Blender-independent helpers provide validated operations used by the
native resource producers. They do not allocate sockets, reconstruct missing
source schedules or make incomplete skill packages installable.

`skill_conversion.mesh_profile.prepare_local_mesh_samples(node, particle)`
accepts local simulation with local mesh alignment. It preserves sampled mesh
basis/scale and custom streams while separating owner-relative position from
world-axis motion. Its paired `vertex_source(node)` applies world motion after
the owner matrix and selects the captured mesh atlas frame. The resource
producer retains already-baked mesh pivots, original vertex/index bytes and
pixel shaders. World-birth meshes with current orientation remain unsupported.

`skill_conversion.billboard_profile.local_billboard_quaternion(node, sample)`
returns native XYZW rotation for the reflected local quad. Full3D support is
bounded to constant source X/Y start rotation and the captured sampled Z angle.
The helper removes the old sample's Z spin before applying the complete
rotation. Randomized/animated X/Y, lifetime3D rotation and mixed owner clocks
fail explicitly. Independent Unity2022.3.15f1 and6000.4.4f1 fixtures establish
this rotation operation; the source2020 engine and native game remain untested.
This is a rotation-only helper: the producer must admit the renderer, ownership,
pivot and lifetime profile first. The 2D branch alone is not a profile validator.

`native_scene_uv(projected, offset, resolution)` and
`REFRACTION_SCREEN_HLSL` describe the reviewed native scene sampler route:
perspective division, source GL offset, DX Y conversion, then resolution scale.
The captured `Honor_KJniuqu` fragment uses signed `xy / w`, with no absolute
value or epsilon. The HLSL preserves this operation. The CPU reference rejects
nonpositive w and nonfinite results; finite small positive w is not clamped.
Coordinates remain unclamped because the native sampler owns edge behavior.
The scene producer requires an explicit transparent donor with slots0/13 and
the global `BackBufferSampler`/`CorrectViewResolutionParams` contract. It keeps
source noise/mask equations and never packages a static scene image.

Validation includes actual generated shader pairs on D3D11 WARP, both installed
Blender executable versions generating equal resource hash maps, source geometry
and material readback, every sampled source transform, and malformed/unsupported
input rejection. Runtime draw bounds, backbuffer contents/draw ordering,
arbitrary owner/target sockets, lifecycle, sampling parity and game acceptance
remain separate requirements. These helpers are reusable conversion building
blocks; the broader source-to-playable-skill workflow is still incomplete.
