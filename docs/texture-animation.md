# Native texture animation editing — 1.4.5

This workflow connects reviewed native UV uniforms and material animation
channels to a visible Blender preview. Static values export through the existing
GEOM material writer; keys export through the existing ANIM writer. It does not
replace either serializer, and it does not claim full native shader parity.

## User workflow

1. Import the native GEOM with its extracted `shaders` directory next to it.
   Reviewed shader pairs configure automatically. Select a mesh and its desired
   material, then open **DSTS → Native Texture Animation** in the 3D View sidebar.
   **Configure verified UV preview** also accepts an explicit shaders directory.
   A family without a reviewed contract is explicitly rejected by this button;
   ordinary model import retains that material and records the reason in
   `DSTS_texture_preview_notes` on the collection.
2. Edit the labeled **default** values for static GEOM UV settings. A selected
   UV clip can override a bound component in preview. **Use static native UV
   defaults** clears that clip selection and stops the preview clock so the
   material shows its defaults again. It retains all authored keys.
3. Use **Animate** to append an absent UV component to the native skeleton's
   float-binding table and every imported clip. The initial value is the exact
   GEOM default, including zero and negative values. Every owned clip receives a
   constant channel and an updated count. Shared actions and mismatched native
   channel counts are rejected before editing. Export the GEOM **and every
   affected clip** after adding a binding; old clips have the old channel count.
4. Use **Key** to select an imported native clip, zero-based native frame, and
   value. Existing keys remain editable in Blender's Graph Editor under the
   existing `DSTS_float_N` curves. Rotation values are radians. Keys beyond the
   clip end require the explicit **Extend native clip duration** checkbox;
   enabling it extends that action and its actor NLA strips. Ordinary in-range
   edits do not extend the actor timeline.
5. Use **Preview native UV clip only**, then scrub the scene timeline. This
   samples the original Action curves using that clip's NLA start and scale.
   It leaves actor pose, active action, NLA blending, influence and mute states
   unchanged. Select/play actor motion through the existing animation controls.
   Repeated, reversed or time-remapped strips require baking before this preview.
   Nondefault native float masks are explicitly rejected for UV editing and
   preview until their layered blending behavior is implemented; they remain
   intact in ordinary native animation preservation/export. The editor does
   not silently convert a zero or partial mask to full weight.
6. Export with the existing native GEOM and ANIM operators. Edited preview
   arithmetic or material channel targets that cannot be represented by the
   verified contract cause an explicit export error. Reconfigure the verified
   preview to restore its supported graph. Export keeps the native GEOM defaults
   separate from the sampled animation values.
7. Save the `.blend` normally; use Blender's **Pack Resources** when distributing
   its textures. The clip Action pointer, native metadata, graph and clock
   controls survive reopening with the add-on enabled. Frame handlers are
   registered once and removed when the add-on is disabled. Preview updates are
   restricted to materials whose armature belongs to the updated scene.
   Moving a scrolling actor to another scene rebinds its clock to that scene's
   FPS. An actor linked into multiple scenes has an ambiguous clock and is
   explicitly rejected; make those actors independent before clock preview.

**Preview native time**, **Start at scene frame**, and **Animator rate** describe
the unwrapped animator clock in the preview. They are preview controls, not new
GEOM or ANIM fields. Selecting a UV clip sets the start from its strip and the
animator rate to normal speed (1). NLA retiming changes the exported ANIM FPS
and key sampling; it does not implicitly change the engine's separate animator
rate. Time advances in seconds using scene FPS and FPS base,
continues beyond the skeletal clip, and wraps using `fmod(seconds, 86400)`.
The native engine controls Time58; the editor rejects adding an animation
binding for Time58 itself.

## Reviewed native contracts

Contracts are selected by the SHA-256 of the actual vertex **and** pixel shader
files, not by a character ID, filename prefix or presumed meaning of a label.
The full fingerprints and ordered sampler operations are in
`src/texture_animation.py::CONTRACTS`. A replacement shader with a familiar
basename does not inherit an unsupported preview.

| Verified formula in Blender's native UV convention | Samplers / UV sets | Actual source fixture |
| --- | --- | --- |
| `uv + OffsetSet1` | Color24 / UV1 | `chr000` |
| `uv + OffsetSet1`; emissive unchanged | Color24 / UV1; Emissive303 / UV2 | `chr074` |
| `(uv + OffsetSetN) * ScaleSetN` | Color24 / UV1; Color2 42 / UV2 | `ef_e_m430_010_01`, material 23 |
| rotation in radians about `(0,0)`, then offset | Color24 / UV1 | `ef_e_com_110`, material 0 |
| `uv + frac(Time58 * ScrollSpeedSet1)`; emissive unchanged | Color24 / UV1; Emissive303 / UV3 | `atchiddentreasure00`, material 5 |

