# Native skill asset bindings

Version 1.4.10 adds reusable bindings to the existing recipe controls. It writes
ordinary game assets. There is no game runtime, observer, targeting controller,
movement controller or custom cleanup system.

## Reuse an associated animation

Select the native actor and motion suffix, then **Use Audited Actor Motion**.
Choose a prior `ordinary-attack-actor-fragments-v1` audit. The captured recipe's
clip selects exactly one motion. Its complete source-pose audit, source model,
native geometry and animation hashes are rechecked. **Generate and Add Components**
copies that animation into a new folder using the chosen native motion filename.
Every animation byte remains unchanged; a different actor is rejected.

This reuses a verified conversion. It does not concatenate source phases or bake
target movement into the skeleton. The reusable `create_actor_job`,
`inspect_actor_job` and `produce_actor_component` APIs accept explicit data, with
no character paths or skill IDs. Jobs loaded from JSON and choices saved in the
blend both reject changed source/native mappings before publication.

## Author effect bindings

Enable **Author Native Effect Bindings**, choose separate caster/target names,
and enter the authored **Native Hit Frames** as increasing 30 Hz frame numbers.
The compiler supports one captured event per owner in this named binding profile;
additional events remain separate recipes. It writes the game's existing `hf0`
through `hf7` BSON fields. The game executes the skill and dispatches target hits.

**Bind Source Sound** writes the mapped source cue and captured offset into the
caster's native sound record. This bounded profile accepts one source sound.
The selected donor supplies its original sound schema. Gain is one, no fade is
invented, and the explicit **Stop Sound With Effect** choice uses the existing
native sound flag. Missing audio components, unsupported donor fields and invalid
frames reject the build. Source events and original donors are preserved.

Socket choices are distinct:

- **Native Actor Root** (`@root`) attaches to `CompositeModel` with empty socket
  names and native attachment flags. A named socket can instead be verified
  against the selected NLST inventory.
- **Native Hit Origin** (`@effect`) leaves owner/socket fields empty and native
  attachment flags zero. It consumes the normal effect origin resolved by the
  game's battle-effect row, retaining that row's locator and height behavior.

The staged reference uses target root for Sevenfold's foot event and the standard
native CP01 hit origin for Triple. This is an explicit port mapping; it does not
claim arbitrary source and destination target species have identical sockets.

Recipe `native_bindings` contains `caster_basename`, `target_basename`, `hit_frames`,
`include_audio`, `sound_template_set` and `stop_audio_on_end`. Omitting this object
preserves the prior event-local review behavior. Table rows remain a separate
package assembly step. Named BSON does not by itself make a complete installable
skill. The existing candidate builder still protects source trees, stale plans,
collisions and existing destinations.

## Verification

Pure cases check native hit/sound readback, outer-origin flags, target separation,
invalid frames, audited animation identity and publication protection. Installed
Blender tests load, edit, save/reopen and export the actual recipe controls. The
generic `tools/blender_skill_workbench_verify.py` rehashes any saved workbench
without hardcoded phase counts or per-skill Python edits.
