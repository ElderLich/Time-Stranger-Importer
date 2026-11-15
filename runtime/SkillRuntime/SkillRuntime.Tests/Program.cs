using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Tasks;
using SkillRuntime.Core;

internal static class Program
{
    private sealed class State
    {
        public Point3 Position = new Point3(1, 2, 3);
        public float Yaw = 0.25f;
        public int PositionTween = 17, YawTween = 23;
        public bool RotationLock;
        public float Scale = 1;
        public string Animation = "idle";
    }
    private sealed class FakeBackend : IActionBackend
    {
        public bool IsAvailable { get; set; } = true;
        public string UnavailableReason => "Fixture unavailable";
        public readonly Dictionary<ActionHandle, ResolvedAction> Actions = new();
        public readonly Dictionary<ActorIdentity, State> Actors = new();
        public readonly List<Lease> Leases = new();
        public bool FailCapture, WrongReceipt, FailApply, FailRelease;
        public Action? DuringRelease;
        public Action? DuringApply, DuringCapture, DuringLiveCheck;
        public ResolvedAction CaptureResolvedAction(ActionHandle handle) => Actions[handle];
        public bool IsSameLiveActor(ActorIdentity actor)
        { var callback = DuringLiveCheck; DuringLiveCheck = null; callback?.Invoke(); return Actors.ContainsKey(actor); }
        public IPlacementLease CapturePlacement(ActorIdentity actor, PlacementChannels channels)
        {
            if (FailCapture) throw new IOException("capture fault");
            var lease = new Lease(this, WrongReceipt ? new ActorIdentity(actor.Battle, actor.Spawn + 100, actor.Group, actor.Index) : actor,
                Actors[actor], channels);
            Leases.Add(lease); DuringCapture?.Invoke(); return lease;
        }
        public sealed class Lease : IPlacementLease
        {
            private readonly FakeBackend _backend;
            private readonly State _state;
            private readonly Point3 _position;
            private readonly float _yaw;
            private readonly int _positionTween, _yawTween;
            private readonly bool _rotationLock;
            public ActorIdentity Actor { get; }
            public PlacementChannels Channels { get; }
            public int ReleaseCount, ApplyCount;
            public bool Restored;
            public EndReason Reason;
            public Lease(FakeBackend backend, ActorIdentity actor, State state, PlacementChannels channels)
            {
                _backend = backend; Actor = actor; _state = state; Channels = channels;
                _position = state.Position; _yaw = state.Yaw;
                _positionTween = state.PositionTween; _yawTween = state.YawTween; _rotationLock = state.RotationLock;
            }
            public void Apply(Placement placement)
            {
                ApplyCount++;
                if (placement.Position.HasValue) { _state.Position = placement.Position.Value; _state.PositionTween = 0; }
                if (placement.YawRadians.HasValue) { _state.Yaw = placement.YawRadians.Value; _state.YawTween = 0; _state.RotationLock = true; }
                _backend.DuringApply?.Invoke();
                if (_backend.FailApply) throw new IOException("partial apply fault");
            }
            public void Release(EndReason reason, bool restoreAllowed)
            {
                ReleaseCount++; Reason = reason;
                _backend.DuringRelease?.Invoke();
                if (_backend.FailRelease) throw new IOException("release fault");
                Restored = restoreAllowed;
                if (!restoreAllowed) return;
                if ((Channels & PlacementChannels.Position) != 0) { _state.Position = _position; _state.PositionTween = _positionTween; }
                if ((Channels & PlacementChannels.Yaw) != 0) { _state.Yaw = _yaw; _state.YawTween = _yawTween; _state.RotationLock = _rotationLock; }
            }
        }
    }
    private sealed class Fixture : IDisposable
    {
        public readonly FakeBackend Backend = new();
        public readonly ActorIdentity Caster = new(1, 1, 0, 0), Target = new(1, 2, 1, 0);
        public readonly ActionHandle Handle = new(1, 1);
        public readonly ActionContexts Contexts;
        public Fixture()
        {
            Backend.Actors[Caster] = new State(); Backend.Actors[Target] = new State();
            Backend.Actions[Handle] = new ResolvedAction(Handle, Caster, new[] { Target });
            Contexts = new ActionContexts(Backend);
        }
        public ActionToken Acquire(PlacementChannels channels = PlacementChannels.Position | PlacementChannels.Yaw)
        { var token = Contexts.Begin(Handle); Contexts.AcquirePlacement(token, channels); return token; }
        public void Dispose() => Contexts.Dispose();
    }
    private static void Check(bool value, string message = "assertion failed") { if (!value) throw new Exception(message); }
    private static T Throws<T>(Action action) where T : Exception
    {
        try { action(); } catch (T error) { return error; }
        throw new Exception("Expected " + typeof(T).Name);
    }
    private static byte[] FixturePe()
    {
        var data = new byte[512]; using var stream = new MemoryStream(data); using var writer = new BinaryWriter(stream);
        writer.Write((ushort)0x5A4D); stream.Position = 0x3C; writer.Write(0x80);
        stream.Position = 0x80; writer.Write(0x4550); writer.Write((ushort)0x8664);
        stream.Position = 0x94; writer.Write((ushort)240);
        stream.Position = 0x98; writer.Write((ushort)0x20B);
        stream.Position = 0xB0; writer.Write((ulong)0x140000000);
        stream.Position = 0xD0; writer.Write((uint)0x1000000); return data;
    }
    private static NativeProfile FixtureProfile(byte[] data, uint rva = 0x1000) => new("fixture", Convert.ToHexString(SHA256.HashData(data)), 0x140000000, new[] { new NativeSeam("fixture", rva) });
    private static ProfileAssessment Assess(byte[] bytes, NativeProfile? profile = null)
    { using var stream = new MemoryStream(bytes); return NativeProfileValidator.Assess(stream, profile ?? FixtureProfile(bytes)); }