The native DXBC vertex shaders apply their Direct3D V flip after these
operations. The existing mesh/image importer preserves the corresponding
Blender UV/image convention, so the preview does not add a second V flip.
Negative scrolling uses `frac(x) = x - floor(x)`, not signed remainder.
Scaling follows offset for the reviewed scale family. Rotation is neither
degrees nor a half-UV pivot.

Numeric IDs come from the native parameter table at `0x141AC4F00` in the pinned
Time Stranger executable, as recorded in
`../../Time Stranger Formats/docs/shader-parameters.md` and its JSON evidence.
The applicable vector IDs are OffsetSet1 68 / Set2 71, ScaleSet1 103 / Set2 106,
RotationSet1 94 and ScrollSpeedSet1 59. The table's explicit IDs take precedence
over legacy display labels.

Native binding evidence is
`../../Time Stranger/src/native/figure-geom/00000001402e1640.cpp`: material name
hash selects the target; `array_index >> 4` selects the uniform; its low four
bits select the component; material binding flag 8 is preserved. Existing
non-UV records are retained in the shared skeleton/action path.

Native clock evidence is
`../../Time Stranger/local/camera-forward-native/projection-clock/00000001402ea410.asm`
and `constants.json`, plus
`../../Time Stranger/src/native/camera-animation-link/00000001402dd010.cpp` and
`../../Time Stranger/docs/native-persistent-companion-clock.md`. Function
`0x1402EA410` copies the first active animator's unwrapped float at `+108` into
the figure's double at `+376`, wraps it at 86400 seconds, and supplies Time58
bindings. The animator updates this clock separately from its clamped/looped
clip time at `+116`. Recorded live advancement establishes seconds, rather
than inferring the unit solely from the wrap constant.

Local DXBC disassembly and fixture inventories are retained under
`local/texture-animation/`; `treasure-vp.txt` and `treasure-fp.txt` show the
actual scrolling color / fixed third-UV emissive pair. Shader binaries and
game assets are not committed. Upstream importer and animation credits remain
unchanged.

## Verification and remaining acceptance

`tools/blender_texture_animation.py` passes in Blender **5.1.2** and **5.3.0**
using the five actual fixture families above. It checks:

- Public editing operations, existing channels, added binding counts, native
  GEOM/ANIM serialization and playback-rate retention.
- Cycles evaluation of the actual configured UV graph: negative and zero scale,
  offset-before-scale, origin rotation, negative scroll fraction and an unmoved
  emissive UV set. Native ColorSampler before/after images visibly show the
  edited offset (`local/texture-animation/texture-before.png` and `texture-after.png`).
- Fractional FPS, an unwrapped clock beyond a one-frame clip, 86400-second wrap,
  clock disable, and negative/zero speed key export.
- Exact actor pose/NLA-state equality while enabling/disabling UV preview over
  a real base-plus-motion animation at three frames; isolated scene updates.
- Explicit duration-extension opt-in, rejected unknown shaders/modified preview
  graphs (including muted nodes/drivers), retained nondefault masks, rollback
  after injected binding/key failures even when a failing API already inserted
  a curve, moved-scene clocks, packed texture save and
  reopen, and idempotent handler registration.

The test script accepts `--addon-root` and `--report` after Blender's `--`
separator. It asserts that the package and all its imported submodules came
from that exact directory, so the same workflow can verify an installed copy
without a source fallback. Reports and render evidence are written beside the
chosen report path, outside the installed add-on.

Some effect fixtures have pre-existing nondecomposable bind-pose warnings. The
tests use the normal importer warning policy and the existing retained native
bind-pose path; they do not claim to repair those source poses.

Still release blockers: other shader pairs, texture sequence/atlas families,
full material/render-pass parity, native blended/layered material-animation
parity and in-game verification of the edited assets. The UV-only preview
deliberately samples one maintained clip and does not claim to emulate the
engine's actor-animation scheduler. Its explicit clock origin can represent a
persistent animator; selecting a different UV clip is not proof of the native
runtime's reset policy. These are unfinished requirements, not permanent
format exclusions.
