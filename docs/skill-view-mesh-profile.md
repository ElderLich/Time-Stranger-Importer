# Bounded live-camera mesh profile

`skill_conversion.view_mesh_profile` supplies the geometry contract consumed
by the finite native view-mesh producer. It requires local simulation, view
alignment, centered unflipped geometry, a positive uniform source prefab
basis and uniform nonnegative sampled particle sizes. It rejects unsupported
material state, atlas/custom-stream animation, source shear and malformed
samples. It does not replace any source emission or particle clock.

The native Model4 palette carries the current owner and the uniform particle
size. Seven material channels carry the camera-relative quaternion and the
separate world offset. The vertex shader applies particle rotation, then
owner scale, then the live camera axes. Owner orientation transforms the
particle center; it does not turn the camera-facing mesh. Uniform particle
size is validated and retained, never averaged or coerced.

The runtime owner must have independently evidenced positive scale and an
orthogonal TRS basis. `validate_runtime_owner(matrix, source_scale)` rejects
negative scales, reflection, shear and inconsistent matrix/scale evidence.
The supplied scale cannot be inferred from the same matrix's column lengths,
because those lengths lose signs. Offline candidates have no accepted runtime
owner and retain that requirement as an explicit blocker. The native shader
does not dynamically detect unsupported owners.

The paired refraction material uses the explicit native transparent donor,
both slots 0/13, the global scene sampler and resolution scale. Captured signed
perspective division, noise/mask equations, source mesh bytes, source UVs and
native pass state are retained. Source RGB and alpha blending must match;
separate blend factors remain unsupported by this profile.

Validation includes generated Unity 2022/6000 source geometry, every finite
source frame, native material/animation/dependency readback, actual native
vertex shader stream output, actual pixel shader raster tests and equal
Blender 5.1/5.3 resource hashes. Captured Unity 2020 parity, mirrored/sheared
owners, arbitrary sockets, draw bounds, source/native scene composition,
event lifecycle and in-game appearance remain unverified. These are review
components; they do not constitute a playable skill package.
