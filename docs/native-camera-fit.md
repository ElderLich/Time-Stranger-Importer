# Native skill camera assets

Native special-motion bindings such as `bs02` and `bs03` also select camera
resources for player and enemy use. Supplying an actor motion without its
camera GEOM/ANIM/NLST can leave the native camera at the origin. Filename
presence alone is insufficient: the internal camera name, skeleton hash,
indexed FOV hashes and NLST parent references must agree.

Version 1.5.0 promotes the established camera rename into
`skill_conversion.camera_assets.rename_camera_bindings`. It preserves the rig,
unknown optical fields, auxiliary NLST entries and animation indices. Both
zero-float ordinary cameras and indexed-FOV donors are supported by the rename.

`tools/blender_native_camera_fit.py` accepts `--addon-root`, `--job` and a new
`--output` directory. The JSON job uses `native-camera-fit-job-v1` with:

- Pinned `actor_geom`, matching `actor_nlst` and `actor_base_anim`, explicit
  `opaque_mesh_prefixes` and expected mesh count.
- `aspect_ratios` and a framing `margin` between 0.05 and 0.3.
- Camera entries containing `stem`, internal `name`, and pinned `actor_motion`,
  `donor_geom`, `donor_anim` and `donor_nlst` files.

Each entry retains its native ordinary donor's first orientation and static
lens, then fits one constant position to every vertex of the selected opaque
meshes across the complete actor clip. The fit uses the same projection method
as the verified victory-camera workflow. The supported camera is parentless
and uses native actor-origin placement with actor world scale; ordinary shared
camera groups with different offsets/scaling are not interchangeable.

The tool rereads serialized native tracks, checks depth/framing at every frame
and requested aspect, and compares projection extrema with Blender. It writes
native resources, three framing previews and a Blender review file per camera.
Only native resource entries from `camera-fit-report.json` belong in a game
package. Jobs and resource choices require no per-skill Python edits.

This authors a bounded mod camera, not a recovered source cinematic. The fit
covers opaque actor geometry; target effects, arena obstructions and final
presentation still require in-game visual acceptance. Its current fit profile
uses 30 Hz actor clips and zero-float/static-lens ordinary camera donors.
