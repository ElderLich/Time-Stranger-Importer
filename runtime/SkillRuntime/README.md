# Dormant skill runtime

This is an isolated, reusable action-context and placement-ownership core plus a
dormant Reloaded extension scaffold. It does not provide a native backend, Lua
bridge, hooks, injector, installer, source animation evaluator, or `bs03` export.
The Blender add-on and Helper do not import or package this directory.

The separate [passive observer](observer/README.md) decodes captured memory and
can read an explicitly selected Windows process through read-only OS APIs. It
does not connect its observations to an active native backend. Its 23 offline
fixture tests pass; no game process was running for a live readiness capture.

## Implemented contract

`SkillRuntime.Core` targets `netstandard2.1` and has no package dependencies.
`ActionContexts` takes an `IActionBackend`. That backend must capture the exact
current action's resolved targets and supply battle/spawn generations; the core
does not infer targets from commands, party slots, source captures or pointers.
The current tree implements only the fake backend in the test executable.

Each context gets an unforgeable service-owned token. Action generations cannot
be replayed, snapshots are defensive, and placement requires exactly one unique
resolved target. Multi-target and missing-target placement fail explicitly.
Caster leases are exclusive, including while cleanup executes. Mutations and
cleanup run on the creating thread. End, abort, battle teardown and disposal
release once; stale actor generations discard their receipts without restoring
into a replacement actor. Losing the target ends placement and restores a live
caster. A partially failed backend write triggers cleanup. Release failures are
recorded in `CleanupFailures` and quarantine that actor generation from later
placement. Teardown continues releasing other actors after a failure.
An abort re-entering an active backend `Apply` is marked pending and restores
after that call returns; `ReleaseReport.DeferredUntilApplyReturns` makes this
visible. Further application is refused while ending. Cleanup failures remain
available in `CleanupFailures`, including failures from deferred release.

The only owned channels are position and yaw. A backend receipt must restore just
those channels and their **proven native setter/tween side effects**. It must
preserve unrelated animation, scale and visibility. Capturing a lease is a
read-only transaction; no mutation is permitted before a matching receipt is
accepted. `Yaw` is an abstract request, not evidence that saving one float is
sufficient for the native quaternion setter. No whole-transform restore exists.
The fake backend demonstrates selective restoration; it is not native ABI proof.

`NativeProfileValidator` reads an explicitly supplied executable, verifies its
SHA256, AMD64/PE32+ header, preferred image base and research RVA bounds, and
preserves the input stream position. The known research profile is pinned to
`ff9de825a543bf874cfb7e73ed951256d3ce4e8702957afa3b26ca6487a81688`.
`RuntimeEnabled` is always false. Matching the build never certifies the native
contracts or exposes an activation override. The listed addresses are research
seams, not callable declarations.

`SkillRuntime.Reloaded.Startup` implements the reviewed Reloaded `IMod` interface.
Starting it only requests logging and reports refused native activation. It has
no controllers, native addresses, delegates, process attachment or hooks. The
host smoke test uses a fake loader that throws if any capability other than
logging is requested; it does not run Reloaded or the game.

## Build and tests

From this directory, using the installed .NET SDK:

```powershell
./build.ps1 -CoreOnly
./build.ps1 -ReloadedInterfacesPath 'D:/Digimon Modding/Programs/Reloaded II/Loader/X64/Reloaded.Mod.Interfaces.dll' `
  -ExecutablePath 'D:/Programs/Steam/steamapps/common/Digimon Story Time Stranger/Digimon Story Time Stranger.exe' `
  -Net9ReferencePackage 'C:/Users/Shanalotte/.nuget/packages/microsoft.netcore.app.ref/9.0.22/microsoft.netcore.app.ref.9.0.22.nupkg'
```

The second command reads the executable only. Build outputs and logs stay in
this directory's ignored `bin`, `obj` and `artifacts` directories. Package sources
are cleared in `NuGet.Config`. The core/tests need no packages; the net9 host
restores the official reference pack from a workspace-local feed with transitive
framework downloads disabled. No network restore occurs. The host project also
enforces the interface DLL hash itself, including
when built directly through MSBuild. The host output does not copy that DLL;
only the fake-host test output copies it for local test execution.

Observed SDK: **10.0.403**. Core: `netstandard2.1`. Host and its fake-loader test:
**net9.0**. Core test runner: `net10.0`. The earlier reference-pack gap is now
resolved: the official `Microsoft.NETCore.App.Ref 9.0.22` package was found in the
existing local NuGet cache and copied into `artifacts/dependencies`; restored
reference files stay in `artifacts/packages`. Installed runtimes are unchanged.
Its source metadata identifies `https://api.nuget.org/v3/index.json`, Microsoft,
and runtime commit `faaecba82df8933d7af60972138ce25f9c776cbd`. Package SHA256 is
`d54eb743bfb407095944d32fc99183da6e8a7f50f1ba930d600a2bcb55cc8813`.
`prepare-net9-reference.ps1` checks that hash before copying. The package contains
an embedded NuGet signature; this milestone does not claim a new online signature
chain/revocation verification. Host compile and fake-loader execution pass on
the installed .NET 9 runtime. Actual Reloaded loading remains untested. No live
load was performed and no ready-to-install ModConfig or mod package is emitted.

Reviewed reference: `Reloaded.Mod.Interfaces` **2.5.0**, informational commit
`c7f519cf8cabaf3a7af261ae4c1b185769e2ceda`, 40,960 bytes, SHA256
`98b33df0f380ca7361bffa9a0328c63c7d96140fc2070d180864fa157f00c88a`.
The installed DLL was read/reconstructed for interface inspection; its source is
not vendored. Original local Ryo source at commit
`1d661e153ad46efa0707379bd9fd61255f357a2c` provided Reloaded lifecycle examples.
Its live deployment output settings were not reused. The installed DSTS/MVGL
loaders offer file replacement APIs and are not dependencies of this scaffold.

The verified run passed **28 core tests** and the fake Reloaded host smoke test,
with zero compiler warnings/errors. Cases include nested ownership, reentrant
cleanup, stale generations, target loss, partial writes, restoration failures,
selective channel restoration, battle teardown, thread affinity and malformed or
mismatched native profiles. The actual pinned executable passed offline identity
checks while retaining all seven refusal reasons. `artifacts/build-report.json`
records the SDK, dependency hash, DLL hashes and profile result. The durable
`research/passive-observer-evidence.json` records the net9/observer milestone;
the original `research/offline-build-evidence.json` remains historical evidence
for the first dormant net10 scaffold.

## Remaining native adapter work

The exact current-action descriptor route and setter writes are documented in
[research/native-layout-evidence.md](research/native-layout-evidence.md).
The remaining minimum interfaces are a proven current-action snapshot/lifetime
adapter, battle and spawn generation authority, a selective placement/tween
receipt, and hooks that release that receipt before actor/action destruction on
every completion and abort path. Lua coroutine association must survive yield.
Target redirection/multi-hit timing and default-shoot queue concurrency still
require proof. These are engineering dependencies, not a new permission rule.
