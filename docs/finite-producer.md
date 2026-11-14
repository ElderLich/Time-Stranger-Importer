# Finite current-owner mesh component producer

`src.skill_conversion.finite_producer` generates native review components from an explicitly selected captured event, finite particle samples, reviewed material/mesh carriers, and explicit native donors. It authors new per-particle GEOM/ANIM/NLST files and a direct Group→Model4 graph using the maintained serializers. It does not invoke the research scripts or choose skill-specific paths, IDs, sockets, or resource names.

The first profile is `local-mesh-current-owner-world-offset-v1`: one local-aligned mesh material, local simulation, a current external owner, separate world-axis movement, and contiguous integer samples at 30 Hz. It retains captured mesh rotation and scale, color, texture-sheet frames, finite particle lifetimes, material clock offsets, and the admitted Custom2.w stream. Other Custom1/Custom2.xyz values must match the reviewed constant material profile. Unsupported selected nodes, missing samples, stale evidence, and incomplete native resource dependencies fail. Unselected source nodes remain listed and remain uncovered in the recipe.

This is a finite deterministic sample conversion. It does not establish Unity seed equivalence, fractional-frame behavior, source-engine runtime equivalence, arbitrary-target sockets, gameplay routes, or in-game cleanup. Reviewed mesh/material carriers and an existing source particle bake remain required inputs. Producing these inputs directly from arbitrary captured particle modules is outside this producer. Every output remains `installable: false`.

## Public API

```python
from addon.src.skill_conversion.finite_producer import (
    inspect_finite_job, produce_finite_components,
    material_fingerprint, node_fingerprint,
)

review = inspect_finite_job(job)
result = produce_finite_components(
    job, new_output_folder, expected_plan_sha256=review['plan_sha256'])
```

Inspection is pure Python. It returns `errors: [{code, message, ...}]`, `blockers`, `selected_nodes`, `omitted_nodes`, all directly consumed `inputs`, `sampling`, `buildable`, and `plan_sha256`. Inspection pins the source snapshot, nested carrier evidence and dependencies, donor files, and the producer's Python implementation. A buildable inspection authorizes attempting the bounded conversion; native geometry/material parsing and ABI validation happen during generation.

Generation requires the installed add-on and its compatible Windows backend. It independently inspects the immutable job again and checks the expected plan before writing. Its result contains absolute final `components` pins suitable for `recipe['components']`. Normal `inspect_recipe` validates the generated components and their remaining source coverage. Reusing an already represented event/node must be resolved explicitly in the recipe.

## Job schema

Save UTF-8 JSON with this shape. Each `pin` is `{path, sha256, bytes}`; job paths must be absolute. The `source` value is the unchanged `create_recipe(... )['source']` snapshot.

```text
{
  format: "nc-finite-local-mesh-job-v1",
  source: recipe.source,
  event_order: integer,
  bake: pin,
  bake_reference_root: absolute directory,
  carriers: pin,
  donors: {
    group_sprk: pin, group_node_id: integer,
    model_sprk: pin, model_node_id: integer,
    wrapper_geom: pin
  },
  prefix: ASCII letter followed by letters/digits/underscores, maximum 18 characters,
  node_indices: [explicit distinct captured node indices]
}
```

The Group donor must be native kind 0; the Model donor must be kind 4. Their established clock byte must select the effect-local clock. Only the selected payload templates and preserved native chunk padding are used; the generated graph replaces names, references, primary transform/color channels, finite timing, ownership, and child lists. The wrapper donor supplies the reviewed wrapper header fields and CLUT; a new single-bone identity skeleton is authored.

The bake must be `nc-native-particle-bake-v1`, `fps: 30`, and `local_mounted: true`. Its `source` resolves against the explicitly selected `bake_reference_root` if relative, and must match the selected event asset bytes. Node and particle indices/paths must match. The full job snapshot and source/bake provenance and limitations are retained. A selected bake `skipped` or `unresolved` record is an error, including a zero-sample selection.

## Reviewed carrier schema

```text
{
  format: "nc-finite-local-mesh-carriers-v1",
  profile: "local-mesh-current-owner-world-offset-v1",
  source_asset: pin,
  source_bake: pin,
  nodes: [{
    node_index: integer,
    node_path: captured node path,
    source_node_sha256: node_fingerprint(captured_node),
    source_material_sha256: material_fingerprint(captured_node),
    geometry: pin,
    dependencies: [{path, sha256, bytes, destination, references: []}],
    provenance: [pin, ...]
  }]
}
```

Carrier paths may be relative to the carrier manifest. Native dependency destinations are canonical patch-relative `images/*.img` and `shaders/*.shad` names. The complete actual material texture/shader closure is checked after native parsing. Conflicting destinations, unsafe names, symbolic-link input chains, and stale hashes fail. The carrier is one material and one bone, with float bindings ordered as optional `302.x`, `301.z`, and `178.xyz`, all material bindings to the same material. Native matrix alignment, physical parent lanes, vertex/index preservation, and animation streams are checked before publication.

Fingerprints use the module's canonical UTF-8 JSON serialization, including sorted keys, compact separators, and a final newline. Use the helpers instead of another JSON writer's default hash. Pinning the full source node additionally prevents reusing a material carrier for another source mesh, pivot, renderer, or particle configuration.

## Output and Blender workflow

Choose a new directory under an existing regular parent outside the source library. The producer never replaces an existing destination. It stages with inherited directory permissions, supports long Windows paths for file I/O, rechecks inputs after generation, and atomically publishes using Windows no-replace directory rename. Failed generation removes only its private staging directory and restores the native backend's error mode. Other platforms currently reject publication explicitly.

The output contains `job.json`, `inspection.json`, `producer-report.json`, `components/*.json`, and `resources/patch/*`. Component provenance pins the saved job/inspection and consumed source inputs. The result also records the loaded native backend path/hash, reported version if available, Python/platform identity, and loaded animation/serialization module pins. These runtime pins are rechecked before publication and included in component provenance. An absent backend version is recorded as null; its exact file hash remains authoritative.

In Blender, load or create a Native Skill Recipe, then use **Load Component Job** to select the reviewed job through its file picker. **Open Component Review** shows all selected/omitted nodes, input pins and generation limits. Inspect the job, choose a new output directory, and use **Generate and Add Components**. The scene retains the job file pin, saved job snapshot, inspection, output choice, and result through save/reopen and source preview/reset. The operator rejects a source snapshot mismatch, stale saved job bytes, or duplicate selected event/node components before producing output. Generated component pins enter the ordinary recipe workflow; inspect the full conversion again before building a review candidate.

## Validation

`tests/test_skill_finite_producer.py` covers captured-source and particle identity, source/carrier staleness, explicit unsupported/empty selections, native owner/timing/channel readback, output guards, and failure cleanup with backend-state restoration. Actual Blender workflow checks live in `tools/blender_skill_producer.py`.

The local reviewed fixture uses 14 particles from one captured mesh system. An independent auditor checks every sampled frame against source motion, geometry, world-owner transforms, shader/material closure, and UVs. Its 208 samples cover 1,872 vertex checks and 1,872 UV checks; generated graph bytes match the frozen reviewed graph after resource-name normalization. This evidence concerns the admitted fixture/profile, not all skill effects or in-game behavior.
