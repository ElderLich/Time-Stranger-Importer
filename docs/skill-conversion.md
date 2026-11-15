# Captured New Century skill candidates

The **New Century → Native Skill** Blender panel keeps a captured model/phase,
native actor/motion IDs, caster/target donor templates, socket mappings, cue
mappings and producer manifests in the scene. Save/reopen retains that recipe.
Inspect the current inputs, then build a separate review candidate. Source
preview uses the existing captured-phase importer and its idle reset.

Open **File → Import → Digimon New Century → Native Skill Recipe (.glb)**,
then use **New Century Native Skill** in the DSTS sidebar. Select the native
actor, motion and IDs, the caster/target BSON donors and Figure template set,
and map sockets with the native-root checkbox or the side's NLST inventory.
Map source audio cues, use **Add Reviewed Component**, then **Inspect
Conversion**. The full review is available in the Text Editor. Choose **New
Candidate Folder**, then **Build Review Candidate**. Recipe, mappings and
review are saved in the .blend. Preview imports the captured actor, audio and
effect references into a separate scene, including idle reset; those
references are not a complete rendered-particle preview.

This workflow is an asset port. The game supplies battle execution; no custom
runtime, observer, movement or cleanup controller is required or generated.

This milestone produces actual native component resources and bounded BSON
event roots. It is not a complete playable skill or an installable mod. It does
not edit DigiTS Helper or an installed game. Sevenfold's ordinary Cast and
Triple's full attack still require a reviewed native package and game acceptance.
A skill includes actor motion, particles,
materials, sockets, audio and tables; it is not one SPRK/BSON conversion.

## Pure Python API

The module does not require `bpy`. Source catalog inspection requires NumPy,
as does the existing captured-source reader.

```python
from package.src.skill_conversion import (
    create_recipe, inspect_recipe, build_candidate, pin_file,
)

recipe = create_recipe(model_glb_or_directory, phase_id,
                       target_chr=native_actor, motion_suffix=native_motion)
recipe['components'].append(pin_file(component_manifest))
inspection = inspect_recipe(recipe)
report = build_candidate(recipe, new_output_directory,
                         expected_plan_sha256=inspection['plan_sha256'])
```

Choose phase IDs from `nc_preview_source.catalog_model`; never guess source
clips. Model directory and `model/model.glb` selections are both accepted.
Source side, event order, dispatch frame, clip and independent clocks remain
captured data. Selecting a return or held-pose phase does not synthesize a full
skill. The returned mappings remain blank/null until selected explicitly.

Inspection returns `source`, `effects`, `requirements`, `errors`, `blockers`,
`components`, `files`, `compositions`, `coverage`, `buildable`, `installable:false` and
`plan_sha256`. Errors reject a build. Blockers describe missing release work;
they may coexist with buildable, incomplete review assets. At least one valid
component is required. Changing inputs or controls invalidates the reviewed
plan. `pin_file(path)` returns `{path, sha256, bytes}` with an absolute path.

## Recipe schema

`format` is `nc-native-skill-recipe-v1`.

- `source` is created from the catalog. It contains `model_path`, `model_dir`,
  `library`, `model_code`, `role`, `phase_id`, the exact `phase`, all source
  `files` pins, ordered `effects`, and explicit missing source records. Do not
  edit this snapshot. Recreate a recipe after changing captured source data.
- `target` contains `model_stem`, `motion_suffix`, `animation_selector`,
  `primary_effect_id`, `target_effect_id`, and optional `socket_inventory` pins
  for `caster` and `target` NLST files. IDs are not allocated automatically.
- `donor_bson` contains `caster` and `target` file pins or null. The optional
  `donor_template_set` selects a set on each side (default `set1`).
- `socket_mappings` contains `{side, source_socket, native_socket}` rows. Blank
  means unchosen. `@root` explicitly selects the owning actor root and writes
  empty BSON socket names; it is not a fabricated native bone. Other names
  are checked against the side's pinned NLST when supplied. Missing owner
  inventories retain an explicit validation blocker.
