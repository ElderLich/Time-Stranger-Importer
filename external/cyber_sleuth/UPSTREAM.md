# Cyber Sleuth reader provenance

Vendored from [Romsstar/Blender-Tools-for-DSCS](https://github.com/Romsstar/Blender-Tools-for-DSCS),
commit `93221225d8576895e17a94522023896e530542b8`. Original author Pherakki,
copyright 2020, MIT license reproduced in full in `LICENSE`. Romsstar maintains
the upstream fork. Upstream also credits SydMontague (DSCSTools) and
xdanieldzd (CSGeom).

Only Python reader/interface/intermediate/utility sources are included. Game
assets, upstream placeholder texture and old Blender UI/export operators are
not distributed. Our Blender 5 adapter is `src/cyber_sleuth.py`.

Local changes: normalize trailing whitespace; retain declared animation duration/frame count for sparse holds;
no changes to native layouts or interpolation decoding. Reader write methods
remain upstream implementation detail and are not exposed as supported exports.
Cyber Sleuth export is explicitly rejected by the add-on.