    private static readonly (string Name, Action Run)[] Tests = {
        ("known fingerprint still refuses activation", () => {
            var result = Assess(FixturePe()); Check(result.FingerprintMatches); Check(!result.RuntimeEnabled); Check(result.Refusals.Count == 7);
        }),
        ("mismatched executable refuses", () => {
            var result = Assess(FixturePe(), NativeProfile.TimeStrangerResearch); Check(!result.FingerprintMatches); Check(!result.RuntimeEnabled);
        }),
        ("PE architecture and bounds validated", () => {
            var bytes = FixturePe(); bytes[0x84] = 0x4C; bytes[0x85] = 1; Check(!Assess(bytes).FingerprintMatches);
            bytes = FixturePe(); bytes[0x3C] = 0xFF; bytes[0x3F] = 0x7F; Check(!Assess(bytes).FingerprintMatches);
            Check(!Assess(new byte[4]).FingerprintMatches);
            bytes = FixturePe(); Check(!Assess(bytes, FixtureProfile(bytes, 0x1000000)).FingerprintMatches);
        }),
        ("profile assessment preserves caller stream position", () => {
            var bytes = FixturePe(); using var stream = new MemoryStream(bytes); stream.Position = 7;
            NativeProfileValidator.Assess(stream, FixtureProfile(bytes)); Check(stream.Position == 7);
        }),
        ("unavailable backend fails before capture", () => {
            using var f = new Fixture(); f.Backend.IsAvailable = false; Throws<NotSupportedException>(() => f.Contexts.Begin(f.Handle)); Check(f.Backend.Leases.Count == 0);
        }),
        ("resolved target snapshot is defensive and deduplicated", () => {
            using var f = new Fixture(); var list = new List<ActorIdentity> { f.Target, f.Target };
            f.Backend.Actions[f.Handle] = new ResolvedAction(f.Handle, f.Caster, list); list.Clear(); list.Add(f.Caster);
            var token = f.Contexts.Begin(f.Handle); Check(f.Contexts.RequireSingleTarget(token).Equals(f.Target));
            Check(f.Contexts.Inspect(token).Targets.Count == 1);
        }),
        ("no first-target or empty-target substitution", () => {
            using var f = new Fixture(); f.Backend.Actions[f.Handle] = new ResolvedAction(f.Handle, f.Caster, new[] { f.Target, f.Caster });
            var token = f.Contexts.Begin(f.Handle); Throws<NotSupportedException>(() => f.Contexts.AcquirePlacement(token, PlacementChannels.Position));
            var handle = new ActionHandle(1, 2); f.Backend.Actions[handle] = new ResolvedAction(handle, f.Caster, Array.Empty<ActorIdentity>());
            token = f.Contexts.Begin(handle); Throws<NotSupportedException>(() => f.Contexts.RequireSingleTarget(token)); Check(f.Backend.Leases.Count == 0);
        }),
        ("nested action cannot steal caster lease", () => {
            using var f = new Fixture(); var first = f.Acquire(); var secondHandle = new ActionHandle(1, 2);
            f.Backend.Actions[secondHandle] = new ResolvedAction(secondHandle, f.Caster, new[] { f.Target }); var second = f.Contexts.Begin(secondHandle);
            Throws<InvalidOperationException>(() => f.Contexts.AcquirePlacement(second, PlacementChannels.Position));
            f.Contexts.End(first, EndReason.Completed); f.Contexts.AcquirePlacement(second, PlacementChannels.Position); Check(f.Backend.Leases.Count == 2);
        }),
        ("owned channels restore without overwriting unrelated state", () => {
            using var f = new Fixture(); var token = f.Acquire(PlacementChannels.Position); var state = f.Backend.Actors[f.Caster];
            f.Contexts.Apply(token, new Placement(new Point3(9, 8, 7))); state.Yaw = 2; state.YawTween = 77; state.RotationLock = true; state.Scale = 3; state.Animation = "hit";
            f.Contexts.End(token, EndReason.Completed); Check(state.Position.X == 1 && state.PositionTween == 17);
            Check(state.Yaw == 2 && state.YawTween == 77 && state.RotationLock && state.Scale == 3 && state.Animation == "hit");
        }),
        ("unowned channel and nonfinite placement rejected", () => {
            using var f = new Fixture(); var token = f.Acquire(PlacementChannels.Position);
            Throws<InvalidOperationException>(() => f.Contexts.Apply(token, new Placement(yawRadians: 2))); Check(f.Backend.Leases[0].ApplyCount == 0);
            Throws<ArgumentException>(() => new Placement(yawRadians: float.NaN)); Throws<ArgumentException>(() => new Point3(float.PositiveInfinity, 0, 0));
            Throws<ArgumentException>(() => new Placement());
        }),
        ("yaw lease restores its lock and tween only", () => {
            using var f = new Fixture(); var token = f.Acquire(PlacementChannels.Yaw); var state = f.Backend.Actors[f.Caster];
            f.Contexts.Apply(token, new Placement(yawRadians: 2)); state.Position = new Point3(9, 9, 9); state.PositionTween = 99;
            f.Contexts.End(token, EndReason.ScriptAborted); Check(state.Yaw == .25f && state.YawTween == 23 && !state.RotationLock);
            Check(state.Position.X == 9 && state.PositionTween == 99);
        }),
        ("stale actor generation discards receipt without restore", () => {
            using var f = new Fixture(); var token = f.Acquire(); f.Backend.Actors.Remove(f.Caster);
            var replacement = new ActorIdentity(1, 3, 0, 0); var state = new State { Yaw = 9 }; f.Backend.Actors[replacement] = state;
            Throws<InvalidOperationException>(() => f.Contexts.Apply(token, new Placement(yawRadians: 2)));
            Check(f.Backend.Leases[0].ReleaseCount == 1 && !f.Backend.Leases[0].Restored && state.Yaw == 9);
        }),
        ("target loss restores live caster and ends action", () => {
            using var f = new Fixture(); var token = f.Acquire(); f.Contexts.Apply(token, new Placement(yawRadians: 2)); f.Backend.Actors.Remove(f.Target);
            Throws<InvalidOperationException>(() => f.Contexts.Apply(token, new Placement(yawRadians: 3)));
            Check(f.Backend.Actors[f.Caster].Yaw == .25f); Check(f.Backend.Leases[0].Reason == EndReason.TargetLost);
        }),
        ("duplicate and reentrant release execute once", () => {
            using var f = new Fixture(); var token = f.Acquire(); f.Backend.DuringRelease = () => Check(f.Contexts.End(token, EndReason.Cancelled).AlreadyEnded);
            Check(!f.Contexts.End(token, EndReason.Completed).AlreadyEnded); Check(f.Contexts.End(token, EndReason.Cancelled).AlreadyEnded);
            Check(f.Backend.Leases[0].ReleaseCount == 1);
        }),
        ("reentrant release cannot acquire actor before restoration", () => {
            using var f = new Fixture(); var token = f.Acquire(); var handle = new ActionHandle(1, 2);
            f.Backend.Actions[handle] = new ResolvedAction(handle, f.Caster, new[] { f.Target }); var next = f.Contexts.Begin(handle);
            f.Backend.DuringRelease = () => Throws<InvalidOperationException>(() => f.Contexts.AcquirePlacement(next, PlacementChannels.Yaw));
            f.Contexts.End(token, EndReason.Completed); f.Backend.DuringRelease = null; f.Contexts.AcquirePlacement(next, PlacementChannels.Yaw);
        }),
        ("partial backend write faults trigger cleanup", () => {
            using var f = new Fixture(); var token = f.Acquire(); f.Backend.FailApply = true;
            Throws<IOException>(() => f.Contexts.Apply(token, new Placement(yawRadians: 2))); Check(f.Backend.Actors[f.Caster].Yaw == .25f);
            Check(f.Backend.Leases[0].Reason == EndReason.BackendFault && f.Backend.Leases[0].ReleaseCount == 1);
        }),
        ("capture failure rolls back ownership reservation", () => {
            using var f = new Fixture(); var token = f.Contexts.Begin(f.Handle); f.Backend.FailCapture = true;
            Throws<IOException>(() => f.Contexts.AcquirePlacement(token, PlacementChannels.Yaw)); f.Backend.FailCapture = false;
            f.Contexts.AcquirePlacement(token, PlacementChannels.Yaw); Check(f.Backend.Leases.Count == 1);
        }),
        ("wrong receipt is discarded and cannot restore", () => {
            using var f = new Fixture(); var token = f.Contexts.Begin(f.Handle); f.Backend.WrongReceipt = true;
            Throws<InvalidOperationException>(() => f.Contexts.AcquirePlacement(token, PlacementChannels.Position));
            Check(f.Backend.Leases[0].ReleaseCount == 1 && !f.Backend.Leases[0].Restored); f.Backend.WrongReceipt = false;
            f.Contexts.AcquirePlacement(token, PlacementChannels.Position);
        }),
        ("foreign stale and replayed action handles rejected", () => {
            using var f = new Fixture(); using var g = new Fixture(); var token = f.Acquire();
            Throws<ArgumentException>(() => g.Contexts.End(token, EndReason.Completed)); f.Contexts.End(token, EndReason.Completed);
            Throws<InvalidOperationException>(() => f.Contexts.Apply(token, new Placement(yawRadians: 1)));
            Throws<InvalidOperationException>(() => f.Contexts.Begin(f.Handle)); Throws<ArgumentException>(() => f.Contexts.Begin(default));
        }),
        ("thread affinity enforced", () => {
            using var f = new Fixture(); Task.Run(() => Throws<InvalidOperationException>(() => f.Contexts.Begin(f.Handle))).GetAwaiter().GetResult();
        }),
        ("battle teardown is scoped and prevents new actions", () => {
            using var f = new Fixture(); f.Acquire(); var actor = new ActorIdentity(2, 1, 0, 0); var handle = new ActionHandle(2, 1);
            f.Backend.Actors[actor] = new State(); f.Backend.Actions[handle] = new ResolvedAction(handle, actor, new[] { actor });
            var token = f.Contexts.Begin(handle); f.Contexts.AcquirePlacement(token, PlacementChannels.Yaw); f.Contexts.EndBattle(1);
            Check(f.Backend.Leases[0].ReleaseCount == 1 && f.Backend.Leases[1].ReleaseCount == 0); f.Contexts.Apply(token, new Placement(yawRadians: 2));
            var next = new ActionHandle(1, 2); Throws<InvalidOperationException>(() => f.Contexts.Begin(next));
        }),
        ("cleanup failures do not prevent other leases releasing", () => {
            using var f = new Fixture(); f.Acquire(); var handle = new ActionHandle(1, 2);
            f.Backend.Actions[handle] = new ResolvedAction(handle, f.Target, new[] { f.Caster }); var token = f.Contexts.Begin(handle);
            f.Contexts.AcquirePlacement(token, PlacementChannels.Yaw); f.Backend.FailRelease = true; var report = f.Contexts.EndBattle(1);
            Check(report.Failures.Count == 2 && f.Contexts.CleanupFailures.Count == 2); Check(f.Backend.Leases.All(l => l.ReleaseCount == 1));
            f.Contexts.EndBattle(1); Check(f.Backend.Leases.All(l => l.ReleaseCount == 1));
        }),
        ("dispose is idempotent and refuses future work", () => {
            var f = new Fixture(); f.Acquire(); f.Dispose(); f.Dispose(); Check(f.Backend.Leases[0].ReleaseCount == 1);
            Check(f.Contexts.DisposalReport!.Failures.Count == 0); Throws<ObjectDisposedException>(() => f.Contexts.Begin(new ActionHandle(1, 2)));
        }),
        ("failed restoration quarantines actor generation", () => {
            using var f = new Fixture(); var token = f.Acquire(); f.Backend.FailRelease = true;
            Check(f.Contexts.End(token, EndReason.Cancelled).Failures.Count == 1); f.Backend.FailRelease = false;
            var handle = new ActionHandle(1, 2); f.Backend.Actions[handle] = new ResolvedAction(handle, f.Caster, new[] { f.Target });
            var next = f.Contexts.Begin(handle); Throws<InvalidOperationException>(() => f.Contexts.AcquirePlacement(next, PlacementChannels.Yaw));
            Check(f.Backend.Leases.Count == 1);
        }),
        ("abort during backend apply defers restore until final write", () => {
            using var f = new Fixture(); var token = f.Acquire();
            f.Backend.DuringApply = () => {
                Check(f.Contexts.End(token, EndReason.ScriptAborted).DeferredUntilApplyReturns);
                Check(f.Backend.Leases[0].ReleaseCount == 0);
                Throws<InvalidOperationException>(() => f.Contexts.Apply(token, new Placement(yawRadians: 4)));
                f.Backend.Actors[f.Caster].Yaw = 7;
            };
            f.Contexts.Apply(token, new Placement(yawRadians: 2));
            Check(f.Backend.Leases[0].ReleaseCount == 1 && f.Backend.Actors[f.Caster].Yaw == .25f);
        }),
        ("abort during validation cannot apply a released receipt", () => {
            using var f = new Fixture(); var token = f.Acquire();
            f.Backend.DuringLiveCheck = () => f.Contexts.End(token, EndReason.Cancelled);
            Throws<InvalidOperationException>(() => f.Contexts.Apply(token, new Placement(yawRadians: 2)));
            Check(f.Backend.Leases[0].ReleaseCount == 1 && f.Backend.Leases[0].ApplyCount == 0);
        }),
        ("revoked backend availability ends existing placement", () => {
            using var f = new Fixture(); var token = f.Acquire(); f.Backend.IsAvailable = false;
            Throws<NotSupportedException>(() => f.Contexts.Apply(token, new Placement(yawRadians: 2)));
            Check(f.Backend.Leases[0].ReleaseCount == 1 && f.Backend.Leases[0].ApplyCount == 0);
        }),
        ("abort during capture discards unaccepted receipt", () => {
            using var f = new Fixture(); var token = f.Contexts.Begin(f.Handle);
            f.Backend.DuringCapture = () => f.Contexts.End(token, EndReason.Cancelled);
            Throws<InvalidOperationException>(() => f.Contexts.AcquirePlacement(token, PlacementChannels.Position));
            Check(f.Backend.Leases[0].ReleaseCount == 1 && !f.Backend.Leases[0].Restored);
        }),
    };
    private static int Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "--assess")
        {
            var result = NativeProfileValidator.Assess(args[1], NativeProfile.TimeStrangerResearch);
            Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true })); return result.FingerprintMatches ? 0 : 2;
        }
        if (args.Length != 0) { Console.Error.WriteLine("Usage: SkillRuntime.Tests [--assess EXECUTABLE]"); return 2; }
        int failures = 0;
        foreach (var test in Tests)
        {
            try { test.Run(); Console.WriteLine("PASS " + test.Name); }
            catch (Exception ex) { failures++; Console.Error.WriteLine("FAIL " + test.Name + ": " + ex); }
        }
        Console.WriteLine(JsonSerializer.Serialize(new { tests = Tests.Length, passed = Tests.Length - failures, failed = failures, nativeRuntimeEnabled = false }));
        return failures == 0 ? 0 : 1;
    }
}