- `cue_mappings` contains `{source_key, cue_id, component}`. Cue mapping remains
  a review choice; the current composer does not install banks or silently
  borrow donor sound. Source timing and lifetimes remain required work.
- `components` is an ordered list of exact producer-manifest pins.
- Optional `coverage_evidence` pins producer coverage reports. Components can
  also carry `coverage_evidence` pins, so choosing a reviewed component brings
  its source rejection reasons into the inspection without script edits.

The API does not contain model-specific paths, actor IDs or effect allocations.

## Component schema

Each pinned JSON manifest uses `format: "nc-native-skill-component-v1"` and a
unique ASCII `id`. `kind` is `effect`, `actor`, `audio` or `table`.
`destination_root` defaults to `patch`. Audio/table components can choose
`mod` for exact mod-root paths such as `Ryo/...`; these are reviewed under
`resources/mod` and never incorrectly placed inside `patch`. Logical collision
checks equate patch paths with `dsts-loader/patch/...` in a mod-root component.
Effect/actor components must use `patch`.

```json
{
  "format": "nc-native-skill-component-v1",
  "id": "example_cast_system_0",
  "kind": "effect",
  "source": {
    "model_code": "captured_code",
    "role": "captured_role",
    "phase_id": "captured_slot:phase",
    "asset": {"path": "absolute captured JSON path", "sha256": "64 lowercase hex characters", "bytes": 1234},
    "node_index": 0,
    "node_path": "captured/path",
    "event_order": 0,
    "source_event": {"exact": "captured phase event object"}
  },
  "profiles": {
    "coordinate": "producer coordinate profile",
    "render": "producer renderer profile",
    "ownership": "producer owner profile"
  },
  "sampling": {"fps": 30, "duration_frames": 120},
  "native": {"figure_stem": "converted_figure"},
  "dependencies": [
    {"path": "relative/source/converted_figure.geom", "destination": "converted_figure.geom", "sha256": "64 lowercase hex characters", "bytes": 1234, "references": []}
  ],
  "provenance": [],
  "blockers": ["Producer's remaining source/runtime limitations"]
}
```

This is a shape example; placeholder hashes/events are not valid manifests.
`asset` and `provenance` are file pins. Relative paths resolve against the
component manifest. Dependency destinations are relative to native `patch`,
with forward slashes and no traversal, reserved Windows names or case aliases.
Each effect must declare its same-stem GEOM, ANIM, NLST and SPRK plus all needed
mesh, shader and texture resources. Optional `references` name other exact
destinations in that component, allowing explicit closure validation. SPAK
Model4 Figure/motion references are independently read by the maintained codec
and must also resolve. Unknown node families and GEOM material dependency
semantics retain a native audit blocker. A producer's pin is provenance, not
proof that every source feature or native shader has passed runtime acceptance.

Actor/audio/table manifests share identity, source pins, dependencies,
provenance and blockers. They retain native metadata appropriate to their kind
(`model_stem`/`motion_suffix` for an actor, with the named motion present and
`source.actor_clip` matching the selected model/phase). Audio requires
`source.source_key`, the exact captured sound `source.source_event` and its
WAV asset pin, plus `native.cue_id`, `native.raw_cue` and `native.bank`. Selected
cue mappings must match that audio component. Missing audio components and
actor fragments are reported separately from lifecycle/route review.
This milestone packages their exact
review bytes; it does not claim general actor conversion, cue-bank allocation
or gameplay table construction merely because those files can be preserved.

## Native BSON composition

The bounded `neutral-attached-figure-event-v1` profile promotes the proven
Figure cloning operation from local research. It uses the maintained
`EffectDocument` parser, string interning and readback validation, not a second
BSON reader. The selected template must have the exact supported neutral
single-Figure emitter, root controls and attachment clock. Nonneutral emitter
counts, transforms, randomization, lifetimes and unknown controls fail.

