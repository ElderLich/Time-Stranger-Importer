# Native actor adapter evidence — 2026-11-15

This is read-only static evidence for executable SHA256
`ff9de825a543bf874cfb7e73ed951256d3ce4e8702957afa3b26ca6487a81688`, preferred
base `0x140000000`. Addresses below are linked VAs; subtract that base for an RVA.
No signatures, layouts or function prototypes in this report are activated.
`native-seams-evidence.json` pins the evidence and checks each captured instruction
prefix against the actual executable's PE section mapping.

## Current-action target identity

The existing action copy at direction `+512` is made by `14050BAC0`. The action's
result vectors at `+1480` and `+1504` contain 11216-byte records. `140A02FB0`
walks `+1504..+1512` and, for records with nonempty callback data at
`+9568..+9576`, resolves the descriptor at record `+8136` through `1409E6520`.
This is action-owned result material; it is distinct from last COMMAND targets.
It does not prove that this callback-producing traversal includes every visual
target or that the copied results are final at `Skill_Shoot` entry.

`1409E6520 -> 140A38E00 -> 140C3F860` is now recovered. `A38E00` chooses a party
using descriptor `+12`. `C3F860` reads descriptor type `+16`, searches party slots
`+280..+328` then `+336..+352` for type 1, or `+360..+384` for type 2, and calls
`A40F90(actor,0)` followed by **`A411C0(actor,descriptor)`**. The latter tailcalls
`A3A410(actor+232,descriptor)`, comparing 32-bit identifier fields at `+20`.
The identifier therefore resides at actor `+252` for this route. `A40F90` checks
descriptor type `+16` is nonzero; that check alone does not prove actor lifetime.

**Decompiler caveat:** the original `C3F860` pseudocode omitted the second
`A411C0` argument, and the wrapper pseudocode itself showed only one argument.
Raw instructions at `140C3F8A4`, `140C3F8F4`, `140C3F954`, `140C3F9D0` and
`140C3FA3B` explicitly load `RDX` with the preserved descriptor. `A411C0` adds
`0xE8` to `RCX` and jumps to `A3A410`, preserving `RDX`. This is identity matching,
not a first-live-actor lookup. Do not use the reconstructed prototypes as ABI.

The resolver also has special battle-unit fallbacks. Most still compare the
descriptor and check actor byte `+38606`, but the active special-target branch
at `140C3F9F9..140C3FA10` returns battle `+182656` after its flag checks without
the descriptor comparison. A first native adapter must reject unproven special
branches, or reproduce their semantics with fixtures. The identifier's allocation
and reuse lifetime is still unproven; it is not itself a spawn generation.

Remaining target work is concrete: establish which copied action vector provides
the final target set for ordinary, redirected, countered and multi-hit actions at
the placement event; prove its mutation/lifetime window; and connect it to a
stable action token plus actor generation. No public Lua getter provides that
contract. The core deliberately requires a backend-certified `ResolvedAction`.

## Position and rotation receipt boundaries

| Owner / field | Observed write or use | Consequence |
| --- | --- | --- |
| actor `+38592` | handle used by `A437B0` and `A43E20` | Requires a valid, same-generation actor and handle. |
| handle `+264` | figure selected by `A1DC50` / `A1DC80` | Must remain the same figure throughout the receipt lifetime. |
| figure `+8536` | model/transform container used by `A1BD50` / `A1BE20` | Public joint-position queries may refer to a different owner. |
| figure `+8544..8559` | `A1BD50` copies the input base-position vector | This is not necessarily the displayed, height-adjusted position. |
| figure `+8564`, `+8568`, `+8580`, evaluator at `+8592` | `A1BD50` evaluates a height offset and adds it to the base position | Capturing displayed position alone would double-apply an offset when restoring through this setter. Exact height-state lifetime remains to prove. |
| figure byte `+8560` | `A1BD50` clears it after setting position | Receipt must account for this side effect; this report does not assign an unproven flag name. |
| resolved transform `+128..143` | `21CCB0` writes height-adjusted position | Must restore only owned position state using a verified owner path. |
| resolved transform `+112..127` | `21CD30`, called by `A1BE20`, copies a quaternion | A yaw call affects quaternion state; saving one yaw float is insufficient for exact restoration. |
| actor byte `+38605` | rotation lock; `B14900` clears it, calls `A43E20`, then sets it | Restore the prior lock deliberately, not by replaying the public setter. |
| actor `+38400..38460` | `A43E20` changes the rotation tween state | Selective receipt must preserve the touched fields and the correct clock policy. |
| actor byte `+38320`, tween at `+38336..38392` | `A43430` starts a position tween; `A41700` updates and writes position | A direct position setter does not stop this tween. Acquisition needs proven arbitration or suspension to prevent the next native frame overwriting placement. |

`A43E20` does nothing while the rotation lock is set. Otherwise it forwards the
quaternion, copies tween endpoint `+38416..38431` to `+38400..38415`, zeros
`+38432..38439`, writes `0x000000003F800000` at `+38440..38447`, clears byte
`+38448` and dword `+38452`, then sets byte `+38448` if byte `+38460` is zero.
It does not overwrite every byte in the surrounding struct. A future receipt
must specify exact fields rather than memcpy a broad actor region.

`A469C0` updates the tween elapsed/progress/completion fields. `A41700` supplies
actor-speed-scaled delta (`delta * actor[38600]`); the pseudocode omits a float
argument at one call, so ABI/clock work must use the instructions too. Restoring
a pre-lease tween snapshot after elapsed time would implicitly rewind it;
resuming, rebasing or finishing the tween needs an explicit source/native policy.
None is silently chosen by this scaffold.

`21CCB0`/`21CD30` select the first model entry and may lazily resolve/cache its
transform through a virtual call before writing. Their reconstructed code has
unsafe empty-container paths for an unvalidated caller. No raw pointer or native
delegate is exposed by the core.

## Minimum adapter still unsupported

1. An ABI-verified current-action and coroutine association, including yield and
   every abort/destruction path, before copied action storage is freed.
2. Final resolved-target timing and special-unit semantics, plus generation
   authority that rejects pointer and descriptor-identifier reuse.
3. Selective position/rotation receipt capture, mutation arbitration and exact
   tween/height/lock restoration with a proven clock policy. No whole TRS copy.
4. Callback thread/ordering proof, retained hook/delegate lifetime and teardown
   ordering while the actor/model still exists.

The native seams are concrete enough for additional offline layout fixtures and
adapter design. They are not yet sufficient for a live placement implementation.
