# Import and animation corrections

## New Century GLB orientation

Some nc-extractor models import through Blender with the coordinate conversion
already present in their bone rests. Applying an assumed object rotation again
while sampling animations made the converted model tip over when an action was
enabled. Shoutmon X4 (`m248`) reproduces this case. Shoutmon X7 (`u288`) also
requires a nonzero armature origin; rotation alone cannot align its bind pose.

The importer now derives the source static transform from the verified bind
alignment, including a rigid translation when needed, before sampling motion.
It retains the existing world-pose conversion, native bone flags and root-motion
baking. These changes do not use model-name exceptions or per-skill scripts.

Reimport the original GLB using **File → Import → Digimon New Century → Model**.
Previously saved actions contain the old baked coordinates and are not repaired
simply by installing the update. Keep the previous collection until any manual
edits have been transferred to the corrected import.

## Native facial rest scales

[Upstream issue #4](https://github.com/Named-Blade/dsts-formats/issues/4) describes
`chr189` facial bones whose rest scale is `0.1` while their inverse-bind matrices
use scale `1`. These are different authored values: native animation scale `10`
reveals a face state, while scale `1` leaves it suppressed at `0.1`.

The maintained importer preserves the original native transforms and bind
snapshot. Its bounded correction applies to validated leaf-bone scale
differences. Unsupported differences still produce diagnostics. Handled
messages remain in the imported collection's metadata.

For affected models, the locked **Native rest scale** NLA track supplies the
rest correction once; ordinary native animation tracks retain their original
relative channels. Keep that base track enabled while previewing clips. Use
the ordinary track mute controls; soloing only a motion track excludes the
base correction. The internal track is preview data and is excluded from
native animation exports.

The reference Formats adapter also preserves separate bind and rest data. Its
scope is geometry import/export; animation playback belongs to this importer.
The compiled backend and its binary validation remain unchanged.