For each captured event with converted components, an explicit socket mapping
and its side's donor, the composer emits one event-local BSON. It clones a set
for each Figure, uses `CompositeModel` as the external owner and applies the
explicit socket. It retains the donor emitter's neutral native records and
preserves the original donor bytes. The generated template deliberately omits
donor movie, flash, blur, sound and other common controllers; the complete
omitted-field list is recorded. It never drops source effects silently:
unconverted source coverage remains an explicit blocker.

Event-local time starts at zero. Captured dispatch frame/order and target side
remain in the composition report. No caster/target aliases, hit timing or
concatenated actor clips are invented. The event root's hit table is zeroed;
native skill/per-hit scheduling is authored separately through the game's asset bindings. This explains
why valid BSON can be produced while the candidate remains non-installable.

## Exact source-system coverage

Inspection now enumerates every captured particle-system node for each source
event. The `coverage` report contains `systems` and `represented` counts,
ordered `events`, exact `uncovered` rows and independent `errors`. Represented
means a selected component references that exact source model/role/phase,
asset hash, event order and node index/path. It does not mean supported or
accepted in game. Reusing the same asset for caster and target events preserves
both independent rows and clocks.

Each row records the actual render mode, alignment, simulation space, active
and renderer flags, emission descriptor, 3D rotation, pivot, stretch, custom
streams, source shader and pass states. Missing nodes remain visible even when
their recorded rate is zero, renderer is disabled or a producer bake has no
particles. Additional captured light/controller/scene records remain a separate
coverage requirement. Prefab matching follows the existing source importer's
case-insensitive asset selection while retaining the exact source strings.

Producer reports use `nc-native-skill-coverage-evidence-v1` with `records`:
each record carries `source` identity/asset pin/event order/exact source event,
`node_index`, `node_path`, `status`, `reason` and nonempty pinned `provenance`.
Producer status is `covered`, `rejected`, `unsupported` or `inactive`. A producer
`covered` claim cannot replace a missing selected component; an inactive claim
cannot silently omit a source node. A selected component conflicting with a
rejection is an inspection error. Changed report/provenance hashes reject a
build, and accepted report snapshots accompany the candidate.

`coverage_complete` only describes source-node representation. Native material
dependency closure, render/lifecycle parity and game acceptance remain separate
requirements. The current reference represents 61 of 61 ordinary Sevenfold
systems and 13 of 13 Triple systems. Three Sevenfold systems use the explicit
stationary-root/yaw profile; moving-root equivalence is not claimed. Source
engine and native rendering limitations remain in each component's provenance.
See [native bindings and audited animation reuse](native-skill-bindings.md).

## Publication and tests

Build publishes a new sibling-staged directory containing `recipe.json`,
`inspection.json`, `candidate.json`, component provenance snapshots and exact
`resources/patch` files (and `resources/mod` for explicit audio/table mod-root
dependencies). It does not use a loader-install layout. Existing
destinations, source-library/producer/template output trees, source changes,
dependency collisions, traversal and stale review hashes are rejected. Source
and manifest hashes are checked again before publication. Failed preparation
or rename removes only the compiler-owned temporary directory. The verified
publication platform is Windows; the POSIX rename guard is cooperative, not an
interprocess lock.

Run `python -m unittest discover -s tests -p test_skill_conversion.py -v`.
Run `python -m unittest discover -s tests -p test_skill_coverage.py -v` for
source coverage, same-asset ownership separation, producer provenance,
conflicting/missing components and explicit inactive/zero-rate retention.
Checks exercise actual BSON parse/composition/readback, independent source
event timing, exact dependency inclusion, case/prefix/traversal conflicts,
source/node identity, stale plans, mid-build source edits, existing destination
preservation and injected publication failure. Symbolic-link rejection is
implemented; its filesystem regression is skipped when Windows denies symlink
creation. Actual installed Blender save/reopen/operator checks are a separate
workflow, and offline checks do not establish in-game acceptance.

Upstream geometry and animation credits in the project's provenance/licensing
documents remain applicable. No proprietary captured assets or native donors
are included in this public source module or documentation.
