# Updating the installed add-on

After each verified source milestone, build `tools/package_addon.py`, then run
`tools/sync_installed_addon.py` with the ZIP, explicit Blender configuration root
and explicitly selected `--versions` (currently `5.3`). Without `--apply` this reports the changes. With `--apply`
it stages the ZIP, verifies every file, preserves the entire previous add-on in
`scripts/.codex-addon-backups`, replaces only `Time_Stranger_Importer`, and checks
the installed hashes. `--report` records the ZIP hash, changes and backup paths.
An error rolls back installations already updated in that invocation.

For a fresh Blender installation whose add-on folder is absent, pass
`--allow-new`. This opt-in creates only the selected version's add-on folder,
verifies the same complete ZIP hash map and records `new: true` with no prior
backup. Existing installations still receive whole-folder backups. A failed
multi-version operation also restores absent destinations to their prior state.

This updates disk files. An already running Blender process retains Python
modules and native libraries it loaded earlier. Save work and reopen Blender to
load the updated version; never force-close it or overwrite a locked backend.
Other add-ons, preferences and saved projects are left intact. The helper accepts
Windows Blender 5.1 and 5.3 configuration layouts. Current milestone validation
uses the installed Blender 5.3; 5.1 was removed by the user.
