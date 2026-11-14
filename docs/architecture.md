# Implementation and validation

## What was rewritten in 1.3

The proven binary serializers and New Century bind-pose conversion are retained.
The Blender-facing animation and mesh export paths are rebuilt around explicit
data ownership and testable conversion functions.

| Module | Responsibility |
| --- | --- |
| `src/blender_compat.py` | Detect action APIs; select one owner's action slot; create NLA tracks. |
| `src/animation.py` | Shared animation import, sampled vector/quaternion export, duration, masks and opaque float channels. |
| `src/mesh.py` | Convert per-corner Blender attributes into seam-split GEOM vertices; filter/normalize skin weights. |
| `src/scene_state.py` | Track newly created datablocks; roll back imports without deleting unrelated user data. |
| `src/native_camera.py` | Preserve camera GEOM/NLST bytes and expose its skeletal motion for `.anim` editing. |
| `src/cinematic.py` | Import a source-backed NC camera/audio preview into a separate scene. |
| `src/nc_glb.py` | Preserve NC bind poses, root motion and DSTS bone-location semantics; borrow donor material metadata. |
| `src/backend.py` | Load the geometry extension using CPython's supported ABI suffixes. |

### Repaired failures

- A 30 fps scene used 24 fps NLA conversion, changing playback speed.
- Bezier quaternion component curves overshot native spherical interpolation.
- Animation export read isolated key values, resetting other components at
  asynchronous keys and truncating fractional key times. It now samples complete
  vectors, normalizes quaternions, and handles Euler/axis-angle bone curves.
- Float channels, blend masks, unknown animation header values and static-clip
  duration were discarded. They now survive the Blender round trip. Float
  channels remain indexed custom properties; their game-specific bindings are
  not guessed.
- Layered actions could mix channels from different owners. NLA export uses its
  exact action slot and trim range. Reversed, repeated, time-remapped and
  multi-strip tracks require an explicit bake instead of silently exporting
  just their first action.
- Mesh export collapsed UV, split-normal, color and tangent seams onto the
  first corner of each Blender vertex. Export now splits by the full corner
  tuple and retains the original skin influences.
- Non-bone vertex groups could displace actual skin weights. Filtering now
  precedes the four-influence limit. Empty/unweighted meshes fail clearly.
- New Century cleanup removed *all* unused meshes/materials/images in the file.
  Only datablocks created by the current import are eligible for cleanup.
- Texture export reused stale source DDS bytes after pixel editing, wrapped
  out-of-range channels and omitted mipmaps. Edited/generated textures now
  export clipped BGRA DDS with mipmaps and the game's `.img` extension.
- Mixed-rate cinematic sequences were concatenated without resampling. They
  now sample each source strip at a shared output rate and share join endpoints
  without adding a frame. The next phase owns the key at a cut.
- Failed donor animation replacement deleted the previous clip before validation.
  Replacement now stages successfully before removing the old track. Temporary
  export actions restore the user's prior active action and slot.

## Blender 5.x compatibility

The Python API layer uses capability detection rather than a hardcoded 5.1
branch. The bundled geometry backend is Windows x64 CPython 3.13; it was loaded
and exercised in Blender **5.1.2** (Python 3.13.9) and **5.3.0** (Python 3.13.13).
Other 5.x releases need the same supported APIs and a backend matching their
embedded Python ABI. This is a compatibility target, not a claim that every
uninstalled or future release has been tested.

`dsts_formats` is a pybind11 C++ extension. To build another ABI/platform, use
the DSTS Formats Parser source with that Blender release's Python headers and
libraries, CMake and a C++ compiler, then place the resulting
`dsts_formats<extension-suffix>` file in `src/`. Renaming a CPython extension
does not change its ABI. Animation reading/writing uses the vendored Python
serializers; native geometry still requires the extension.

## Cameras, particles and audio

Native `cam_chr039_bs01_pc.geom` (Lucemon Chaos Mode) has a camera section that
the legacy C++ writer cannot emit. Import retains its complete original GEOM
and NLST in collection properties, imports the skeleton and neighboring ANIM,
and allows animation editing. Geometry export is byte-preserving and refuses a
changed rest rig. The seven camera parameter floats are available for research;
their optical meaning has not been established, so no editable native lens
writer is claimed. Mixed mesh/light/camera GEOM files are rejected.

`dsts-cinematic-preview-v1` is a preview interchange, not a game effect format.
It supplies a source actor GLB/clip, frame rate/origin, sampled Unity camera
position/quaternion/vertical FOV, audio cues, and effect-prefab references.
The importer reflects Unity X, converts glTF Y-up to Blender Z-up, and converts
camera +Z forward to Blender -Z forward. It creates a separate scene, camera
motion and lens actions, VSE sound strips and nonrendered source-effect records.
It does **not** translate Unity particles/shaders into Time Stranger's effect
format or inject a sound bank into the game.

## Run the checks

```powershell
blender --background --factory-startup --python-exit-code 1 --python tools/blender_smoke.py
blender --background --factory-startup --python-exit-code 1 --python tools/blender_smoke.py -- --nc-export
python tools/package_addon.py
```

The smoke script verifies registration lifecycle, layered slots, escaped bone
names, asynchronous component keys, quaternion normalization, native animation
serialization, masks/float channels and FPS conversion. When local fixtures
exist, it also imports/exports chr050, tests UV seams/non-bone weights, verifies
Lucemon's camera bytes, and loads the real Leviamon X cinematic manifest.
`--nc-export` additionally runs the full Leviamon X donor-model conversion and
geometry/animation export. Mixed-rate multi-part sequence timing is covered by
the synthetic fixture even when the chosen NC actor has no three-part skill.
Game assets remain local.

The smoke tests establish serializer and Blender behavior. In-game rendering,
particle parity and optical-camera equivalence need separate game validation.
