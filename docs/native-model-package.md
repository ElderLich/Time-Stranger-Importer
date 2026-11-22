# Native model packaging and conversion boundaries

The add-on converts supported model/animation data and preserves a native
donor's material records. It is not yet an automatic New Century skill,
particle or shader converter. A successful package check is not an in-game
rendering acceptance result.

## Capability map

| Requested asset | Current add-on behavior | Still separate |
|---|---|---|
| NC model and skin | GLB import, source bind matrices, native rig conversion, donor GEOM export | Donor choice, model-specific visual verification |
| Supplied motion clips | Source clips, root motion, native location axes, action-slot timing; explicit NC Moveset export | Missing clips are not invented; native table hooks and skill phase handoff |
| Body materials | Independent donor metadata, all14 pass descriptors, numeric payloads, typed texture bindings | Unity shader family/keyword translation and native per-pass shader generation |
| Existing native shaders | Preserve pass names; resolve and package local compiled DXBC dependencies | Shader compilation, linkage, pixel equivalence and runtime pass selection |
| Textures | Export supported images to DDS `.img` with mipmaps | Source-specific shader texture roles, lightmap decoding and scene lighting |
| Existing native camera | Preserve original rig/records; edit motion and verified lens fields | General camera creation, cinematic attachment/framing/reset orchestration |
| NC cinematic JSON | Separate Blender actor/camera/audio preview; nonrendered effect references | Native particle, scenery, camera, sound and display/cast/target phase package |
| Persistent head effects | No generic converter or socket/loop authoring workflow | Source attachment contract, continuous clocks, native effect composition |
| Sound | Preview sound strips | HCA/cue registry/routing, voice identity and audible game verification |
| Package output | One validated native mesh model, base/optional same-stem motions, local shader/image dependencies | MBE entries, LOD set, SPRK/BSON, Ryo/Reloaded configuration and deployment |

Research artifacts for one Digimon are not treated as reusable acceptance
fixtures merely because they compiled or appeared in a viewer. The completed
bind-pose and material-preservation repairs are shared add-on capabilities;
unfinished native effect/shader conversions remain outside the add-on.

## Use the package workflow

1. Export the model to an isolated folder. Keep its `.geom`, matching `.nlst`
   and base `.anim` under one stem. For NC conversion, use **NC Moveset** to
   generate the sparse base animation and mapped motion filenames.
2. Put custom images in `images/` and custom compiled native shaders in
   `shaders/` beside the GEOM. Export PNG previews are not native `.img` files.
3. Choose **Package Exported Native Model** from the DSTS export submenu.
   Select the existing GEOM and a new destination folder. An optional native
   resource folder must directly contain `images/` and `shaders/`.
4. Review `model-package.json`. The payload lives under `dsts-loader/patch`.
   `external_native_dependencies` lists exact hashes of native resources
   resolved outside the model export folder. They are intentionally omitted
   from the payload. Their continued availability is a deployment requirement.

The optional motion selection includes `model_*.anim` but excludes
`model_lod_*.anim`. It does not rename animations or create native table hooks.
A LOD is a separate model, not a motion. Donor animation channel counts alone
cannot establish correct bone ordering; use clips exported against this rig.

The reusable Python entry points in `src/model_package.py` are
`plan_model_package(geom_path, resource_root=None, include_motions=True)` and
`package_native_model(geom_path, output, resource_root=None, include_motions=True)`.
They run inside the add-on's compatible Blender/Python environment. Planning
does not write files. Publishing validates everything first, writes and hashes
an isolated temporary directory, then renames it into a new output folder.
No payload is rewritten; existing folders are rejected.

## What is validated

- The maintained Formats reader must accept the native GEOM strictly. Backend
  diagnostic settings are restored after the call. Unsupported mesh controllers
  remain rejected by the backend.
- GEOM v316 absolute inverse-bind, skeleton and transform addresses must be
  aligned16 and matrix tables must fit in the file. The native matrix path at
  `0x14036680B` uses aligned SIMD operands; an eight-byte shift was an actual
  game-load crash, so this is a native safety check beyond offline round trips.
- Meshes with the native contiguous-palette flag must reference an ascending,
  contiguous skeleton range. A sparse or reordered list under this flag is
  rejected: native upload otherwise uses different bones despite valid indices.
- Every physical parent lane consumed by the native four-lane SIMD loop must
  fit before the transform section, reference valid bones and avoid self-parenting.
  This includes padding beyond a declared count that is not a multiple of four.
  Native repeated root pairs are accepted; zero padding that overwrites the root
  transform is rejected. The package does not silently repair either contract.
- NLST names, numeric hashes, parents and types must match the parsed GEOM.
  Native repeated mesh-name records retain their multiplicity; unpadded native
  hex hashes compare correctly to padded exporter hashes.
- Base and selected motion ANIMs are read with the existing shared serializer.
  Bone and float-channel counts must match this GEOM; timing and channel indices
  are checked. No replacement animation codec is introduced.
- Every active material pass resolves both vertex/pixel `.shad` files, retaining
  native pass topology. Texture parameters must be material-scoped textures.
  Local files take precedence; a broken local override cannot silently fall
  back to a native file. Shader DXBC table/chunk bounds and basic DDS header,
  dimensions and pixel-payload presence are checked.
- DX10 texture headers must use a format accepted by the PC image reader.
  Its dispatch table at `0x14038FA50` rejects uncompressed sRGB formats 29
  and 91, which can leave the character black in game. For body color, stock
  models use BC1 sRGB (72) or BC7 sRGB (99). Use linear formats for normal
  maps and masks.
- Every copied payload file has a manifest SHA256 and is verified after writing.

The DDS check is not a full mip/decompression validation, and the DXBC check
does not prove input/output linkage or correct render-pass semantics. Numeric
uniforms and opaque material words are copied unchanged; the packager does not
guess new numeric contracts. No GPU or game fidelity claim follows from the
dependency report.

## Explicit boundaries

This first package schema accepts mesh models only. Camera/light/scene GEOMs,
same-stem `.sprk` or `.bson` companions in either chosen source root, preview
JSON and GLB inputs are rejected. Effects attached through other names or game
tables cannot be inferred from a standalone model; this workflow does not claim
to discover or package those. It never generates fake native substitutes for
Unity effects, compiles arbitrary Unity shaders, or concatenates cinematic phases.

Native assets, source captures and generated game payloads remain local. Credits
to Named-Blade/Nymic_Razor and Pherakki's animation serializers remain unchanged.

## Validation

```powershell
blender --background --factory-startup --python-exit-code 1 --python tools/blender_model_package.py
```

The local native chr050 fixture exercises byte-identical packaging, local versus
external resource handling, missing/corrupt dependencies, actual aligned-matrix
rejection, invalid fast palettes, mismatched NLST/ANIM, motion scope, unsupported
companions and existing destination preservation. A native one-bone prop exercises
valid repeated root pairs and the historical zero-padding failure. These checks
also cover forensic inputs accepted by the reader but unsafe for native execution.
Executed on Windows x64 Blender5.1.2 and5.3.0; other
versions/platforms are unverified. Game assets are not committed with the test.
