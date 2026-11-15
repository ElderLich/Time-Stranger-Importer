# Passive native action observer

The observer reads an explicitly selected Time Stranger process or a captured
sparse-memory fixture. It does not inject, install a mod, call game functions,
write process memory, suspend threads, control the game or alter saves. Its only
native operations are Windows OS APIs for process/module queries and reading
memory. Core runtime activation remains disabled.

## Verified scope

The executable SHA256 must be
`ff9de825a543bf874cfb7e73ed951256d3ce4e8702957afa3b26ca6487a81688`. PE parsing
checks AMD64/PE32+, image base, section/file bounds and image size. A live reader
also compares ten loaded instruction prefixes against that exact executable.
An unknown executable, changed code prefix or unsupported vtable is refused.

Proven root/snapshot points, relative to the actual ASLR module base:

| Point | Read route / guard |
| --- | --- |
| Application singleton | pointer at RVA `0x1BA6B60`; scene type through `app+248 -> +56 -> DWORD +64` |
| Active scene battle | for type 6, `app+256` is a subobject; subtract 224, then read pointer `+1064` |
| Existing fallback battle | `RVA 0x1B9A5D0 -> CGameData+128 -> existing pointer`; never call lazy allocation |
| BattleSystem identity | exact vtable RVA `0x143CED0` |
| Direction manager | battle `+304`, exact vtable RVA `0x143C5E0` |
| Current direction | manager `+272`; only script vtable RVA `0x1387BB8` receives action decoding |
| Copied current action | script direction `+512`; caster pointer at action `+16`, kind `+0`, skill `+4` |
| Action vector boundaries | selection `+1384` stride 1296; results `+1480` and `+1504` stride 11216; begin/end/capacity checked |
| Result identity | descriptor at record `+8136`; `C54C20` proves this offset for both result vectors |
| Ordinary actor matching | descriptor party `+12`, type `+16`, identifier `+20`; exact BattleUnit vtable RVA `0x143DCA8`; compare actor descriptor `+232` identifier |
| Transform observations | actor handle `+38592 -> +264` figure; figure base position `+8544`; cached model transform from figure `+8536`, first entry `+144`; quaternion `+112`, rendered position `+128` |
| Timing/state observations | direction DWORD `+260`, scheduler byte `+288`, speed `+500`; actor speed, lock and raw position/rotation tween fields; wall-clock UTC, monotonic elapsed/read duration |

Special/attached actor lookup fallbacks, native group/index conversion, uncached
virtual transform resolution and coroutine/native-clock association are omitted
explicitly. Ordinary descriptor matches remain **candidate identities**, not a
certified final redirected/countered/multi-hit target set. Selection records are
reported as raw prefixes; they are never substituted for action result targets.

Reads require committed, readable Windows memory and reject guard/noaccess pages,
partial reads, invalid/alignment pointers, nonfinite transforms, excessive vector
capacity (64 records per vector), and malformed model ranges. Per-sample limits
are 1 MiB/4096 calls/64 KiB per read. Control pointers, action headers, descriptors
and ownership pointers are read again to reject torn snapshots. This does **not**
make cross-frame transform reads atomic. Refused samples are preserved in JSONL.

`observation_epoch` labels continuity only. `native_action_generation` and
`native_spawn_generation` stay null; polling cannot detect reuse entirely between
samples. Losing an observation resets continuity. These labels must never be
passed to the runtime core as certified generations. Native clock time remains
null until action/coroutine association is proven.

## Offline checks

Run from `runtime/SkillRuntime` with 64-bit Python (standard library only):

```powershell
python observer/test_observer.py
python observer/write_test_fixtures.py --executable 'D:/Programs/Steam/steamapps/common/Digimon Story Time Stranger/Digimon Story Time Stranger.exe' --output artifacts/observer-fixtures
python observer/observe.py decode --fixture artifacts/observer-fixtures/current-action.json --executable 'D:/Programs/Steam/steamapps/common/Digimon Story Time Stranger/Digimon Story Time Stranger.exe'
```

All **23 fixture tests** pass. Tests cover ordinary descriptor matching, current
action decoding, primary/fallback roots, changed vtables, malformed vectors,
unreadable/stale memory, changed control pointers, unresolved transforms, raw vs
rendered position, replay and honest continuity epochs. Generated fixture files
are labeled `synthetic_test_fixture`. They prove parser behavior, not real game
layout/lifetime behavior. Native layout provenance is separate and version-pinned.

## Concrete user capture procedure

No Time Stranger process was present during the readiness inventory, so the
Windows reader and actual battle snapshots remain unverified. A user-controlled
capture is the next checkpoint:

1. Launch the game normally. Enter an ordinary battle and stop at command
   selection. Keep the game on its normal speed for the first capture.
2. In PowerShell, from `runtime/SkillRuntime`, list the exact process ID:

   ```powershell
   Get-Process -Name 'Digimon Story Time Stranger' | Select-Object Id, ProcessName
   ```

3. Substitute that numeric ID in this command. Use a fresh output/capture name:

   ```powershell
   python observer/observe.py observe --pid 12345 --seconds 45 --interval-ms 16 --output artifacts/capture-single-target.jsonl --capture-dir artifacts/capture-single-target
   ```

4. While it runs, manually perform one ordinary single-target skill. Let its
   visual action finish and return to command selection. The observer sends no
   input. Record the chosen caster, target, skill and whether the observed skill
   uses a shipped `Skill_Shoot` script.
5. Repeat with another target using a new output name. Then capture a naturally
   available cancel/abort/teardown case through normal game controls; do not force
   process termination for this test. Record exactly what ended the action.

The ordinary script slot can be absent for many skills. `other_native_direction`
is an expected observation in that case; the tool will not reinterpret its memory
as `BattleDirectionScript`. If the battle produces no `script_action` samples,
the next step is identifying an existing shipped script-owned action, or proving
the ordinary native direction layout separately. No testing mod is installed by
this unit.

Default duration is 30 seconds; limits are 120 seconds, 10000 samples, and 64 MiB
of sparse captures. Ctrl+C stops observation and closes the read handle. JSONL
is flushed after every sample. Existing output files/capture directories are
never overwritten. `--capture-every 2` halves fixture frequency if needed.

Replay any successful captured snapshot with the same `decode` command. Preserve
the JSONL, captured JSON files and the short action/cancellation description.
Do not interpret an all-refused run as target or lifecycle evidence; its reasons
identify the next guard/layout issue to inspect. No debug privileges are enabled;
access denial is reported without escalating itself.
