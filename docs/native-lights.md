# Edit existing native light records

Choose **File → Import → Digimon Time Stranger → Native Light Records (.geom)**.
Select a native GEOM containing lights, then select a light object and open the
**DSTS → Native Light** panel. The complete source GEOM is embedded in the blend
file, so saving and reopening preserves the source and edits.

Native types 0, 2 and 3 expose RGB and intensity. Type 0 also exposes range.
These are native float values, including HDR RGB above one and intentional zero;
the editor does not apply an implicit gamma conversion or normalize the source.
Other native types remain inspectable and unchanged. Types and slots are not
editable. Select **Export Native Light Edits** to write a GEOM with only those
edited record fields changed. All other source bytes, including mesh/material,
camera, skeleton, animation bindings and unknown light words, survive exactly.
Existing source name lists and animation files remain required companions.

Point lights preview their source bind-pose position and range. This preview
maps native intensity to a relative Blender energy value; watt equivalence and
native falloff/shadow behavior are not established. The panel retains the exact
native values separately. Types 2/3 use non-rendering guide objects; they are
not guessed environment or directional lamps. A missing/ambiguous bone binding
or out-of-profile source optical values also yields a guide, preserving bytes.
Source animation can override static light fields; such bindings are identified
in the panel but are not evaluated by this standalone editor.

Edit the **Native Light** panel. Direct Blender light-data edits, placement,
constraints, animation, type/slot/bone changes and added/deleted records are
rejected on export. Exporting arbitrary new lights, moving their bound rigs,
light animation, directional/environment rendering, native photometry,
mixed-scene assembly and dependency packaging remain release requirements.
They are not silently translated or discarded.

Exports serialize and validate before atomic publication. Replacing a file
requires the explicit option in the export dialog. Imported-source and repeated
output paths have SHA256 stale-write guards, retained when the blend is saved.
An external edit after the previous export is rejected; reimport or choose a
new output path. Failed replacement leaves the previous file intact.

The field contract comes from Time Stranger Formats `docs/scene-records.md`
and the maintained `LightRecord` API; original Named-Blade / Nymic_Razor credits
remain applicable. No extra native field offsets are inferred by the UI.

`tools/blender_native_lights.py` tests real `d0101l`, `d0101sun`, and DLC
`ef_b_chr689_bs01` on Blender 5.1.2 and 5.3.0. Four records include native types
0/2/3 and two point previews. Tests cover actual field changes, exact unrelated
bytes, save/reopen, repeated exports, stale/failed writes, invalid inputs,
unsupported record edits, and rejection of preview-only data changes. Local
reports are `build/native-lights-51.json` and `build/native-lights-53.json`.
No edited light from these checks has been installed in the game. Passing these
checks does not establish native rendering or finish the full light workflow.
