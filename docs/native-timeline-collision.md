# Native effect timeline and collision editing (1.4.2)

These editors operate through Blender menus and panels. They preserve source
records and expose bounded native changes; their addition does not complete the
full asset-package, rendering or in-game acceptance requirements.

## Effect timeline

Choose **File → Import → Digimon Time Stranger → Native Effect Timeline (.bson)**.
The importer creates a separate 30 fps scene and embeds its original source
bytes and hash in the timeline controller. In **DSTS → Native Effect Timeline**,
select a named field to edit duration, hit timing, sound start, cleanup/fade,
gain, streaming or spatial flags. Values are the native 30 Hz clock, independent
of any SPRK animation rate. Duration `-1` retains indefinite native lifetime.

Duration, hit and sound-start fields appear as timeline markers. Moving a marker
requires **Apply Marker Timing** before export. Field edits refresh markers;
pending marker moves are retained, and conflicting edits to the same field are
rejected. **Refresh Timeline** explicitly keeps the field values. Native event
creation/deletion/reordering, changing the scene's rate, and keying/moving the
controller are unsupported.

For a drawable field, **Select Drawable Geometry** validates a chosen GEOM and
matching NLST, native alignment and palette safety, then records their hashes.
Changing either selected file invalidates the reference until reselected.
Full shader/texture/animation dependency packaging is still required separately.
Sound identity is displayed read-only until a validated sound-bank mapping UI
exists; editing cue start/cleanup does not change the proven custom bank route.
Inter-set socket fields remain preserved; the shared codec supports bounded
socket edits, but this panel does not infer owner/rig associations.

**Export Native Effect Timeline** writes a new BSON by default. Exporting over
an existing destination requires explicit replacement; an original or previously
written destination must still match its recorded hash. Save/reopen retains the
source and edits. The preview is an event timetable, without native particle or
audio-bank playback.

## Object collisions

Choose **Object Collisions (.phys)** in the Time Stranger import submenu. This
reads the actual native PHYS v2 resource, not mesh bounds or ID-conflict data.
Bodies appear as wireframe geometry using the same native Y-up to Blender Z-up
basis as the model importer.

Move or rotate a body, and edit native name/mass in **DSTS → Native Object
Collision**. For boxes or spheres, edit native half-extents/radius and select
**Refresh Primitive Preview**. Shared native shapes remain shared Blender mesh
data. Editing a primitive's mesh vertices cannot change its native shape and is
rejected; use its native dimensions.

For triangle collision meshes, edit existing vertices and triangle indices in
Edit Mode. Vertex and triangle counts must remain fixed. **Set Selected Face
Associations** selects existing entries from native association tables A/B and
shows the corresponding source names. The labels deliberately retain A/B:
their complete semantic categories are still being established. Primitive
associations are editable in the same panel.

Leave Edit Mode before export. Additional/deleted bodies or shapes, shape type
changes, body-shape reassignment, scales, modifiers, skinning, shape keys,
parenting, animation, loose edges and topology allocation are rejected. Raw
unknown fields remain exact. Native loader/BVH evidence establishes that the
six opaque mesh floats are not serialized bounds required by these vertex edits;
the native constructor computes its acceleration bounds from the mesh data.

**Export Native Object Collisions** preserves all unaffected bytes and uses the
same atomic/no-clobber/stale-destination protection as effect export. This is a
geometric preview, without physics simulation. Scene association packaging and
a controlled in-game collision test remain release blockers. Source spheres are
supported by the native constructor and synthetic tests; no sphere fixture was
present in the inspected 1,976 base/DLC files.

## Verification and provenance

`tools/blender_native_timeline.py` and `tools/blender_collision_workflow.py` pass
on Windows x64 Blender 5.1.2 and 5.3.0. They demonstrate edit → save/reopen →
export → reimport, compare scoped native changes, and reject unsupported edits
and stale originals. Actual native fixtures include `ef_b_chr395_bs01.bson`,
`chr050.geom`/NLST, `d0101p.phys` boxes and `d0101c.phys` triangle geometry.
No extracted source, live game asset, save or installed mod is modified.

The source reset regression also passes both versions. Its previous NLA track
ended at frame 136 while the effect inspection horizon continued to frame 241,
exposing the GLB bind pose and lowering the actor. It now uses the captured stand
clip without stretching the attack or sound. The user confirmed **both reset
correctly** in the corrected Triple Demon Jaw / Sevenfold project on 14 November
2026. That accepts this Blender preview reset, not full native skill rendering.
Static duplicate GLB empty/socket trees are retained but hidden by default.

The authoritative codecs remain in Time Stranger Formats:

- `effect_bson.py`, commit `089ee89`, SHA256
  `ee2a05a5ac56466f4dcee9df39dbcb4e487e820e83ef1aab4f89f0f9e6d1ec2f`.
- `physics.py`, including the validated batch mesh API at `cc6c79d`, SHA256
  `e3432b498af9da13222d143b83bd65404284aafcd9b4120cd7f1be2cc1280b0d`.
- Native backend after `2ede676`, SHA256
  `4f67d9c1562ce8a435d180e81a78c2079967b713eb2d5fb87a87423e074d32fe`.

The byte-identical modules are bundled in `src/`; editor code does not introduce
parallel parsers. Named-Blade / Nymic_Razor, Pherakki and existing upstream
credits/provenance remain in force. The separate [native light editor](native-lights.md)
adds bounded static optical edits and point-light previews. Full texture/effect
rendering, scene lifecycle and package acceptance work remains open.
